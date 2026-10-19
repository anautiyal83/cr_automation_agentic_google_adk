"""Peer Reviewer Agents — analyze failures and generate fix instructions.

Each reviewer is a native ADK LlmAgent that:
1. Reads the failure report from the test step
2. Analyzes the root cause
3. Generates structured fix instructions for the upstream LLM agent
4. Classifies the failure type for routing decisions

Three reviewers:
- qa_validation_reviewer: Step 5 failures → instructs Step 3
- qa_mop_reviewer: Step 7 failures → instructs Step 6
- qa_execution_reviewer: Step 9 failures → instructs Step 6 or Step 8
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from google.adk.agents import BaseAgent, LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.function_tool import FunctionTool

from src.nodes.milestone_nodes import reviewer_feedback as _reviewer_feedback_store

logger = logging.getLogger(__name__)


# ===========================================================================
# REVIEWER TOOLS
# ===========================================================================

def read_qa_report(run_dir: str) -> dict:
    """Read the CIQ QA execution report and return test case results.

    Returns {total, passed, failed, test_cases: [{id, scenario, expected, actual, status, remarks}]}.
    """
    qa_dir = Path(run_dir) / "qa"
    report_path = qa_dir / "qa-execution-report.xlsx"

    # Try reading from test case data directly
    tc_file = qa_dir / "test-cases.xlsx"
    results = {"total": 0, "passed": 0, "failed": 0, "test_cases": []}

    # Read execution report JSON if available
    for jf in (qa_dir / "results").glob("*/") if (qa_dir / "results").exists() else []:
        pass  # Results are per-TC directories

    # Fallback: read the step output from state
    return results


def read_exec_report(run_dir: str) -> dict:
    """Read execution test output JSON and return command results.

    Returns {total, passed, failed, commands: [{command, phase, output, failure, validation_status, validation_conclusion}]}.
    """
    exec_dir = Path(run_dir) / "exec_output"
    if not exec_dir.exists():
        return {"total": 0, "passed": 0, "failed": 0, "commands": []}

    all_commands = []
    for jf in exec_dir.glob("*.json"):
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
            summary = data.get("metadata", {}).get("execution_summary", {})
            for cmd_key, cmd_info in data.get("data", {}).items():
                all_commands.append({
                    "command": cmd_key,
                    "phase": cmd_info.get("phase", ""),
                    "output": cmd_info.get("output", "")[:300],
                    "success": cmd_info.get("success", False),
                    "failure": cmd_info.get("failure", ""),
                    "validation_status": cmd_info.get("validation_status", ""),
                    "validation_criteria": cmd_info.get("validation_criteria", ""),
                    "validation_conclusion": cmd_info.get("validation_conclusion", ""),
                })
        except Exception:
            pass

    passed = sum(1 for c in all_commands if c["success"] and c["validation_status"] != "failed")
    failed = len(all_commands) - passed
    return {"total": len(all_commands), "passed": passed, "failed": failed, "commands": all_commands}


def read_mop_report(run_dir: str, uc_name: str) -> dict:
    """Read MOP generation report and return scenario results.

    Returns {total, passed, failed, scenarios: [{scenario, status, error}]}.
    """
    report_path = Path(run_dir) / f"{uc_name}_mop-generation-report.html"
    if not report_path.exists():
        return {"total": 0, "passed": 0, "failed": 0, "scenarios": []}

    # Parse HTML for scenario results (simple extraction)
    import re
    html = report_path.read_text(encoding="utf-8")

    scenarios = []
    # Extract from table rows
    for match in re.finditer(
        r'<tr class="(?:pass|fail)-row">\s*<td[^>]*>(\w+)</td>\s*<td>([^<]*)</td>\s*<td>([^<]*)</td>\s*<td>([^<]*)</td>\s*<td>(.*?)</td>',
        html, re.DOTALL
    ):
        scenarios.append({
            "status": match.group(1),
            "scenario": match.group(2),
            "cr_group": match.group(3),
            "node": match.group(4),
            "error": match.group(5)[:300],
        })

    passed = sum(1 for s in scenarios if s["status"] == "PASSED")
    return {"total": len(scenarios), "passed": passed, "failed": len(scenarios) - passed, "scenarios": scenarios}


def read_workflow_yaml(run_dir: str, uc_name: str) -> str:
    """Read the current workflow YAML."""
    path = Path(run_dir) / f"{uc_name}.yaml"
    if path.exists():
        return path.read_text(encoding="utf-8")[:5000]
    return "Workflow file not found"


def read_validation_rules(run_dir: str, uc_name: str) -> str:
    """Read the current validation rules YAML."""
    path = Path(run_dir) / f"{uc_name}_validation-rules.yaml"
    if path.exists():
        return path.read_text(encoding="utf-8")[:5000]
    return "Validation rules file not found"


def read_simulator_config(run_dir: str, node_name: str = "target_node") -> str:
    """Read the simulator config YAML for a node."""
    sim_dir = Path(run_dir) / "simulator" / "config"
    if sim_dir.exists():
        configs = list(sim_dir.glob("*.yaml"))
        if configs:
            return configs[0].read_text(encoding="utf-8")[:5000]
    return "Simulator config not found"


def classify_failure_type(failures: list[dict]) -> dict:
    """Classify execution failures into categories.

    Returns {
        classification: 'workflow' | 'simulator' | 'mixed',
        workflow_issues: [str],
        simulator_issues: [str],
        retry_target: 6 | 8,
    }.
    """
    WORKFLOW_PATTERNS = [
        "Cannot create property", "mapping values are not allowed",
        "PatternSyntaxException", "named capturing group",
        "node not found: null", "Unable to find property",
        "validation_status.*failed",
    ]
    SIMULATOR_PATTERNS = [
        "Connection refused", "ssh login failed", "command not found",
        "prompt not reached", "Failed to start MockWire", "empty response",
    ]

    workflow_issues = []
    simulator_issues = []

    for f in failures:
        text = json.dumps(f, default=str).lower()
        is_workflow = any(p.lower() in text for p in WORKFLOW_PATTERNS)
        is_simulator = any(p.lower() in text for p in SIMULATOR_PATTERNS)

        if is_workflow:
            workflow_issues.append(f.get("command", "unknown"))
        elif is_simulator:
            simulator_issues.append(f.get("command", "unknown"))
        else:
            # Default: if validation failed but command succeeded, it's likely workflow
            if f.get("success") and f.get("validation_status") == "failed":
                workflow_issues.append(f.get("command", "unknown"))
            else:
                simulator_issues.append(f.get("command", "unknown"))

    if len(simulator_issues) > len(workflow_issues):
        classification = "simulator"
        retry_target = 8
    elif workflow_issues:
        classification = "workflow"
        retry_target = 6
    else:
        classification = "mixed"
        retry_target = 6

    return {
        "classification": classification,
        "workflow_issues": workflow_issues,
        "simulator_issues": simulator_issues,
        "retry_target": retry_target,
    }


# ===========================================================================
# REVIEWER AGENT DEFINITIONS
# ===========================================================================

QA_VALIDATION_REVIEWER_INSTRUCTION = """You are a QA Validation Reviewer for the CR Automation pipeline.

