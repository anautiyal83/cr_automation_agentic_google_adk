"""Milestone nodes — pause pipeline for user review.

Each milestone sets milestone_N_waiting=True in session state and
waits on an asyncio.Event until the runner signals a decision.
"""
from __future__ import annotations

import asyncio
import logging

from google.adk.agents import BaseAgent

logger = logging.getLogger(__name__)

# Shared registries — used by milestone nodes, runner, and pipeline agent
milestone_events: dict[int, asyncio.Event] = {}   # step → Event (signaled by runner)
milestone_decisions: dict[int, str] = {}            # step → decision
milestone_waiting: dict[int, dict] = {}             # step → {waiting, name, status}

# Pipeline-level actions — set by milestone nodes, read by PipelineAgent
# This bypasses ADK session state which may use copies/deltas
pipeline_actions: dict[str, dict] = {}  # run_id → {"action": "rerun"/"cancel", "target_step": N}

# Reviewer feedback — set by MilestoneReviewerNode, read by step nodes on retry
reviewer_feedback: dict[str, str] = {}  # "run_id:step_num" → feedback text


class MilestoneNode(BaseAgent):
    """Pauses pipeline for user review at a specific step."""
    name: str = "milestone"
    description: str = "Pause for user review"
    step_num: int = 0
    milestone_name: str = ""

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(self, ctx):
        from pathlib import Path
        step = self.step_num

        # Determine step status from disk (ADK session state is unreliable)
        run_dir = ctx.session.state.get("run_dir", "")
        err_file = Path(run_dir) / f"step_{step}_error.json" if run_dir else None
        status = "failed" if (err_file and err_file.exists()) else "passed"

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

        run_id = ctx.session.state.get("run_id", "")
        logger.info("Milestone Step %d: decision=%s, run_id=%s", step, decision, run_id[:8])

        # Write action to shared dict (bypasses ADK session state issues)
        if decision == "cancel":
            pipeline_actions[run_id] = {"action": "cancel"}
            logger.info("Milestone Step %d: pipeline_actions → cancel", step)
        elif decision == "rerun":
            # Default rerun targets for test steps
            _DEFAULT_RERUN = {
                5: 3,   # QA-Validation → Step 3 (Validation Rules)
                7: 6,   # QA-MOP → Step 6 (Workflow Gen)
            }
            target = _DEFAULT_RERUN.get(step, step)

            # For step 9: use reviewer classification to determine target dynamically
            if step == 9:
                import json as _json
                fb = reviewer_feedback.get(f"{run_id}:9", "")
                if fb:
                    try:
                        fb_data = _json.loads(fb) if isinstance(fb, str) else fb
                        classification = fb_data.get("classification", "")
                        reviewer_target = fb_data.get("retry_target")
                        if reviewer_target in (6, 8):
                            target = reviewer_target
                            logger.info("Milestone Step 9: reviewer classified as '%s' → target step %d",
                                        classification, target)
                    except (_json.JSONDecodeError, TypeError):
                        pass
                if target == step:
                    # Fallback: default to step 6 for workflow issues
                    target = 6

            ctx.session.state[f"retry_count_{target}"] = 0
            pipeline_actions[run_id] = {"action": "rerun", "target_step": target}
            logger.info("Milestone Step %d: pipeline_actions → rerun step %d (retry counter reset)", step, target)

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
