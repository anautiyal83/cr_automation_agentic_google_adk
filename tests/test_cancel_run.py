"""OB-103: Cancel while an agent is at work stopped nothing — cancel_run answered a review of "step 0" and set the
flag on a copy of the ADK session, never the cancel action the pipeline loop reads (run 7e668a02 went on to its
step 5 review and stayed "running")."""
from __future__ import annotations

import asyncio

from orchestrator.runner import PipelineRunner
from shared.state import milestone_decisions, milestone_events, milestone_waiting, pipeline_actions


def _runner(run_id: str, status: str) -> PipelineRunner:
    runner = PipelineRunner.__new__(PipelineRunner)  # no ADK / DB: cancel_run only touches the shared state
    runner._active_runs = {run_id: {"status": status, "started_by": "u1", "uc_name": "UC"}}
    return runner


def test_cancel_while_an_agent_works_reaches_the_pipeline_loop():
    runner = _runner("run-a", "running")
    try:
        asyncio.run(runner.cancel_run("run-a"))
        assert pipeline_actions.get("run-a") == {"action": "cancel"}  # read before / after every agent
        assert 0 not in milestone_decisions  # no stale answer for a question round that is not waiting
        assert runner.get_status("run-a")["status"] == "cancelling"
        # still at work until its agent finishes: no other run starts beside it
        assert runner.active_run() == {"run_id": "run-a", "started_by": "u1", "status": "cancelling"}
    finally:
        pipeline_actions.pop("run-a", None)


def test_cancel_at_a_review_answers_that_review():
    runner = _runner("run-b", "paused_milestone")

    async def scenario():
        milestone_events[5] = asyncio.Event()
        milestone_waiting[5] = {"waiting": True, "name": "QA-Validation", "status": "passed"}
        await runner.cancel_run("run-b")
        await asyncio.sleep(0)
        return milestone_events[5].is_set()

    try:
        assert asyncio.run(scenario())
        assert milestone_decisions.get(5) == "cancel"
        assert pipeline_actions.get("run-b") == {"action": "cancel"}
        assert runner.get_status("run-b")["status"] == "cancelling"
    finally:
        pipeline_actions.pop("run-b", None)
        milestone_events.pop(5, None)
        milestone_waiting.pop(5, None)
        milestone_decisions.pop(5, None)


def test_a_finished_run_keeps_its_status():
    runner = _runner("run-c", "completed")
    try:
        asyncio.run(runner.cancel_run("run-c"))
        assert runner.get_status("run-c")["status"] == "completed"
        assert runner.active_run() is None
    finally:
        pipeline_actions.pop("run-c", None)
