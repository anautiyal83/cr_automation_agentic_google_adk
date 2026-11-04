"""Spec 007 phase 1: the run queue in PipelineRunner (launching is stubbed — queue logic only)."""
from __future__ import annotations

import asyncio

import pytest

from orchestrator.runner import PipelineRunner, QueueRefused
from storage.db import init_database


def _runner(tmp_path, monkeypatch, fail_uc: str = ""):
    launched: list[str] = []

    async def fake_launch(self, run_id, document_set, review_milestones, started_by):
        if document_set.get("uc_name") == fail_uc:
            raise RuntimeError("cannot start")
        launched.append(document_set["uc_name"])
        self._active_runs[run_id] = {"status": "running", "uc_name": document_set["uc_name"],
                                     "started_by": started_by, "review": review_milestones}

    monkeypatch.setattr(PipelineRunner, "_launch", fake_launch)

    async def make():
        return PipelineRunner(config={}, db=await init_database(str(tmp_path / "p.db")))
    return asyncio.run(make()), launched


async def _finish(runner, run_id, status="completed"):
    runner._active_runs[run_id]["status"] = status
    await runner._persist_run_status(run_id, status)
    await runner.dispatch()


def _doc(uc):
    return {"uc_name": uc, "ciq_file_path": f"{uc}.xlsx", "mop_file_path": f"{uc}.docx", "developer_notes": "n"}


async def _statuses(runner):
    rows = await runner._db.execute("SELECT r.id, r.status, d.uc_name FROM pipeline_runs r "
                                    "JOIN uc_document_sets d ON d.id = r.document_set_id ORDER BY r.queued_at")
    return {r["uc_name"]: r["status"] for r in rows}


def test_fifo_and_next_run_starts_by_itself(tmp_path, monkeypatch):
    runner, launched = _runner(tmp_path, monkeypatch)

    async def scenario():
        a = await runner.submit(_doc("UC_A"), started_by="u1")
        b = await runner.submit(_doc("UC_B"), started_by="u2", review_milestones=False)
        c = await runner.submit(_doc("UC_C"), started_by="u1")
        first = [a["status"], b["status"], b["position"], c["status"], c["position"]]
        await _finish(runner, a["run_id"])
        after_a = (list(launched), await runner.queue_position(c["run_id"]), await _statuses(runner))
        review = runner._active_runs[b["run_id"]]["review"]
        await _finish(runner, b["run_id"], "failed")
        return first, after_a, review, list(launched)
    first, (launched_after_a, c_position, statuses), review, final = asyncio.run(scenario())
    assert first == ["running", "queued", 1, "queued", 2]
    assert launched_after_a == ["UC_A", "UC_B"] and c_position == 1
    assert statuses == {"UC_A": "completed", "UC_B": "running", "UC_C": "queued"}
    assert review is False  # the queued run keeps its "review milestones" choice
    assert final == ["UC_A", "UC_B", "UC_C"]  # a failed run also frees the pipeline


def test_one_job_per_use_case_and_per_user_limit(tmp_path, monkeypatch):
    runner, _ = _runner(tmp_path, monkeypatch)

    async def scenario():
        await runner.submit(_doc("UC_A"), started_by="u1")                     # running
        refused = []
        for doc, user in ((_doc("UC_A"), "u2"), (_doc("UC_X"), "u1")):
            try:
                await runner.submit(doc, started_by=user)
            except QueueRefused as e:
                refused.append(str(e))
        await runner.submit(_doc("UC_Y"), started_by="u1")
        await runner.submit(_doc("UC_Z"), started_by="u1")                     # u1: X, Y, Z waiting
        try:
            await runner.submit(_doc("UC_W"), started_by="u1")
        except QueueRefused as e:
            refused.append(str(e))
        try:
            await runner.submit(_doc("UC_Y"), started_by="u2")                 # Y is waiting
        except QueueRefused as e:
            refused.append(str(e))
        await runner.submit(_doc("UC_V"), started_by="u2")                     # another user: fine
        return refused
    refused = asyncio.run(scenario())
    assert len(refused) == 3
    assert "UC_A already has a job in the pipeline (running)" in refused[0]
    assert "3 jobs waiting" in refused[1]
    assert "UC_Y already has a job in the pipeline (waiting)" in refused[2]


