"""Network Configuration Testing — MOP generation + execution test + QA report.

Phases:
1. Generate test cases via LLM (MOP, execution, integration scenarios)
2. Run MOP generation test via cli-automation JAR
3. Run execution test via cli-automation JAR against MockWire simulator
4. Evaluate test cases against actual results
5. Classify failures (workflow vs simulator) for retry routing
6. Generate professional QA report

Two entry points:
- ``test(input)`` — full cycle: generate TCs → MOP test → execution test → report
- ``test_mop_only(input)`` — MOP generation test only (no simulator needed)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from .test_case_gen import TestCase, generate_test_cases
from .jar_executor import run_mop_test, run_execution_test, write_yaml_to_file
from .result_parser import (
    TestCaseResult, MopScenario, CommandResult,
    parse_mop_results, parse_execution_results,
    evaluate_test_cases, classify_failures,
)
from .report import generate_qa_report

logger = logging.getLogger(__name__)

LlmFn = Callable[[str, str], Awaitable[str]]


@dataclass
class SkillInput:
    """Input for network configuration testing."""
    uc_name: str
    workflow_yaml: str = ""
    simulator_config_yaml: str = ""
    json_output_yaml: str = ""
    ciq_file_path: str = ""
    requirements: dict = field(default_factory=dict)
    run_dir: str = ""
    cli_automation_jar: str = "lib/cli-automation-standalone.jar"
    mockwire_jar: str = "lib/mockwire/mockwire-simulator/mockwire.jar"
    java_home: str | None = None
    simulator_host: str = "localhost"
    simulator_port: int = 2222


@dataclass
class SkillOutput:
    """Output from network configuration testing."""
    total: int = 0
    passed: int = 0
    failed: int = 0
    test_cases: list[TestCaseResult] = field(default_factory=list)
    mop_scenarios: list[MopScenario] = field(default_factory=list)
    command_results: list[CommandResult] = field(default_factory=list)
    failure_classification: dict = field(default_factory=dict)
    report_html: str = ""
    status: str = "pending"  # "passed" | "failed" | "pending" | "error"
    error: str = ""


class NetworkConfigTestSkill:
    """QA engine for workflow + simulator testing.

    Generates test cases via LLM, runs MOP + execution tests via JAR,
    evaluates results, classifies failures, and produces a professional report.
    """

    def __init__(
        self,
        llm_fn: LlmFn | None = None,
        cli_automation_jar: str = "lib/cli-automation-standalone.jar",
    ):
        self._llm_fn = llm_fn
        self._cli_jar = cli_automation_jar

    async def test(self, inp: SkillInput) -> SkillOutput:
        """Full test cycle: generate TCs → MOP test → execution test → report."""
        t0 = time.monotonic()
        cli_jar = inp.cli_automation_jar or self._cli_jar

        if not inp.run_dir:
            return SkillOutput(status="error", error="run_dir is required")

        # Write YAMLs to disk
        wf_path = ""
        json_path = ""
        if inp.workflow_yaml:
            wf_path = write_yaml_to_file(inp.workflow_yaml, inp.run_dir, f"{inp.uc_name}.yaml")
        if inp.json_output_yaml:
            json_path = write_yaml_to_file(inp.json_output_yaml, inp.run_dir, f"{inp.uc_name}_json-output.yaml")

        # Phase 1: Generate test cases
        test_cases: list[TestCase] = []
        if self._llm_fn:
            try:
                test_cases = await generate_test_cases(
                    self._llm_fn, inp.uc_name,
                    inp.workflow_yaml, inp.simulator_config_yaml,
                    inp.requirements,
                )
                logger.info("Phase 1: Generated %d test cases", len(test_cases))
            except Exception as e:
                logger.error("TC generation failed: %s", e, exc_info=True)

        # Phase 2: MOP generation test
        mop_scenarios: list[MopScenario] = []
        if wf_path and json_path and inp.ciq_file_path:
            mop_result = await run_mop_test(
                cli_jar, wf_path, json_path, inp.ciq_file_path,
                inp.run_dir, inp.java_home,
            )
            mop_scenarios = parse_mop_results(inp.run_dir, inp.uc_name)
            logger.info("Phase 2: MOP test — %d scenarios, jar_success=%s",
                         len(mop_scenarios), mop_result["success"])

        # Phase 3: Execution test
        command_results: list[CommandResult] = []
        if wf_path and json_path and inp.ciq_file_path:
            exec_result = await run_execution_test(
                cli_jar, wf_path, json_path, inp.ciq_file_path,
                inp.run_dir, inp.simulator_host, inp.simulator_port,
                inp.java_home,
            )
            command_results = parse_execution_results(inp.run_dir)
            logger.info("Phase 3: Execution test — %d commands, jar_success=%s",
                         len(command_results), exec_result["success"])

        # Phase 4: Evaluate test cases
        if test_cases:
            results = evaluate_test_cases(test_cases, mop_scenarios, command_results)
        else:
            # No generated TCs — create summary results from JAR output
            results = self._build_default_results(mop_scenarios, command_results)

        # Phase 5: Classify failures
        failed_cmds = [c for c in command_results if not c.success or c.validation_status == "failed"]
        classification = classify_failures(command_results) if failed_cmds else {}

        total = len(results)
        passed = sum(1 for r in results if r.status == "PASS")
        failed_count = total - passed
        duration = time.monotonic() - t0

        # Phase 6: Generate report
        report_html = generate_qa_report(
            uc_name=inp.uc_name,
            test_results=results,
            mop_scenarios=mop_scenarios,
            command_results=command_results,
            failure_classification=classification,
            workflow_file=wf_path,
            simulator_config_file=inp.simulator_config_yaml[:50] + "..." if inp.simulator_config_yaml else "",
            duration_sec=duration,
        )

        status = "passed" if failed_count == 0 else "failed"
        logger.info("QA complete: %d/%d passed, classification=%s (%.1fs)",
                     passed, total, classification.get("classification", "n/a"), duration)

        return SkillOutput(
            total=total,
            passed=passed,
            failed=failed_count,
            test_cases=results,
            mop_scenarios=mop_scenarios,
            command_results=command_results,
            failure_classification=classification,
            report_html=report_html,
            status=status,
        )

    async def test_mop_only(self, inp: SkillInput) -> SkillOutput:
        """Run only the MOP generation test (no simulator needed)."""
        t0 = time.monotonic()
        cli_jar = inp.cli_automation_jar or self._cli_jar

        if not inp.run_dir:
            return SkillOutput(status="error", error="run_dir is required")

        wf_path = write_yaml_to_file(inp.workflow_yaml, inp.run_dir, f"{inp.uc_name}.yaml") if inp.workflow_yaml else ""
        json_path = write_yaml_to_file(inp.json_output_yaml, inp.run_dir, f"{inp.uc_name}_json-output.yaml") if inp.json_output_yaml else ""

        if not wf_path or not json_path or not inp.ciq_file_path:
            return SkillOutput(status="error", error="workflow_yaml, json_output_yaml, and ciq_file_path are required")

        mop_result = await run_mop_test(
            cli_jar, wf_path, json_path, inp.ciq_file_path,
            inp.run_dir, inp.java_home,
        )
        mop_scenarios = parse_mop_results(inp.run_dir, inp.uc_name)

        results = [
            TestCaseResult(
                id=f"MOP_{i+1:03d}",
                description=f"MOP scenario: {s.name}",
                category="mop_generation",
                severity="critical",
                test_type="mop_test",
                expected_outcome="Scenario generates successfully",
                actual_outcome="Passed" if s.status == "PASS" else f"Failed: {s.error[:80]}",
                status=s.status,
                errors=[s.error] if s.status == "FAIL" else [],
            )
            for i, s in enumerate(mop_scenarios)
        ]

        if not results:
            results = [TestCaseResult(
                id="MOP_BASE",
                description="MOP generation JAR execution",
                category="mop_generation",
                severity="critical",
                test_type="mop_test",
                expected_outcome="JAR exits successfully",
                actual_outcome="Success" if mop_result["success"] else f"Failed: exit {mop_result['exit_code']}",
                status="PASS" if mop_result["success"] else "FAIL",
            )]

        total = len(results)
        passed = sum(1 for r in results if r.status == "PASS")
        duration = time.monotonic() - t0

        report_html = generate_qa_report(
            uc_name=inp.uc_name,
            test_results=results,
            mop_scenarios=mop_scenarios,
            command_results=[],
            workflow_file=wf_path,
            duration_sec=duration,
        )

        return SkillOutput(
            total=total, passed=passed, failed=total - passed,
            test_cases=results, mop_scenarios=mop_scenarios,
            report_html=report_html,
            status="passed" if passed == total else "failed",
        )

    @staticmethod
    def _build_default_results(
        mop_scenarios: list[MopScenario],
        command_results: list[CommandResult],
    ) -> list[TestCaseResult]:
        """Build default test case results when no LLM-generated TCs available."""
        results = []

        # MOP results
        if mop_scenarios:
            mop_failed = sum(1 for s in mop_scenarios if s.status == "FAIL")
            results.append(TestCaseResult(
                id="TC_MOP",
                description="MOP generation test",
                category="mop_generation",
                severity="critical",
                test_type="mop_test",
                expected_outcome="All MOP scenarios pass",
                actual_outcome=f"{len(mop_scenarios) - mop_failed}/{len(mop_scenarios)} passed",
                status="PASS" if mop_failed == 0 else "FAIL",
            ))

        # Execution results — group by phase
        if command_results:
            phases = sorted(set(c.phase for c in command_results if c.phase))
            for phase in phases:
                phase_cmds = [c for c in command_results if c.phase == phase]
                phase_failed = [c for c in phase_cmds if not c.success or c.validation_status == "failed"]
                results.append(TestCaseResult(
                    id=f"TC_EXEC_{phase.upper()[:10]}",
                    description=f"Execution test: {phase}",
                    category="command_execution",
                    severity="critical" if phase_failed else "major",
                    test_type="execution_test",
                    phase=phase,
                    expected_outcome=f"All {len(phase_cmds)} commands pass",
                    actual_outcome=f"{len(phase_cmds) - len(phase_failed)}/{len(phase_cmds)} passed",
                    status="PASS" if not phase_failed else "FAIL",
                    errors=[f"{c.command[:40]}: {c.failure_reason[:60]}" for c in phase_failed[:5]],
                ))

        return results
