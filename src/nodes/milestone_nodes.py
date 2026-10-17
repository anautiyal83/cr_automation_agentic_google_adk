"""Milestone nodes — pause pipeline for user review.

Each milestone sets milestone_N_waiting=True in session state and
waits on an asyncio.Event until the runner signals a decision.
"""
from __future__ import annotations

import asyncio
import logging

from google.adk.agents import BaseAgent

logger = logging.getLogger(__name__)

# Shared registry: milestone_step → asyncio.Event
# Managed by the runner (created before pipeline starts)
milestone_events: dict[int, asyncio.Event] = {}
milestone_decisions: dict[int, str] = {}


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

        # Signal that we're waiting
        ctx.session.state[f"milestone_{step}_waiting"] = True
        ctx.session.state[f"milestone_{step}_step_name"] = self.milestone_name
        ctx.session.state[f"milestone_{step}_step_status"] = status

        # Wait on the asyncio.Event (set by runner.respond_milestone)
        evt = milestone_events.get(step)
        if evt:
            await evt.wait()
            evt.clear()  # Reset for potential re-use
        else:
            # Fallback: poll state (shouldn't happen in normal operation)
            logger.warning("No asyncio.Event for milestone %d — falling back to polling", step)
            while not ctx.session.state.get(f"milestone_{step}_decision"):
                await asyncio.sleep(0.5)

        decision = ctx.session.state.get(f"milestone_{step}_decision", "continue")
        ctx.session.state[f"milestone_{step}_waiting"] = False
        # Clear decision for next potential use
        ctx.session.state.pop(f"milestone_{step}_decision", None)

        logger.info("Milestone Step %d: decision=%s", step, decision)

        if decision == "cancel":
            ctx.session.state["pipeline_cancelled"] = True

        yield  # Required for async generator


def create_milestone_node(step_num: int, name: str) -> MilestoneNode:
    """Factory for milestone nodes."""
    node = MilestoneNode()
    node.name = f"milestone_step{step_num}"
    node.description = f"Milestone: Review {name} (Step {step_num})"
    node.step_num = step_num
    node.milestone_name = name
    return node
