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

from src.core.logging_config import get_flow_logger

logger = logging.getLogger(__name__)
flow = get_flow_logger(__name__)

from src.nodes.cr_import import get_agent_class, import_cr_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _call_existing_agent(agent_cls, input_data: dict, config: dict) -> dict:
    """Call an existing cr_automation-agentic agent and return its output."""
    import time as _time
    agent = agent_cls() if not hasattr(agent_cls, '__init__') else agent_cls
    models_mod = import_cr_module("src.core.models")
    RetryContext = models_mod.RetryContext
    retry_ctx = None
    if input_data.get("retry_context"):
        retry_ctx = RetryContext(**input_data["retry_context"])

    agent_name = type(agent).__name__
    flow.flow("CALL AGENT %s — input_keys=%s", agent_name, list(input_data.keys())[:8])
    t0 = _time.monotonic()

    output = await agent.execute(
        input_data=input_data,
        config=config,
        few_shot_examples=input_data.get("few_shot_examples"),
        retry_context=retry_ctx,
    )
    elapsed = _time.monotonic() - t0
    result = output.model_dump()
    flow.flow("AGENT RESULT %s — %.1fs — status=%s", agent_name, elapsed, result.get("status", "?"))
    return result


def _get_config_from_state(state: dict) -> dict:
    """Extract pipeline config from session state."""
    return state.get("pipeline_config", {})


