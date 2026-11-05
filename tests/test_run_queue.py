"""Spec 007 phase 1: the run queue in PipelineRunner (launching is stubbed — queue logic only)."""
from __future__ import annotations

import asyncio

import pytest

from orchestrator.runner import PipelineRunner, QueueRefused
from storage.db import init_database


def _runner(tmp_path, monkeypatch, fail_uc: str = ""):
    launched: list[str] = []

    async def fake_launch(self, run_id, document_set, review_milestones, started_by, resume=None):
        if document_set.get("uc_name") == fail_uc:
            raise RuntimeError("cannot start")
        launched.append(document_set["uc_name"] + (" (resumed)" if resume else ""))
        self._active_runs[run_id] = {"status": "running", "uc_name": document_set["uc_name"],
                                     "started_by": started_by, "review": review_milestones, "resume": resume}

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
    assert statuses == {"UC_A": "failed", "UC_B": "running"} and launched == ["UC_A", "UC_B"]  # no checkpoint


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

    async def fake_launch(self, run_id, document_set, review_milestones, started_by, resume=None):
        calls.append(run_id)

    monkeypatch.setattr(PipelineRunner, "_launch", fake_launch)
    result = asyncio.run(PipelineRunner(config={}).submit(_doc("UC_A")))
    assert result["status"] == "running" and calls == [result["run_id"]]


@pytest.fixture(autouse=True)
def _no_trail_noise(monkeypatch):
    """The dispatcher records run.dequeued — fine in the test DB; nothing to patch."""
    yield


def test_interrupted_run_resumes_ahead_of_the_queue(tmp_path, monkeypatch):
    from orchestrator import checkpoints
    runner, launched = _runner(tmp_path, monkeypatch)
    run_dir = tmp_path / "run_a"
    run_dir.mkdir()

    async def scenario():
        a = await runner.submit(_doc("UC_A"), started_by="u1")
        await runner.submit(_doc("UC_B"), started_by="u2")
        await checkpoints.save(a["run_id"], 3, "network_config_gen", "agent",
                               {"run_id": a["run_id"], "run_dir": str(run_dir), "artifacts": {"x": "y"}},
                               {"uc": "UC_A"})
        runner._active_runs.clear()                       # restart
        await runner.resume_queue()
        resumed = runner._active_runs[a["run_id"]]["resume"]
        first = (list(launched), await _statuses(runner))
        runner._active_runs.clear()                       # a second and a third restart at the same point
        await runner.resume_queue()
        runner._active_runs.clear()
        await runner.resume_queue()
        return resumed, first, await _statuses(runner), list(launched)
    resumed, (launched_1, statuses_1), statuses_3, launched_3 = asyncio.run(scenario())
    assert launched_1 == ["UC_A", "UC_A (resumed)"] and statuses_1 == {"UC_A": "running", "UC_B": "queued"}
    assert resumed["agent_name"] == "network_config_gen" and resumed["state"]["artifacts"] == {"x": "y"}
    assert resumed["requirements"] == {"uc": "UC_A"}
    # resumed twice; the third interruption at the same step fails it and the queue goes on
    assert statuses_3 == {"UC_A": "failed", "UC_B": "running"}
    assert launched_3 == ["UC_A", "UC_A (resumed)", "UC_A (resumed)", "UC_B"]


