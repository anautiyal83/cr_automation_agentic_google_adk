"""Pipeline step nodes — FunctionNode wrappers for each pipeline step.

Each node reads from / writes to session state (ctx.state) and sets
ctx.route for conditional routing decisions.

State keys convention:
  - step_{N}_status: "passed" | "failed" | "skipped"
  - step_{N}_output: dict with step output data
  - artifacts: dict of generated YAML/config content
  - exec_feedback: list of execution failure details
  - qa_feedback: list of QA test failure details
  - milestone_{N}_decision: "continue" | "rerun" | "cancel"
  - retry_count_{N}: int tracking retries per step
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

from google.adk.agents import BaseAgent

logger = logging.getLogger(__name__)

from src.nodes.cr_import import get_agent_class, import_cr_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _call_existing_agent(agent_cls, input_data: dict, config: dict) -> dict:
    """Call an existing cr_automation-agentic agent and return its output."""
    agent = agent_cls() if not hasattr(agent_cls, '__init__') else agent_cls
    models_mod = import_cr_module("src.core.models")
    RetryContext = models_mod.RetryContext
    retry_ctx = None
    if input_data.get("retry_context"):
        retry_ctx = RetryContext(**input_data["retry_context"])

    output = await agent.execute(
        input_data=input_data,
        config=config,
        few_shot_examples=input_data.get("few_shot_examples"),
        retry_context=retry_ctx,
    )
    return output.model_dump()


def _get_config_from_state(state: dict) -> dict:
    """Extract pipeline config from session state."""
    return state.get("pipeline_config", {})


def _get_step_data(state: dict) -> dict:
    """Build step_data dict from session state (same structure existing agents expect)."""
    return {
        "uc_name": state.get("uc_name", ""),
        "run_dir": state.get("run_dir", ""),
        "ciq_file_path": state.get("ciq_file_path", ""),
        "document_set": state.get("document_set", {}),
        "intermediate_repr": state.get("intermediate_repr", {}),
        "ciq_data": state.get("ciq_data", {}),
        "mop_data": state.get("mop_data", {}),
        "constraints": state.get("constraints", {}),
        "command_outputs": state.get("command_outputs", {}),
        "artifacts": state.get("artifacts", {}),
        "qa_feedback": state.get("qa_feedback", []),
        "exec_feedback": state.get("exec_feedback", []),
        "few_shot_examples": state.get("few_shot_examples", []),
        "step_results": state.get("step_results", []),
        "run_data": state.get("run_data", {}),
    }


def _save_step_result(state: dict, step_num: int, result: dict):
    """Save step output back into session state."""
    state[f"step_{step_num}_status"] = result.get("status", "passed")
    state[f"step_{step_num}_output"] = result.get("output_data", {})

    # Merge output_data into step_data fields
    output = result.get("output_data", {})
    for key in ("intermediate_repr", "ciq_data", "mop_data", "constraints",
                "command_outputs", "workflow_yaml", "simulator_config_yaml"):
        if key in output:
            if key in ("workflow_yaml", "simulator_config_yaml"):
                artifacts = state.get("artifacts", {})
                artifacts[key] = output[key]
                state["artifacts"] = artifacts
            else:
                state[key] = output[key]

    # Track step results for final report
    results = state.get("step_results", [])
    results.append({
        "step_number": step_num,
        "step_name": STEP_NAMES.get(step_num, f"Step {step_num}"),
        "status": result.get("status", "passed"),
        "error_message": str(result.get("error", ""))[:500] if result.get("error") else "",
    })
    state["step_results"] = results


STEP_NAMES = {
    1: "DOC Parser", 2: "DOC Analyzer", 3: "CIQ Config Generation",
    4: "Generate CIQ JSON Config", 5: "QA-Validation", 6: "Generate Execution Config",
    7: "QA-MOP", 8: "Simulator Setup", 9: "QA-Execution", 10: "Final Report",
}

MAX_RETRIES = 3


# ---------------------------------------------------------------------------
# Step 1: Document Parser
# ---------------------------------------------------------------------------
class DocParserNode(BaseAgent):
    name: str = "step1_doc_parser"
    description: str = "Step 1: Parse CIQ/MOP documents into intermediate representation"

    async def _run_async_impl(self, ctx):
        DocParserAgent = get_agent_class("src.agents.doc_parser_agent", "DocParserAgent")
        logger.info("Step 1: Document Parser — starting")
        step_data = _get_step_data(ctx.session.state)
        config = _get_config_from_state(ctx.session.state)
        result = await _call_existing_agent(DocParserAgent(), step_data, config)
        _save_step_result(ctx.session.state, 1, result)
        logger.info("Step 1: Document Parser — %s", result.get("status"))
        yield  # Required for async generator

doc_parser_node = DocParserNode()


# ---------------------------------------------------------------------------
# Step 2: UC Analyzer
# ---------------------------------------------------------------------------
class UCAnalyzerNode(BaseAgent):
    name: str = "step2_uc_analyzer"
    description: str = "Step 2: Analyze and enrich UC intermediate representation"

    async def _run_async_impl(self, ctx):
        UCAnalyzerAgent = get_agent_class("src.agents.uc_analyzer_agent", "UCAnalyzerAgent")
        logger.info("Step 2: UC Analyzer — starting")
        step_data = _get_step_data(ctx.session.state)
        config = _get_config_from_state(ctx.session.state)
        result = await _call_existing_agent(UCAnalyzerAgent(), step_data, config)
        _save_step_result(ctx.session.state, 2, result)
        logger.info("Step 2: UC Analyzer — %s", result.get("status"))
        yield

uc_analyzer_node = UCAnalyzerNode()


# ---------------------------------------------------------------------------
# Step 3: Validation Rules Generation (LLM + retry)
# ---------------------------------------------------------------------------
class ValidationRulesNode(BaseAgent):
    name: str = "step3_validation_rules"
    description: str = "Step 3: Generate CIQ validation rules via LLM"

    async def _run_async_impl(self, ctx):
        ValidationGenAgent = get_agent_class("src.agents.validation_gen_agent", "ValidationGenAgent")
        logger.info("Step 3: Validation Rules — starting")

        retries = ctx.session.state.get("retry_count_3", 0)
        step_data = _get_step_data(ctx.session.state)
        config = _get_config_from_state(ctx.session.state)

        retry_context = None
        if retries > 0:
            prev_error = ctx.session.state.get("step_3_error", {})
            retry_context = {"attempt_number": retries + 1, "previous_error": prev_error}
            step_data["retry_context"] = retry_context

        result = await _call_existing_agent(ValidationGenAgent(), step_data, config)
        _save_step_result(ctx.session.state, 3, result)

        if result.get("status") == "failed":
            ctx.session.state["step_3_error"] = result.get("error", {})
            retries += 1
            ctx.session.state["retry_count_3"] = retries
            if retries < MAX_RETRIES:
                logger.warning("Step 3 failed (attempt %d/%d) — will retry", retries, MAX_RETRIES)
            else:
                logger.error("Step 3 exhausted %d retries", MAX_RETRIES)

        logger.info("Step 3: Validation Rules — %s", result.get("status"))
        yield

validation_rules_node = ValidationRulesNode()


# ---------------------------------------------------------------------------
# Step 4: JSON Template Generation (LLM + retry)
# ---------------------------------------------------------------------------
class JsonTemplateNode(BaseAgent):
    name: str = "step4_json_template"
    description: str = "Step 4: Generate CIQ JSON output template via LLM"

    async def _run_async_impl(self, ctx):
        JsonTemplateAgent = get_agent_class("src.agents.json_template_agent", "JsonTemplateAgent")
        logger.info("Step 4: JSON Template — starting")
        step_data = _get_step_data(ctx.session.state)
        config = _get_config_from_state(ctx.session.state)
        result = await _call_existing_agent(JsonTemplateAgent(), step_data, config)
        _save_step_result(ctx.session.state, 4, result)
        logger.info("Step 4: JSON Template — %s", result.get("status"))
        yield

json_template_node = JsonTemplateNode()


# ---------------------------------------------------------------------------
# Step 5: CIQ QA Validation (deterministic)
# ---------------------------------------------------------------------------
class CiqQANode(BaseAgent):
    name: str = "step5_ciq_qa"
    description: str = "Step 5: QA cycle — test validation rules + JSON template via ciq-processor JAR"

    async def _run_async_impl(self, ctx):
        TestExecutor = get_agent_class("src.agents.test_executor", "TestExecutor")
        logger.info("Step 5: CIQ QA — starting")
        step_data = _get_step_data(ctx.session.state)
        config = _get_config_from_state(ctx.session.state)
        executor = TestExecutor(mode="ciq")
        result = await _call_existing_agent(executor, step_data, config)
        _save_step_result(ctx.session.state, 5, result)

        # Save QA feedback for potential Step 3 retry
        if result.get("status") == "failed":
            test_cases = result.get("output_data", {}).get("test_cases", [])
            failed_tcs = [tc for tc in test_cases if tc.get("status") == "FAIL"]
            if failed_tcs:
                ctx.session.state["qa_feedback"] = failed_tcs

        logger.info("Step 5: CIQ QA — %s", result.get("status"))
        yield

ciq_qa_node = CiqQANode()


# ---------------------------------------------------------------------------
# Step 6: Workflow Generation (LLM + retry)
# ---------------------------------------------------------------------------
class WorkflowGenNode(BaseAgent):
    name: str = "step6_workflow_gen"
    description: str = "Step 6: Generate execution workflow YAML via LLM"

    async def _run_async_impl(self, ctx):
        WorkflowGenAgent = get_agent_class("src.agents.workflow_gen_agent", "WorkflowGenAgent")
        logger.info("Step 6: Workflow Gen — starting")
        step_data = _get_step_data(ctx.session.state)
        config = _get_config_from_state(ctx.session.state)
        result = await _call_existing_agent(WorkflowGenAgent(), step_data, config)
        _save_step_result(ctx.session.state, 6, result)
        logger.info("Step 6: Workflow Gen — %s", result.get("status"))
        yield

workflow_gen_node = WorkflowGenNode()


# ---------------------------------------------------------------------------
# Step 7: MOP Generation Test (deterministic)
# ---------------------------------------------------------------------------
class MopTestNode(BaseAgent):
    name: str = "step7_mop_test"
    description: str = "Step 7: Test MOP generation via cli-automation-standalone JAR"

    async def _run_async_impl(self, ctx):
        MopGenTester = get_agent_class("src.agents.mop_gen_tester", "MopGenTester")
        logger.info("Step 7: MOP Test — starting")
        step_data = _get_step_data(ctx.session.state)
        config = _get_config_from_state(ctx.session.state)
        result = await _call_existing_agent(MopGenTester(), step_data, config)
        _save_step_result(ctx.session.state, 7, result)
        logger.info("Step 7: MOP Test — %s", result.get("status"))
        yield

mop_test_node = MopTestNode()


# ---------------------------------------------------------------------------
# Step 8: Simulator Setup (LLM)
# ---------------------------------------------------------------------------
class SimulatorSetupNode(BaseAgent):
    name: str = "step8_simulator_setup"
    description: str = "Step 8: Generate MockWire simulator configs via LLM"

    async def _run_async_impl(self, ctx):
        SimulatorSetupAgent = get_agent_class("src.agents.simulator_setup", "SimulatorSetupAgent")
        logger.info("Step 8: Simulator Setup — starting")
        step_data = _get_step_data(ctx.session.state)
        config = _get_config_from_state(ctx.session.state)
        result = await _call_existing_agent(SimulatorSetupAgent(), step_data, config)
        _save_step_result(ctx.session.state, 8, result)
        logger.info("Step 8: Simulator Setup — %s", result.get("status"))
        yield

simulator_setup_node = SimulatorSetupNode()


# ---------------------------------------------------------------------------
# Step 9: Execution Test (deterministic)
# ---------------------------------------------------------------------------
class ExecutionTestNode(BaseAgent):
    name: str = "step9_execution_test"
    description: str = "Step 9: Execute workflow via cli-automation JAR + MockWire simulator"

    async def _run_async_impl(self, ctx):
        TestExecutor = get_agent_class("src.agents.test_executor", "TestExecutor")
        logger.info("Step 9: Execution Test — starting")
        step_data = _get_step_data(ctx.session.state)
        config = _get_config_from_state(ctx.session.state)
        executor = TestExecutor(mode="exec")
        result = await _call_existing_agent(executor, step_data, config)
        _save_step_result(ctx.session.state, 9, result)

        # Save execution feedback for potential Step 6/8 retry
        if result.get("status") == "failed":
            ctx.session.state["exec_feedback"] = _classify_exec_failures(
                ctx.session.state.get("run_dir", "")
            )

        logger.info("Step 9: Execution Test — %s", result.get("status"))
        yield

execution_test_node = ExecutionTestNode()


# ---------------------------------------------------------------------------
# Step 10: Final Report (deterministic)
# ---------------------------------------------------------------------------
class FinalReportNode(BaseAgent):
    name: str = "step10_final_report"
    description: str = "Step 10: Generate final HTML + Excel pipeline report"

    async def _run_async_impl(self, ctx):
        ReportGenerator = get_agent_class("src.agents.report_generator", "ReportGenerator")
        logger.info("Step 10: Final Report — starting")
        step_data = _get_step_data(ctx.session.state)
        config = _get_config_from_state(ctx.session.state)
        result = await _call_existing_agent(ReportGenerator(), step_data, config)
        _save_step_result(ctx.session.state, 10, result)
        logger.info("Step 10: Final Report — %s", result.get("status"))
        yield

final_report_node = FinalReportNode()


# ---------------------------------------------------------------------------
# Milestone Review Node (HITL pause)
# ---------------------------------------------------------------------------
def milestone_review_node(node_name: str, step_num: int, name: str) -> BaseAgent:
    """Create a milestone node that pauses for user review.

    Reads step_{step_num}_status from state. If passed, waits for user
    decision via state key milestone_{step_num}_decision.
    """
    class MilestoneNode(BaseAgent):
        name: str = node_name
        description: str = f"Milestone: Review {name} (Step {step_num})"

        async def _run_async_impl(self, ctx):
            status = ctx.session.state.get(f"step_{step_num}_status", "passed")
            logger.info("Milestone Step %d (%s): status=%s — awaiting user review", step_num, name, status)

            # Signal that we're waiting for review
            ctx.session.state[f"milestone_{step_num}_waiting"] = True
            ctx.session.state[f"milestone_{step_num}_step_name"] = name

            # Wait for user decision (polled externally)
            while True:
                decision = ctx.session.state.get(f"milestone_{step_num}_decision", "")
                if decision:
                    break
                await asyncio.sleep(0.5)

            ctx.session.state[f"milestone_{step_num}_waiting"] = False
            logger.info("Milestone Step %d: user decision = %s", step_num, decision)

            if decision == "cancel":
                ctx.session.state["pipeline_cancelled"] = True
                # Escalate to stop the pipeline
                from google.adk.events.event_actions import EventActions
                yield  # Signal cancellation
            yield

    return MilestoneNode()


# ---------------------------------------------------------------------------
# Failure Router Nodes (conditional retry logic)
# ---------------------------------------------------------------------------
class FailureRouterStep5(BaseAgent):
    """After Step 5: if failed → clear and loop back to Step 3, else continue."""
    name: str = "router_step5"
    description: str = "Route after CIQ QA: retry Step 3 if failed"

    async def _run_async_impl(self, ctx):
        status = ctx.session.state.get("step_5_status", "passed")
        decision = ctx.session.state.get("milestone_5_decision", "continue")

        if status == "failed" and decision == "rerun":
            retries = ctx.session.state.get("retry_count_3", 0)
            if retries < MAX_RETRIES:
                logger.info("Step 5 failed → routing back to Step 3 (retry %d)", retries + 1)
                ctx.session.state["retry_count_3"] = retries
                # Clear downstream artifacts
                artifacts = ctx.session.state.get("artifacts", {})
                artifacts.pop("validation_rules_yaml", None)
                artifacts.pop("json_template_yaml", None)
                ctx.session.state["artifacts"] = artifacts
                ctx.session.state["_retry_target"] = 3
            else:
                logger.error("Step 5: retry limit exhausted for Step 3")
                ctx.session.state["pipeline_failed"] = True
        yield

failure_router_step5_node = FailureRouterStep5()


class FailureRouterStep7(BaseAgent):
    """After Step 7: if failed → loop back to Step 6, else continue."""
    name: str = "router_step7"
    description: str = "Route after MOP Test: retry Step 6 if failed"

    async def _run_async_impl(self, ctx):
        status = ctx.session.state.get("step_7_status", "passed")
        decision = ctx.session.state.get("milestone_7_decision", "continue")

        if status == "failed" and decision == "rerun":
            retries = ctx.session.state.get("retry_count_6", 0)
            if retries < MAX_RETRIES:
                logger.info("Step 7 failed → routing back to Step 6 (retry %d)", retries + 1)
                ctx.session.state["retry_count_6"] = retries + 1
                artifacts = ctx.session.state.get("artifacts", {})
                artifacts.pop("workflow_yaml", None)
                ctx.session.state["artifacts"] = artifacts
                ctx.session.state["_retry_target"] = 6
            else:
                logger.error("Step 7: retry limit exhausted for Step 6")
                ctx.session.state["pipeline_failed"] = True
        yield

failure_router_step7_node = FailureRouterStep7()


class FailureRouterStep9(BaseAgent):
    """After Step 9: classify failure and route to Step 6 or Step 8."""
    name: str = "router_step9"
    description: str = "Route after Execution Test: retry Step 6 (workflow) or Step 8 (simulator)"

    async def _run_async_impl(self, ctx):
        status = ctx.session.state.get("step_9_status", "passed")
        decision = ctx.session.state.get("milestone_9_decision", "continue")

        if status == "failed" and decision == "rerun":
            feedback = ctx.session.state.get("exec_feedback", [])
            target = _classify_retry_target(feedback)

            retries_key = f"retry_count_{target}"
            retries = ctx.session.state.get(retries_key, 0)

            if retries < MAX_RETRIES:
                logger.info("Step 9 failed → routing back to Step %d (retry %d)", target, retries + 1)
                ctx.session.state[retries_key] = retries + 1

                artifacts = ctx.session.state.get("artifacts", {})
                if target <= 6:
                    artifacts.pop("workflow_yaml", None)
                    artifacts.pop("simulator_config_yaml", None)
                else:
                    artifacts.pop("simulator_config_yaml", None)
                ctx.session.state["artifacts"] = artifacts
                ctx.session.state["_retry_target"] = target
            else:
                logger.error("Step 9: retry limit exhausted for Step %d", target)
                ctx.session.state["pipeline_failed"] = True
        yield

failure_router_step9_node = FailureRouterStep9()


# ---------------------------------------------------------------------------
# Failure Classification
# ---------------------------------------------------------------------------

# Patterns indicating workflow issues (route to Step 6)
_WORKFLOW_ERROR_PATTERNS = [
    "Cannot create property",
    "mapping values are not allowed",
    "PatternSyntaxException",
    "named capturing group",
    "node not found: null",
    "Unable to find property",
    "YAML syntax",
    "validation_status.*failed",
]

# Patterns indicating simulator issues (route to Step 8)
_SIMULATOR_ERROR_PATTERNS = [
    "Connection refused",
    "ssh login failed",
    "command not found",
    "prompt not reached",
    "Failed to start MockWire",
    "empty response",
]


def _classify_retry_target(feedback: list[dict]) -> int:
    """Classify execution failures → Step 6 (workflow) or Step 8 (simulator).

    Returns 6 for workflow issues, 8 for simulator issues.
    """
    if not feedback:
        return 6  # default: regenerate workflow

    workflow_score = 0
    simulator_score = 0

    for item in feedback:
        text = json.dumps(item, default=str).lower()

        for pat in _WORKFLOW_ERROR_PATTERNS:
            if pat.lower() in text:
                workflow_score += 1

        for pat in _SIMULATOR_ERROR_PATTERNS:
            if pat.lower() in text:
                simulator_score += 1

    if simulator_score > workflow_score:
        return 8
    return 6


def _classify_exec_failures(run_dir: str) -> list[dict]:
    """Extract execution failure details from exec_output JSON files."""
    failures = []
    exec_dir = Path(run_dir) / "exec_output"
    if not exec_dir.exists():
        return failures

    for jf in exec_dir.glob("*.json"):
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
            for cmd_key, cmd_info in data.get("data", {}).items():
                if not cmd_info.get("success") or cmd_info.get("validation_status") == "failed":
                    failures.append({
                        "command": cmd_key,
                        "phase": cmd_info.get("phase", ""),
                        "output": cmd_info.get("output", "")[:300],
                        "failure": cmd_info.get("failure", ""),
                        "validation_conclusion": cmd_info.get("validation_conclusion", ""),
                        "validation_criteria": cmd_info.get("validation_criteria", ""),
                    })
        except Exception:
            pass
    return failures
