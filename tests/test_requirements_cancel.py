"""OB-19: Cancel at a step 1 question round ends the run (it was read as "0 answers" and the next
round was asked; run 8aee1c6d stayed "running")."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agents.requirements_agent import RequirementsGathererNode, RunCancelled
from shared.state import milestone_decisions, milestone_events, pipeline_actions


def test_cancel_at_a_question_round_ends_the_run():
    ctx = SimpleNamespace(session=SimpleNamespace(state={"run_id": "run-x"}))
    node = RequirementsGathererNode()

    async def scenario():
        task = asyncio.create_task(node._ask_developer(ctx, [{"id": "q1", "question": "?"}], 1, ""))
        while 0 not in milestone_events and not task.done():
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.01)
        milestone_decisions[0] = "cancel"
        milestone_events[0].set()
        with pytest.raises(RunCancelled):
            await task

    try:
        asyncio.run(scenario())
        assert pipeline_actions.get("run-x") == {"action": "cancel"}
    finally:
        pipeline_actions.pop("run-x", None)
        milestone_events.pop(0, None)


def test_answers_still_come_back():
    ctx = SimpleNamespace(session=SimpleNamespace(state={"run_id": "run-y"}))
    node = RequirementsGathererNode()

    async def scenario():
        task = asyncio.create_task(node._ask_developer(ctx, [{"id": "q1", "question": "?"}], 1, ""))
        while 0 not in milestone_events:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.01)
        milestone_decisions[0] = '{"q1": "yes"}'
        milestone_events[0].set()
        return await task

    try:
        assert asyncio.run(scenario()) == {"q1": "yes"}
        assert "run-y" not in pipeline_actions
    finally:
        milestone_events.pop(0, None)
