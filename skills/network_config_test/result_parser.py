"""Parse MOP generation and execution test results from JAR output."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from .test_case_gen import TestCase

logger = logging.getLogger(__name__)


@dataclass
class CommandResult:
    """Result of a single command execution."""
    command: str = ""
    phase: str = ""
    node: str = ""
    success: bool = False
    output: str = ""
    validation_status: str = "skipped"  # success | failed | skipped
    validation_criteria: str = ""
    validation_conclusion: str = ""
    failure_reason: str = ""


@dataclass
class MopScenario:
    """Result of a single MOP generation scenario."""
    name: str = ""
    status: str = "PASS"  # PASS | FAIL
    error: str = ""


@dataclass
class TestCaseResult:
    """Result of evaluating a test case against actual JAR output."""
    id: str = ""
    description: str = ""
    category: str = ""
    severity: str = "major"
    phase: str = ""
    test_type: str = ""
    target_node: str = ""
    expected_outcome: str = ""
    actual_outcome: str = ""
    status: str = "PASS"  # PASS | FAIL | ERROR | SKIPPED
    errors: list[str] = field(default_factory=list)
    command_results: list[CommandResult] = field(default_factory=list)


def parse_mop_results(run_dir: str, uc_name: str = "") -> list[MopScenario]:
    """Parse MOP generation output from mop_output/ directory."""
    mop_dir = Path(run_dir) / "mop_output"
    scenarios = []

    # Look for MOP report HTML
    for html_file in mop_dir.glob("*mop*report*.html"):
        try:
            html = html_file.read_text(encoding="utf-8")
            for match in re.finditer(
                r'<tr class="(?:pass|fail)-row">\s*<td[^>]*>(\w+)</td>\s*<td>([^<]*)</td>'
                r'\s*<td>([^<]*)</td>\s*<td>([^<]*)</td>\s*<td>(.*?)</td>',
                html, re.DOTALL
            ):
                scenarios.append(MopScenario(
                    name=match.group(2),
                    status="PASS" if match.group(1) == "PASSED" else "FAIL",
                    error=match.group(5)[:300],
                ))
        except Exception as e:
            logger.debug("Could not parse MOP report %s: %s", html_file, e)

    # Fallback: check for JSON results
    if not scenarios:
        for jf in mop_dir.glob("*.json"):
            try:
                data = json.loads(jf.read_text(encoding="utf-8"))
                for sc in data.get("scenarios", data.get("results", [])):
                    if isinstance(sc, dict):
                        scenarios.append(MopScenario(
                            name=sc.get("scenario", sc.get("name", jf.stem)),
                            status="PASS" if sc.get("status", "").upper() in ("PASS", "PASSED", "SUCCESS") else "FAIL",
                            error=sc.get("error", "")[:300],
                        ))
            except Exception:
                pass

    logger.info("Parsed MOP results: %d scenarios", len(scenarios))
    return scenarios


def parse_execution_results(run_dir: str) -> list[CommandResult]:
    """Parse execution test output from exec_output/ directory."""
    exec_dir = Path(run_dir) / "exec_output"
    commands = []

    if not exec_dir.exists():
        return commands

    for jf in sorted(exec_dir.glob("*.json")):
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
            for cmd_key, cmd_info in data.get("data", {}).items():
                commands.append(CommandResult(
                    command=cmd_key,
                    phase=cmd_info.get("phase", ""),
                    node=cmd_info.get("node", ""),
                    success=cmd_info.get("success", False),
                    output=cmd_info.get("output", "")[:500],
                    validation_status=cmd_info.get("validation_status", "skipped"),
                    validation_criteria=cmd_info.get("validation_criteria", ""),
                    validation_conclusion=cmd_info.get("validation_conclusion", ""),
                    failure_reason=cmd_info.get("failure", cmd_info.get("reason", "")),
                ))
        except Exception as e:
            logger.debug("Could not parse execution result %s: %s", jf, e)

    logger.info("Parsed execution results: %d commands", len(commands))
    return commands


def evaluate_test_cases(
    test_cases: list[TestCase],
    mop_scenarios: list[MopScenario],
    command_results: list[CommandResult],
) -> list[TestCaseResult]:
    """Evaluate generated test cases against actual MOP + execution results."""
    results = []

    # Index command results by phase and node
    cmds_by_phase: dict[str, list[CommandResult]] = {}
    cmds_by_node: dict[str, list[CommandResult]] = {}
    for cr in command_results:
        cmds_by_phase.setdefault(cr.phase, []).append(cr)
        cmds_by_node.setdefault(cr.node, []).append(cr)

    for tc in test_cases:
        tcr = TestCaseResult(
            id=tc.id,
            description=tc.description,
            category=tc.category,
            severity=tc.severity,
            phase=tc.phase,
            test_type=tc.test_type,
            target_node=tc.target_node,
            expected_outcome=tc.expected_outcome,
        )

        if tc.test_type == "mop_test":
            tcr = _evaluate_mop_test(tc, tcr, mop_scenarios)
        elif tc.test_type == "execution_test":
            tcr = _evaluate_execution_test(tc, tcr, cmds_by_phase, cmds_by_node, command_results)
        else:  # "both"
            tcr = _evaluate_mop_test(tc, tcr, mop_scenarios)
            if tcr.status == "PASS":
                tcr = _evaluate_execution_test(tc, tcr, cmds_by_phase, cmds_by_node, command_results)

        results.append(tcr)

    return results


def classify_failures(command_results: list[CommandResult]) -> dict:
    """Classify execution failures into workflow vs simulator categories.

    Returns dict with classification, retry_target, and issue lists.
    """
    WORKFLOW_PATTERNS = [
        "cannot create property", "mapping values are not allowed",
        "patternsyntaxexception", "named capturing group",
        "node not found: null", "unable to find property",
    ]
    SIMULATOR_PATTERNS = [
        "connection refused", "ssh login failed", "command not found",
        "prompt not reached", "failed to start mockwire",
    ]

    workflow_issues, simulator_issues = [], []

    for cr in command_results:
        if cr.success and cr.validation_status != "failed":
            continue
        text = f"{cr.output} {cr.failure_reason} {cr.validation_conclusion}".lower()
        is_wf = any(p in text for p in WORKFLOW_PATTERNS)
        is_sim = any(p in text for p in SIMULATOR_PATTERNS)

        if is_wf:
            workflow_issues.append(cr.command[:60])
        elif is_sim:
            simulator_issues.append(cr.command[:60])
        elif cr.success and cr.validation_status == "failed":
            workflow_issues.append(cr.command[:60])
        else:
            simulator_issues.append(cr.command[:60])

    classification = "simulator" if len(simulator_issues) > len(workflow_issues) else "workflow"
    return {
        "classification": classification,
        "workflow_issues": workflow_issues,
        "simulator_issues": simulator_issues,
        "retry_target": 8 if classification == "simulator" else 6,
    }


# ---------------------------------------------------------------------------
# Private evaluation helpers
# ---------------------------------------------------------------------------

def _evaluate_mop_test(
    tc: TestCase, tcr: TestCaseResult, mop_scenarios: list[MopScenario]
) -> TestCaseResult:
    """Evaluate a test case against MOP generation results."""
    if not mop_scenarios:
        tcr.status = "SKIPPED"
        tcr.actual_outcome = "No MOP scenarios generated"
        return tcr

    failed_scenarios = [s for s in mop_scenarios if s.status == "FAIL"]
    if failed_scenarios:
        tcr.status = "FAIL"
        tcr.actual_outcome = f"{len(failed_scenarios)}/{len(mop_scenarios)} scenarios failed"
        tcr.errors = [f"{s.name}: {s.error}" for s in failed_scenarios[:5]]
    else:
        tcr.status = "PASS"
        tcr.actual_outcome = f"All {len(mop_scenarios)} scenarios passed"

    return tcr


def _evaluate_execution_test(
    tc: TestCase,
    tcr: TestCaseResult,
    cmds_by_phase: dict[str, list[CommandResult]],
    cmds_by_node: dict[str, list[CommandResult]],
    all_commands: list[CommandResult],
) -> TestCaseResult:
    """Evaluate a test case against execution results."""
    # Find relevant commands
    relevant: list[CommandResult] = []
    if tc.target_command:
        relevant = [c for c in all_commands if tc.target_command.lower() in c.command.lower()]
    elif tc.phase and tc.target_node:
        phase_cmds = cmds_by_phase.get(tc.phase, [])
        relevant = [c for c in phase_cmds if c.node == tc.target_node]
    elif tc.phase:
        relevant = cmds_by_phase.get(tc.phase, [])
    elif tc.target_node:
        relevant = cmds_by_node.get(tc.target_node, [])
    else:
        relevant = all_commands

    if not relevant:
        tcr.status = "SKIPPED"
        tcr.actual_outcome = "No matching commands found in execution results"
        return tcr

    tcr.command_results = relevant
    failed = [c for c in relevant if not c.success or c.validation_status == "failed"]

    if failed:
        tcr.status = "FAIL"
        tcr.actual_outcome = f"{len(failed)}/{len(relevant)} commands failed"
        tcr.errors = [
            f"{c.command[:40]}: {c.failure_reason or c.validation_conclusion or 'unknown'}"
            for c in failed[:5]
        ]
    else:
        tcr.status = "PASS"
        tcr.actual_outcome = f"All {len(relevant)} commands passed"

    return tcr