def test_checkpoint_progress_resets_attempts_and_resume_positions(tmp_path):
    from orchestrator import checkpoints
    from orchestrator.pipeline_agent import _AGENT_DISPLAY_STEPS, _AGENT_NAMES, _first_agent_of_step

    async def scenario():
        db = await init_database(str(tmp_path / "c.db"))
        checkpoints.set_db(db)
        await checkpoints.save("r1", 3, "network_config_gen", "agent", {"a": 1}, None)
        await checkpoints.count_attempt(db, "r1")
        same = (await checkpoints.load(db, "r1"))["resume_attempts"]
        await checkpoints.save("r1", 3, "network_config_gen", "agent", {"a": 2}, None)   # same point
        kept = (await checkpoints.load(db, "r1"))["resume_attempts"]
        await checkpoints.save("r1", 4, "helper_script_gen", "agent", {"a": 3}, None)    # progress
        reset = await checkpoints.load(db, "r1")
        await checkpoints.delete(db, "r1")
        return same, kept, reset, await checkpoints.load(db, "r1")
    same, kept, reset, gone = asyncio.run(scenario())
    assert (same, kept, reset["resume_attempts"], reset["state"], gone) == (1, 1, 0, {"a": 3}, None)

    def pos(index, kind):
        return checkpoints.resume_position({"agent_index": index, "kind": kind}, _AGENT_NAMES,
                                           _AGENT_DISPLAY_STEPS, _first_agent_of_step)
    idx = _AGENT_NAMES.index
    assert pos(idx("network_config_gen"), "agent") == (idx("network_config_gen"), False)    # step 6 again
    assert pos(idx("network_config_exec_qa"), "agent") == (idx("network_simulator_setup"), False)  # 9 → 8
    assert pos(idx("ciq_validation_json_qa"), "milestone") == (idx("ciq_validation_json_qa"), True)  # review
    assert pos(idx("network_simulator_setup"), "milestone") == (idx("network_simulator_setup"), False)  # 8 again


def test_queue_snapshot_statuses_positions_estimates(tmp_path, monkeypatch):
    import time as _time
    from core.auth import ADMIN_ROLE, CurrentUser
    from orchestrator.run_queue import estimate_duration, snapshot
    from ui.pages.queue_panel import job_rows
    runner, _ = _runner(tmp_path, monkeypatch)
    ana = CurrentUser("u1", "ana@x.io", "Ana", "ops")
    admin = CurrentUser("a", "admin@x.io", "Admin", ADMIN_ROLE)

    async def scenario():
        db = runner._db
        # history: UC_B completed twice (working 300 s / 500 s), UC_C once without work_seconds
        for rid, uc, work, dur in (("h1", "UC_B", 300, 900), ("h2", "UC_B", 500, 600), ("h3", "UC_C", None, 240)):
            await db.execute_insert("INSERT INTO uc_document_sets (id, uc_name, ciq_file_path, mop_file_path, "
                                    "command_outputs_path, uploaded_at) VALUES (?, ?, '', '', '', '2026-11-04')",
                                    (rid, uc))
            await db.execute_insert("INSERT INTO pipeline_runs (id, document_set_id, status, started_at, completed_at, "
                                    "duration_seconds, work_seconds, started_by) VALUES (?, ?, 'completed', "
                                    "'2026-11-04T00:00:00+00:00', ?, ?, ?, 'u2')",
                                    (rid, rid, "2099-01-01T00:00:00+00:00", dur, work))
        est = (await estimate_duration(db, "UC_B"), await estimate_duration(db, "UC_C"),
               await estimate_duration(db, "UC_NEW"))
        a = await runner.submit(_doc("UC_A"), started_by="u2")      # running, another user
        b = await runner.submit(_doc("UC_B"), started_by="u1")      # waiting #1, Ana's
        c = await runner.submit(_doc("UC_C"), started_by="u2")      # waiting #2
        runner._active_runs[a["run_id"]].update(started_at=_time.monotonic() - 60, current_step=6)
        running = await snapshot(runner, ana)
        runner._active_runs[a["run_id"]].update(status="paused_milestone", milestone_step=5,
                                                paused_since=_time.monotonic(), paused_at="2026-11-04T10:00:00+00:00")
        paused = await snapshot(runner, ana)
        as_admin = await snapshot(runner, admin)
        return est, a, b, c, running, paused, as_admin
    est, a, b, c, running, paused, as_admin = asyncio.run(scenario())
    assert est == (400.0, 240.0, 300.0)  # median of working time; UC_C falls back to its duration; new UC: all UCs
    live = [j for j in running if j["status"] in ("running", "waiting")]
    assert [(j["uc_name"], j["status"], j["position"], j["owner"], j["can_remove"]) for j in live] == [
        ("UC_A", "running", 0, "Another user", False), ("UC_B", "waiting", 1, "You", True),
        ("UC_C", "waiting", 2, "Another user", False)]
    assert running[0]["step"] == 6 and running[1]["est_start"] < running[2]["est_start"]
    assert not running[1]["after_review"] and running[1]["est_seconds"] == 400.0
    assert {j["status"] for j in running[3:]} == {"completed"}             # recent finished jobs
    assert paused[0]["status"] == "paused" and paused[0]["step"] == 5 and paused[1]["after_review"]
    assert as_admin[0]["can_remove"] is False and "Another user" not in [j["owner"] for j in as_admin]
    assert as_admin[1]["can_remove"] and as_admin[2]["can_remove"]          # the admin removes any waiting job
    rows = job_rows(paused[:3])
    assert rows[0]["detail"] == "Paused — review of step 5" and rows[0]["estimate"] == "now"
    assert rows[1]["estimate"].startswith("after the review") and rows[1]["pos"] == "1"
    assert job_rows(running[:1])[0]["detail"] == "Running — step 6 / 10 Generate Execution Config"


