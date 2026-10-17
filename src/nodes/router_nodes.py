"""Failure router nodes — classify failures and set retry targets.

Each router reads the preceding test step's status and reviewer's analysis,
then sets _retry_target in session state to route the pipeline backward.
"""
from __future__ import annotations

import json
import logging

from google.adk.agents import BaseAgent

logger = logging.getLogger(__name__)

MAX_RETRIES = 3


class FailureRouterStep5(BaseAgent):
    """After Step 5 (CIQ QA): if failed and user chose rerun → retry Step 3."""
    name: str = "router_step5"
    description: str = "Route after CIQ QA: retry Step 3 if failed"

    async def _run_async_impl(self, ctx):
        status = ctx.session.state.get("step_5_status", "passed")
        decision = ctx.session.state.get("milestone_5_decision_action", "continue")

        if status == "failed" and decision == "rerun":
            retries = ctx.session.state.get("retry_count_3", 0)
            if retries < MAX_RETRIES:
                retries += 1
                ctx.session.state["retry_count_3"] = retries
                # Clear downstream artifacts
                artifacts = ctx.session.state.get("artifacts", {})
                artifacts.pop("validation_rules_yaml", None)
                artifacts.pop("json_template_yaml", None)
                ctx.session.state["artifacts"] = artifacts
                ctx.session.state["_retry_target"] = 3
                logger.info("Router5: Step 5 failed → retry Step 3 (attempt %d/%d)", retries, MAX_RETRIES)
            else:
                ctx.session.state["pipeline_failed"] = True
                logger.error("Router5: retry limit exhausted for Step 3")
        return
        yield  # unreachable


class FailureRouterStep7(BaseAgent):
    """After Step 7 (MOP Test): if failed and user chose rerun → retry Step 6."""
    name: str = "router_step7"
    description: str = "Route after MOP Test: retry Step 6 if failed"

    async def _run_async_impl(self, ctx):
        status = ctx.session.state.get("step_7_status", "passed")
        decision = ctx.session.state.get("milestone_7_decision_action", "continue")

        if status == "failed" and decision == "rerun":
            retries = ctx.session.state.get("retry_count_6", 0)
            if retries < MAX_RETRIES:
                retries += 1
                ctx.session.state["retry_count_6"] = retries
                artifacts = ctx.session.state.get("artifacts", {})
                artifacts.pop("workflow_yaml", None)
                ctx.session.state["artifacts"] = artifacts
                ctx.session.state["_retry_target"] = 6
                logger.info("Router7: Step 7 failed → retry Step 6 (attempt %d/%d)", retries, MAX_RETRIES)
            else:
                ctx.session.state["pipeline_failed"] = True
                logger.error("Router7: retry limit exhausted for Step 6")
        return
        yield  # unreachable


class FailureRouterStep9(BaseAgent):
    """After Step 9 (Execution Test): classify failure and route to Step 6 or 8."""
    name: str = "router_step9"
    description: str = "Route after Execution Test: retry Step 6 (workflow) or Step 8 (simulator)"

    async def _run_async_impl(self, ctx):
        status = ctx.session.state.get("step_9_status", "passed")
        decision = ctx.session.state.get("milestone_9_decision_action", "continue")

        if status == "failed" and decision == "rerun":
            # Use reviewer's classification if available
            reviewer_analysis = ctx.session.state.get("reviewer_9_analysis", {})
            target = reviewer_analysis.get("retry_target", 6)

            # Fallback to pattern-based classification
            if not reviewer_analysis:
                feedback = ctx.session.state.get("exec_feedback", [])
                target = _classify_retry_target(feedback)

            retries_key = f"retry_count_{target}"
            retries = ctx.session.state.get(retries_key, 0)

            if retries < MAX_RETRIES:
                retries += 1
                ctx.session.state[retries_key] = retries
                artifacts = ctx.session.state.get("artifacts", {})
                if target <= 6:
                    artifacts.pop("workflow_yaml", None)
                    artifacts.pop("simulator_config_yaml", None)
                else:
                    artifacts.pop("simulator_config_yaml", None)
                ctx.session.state["artifacts"] = artifacts
                ctx.session.state["_retry_target"] = target
                logger.info("Router9: Step 9 failed → retry Step %d (attempt %d/%d)", target, retries, MAX_RETRIES)
            else:
                ctx.session.state["pipeline_failed"] = True
                logger.error("Router9: retry limit exhausted for Step %d", target)
        return
        yield  # unreachable


# Patterns indicating workflow issues (route to Step 6)
_WORKFLOW_PATTERNS = [
    "cannot create property", "mapping values are not allowed",
    "patternsyntaxexception", "named capturing group",
    "node not found: null", "unable to find property",
]

# Patterns indicating simulator issues (route to Step 8)
_SIMULATOR_PATTERNS = [
    "connection refused", "ssh login failed", "command not found",
    "prompt not reached", "failed to start mockwire",
]


def _classify_retry_target(feedback: list[dict]) -> int:
    """Classify execution failures → Step 6 (workflow) or Step 8 (simulator)."""
    if not feedback:
        return 6

    workflow_score = 0
    simulator_score = 0

    for item in feedback:
        text = json.dumps(item, default=str).lower()
        workflow_score += sum(1 for p in _WORKFLOW_PATTERNS if p in text)
        simulator_score += sum(1 for p in _SIMULATOR_PATTERNS if p in text)

    if simulator_score > workflow_score:
        return 8
    return 6