def _get_step_data(state: dict) -> dict:
    """Build step_data dict from session state (same structure existing agents expect)."""
    # Remove empty/missing optional path keys from document_set
    # (prevents DocParserAgent from calling Path("") or Path(None))
    doc_set = dict(state.get("document_set", {}))
    for opt_key in ("mop_file_path", "constraints_file_path", "command_outputs_path"):
        if not doc_set.get(opt_key):
            doc_set.pop(opt_key, None)
    return {
        "uc_name": state.get("uc_name", ""),
        "run_dir": state.get("run_dir", ""),
        "ciq_file_path": state.get("ciq_file_path", ""),
        "document_set": doc_set,
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

    # Keys that are YAML/config artifacts — save to state["artifacts"]
    _ARTIFACT_KEYS = {
        "validation_rules_yaml", "json_template_yaml",
        "workflow_yaml", "simulator_config_yaml",
    }
    # Keys that are data — save directly to state
    _DATA_KEYS = {
        "intermediate_repr", "ciq_data", "mop_data", "constraints", "command_outputs",
    }

    for key in _DATA_KEYS:
        if key in output:
            state[key] = output[key]

    artifacts = state.get("artifacts", {})
    for key in _ARTIFACT_KEYS:
        if key in output and output[key]:
            artifacts[key] = output[key]
    state["artifacts"] = artifacts

    # Write artifact files to disk for milestone review
    run_dir = state.get("run_dir", "")
    uc_name = state.get("uc_name", "UC")
    if run_dir:
        import json as _json
        _artifact_filenames = {
            "validation_rules_yaml": f"{uc_name}_validation-rules.yaml",
            "json_template_yaml": f"{uc_name}_json-output.yaml",
            "workflow_yaml": f"{uc_name}.yaml",
            "simulator_config_yaml": f"{uc_name}_simulator-config.yaml",
        }
        for key, fname in _artifact_filenames.items():
            content = artifacts.get(key)
            if content:
                try:
                    out = Path(run_dir) / fname
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_text(content, encoding="utf-8")
                except Exception:
                    pass

        # Write error details for reviewer agents
        if result.get("status") == "failed":
            try:
                err_file = Path(run_dir) / f"step_{step_num}_error.json"
                err_data = {
                    "step_number": step_num,
                    "status": "failed",
                    "error": str(result.get("error", ""))[:2000],
                    "error_message": str(result.get("error_message", ""))[:2000],
                    "output_keys": list(output.keys()),
                }
                err_file.write_text(_json.dumps(err_data, indent=2), encoding="utf-8")
            except Exception:
                pass

    # Track step results for final report
    results = state.get("step_results", [])
    results.append({
        "step_number": step_num,
        "step_name": STEP_NAMES.get(step_num, f"Step {step_num}"),
        "status": result.get("status", "passed"),
        "error_message": str(result.get("error", ""))[:500] if result.get("error") else "",
    })
    state["step_results"] = results


def _inject_retry_context(state: dict, step_num: int, step_data: dict):
    """If this step has been retried, inject reviewer feedback into step_data."""
    from src.nodes.milestone_nodes import reviewer_feedback as _fb_store

    retries = state.get(f"retry_count_{step_num}", 0)
    if retries == 0:
        return

    prev_error = state.get(f"step_{step_num}_error", {})
    run_id = state.get("run_id", "")

    # Collect feedback from this step AND downstream test steps that triggered the rerun
    # e.g., step 3 retry may be caused by step 5 QA failure
    _DOWNSTREAM_STEPS = {3: [3, 5], 4: [4, 5], 6: [6, 7, 9], 8: [8, 9]}
    check_steps = _DOWNSTREAM_STEPS.get(step_num, [step_num])

    all_feedback = []
    for check_step in check_steps:
        fb = _fb_store.get(f"{run_id}:{check_step}", "")
        if fb:
            all_feedback.append(f"=== Reviewer feedback from Step {check_step} ===\n{fb}")

    combined_feedback = "\n\n".join(all_feedback) if all_feedback else ""

    if combined_feedback:
        if isinstance(prev_error, dict):
            prev_error["reviewer_analysis"] = combined_feedback
        else:
            prev_error = {"error": str(prev_error), "reviewer_analysis": combined_feedback}
        flow.flow("RETRY CONTEXT step %d — injected %d chars of reviewer feedback from steps %s",
                   step_num, len(combined_feedback), check_steps)

    step_data["retry_context"] = {
        "attempt_number": retries + 1,
        "previous_error": prev_error,
    }


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
        return
        yield  # unreachable — async generator

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
        return
        yield  # unreachable

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
        step_data = _get_step_data(ctx.session.state)
        config = _get_config_from_state(ctx.session.state)
        _inject_retry_context(ctx.session.state, 3, step_data)
        result = await _call_existing_agent(ValidationGenAgent(), step_data, config)
        _save_step_result(ctx.session.state, 3, result)
        if result.get("status") == "failed":
            ctx.session.state["step_3_error"] = result.get("error", {})
            ctx.session.state["retry_count_3"] = ctx.session.state.get("retry_count_3", 0) + 1
        logger.info("Step 3: Validation Rules — %s", result.get("status"))
        return
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
        _inject_retry_context(ctx.session.state, 4, step_data)
        result = await _call_existing_agent(JsonTemplateAgent(), step_data, config)
        _save_step_result(ctx.session.state, 4, result)
        if result.get("status") == "failed":
            ctx.session.state["step_4_error"] = result.get("error", {})
            ctx.session.state["retry_count_4"] = ctx.session.state.get("retry_count_4", 0) + 1
        logger.info("Step 4: JSON Template — %s", result.get("status"))
        return
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
        return
        yield  # unreachable

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
        _inject_retry_context(ctx.session.state, 6, step_data)
        result = await _call_existing_agent(WorkflowGenAgent(), step_data, config)
        _save_step_result(ctx.session.state, 6, result)
        if result.get("status") == "failed":
            ctx.session.state["step_6_error"] = result.get("error", {})
            ctx.session.state["retry_count_6"] = ctx.session.state.get("retry_count_6", 0) + 1
        logger.info("Step 6: Workflow Gen — %s", result.get("status"))
        return
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
        return
        yield  # unreachable

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
        _inject_retry_context(ctx.session.state, 8, step_data)
        result = await _call_existing_agent(SimulatorSetupAgent(), step_data, config)
        _save_step_result(ctx.session.state, 8, result)
        if result.get("status") == "failed":
            ctx.session.state["step_8_error"] = result.get("error", {})
            ctx.session.state["retry_count_8"] = ctx.session.state.get("retry_count_8", 0) + 1
        logger.info("Step 8: Simulator Setup — %s", result.get("status"))
        return
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

        # Generate command execution log
        run_dir = ctx.session.state.get("run_dir", "")
        if run_dir:
            _generate_exec_log(run_dir)

        logger.info("Step 9: Execution Test — %s", result.get("status"))
        return
        yield  # unreachable

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
        return
        yield  # unreachable

final_report_node = FinalReportNode()


# NOTE: Milestone, router, and classification code has been moved to
# milestone_nodes.py and router_nodes.py. Only _classify_exec_failures
# remains here as it's used by ExecutionTestNode.


def _generate_exec_log(run_dir: str):
    """Generate a command execution log from step 9 results.

    Writes exec_log.txt with per-command status summary.
    """
    import json as _json
    from datetime import datetime, timezone

    exec_dir = Path(run_dir) / "exec_output"
    if not exec_dir.exists():
        exec_dir.mkdir(parents=True, exist_ok=True)

    json_files = list(exec_dir.glob("*.json"))
    if not json_files:
        # No execution results — write a minimal log
        log_path = Path(run_dir) / "exec_log.txt"
        log_path.write_text(
            "=" * 80 + "\n"
            "COMMAND EXECUTION LOG\n"
            f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            "=" * 80 + "\n\n"
            "NO COMMANDS EXECUTED\n\n"
            "The CLI automation JAR produced no output. Possible causes:\n"
            "- MockWire simulator failed to start or authenticate\n"
            "- Workflow YAML has structural issues the JAR cannot parse\n"
            "- SSH connection to simulator timed out\n"
            "- CIQ JSON output is empty or malformed\n\n"
            "Check logs/error.log and logs/trace.log for details.\n",
            encoding="utf-8",
        )
        flow.flow("EXEC LOG: no execution results found in %s", exec_dir)
        return

    lines = [
        "=" * 80,
        "COMMAND EXECUTION LOG",
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "=" * 80,
        "",
    ]

    total = 0
    cmd_ok = 0
    cmd_fail = 0
    val_ok = 0
    val_fail = 0
    val_skip = 0
    phase_stats: dict[str, dict] = {}

    for jf in sorted(exec_dir.glob("*.json")):
        try:
            data = _json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            continue

        meta = data.get("metadata", {}).get("execution_summary", {})
        lines.append(f"Report: {jf.name}")
        lines.append(f"  Overall: {meta.get('overall_status', '?')}")
        lines.append(f"  Started: {meta.get('startDateAndTime', '?')}")
        lines.append(f"  Ended:   {meta.get('endDateAndTime', '?')}")
        lines.append("")

        for cmd_key, cmd_info in data.get("data", {}).items():
            total += 1
            phase = cmd_info.get("phase", "unknown")
            success = cmd_info.get("success", False)
            v_status = cmd_info.get("validation_status", "skipped")

            if success:
                cmd_ok += 1
            else:
                cmd_fail += 1
            if v_status == "success":
                val_ok += 1
            elif v_status == "failed":
                val_fail += 1
            else:
                val_skip += 1

            # Phase stats
            ps = phase_stats.setdefault(phase, {"total": 0, "cmd_ok": 0, "cmd_fail": 0, "val_ok": 0, "val_fail": 0})
            ps["total"] += 1
            ps["cmd_ok" if success else "cmd_fail"] += 1
            if v_status == "success":
                ps["val_ok"] += 1
            elif v_status == "failed":
                ps["val_fail"] += 1

            # Command line
            cmd_status = "OK" if success else "FAIL"
            val_tag = f"[val:{v_status}]" if v_status != "skipped" else ""
            cmd_short = cmd_key[:60].replace("\n", " ")
            lines.append(f"  [{cmd_status}]{val_tag} [{phase}] {cmd_short}")

            # Show failure details
            if not success or v_status == "failed":
                output = cmd_info.get("output", "")[:150].replace("\n", " ").replace("\r", "")
                reason = cmd_info.get("reason", "")[:100]
                conclusion = cmd_info.get("validation_conclusion", "")[:100]
                if output:
                    lines.append(f"    output: {output}")
                if reason:
                    lines.append(f"    reason: {reason}")
                if conclusion and v_status == "failed":
                    lines.append(f"    conclusion: {conclusion}")

        lines.append("")

    # Summary
    lines.insert(4, "")
    summary = [
        "-" * 80,
        "SUMMARY",
        "-" * 80,
        f"  Total commands:  {total}",
        f"  Command OK:      {cmd_ok}",
        f"  Command FAIL:    {cmd_fail}",
        f"  Validation OK:   {val_ok}",
        f"  Validation FAIL: {val_fail}",
        f"  Validation SKIP: {val_skip}",
        "",
        "PHASE BREAKDOWN:",
    ]
    for phase, ps in phase_stats.items():
        summary.append(f"  {phase}: {ps['total']} cmds | OK: {ps['cmd_ok']} | FAIL: {ps['cmd_fail']} | Val OK: {ps['val_ok']} | Val Fail: {ps['val_fail']}")
    summary.append("")
    summary.append("-" * 80)
    summary.append("")

    lines = lines[:5] + summary + lines[5:]

    log_path = Path(run_dir) / "exec_log.txt"
    log_path.write_text("\n".join(lines), encoding="utf-8")
    flow.flow("EXEC LOG written to %s — %d cmds, %d OK, %d fail", log_path, total, cmd_ok, cmd_fail)


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