def test_mark_finished_only_completed_runs_once(tmp_path, monkeypatch):
    """Spec 008 U-D7: a completed run can be marked finished (it leaves Live Run), once; a failed
    or still running one cannot; the status stays 'completed'."""
    runner, _ = _runner(tmp_path, monkeypatch)

    async def scenario():
        db = runner._db
        await db.execute_insert("INSERT INTO uc_document_sets (id, uc_name, ciq_file_path, mop_file_path, "
                                "command_outputs_path, uploaded_at) VALUES ('d', 'UC', '', '', '', '2026-11-04')")
        for rid, status in (("ok", "completed"), ("bad", "failed"), ("run", "running")):
            await db.execute_insert("INSERT INTO pipeline_runs (id, document_set_id, status, started_at, started_by) "
                                    "VALUES (?, 'd', ?, '2026-11-04T00:00:00+00:00', 'u1')", (rid, status))
        results = [await runner.mark_finished(r, "u1") for r in ("ok", "ok", "bad", "run", "missing")]
        row = (await db.execute("SELECT status, finished_at, finished_by FROM pipeline_runs WHERE id = 'ok'"))[0]
        return results, row
    results, row = asyncio.run(scenario())
    assert results == [True, False, False, False, False]
    assert row["status"] == "completed" and row["finished_at"] and row["finished_by"] == "u1"


def test_owner_liveness():
    """OB-10: a run's owner is <host>:<pid>:<process start>; a reused pid is not the owner."""
    import socket
    import subprocess
    import sys
    from orchestrator.ownership import owner_alive, this_owner
    import psutil
    assert owner_alive(this_owner())
    assert not owner_alive(None) and not owner_alive("garbage")
    assert owner_alive("other-host:1:1")                     # cannot be checked: left alone
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        started = int(psutil.Process(child.pid).create_time())
        host = socket.gethostname()
        assert owner_alive(f"{host}:{child.pid}:{started}")
        assert not owner_alive(f"{host}:{child.pid}:{started - 100}")  # same pid, another process
    finally:
        child.kill()
        child.wait()
    assert not owner_alive(f"{socket.gethostname()}:{child.pid}:{started}")


def test_second_server_leaves_the_live_run_alone(tmp_path, monkeypatch):
    """OB-10: a server starting on the same database neither resumes the run another live server
    executes nor starts a second pipeline next to it."""
    import socket
    import subprocess
    import sys
    import psutil
    runner, launched = _runner(tmp_path, monkeypatch)
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    owner = f"{socket.gethostname()}:{other.pid}:{int(psutil.Process(other.pid).create_time())}"

    async def scenario():
        a = await runner.submit(_doc("UC_A"), started_by="u1")
        await runner.submit(_doc("UC_B"), started_by="u2")
        await runner._db.execute("UPDATE pipeline_runs SET owner = ? WHERE id = ?", (owner, a["run_id"]))
        runner._active_runs.clear()                  # this is the second server: it holds nothing
        started = await runner.resume_queue()
        return started, await _statuses(runner)
    try:
        started, statuses = asyncio.run(scenario())
    finally:
        other.kill()
        other.wait()
    assert started is None and statuses == {"UC_A": "running", "UC_B": "queued"} and launched == ["UC_A"]


