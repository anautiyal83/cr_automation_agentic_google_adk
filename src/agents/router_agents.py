"""Failure router nodes — classify failures and set retry targets.

These routers run AFTER the milestone for test steps (5, 7, 9).
They check if the user chose 'continue' despite test failures,
and for Step 9, classify whether to retry workflow (step 6) or simulator (step 8).

NOTE: Rerun routing is handled by milestone_nodes.py via pipeline_actions.
These routers handle the 'continue despite failure' case and auto-retry logic.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from google.adk.agents import BaseAgent

from src.shared.state import pipeline_actions, reviewer_feedback as _fb_store

logger = logging.getLogger(__name__)

MAX_RETRIES = 3


class FailureRouterStep5(BaseAgent):
    """After Step 5 milestone: no-op — rerun handled by milestone_actions."""
    name: str = "router_step5"
    description: str = "Route after CIQ QA (handled by milestone)"

    async def _run_async_impl(self, ctx):
        # Rerun is handled by milestone_nodes.py pipeline_actions
        # This router is kept for pipeline structure but is a no-op
        return
        yield


class FailureRouterStep7(BaseAgent):
    """After Step 7 milestone: no-op — rerun handled by milestone_actions."""
    name: str = "router_step7"
    description: str = "Route after MOP Test (handled by milestone)"

    async def _run_async_impl(self, ctx):
        return
        yield


class FailureRouterStep9(BaseAgent):
    """After Step 9 milestone: if user continues despite failure, classify for next retry."""
    name: str = "router_step9"
    description: str = "Route after Execution Test — classify workflow vs simulator failure"

    async def _run_async_impl(self, ctx):
        # Check if step 9 failed by looking at error file on disk
        run_dir = ctx.session.state.get("run_dir", "")
        err_file = Path(run_dir) / "step_9_error.json" if run_dir else None
        if not err_file or not err_file.exists():
            return
            yield

        # Read reviewer feedback for classification
        run_id = ctx.session.state.get("run_id", "")
        feedback = _fb_store.get(f"{run_id}:9", "")

        # Try to extract retry_target from reviewer feedback
        target = 6  # default: regenerate workflow
        if feedback:
            try:
                fb_data = json.loads(feedback)
                if isinstance(fb_data, dict):
                    target = fb_data.get("retry_target", 6)
            except (json.JSONDecodeError, TypeError):
                pass

        # Fallback: pattern-based classification from error file
        if target == 6 and err_file.exists():
            try:
                err_text = err_file.read_text(encoding="utf-8").lower()
                sim_score = sum(1 for p in _SIMULATOR_PATTERNS if p in err_text)
                wf_score = sum(1 for p in _WORKFLOW_PATTERNS if p in err_text)
                if sim_score > wf_score:
                    target = 8
            except Exception:
                pass

        logger.info("Router9: classified failure → target step %d", target)
        return
        yield


# Patterns for classification
_WORKFLOW_PATTERNS = [
    "cannot create property", "mapping values are not allowed",
    "patternsyntaxexception", "named capturing group",
    "node not found: null", "unable to find property",
]

_SIMULATOR_PATTERNS = [
    "connection refused", "ssh login failed", "command not found",
    "prompt not reached", "failed to start mockwire",
]