def test_remove_from_queue(tmp_path, monkeypatch):
    runner, launched = _runner(tmp_path, monkeypatch)

    async def scenario():
        a = await runner.submit(_doc("UC_A"), started_by="u1")
        b = await runner.submit(_doc("UC_B"), started_by="u2")
        c = await runner.submit(_doc("UC_C"), started_by="u3")
        removed = await runner.remove_queued(b["run_id"])
        again = await runner.remove_queued(b["run_id"])        # not waiting any more
        running = await runner.remove_queued(a["run_id"])      # the running one is not "removed"
        position = await runner.queue_position(c["run_id"])
        await _finish(runner, a["run_id"])
        return removed, again, running, position, list(launched), await _statuses(runner)
    removed, again, running, position, launched_now, statuses = asyncio.run(scenario())
    assert (removed, again, running, position) == (True, False, False, 1)
    assert launched_now == ["UC_A", "UC_C"] and statuses["UC_B"] == "removed"


def test_resume_after_restart(tmp_path, monkeypatch):
    runner, launched = _runner(tmp_path, monkeypatch)

    async def scenario():
        a = await runner.submit(_doc("UC_A"), started_by="u1")
        await runner.submit(_doc("UC_B"), started_by="u2")
        runner._active_runs.clear()                  # the server process ended: nothing is running
        started = await runner.resume_queue()
        return a, started, await _statuses(runner)
    a, started, statuses = asyncio.run(scenario())
    assert statuses == {"UC_A": "failed", "UC_B": "running"} and launched == ["UC_A", "UC_B"]


def test_single_start_when_dispatched_together(tmp_path, monkeypatch):
    runner, launched = _runner(tmp_path, monkeypatch)

    async def scenario():
        a = await runner.submit(_doc("UC_A"), started_by="u1")
        await runner.submit(_doc("UC_B"), started_by="u2")
        await runner.submit(_doc("UC_C"), started_by="u3")
        runner._active_runs[a["run_id"]]["status"] = "completed"
        await runner._persist_run_status(a["run_id"], "completed")
        await asyncio.gather(runner.dispatch(), runner.dispatch(), runner.dispatch())
        return await _statuses(runner)
    statuses = asyncio.run(scenario())
    assert list(statuses.values()).count("running") == 1 and launched == ["UC_A", "UC_B"]


def test_run_that_cannot_start_does_not_block_the_queue(tmp_path, monkeypatch):
    runner, launched = _runner(tmp_path, monkeypatch, fail_uc="UC_BAD")

    async def scenario():
        a = await runner.submit(_doc("UC_A"), started_by="u1")
        await runner.submit(_doc("UC_BAD"), started_by="u2")
        await runner.submit(_doc("UC_C"), started_by="u3")
        await _finish(runner, a["run_id"])
        await asyncio.sleep(0.05)  # the next dispatch is scheduled
        return await _statuses(runner)
    statuses = asyncio.run(scenario())
    assert statuses == {"UC_A": "completed", "UC_BAD": "failed", "UC_C": "running"}
    assert launched == ["UC_A", "UC_C"]


def test_without_db_runs_start_directly(monkeypatch):
    calls = []

    async def fake_launch(self, run_id, document_set, review_milestones, started_by):
        calls.append(run_id)

    monkeypatch.setattr(PipelineRunner, "_launch", fake_launch)
    result = asyncio.run(PipelineRunner(config={}).submit(_doc("UC_A")))
    assert result["status"] == "running" and calls == [result["run_id"]]


@pytest.fixture(autouse=True)
def _no_trail_noise(monkeypatch):
    """The dispatcher records run.dequeued — fine in the test DB; nothing to patch."""
    yield
