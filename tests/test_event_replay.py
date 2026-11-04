"""Live Run catches up with step events published before it subscribed (re-run with cached steps).

    python -m pytest tests/test_event_replay.py -v
"""
from __future__ import annotations

import asyncio
from uuid import uuid4

from core.events import EventBus, PipelineEvent


def test_late_subscriber_gets_the_runs_step_events():
    bus = EventBus()
    run, other = uuid4(), uuid4()
    seen: list[tuple[str, int | None]] = []

    async def scenario():
        # a re-run: cached steps 1-2 finish before the page subscribes
        for etype, step in [("run_started", None), ("step_started", 1), ("step_completed", 1),
                            ("step_completed", 2), ("milestone_reached", 2)]:
            await bus.publish(PipelineEvent(run_id=run, event_type=etype, step_number=step))
        await bus.publish(PipelineEvent(run_id=other, event_type="step_completed", step_number=1))
        bus.subscribe(lambda e: seen.append((e.event_type, e.step_number)), run)
        n = await bus.replay(lambda e: seen.append((e.event_type, e.step_number)), run)
        await bus.publish(PipelineEvent(run_id=run, event_type="step_started", step_number=3))
        return n
    n = asyncio.run(scenario())
    assert n == 4
    assert seen == [("step_started", 1), ("step_completed", 1), ("step_completed", 2),
                    ("milestone_reached", 2), ("step_started", 3)]


def test_answered_review_is_not_replayed():
    bus = EventBus()
    run = uuid4()
    seen: list[str] = []

    async def scenario():
        for etype, step in [("milestone_reached", 2), ("step_started", 3)]:
            await bus.publish(PipelineEvent(run_id=run, event_type=etype, step_number=step))
        await bus.replay(lambda e: seen.append(e.event_type), run)
    asyncio.run(scenario())
    assert seen == ["step_started"]
