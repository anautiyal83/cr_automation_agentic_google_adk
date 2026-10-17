"""Milestone nodes — pause pipeline for user review.

Each milestone sets milestone_N_waiting=True in session state and
waits on an asyncio.Event until the runner signals a decision.
"""
from __future__ import annotations

import asyncio
import logging

from google.adk.agents import BaseAgent

logger = logging.getLogger(__name__)

# Shared registries — used by both milestone nodes and runner
milestone_events: dict[int, asyncio.Event] = {}   # step → Event (signaled by runner)
milestone_decisions: dict[int, str] = {}            # step → decision
milestone_waiting: dict[int, dict] = {}             # step → {waiting, name, status}


class MilestoneNode(BaseAgent):
    """Pauses pipeline for user review at a specific step."""
    name: str = "milestone"
    description: str = "Pause for user review"
    step_num: int = 0
    milestone_name: str = ""

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(self, ctx):
        step = self.step_num
        status = ctx.session.state.get(f"step_{step}_status", "passed")

        logger.info("Milestone Step %d (%s): status=%s — awaiting review",
                     step, self.milestone_name, status)

        # Signal via shared global dict (visible to monitor task)
        milestone_waiting[step] = {
            "waiting": True,
            "name": self.milestone_name,
            "status": status,
        }

        # Also set in session state for compatibility
        ctx.session.state[f"milestone_{step}_waiting"] = True
        ctx.session.state[f"milestone_{step}_step_name"] = self.milestone_name
        ctx.session.state[f"milestone_{step}_step_status"] = status

        # Wait on the asyncio.Event (set by runner.respond_milestone)
        evt = milestone_events.get(step)
        if evt:
            await evt.wait()
            evt.clear()  # Reset for potential re-use
        else:
            logger.warning("No asyncio.Event for milestone %d — falling back to polling", step)
            while step not in milestone_decisions:
                await asyncio.sleep(0.5)

        decision = milestone_decisions.pop(step, "continue")
        milestone_waiting.pop(step, None)
        ctx.session.state[f"milestone_{step}_waiting"] = False
        ctx.session.state[f"milestone_{step}_decision"] = decision
        ctx.session.state[f"milestone_{step}_decision_action"] = decision

        logger.info("Milestone Step %d: decision=%s", step, decision)

        if decision == "cancel":
            ctx.session.state["pipeline_cancelled"] = True

        return
        yield  # unreachable — async generator


def create_milestone_node(step_num: int, name: str) -> MilestoneNode:
    """Factory for milestone nodes."""
    node = MilestoneNode()
    node.name = f"milestone_step{step_num}"
    node.description = f"Milestone: Review {name} (Step {step_num})"
    node.step_num = step_num
    node.milestone_name = name
    return node