Your job: Analyze CIQ validation test failures (Step 5) and generate precise fix instructions
for the Validation Rules Generator (Step 3).

WORKFLOW:
1. Use read_qa_report to get the test case results
2. Use read_validation_rules to see the current validation rules
3. Analyze each failed test case:
   - What rule caused the failure?
   - Was it a false positive (rule too strict) or a real issue?
   - What specific change would fix it?
4. Generate structured fix instructions

OUTPUT FORMAT (JSON):
{
    "analysis": "Brief summary of failures",
    "fix_instructions": [
        {
            "rule_location": "sheets.IP.columns.ZONE",
            "current_value": "required: true, type: string",
            "problem": "Column allows empty values in CIQ but rule says required",
            "fix": "Change required to false",
            "priority": "high"
        }
    ],
    "retry_recommended": true
}

Be specific — reference exact sheet names, column names, and rule types."""

QA_MOP_REVIEWER_INSTRUCTION = """You are a QA MOP Reviewer for the CR Automation pipeline.

Your job: Analyze MOP generation test failures (Step 7) and generate precise fix instructions
for the Workflow Generator (Step 6).

WORKFLOW:
1. Use read_mop_report to get the generation scenario results
2. Use read_workflow_yaml to see the current workflow
3. Analyze each failed scenario:
   - Is it a YAML parse error? (Java can't deserialize the workflow)
   - Is it a missing node/data issue? (JSON has no nodes)
   - Is it a field validation error? (unknown property on Java model)
4. Generate structured fix instructions

OUTPUT FORMAT (JSON):
{
    "analysis": "Brief summary of failures",
    "failure_type": "yaml_parse" | "missing_data" | "invalid_field" | "other",
    "fix_instructions": [
        {
            "location": "phases.preNodeHealthCheck.steps[2].register",
            "problem": "Register item has 'error_on_missing' field not recognized by Java",
            "fix": "Remove error_on_missing from register items",
            "priority": "critical"
        }
    ],
    "retry_recommended": true
}

Focus on Java model compatibility issues — the workflow must be parseable by cli-automation-standalone.jar."""

QA_EXECUTION_REVIEWER_INSTRUCTION = """You are a QA Execution Reviewer for the CR Automation pipeline.

Your job: Analyze execution test failures (Step 9) and determine whether the issue is in the
Workflow (Step 6) or the Simulator Setup (Step 8), then generate fix instructions.

WORKFLOW:
1. Use read_exec_report to get the command execution results
2. Use classify_failure_type to classify each failure
3. Based on classification:
   - WORKFLOW issues: Use read_workflow_yaml to identify the problematic register/validation
   - SIMULATOR issues: Use read_simulator_config to identify missing/wrong mock responses
4. Generate fix instructions targeted at the right upstream step

FAILURE PATTERNS:
- Validation expression errors (and/or, .contains, parseInt) → WORKFLOW (Step 6)
- YAML parse errors (Cannot create property) → WORKFLOW (Step 6)
- Connection refused, prompt not reached → SIMULATOR (Step 8)
- Command returns empty/wrong output → SIMULATOR (Step 8)
- Validation failed but command succeeded → could be EITHER

OUTPUT FORMAT (JSON):
{
    "analysis": "Brief summary of failures",
    "classification": "workflow" | "simulator" | "mixed",
    "retry_target": 6 | 8,
    "workflow_fixes": [
        {
            "command": "status",
            "problem": "Validation uses 'and' instead of '&&'",
            "fix": "Change expr to use && operator"
        }
    ],
    "simulator_fixes": [
        {
            "command": "cli -c show alarms",
            "problem": "Response doesn't match register pattern '(?<alarmCount>\\d+)'",
            "fix": "Response should contain '0 active alarms' to match the pattern"
        }
    ],
    "retry_recommended": true
}

Be precise — specify the exact command, the exact problem, and the exact fix needed."""


# ===========================================================================
# MILESTONE REVIEWER (generic — analyzes LLM generation failures)
# ===========================================================================

_STEP_CONTEXT = {
    3: {
        "name": "Validation Rules",
        "artifact_key": "validation_rules_yaml",
        "file_pattern": "_validation-rules.yaml",
        "description": "validation-rules.yaml consumed by ciq-processor JAR",
    },
    4: {
        "name": "JSON Output Template",
        "artifact_key": "json_template_yaml",
        "file_pattern": "_json-output.yaml",
        "description": "json-output.yaml consumed by ciq-processor JAR",
    },
    6: {
        "name": "Execution Workflow",
        "artifact_key": "workflow_yaml",
        "file_pattern": ".yaml",
        "description": "workflow YAML consumed by cli-automation-standalone JAR",
    },
    8: {
        "name": "Simulator Config",
        "artifact_key": "simulator_config_yaml",
        "file_pattern": "_simulator-config.yaml",
        "description": "MockWire simulator configuration",
    },
}

MILESTONE_REVIEWER_PROMPT = """Analyze why this YAML generation step failed and produce fix instructions.

STEP: {step_name} (Step {step_number})
GENERATES: {description}

ERROR DETAILS:
{error_details}

GENERATED ARTIFACT (may be partial/invalid):
{artifact_content}

Respond with a JSON object:
{{
    "analysis": "Brief summary of what went wrong",
    "failure_type": "yaml_syntax | schema_validation | content_error | other",
    "fix_instructions": [
        {{
            "location": "YAML path or line where fix is needed",
            "problem": "What's wrong",
            "fix": "Exact change to make"
        }}
    ],
    "general_guidance": "Overall advice for regeneration"
}}

Be specific — reference exact YAML paths, line numbers, and content."""


class MilestoneReviewerNode(BaseAgent):
    """BaseAgent that calls LLM to analyze a failed step and stores feedback in session state."""
    name: str = "milestone_reviewer"
    description: str = "Analyze step failure and generate fix instructions"
    step_number: int = 0

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(self, ctx):
        from src.core.logging_config import get_flow_logger
        _flow = get_flow_logger(__name__)

        step = self.step_number
        status = ctx.session.state.get(f"step_{step}_status", "passed")

        # Only review if step failed
        if status != "failed":
            return
            yield

        run_dir = ctx.session.state.get("run_dir", "")
        uc_name = ctx.session.state.get("uc_name", "UC")
        step_ctx = _STEP_CONTEXT.get(step, {})

        # Read artifact content
        artifact_content = ""
        pattern = step_ctx.get("file_pattern", "")
        if pattern and run_dir:
            candidate = Path(run_dir) / f"{uc_name}{pattern}"
            if candidate.exists():
                artifact_content = candidate.read_text(encoding="utf-8")[:6000]
        if not artifact_content:
            # Try from session state
            art_key = step_ctx.get("artifact_key", "")
            artifact_content = ctx.session.state.get("artifacts", {}).get(art_key, "")[:6000]

        # Read error details
        error_details = ""
        err_file = Path(run_dir) / f"step_{step}_error.json" if run_dir else None
        if err_file and err_file.exists():
            error_details = err_file.read_text(encoding="utf-8")[:2000]
        if not error_details:
            error_details = str(ctx.session.state.get(f"step_{step}_error", "Unknown error"))[:2000]

        # Build prompt
        prompt = MILESTONE_REVIEWER_PROMPT.format(
            step_name=step_ctx.get("name", f"Step {step}"),
            step_number=step,
            description=step_ctx.get("description", ""),
            error_details=error_details,
            artifact_content=artifact_content or "(no content generated)",
        )

        # Call LLM
        try:
            from src.nodes.cr_import import import_cr_module
            agents_mod = import_cr_module("src.agents")
            call_llm = agents_mod.call_llm
            config = ctx.session.state.get("pipeline_config", {})

            _flow.flow("REVIEWER START step %d — analyzing failure", step)
            response = await call_llm(prompt, config=config)
            _flow.flow("REVIEWER DONE step %d — %d chars feedback", step, len(response))

            # Store feedback in shared dict (bypasses ADK session state)
            run_id = ctx.session.state.get("run_id", "")
            _reviewer_feedback_store[f"{run_id}:{step}"] = response
            _flow.flow("REVIEWER STORED feedback for %s:%d", run_id[:8], step)

            # Also write to disk for debugging
            if run_dir:
                fb_file = Path(run_dir) / f"step_{step}_reviewer_feedback.json"
                fb_file.write_text(response, encoding="utf-8")

        except Exception as e:
            logger.error("Milestone reviewer step %d failed: %s", step, e)
            ctx.session.state[f"step_{step}_reviewer_feedback"] = f"Reviewer error: {e}"

        return
        yield  # unreachable


def create_milestone_reviewer(model, step_number: int) -> MilestoneReviewerNode:
    """Create a milestone reviewer for a YAML generation step."""
    node = MilestoneReviewerNode()
    node.name = f"milestone_reviewer_step{step_number}"
    node.description = f"Review Step {step_number} failure and generate fix instructions"
    node.step_number = step_number
    return node


def create_qa_validation_reviewer(model: LiteLlm) -> LlmAgent:
    """Create the QA Validation Reviewer (Step 5 → Step 3)."""
    return LlmAgent(
        name="qa_validation_reviewer",
        model=model,
        instruction=QA_VALIDATION_REVIEWER_INSTRUCTION,
        tools=[
            FunctionTool(func=read_qa_report),
            FunctionTool(func=read_validation_rules),
            FunctionTool(func=read_file_content),
        ],
    )


def create_qa_mop_reviewer(model: LiteLlm) -> LlmAgent:
    """Create the QA MOP Reviewer (Step 7 → Step 6)."""
    return LlmAgent(
        name="qa_mop_reviewer",
        model=model,
        instruction=QA_MOP_REVIEWER_INSTRUCTION,
        tools=[
            FunctionTool(func=read_mop_report),
            FunctionTool(func=read_workflow_yaml),
            FunctionTool(func=read_file_content),
        ],
    )


def create_qa_execution_reviewer(model: LiteLlm) -> LlmAgent:
    """Create the QA Execution Reviewer (Step 9 → Step 6 or Step 8)."""
    return LlmAgent(
        name="qa_execution_reviewer",
        model=model,
        instruction=QA_EXECUTION_REVIEWER_INSTRUCTION,
        tools=[
            FunctionTool(func=read_exec_report),
            FunctionTool(func=read_workflow_yaml),
            FunctionTool(func=read_simulator_config),
            FunctionTool(func=classify_failure_type),
            FunctionTool(func=read_file_content),
        ],
    )


# Import helper for read_file_content
from src.nodes.tools import read_file_content