def _hanging_runner(tmp_path, monkeypatch):
    """A runner whose pipeline waits forever (paused at a review) with a checkpoint saved."""
    import types as _t
    from orchestrator import runner as runner_mod

    async def forever(**kw):
        await asyncio.Event().wait()
        yield None

    async def no_sim(run_id):
        return None
    monkeypatch.setattr(runner_mod, "stop_simulator", no_sim)
    monkeypatch.setattr(runner_mod, "_shutting_down", False)

    async def make():
        r = PipelineRunner(config={}, db=await init_database(str(tmp_path / "p.db")))
        r._runner = _t.SimpleNamespace(run_async=forever)
        return r
    return make


def test_server_stop_keeps_the_run_resumable(tmp_path, monkeypatch):
    """A server stop cancels the pipeline task: the run must stay live with its checkpoint so the
    next start resumes it (run e47b5409 came back as "cancelled — Pipeline task stopped")."""
    from orchestrator import checkpoints
    from orchestrator import runner as runner_mod
    make = _hanging_runner(tmp_path, monkeypatch)

    async def scenario(stop: bool):
        r = await make()
        run_id = "e47b5409-8db6-4445-bd5f-5c3cc59f926f" if stop else "5dcb049c-49ed-4676-8e54-8a13e2163e6e"
        await r._db.execute("INSERT INTO pipeline_runs (id, document_set_id, status, current_step, started_at) "
                            "VALUES (?, ?, 'paused_milestone', 9, '2026-11-04')", (run_id, run_id))
        await checkpoints.save(run_id, 7, "network_config_qa", "milestone", {"run_id": run_id}, {})
        r._active_runs[run_id] = {"status": "paused_milestone"}
        task = asyncio.ensure_future(r._run_pipeline(run_id, "u"))
        await asyncio.sleep(0.05)
        if stop:
            runner_mod.mark_shutdown()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        rows = await r._db.execute("SELECT status FROM pipeline_runs WHERE id = ?", (run_id,))
        return rows[0]["status"], await checkpoints.load(r._db, run_id) is not None
    assert asyncio.run(scenario(stop=True)) == ("paused_milestone", True)   # resumes at the next start
    monkeypatch.setattr(runner_mod, "_shutting_down", False)
    status, kept = asyncio.run(scenario(stop=False))                           # a task stopped otherwise
    assert status == "cancelled" and not kept


def test_resumed_run_shows_the_steps_it_does_again_as_pending():
    """Run 46e5fee1: resumed at step 8's review (step 8 runs again: its simulator is gone), step 9
    still showed "failed" from before the restart and no review buttons, for the whole step 8 re-run."""
    from orchestrator.pipeline_agent import _AGENT_DISPLAY_STEPS, _AGENT_NAMES
    sim = next(n for n in _AGENT_NAMES if 8 in _AGENT_DISPLAY_STEPS.get(n, []))
    done = {f"step_{i}_status": "passed" for i in range(1, 9)}
    state = PipelineRunner._resume_state("r1", {"state": {**done, "step_9_status": "failed"},
                                                "agent_index": _AGENT_NAMES.index(sim), "kind": "milestone"})
    assert [i for i in range(1, 11) if f"step_{i}_status" in state] == list(range(1, 8))
    mop = next(n for n in _AGENT_NAMES if 7 in _AGENT_DISPLAY_STEPS.get(n, []) and 9 not in _AGENT_DISPLAY_STEPS[n])
    state = PipelineRunner._resume_state("r1", {"state": dict(done), "agent_index": _AGENT_NAMES.index(mop),
                                                "kind": "milestone"})
    assert state["step_7_status"] == "passed" and "step_8_status" not in state   # the review of step 7 again
