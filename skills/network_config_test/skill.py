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
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .test_case_gen import TestCase, generate_test_cases
from .jar_executor import run_mop_test, run_execution_test, write_yaml_to_file
from .result_parser import (
    TestCaseResult, MopScenario, CommandResult,
    parse_mop_results, parse_execution_results,
    evaluate_test_cases, classify_failures, read_simulator_events,
)
from .report import generate_qa_report
from .scenario_report import generate_scenario_report
from .scenarios import ScenarioSuite
from .jar_executor import prepare_execution

logger = logging.getLogger(__name__)

LlmFn = Callable[[str, str], Awaitable[str]]


@dataclass
class SkillInput:
    """Input for network configuration testing."""
    uc_name: str
    workflow_yaml: str = ""
    simulator_config_yaml: str = ""
    json_output_yaml: str = ""
    validation_rules_yaml: str = ""
    ciq_file_path: str = ""
    requirements: dict = field(default_factory=dict)
    run_dir: str = ""
    cli_automation_jar: str = "lib/cli-automation-standalone.jar"
    ciq_processor_jar: str = "lib/ciq-processor-1.0.3-cli.jar"
    mockwire_jar: str = "lib/mockwire.jar"
    # Settings → External Modules: HTML report per executed case (step 9)
    execution_report_jar: str = "lib/execution-report-generator-1.0.2-cli.jar"
    html_template_path: str = ""
    java_home: str | None = None
    # Step 8's copy of the workflow pointed at the simulator; execution never uses the
    # production workflow (its hosts are real network elements)
    execution_workflow_path: str = ""
    # Restart/registration of MockWire for scenario runs (spec 005); without it the old
    # single-run execution test is used
    simulator_control: Any = None
    include_negatives: bool = False  # sunny day only until negative cases are enabled


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
    scenario_results: list[dict] = field(default_factory=list)  # test-case report data (JSON)
    notes: list[str] = field(default_factory=list)


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

        no_simulator = not (inp.execution_workflow_path and Path(inp.execution_workflow_path).exists())
        scenario_mode = inp.simulator_control is not None and not no_simulator

        # Phase 1: Generate test cases (scenario mode derives them from the workflow instead)
        test_cases: list[TestCase] = []
        if self._llm_fn and not scenario_mode:
            try:
                test_cases = await generate_test_cases(
                    self._llm_fn, inp.uc_name,
                    inp.workflow_yaml, inp.simulator_config_yaml,
                    inp.requirements,
                )
                logger.info("Phase 1: Generated %d test cases", len(test_cases))
            except Exception as e:
                logger.error("TC generation failed: %s", e, exc_info=True)

        # Write validation-rules if provided
        rules_path = ""
        if inp.validation_rules_yaml:
            rules_path = write_yaml_to_file(inp.validation_rules_yaml, inp.run_dir,
                                            f"{inp.uc_name}_validation-rules.yaml")

        # Phase 2: MOP generation test
        mop_scenarios: list[MopScenario] = []
        if wf_path and json_path and inp.ciq_file_path:
            mop_result = await run_mop_test(
                cli_jar, wf_path, json_path, inp.ciq_file_path,
                inp.run_dir, inp.java_home,
                ciq_processor_jar=inp.ciq_processor_jar,
                validation_rules_path=rules_path,
                html_template_path=inp.html_template_path,
            )
            mop_scenarios = parse_mop_results(inp.run_dir, inp.uc_name)
            logger.info("Phase 2: MOP test — %d scenarios, jar_success=%s",
                         len(mop_scenarios), mop_result["success"])

        if scenario_mode and json_path:
            return await self._run_scenarios(inp, cli_jar, json_path, rules_path, mop_scenarios, t0)

        # Phase 3: Execution test — only against the simulator overlay, never production hosts
        command_results: list[CommandResult] = []
        if no_simulator:
            logger.error("Phase 3: no simulator overlay workflow (%r) — execution test skipped",
                         inp.execution_workflow_path)
        elif json_path and inp.ciq_file_path:
            exec_result = await run_execution_test(
                cli_jar, inp.execution_workflow_path, json_path, inp.ciq_file_path,
                inp.run_dir, inp.java_home,
                ciq_processor_jar=inp.ciq_processor_jar,
                validation_rules_path=rules_path,
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

        # Phase 5: Classify failures (MockWire's event log tells simulator gaps apart)
        failed_cmds = [c for c in command_results if not c.success or c.validation_status == "failed"]
        events = read_simulator_events(inp.run_dir)
        if no_simulator:
            classification = {"classification": "simulator", "retry_target": 8, "workflow_issues": [],
                              "simulator_issues": ["simulator not running (no overlay workflow from step 8)"],
                              "unmatched_commands": []}
        elif failed_cmds or any(e.get("source") == "unmatched" for e in events):
            classification = classify_failures(command_results, events)
        else:
            classification = {}

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

        status = "passed" if failed_count == 0 and not no_simulator else "failed"
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

    async def _run_scenarios(self, inp: SkillInput, cli_jar: str, json_path: str, rules_path: str,
                             mop_scenarios: list[MopScenario], t0: float) -> SkillOutput:
        """Scenario test cases on the simulator → test-case report (spec 005)."""
        plan = await prepare_execution(inp.execution_workflow_path, json_path, inp.ciq_file_path,
                                       inp.run_dir, inp.java_home,
                                       ciq_processor_jar=inp.ciq_processor_jar,
                                       validation_rules_path=rules_path)
        if plan.get("error"):
            return SkillOutput(status="failed", error=plan["error"], mop_scenarios=mop_scenarios,
                               failure_classification={"classification": "workflow", "retry_target": 6,
                                                       "workflow_issues": [plan["error"]],
                                                       "simulator_issues": [], "unmatched_commands": []})
        suite = await ScenarioSuite(
            run_dir=inp.run_dir, uc_name=inp.uc_name, workflow_path=inp.execution_workflow_path,
            plan=plan, cli_jar=cli_jar, mockwire_jar=inp.mockwire_jar, control=inp.simulator_control,
            llm_fn=self._llm_fn, java_home=inp.java_home,
            samples=(inp.requirements.get("doc_extracted") or {}).get("command_samples") or [],
            include_negatives=inp.include_negatives, report_jar=inp.execution_report_jar,
        ).run()
        duration = time.monotonic() - t0
        # Commands of failing sunny-day cases feed the step 6 / step 8 retry
        failing_cmds = [c for r in suite.results if r.status == "FAIL" and r.scenario.kind in ("positive", "rollback")
                        for c in r.commands]
        status = "passed" if suite.results and suite.failed == 0 else "failed"
        logger.info("Scenario tests: %d/%d passed (%.1fs)", suite.passed, len(suite.results), duration)
        return SkillOutput(
            total=len(suite.results), passed=suite.passed, failed=suite.failed,
            test_cases=[TestCaseResult(id=r.scenario.id, description=r.scenario.title,
                                       category=r.scenario.category, test_type="execution_test",
                                       target_node=f"{r.scenario.cr_group}/{r.scenario.node_group}",
                                       expected_outcome=r.scenario.expected, actual_outcome=r.actual,
                                       status=r.status,
                                       errors=[c.description + (f": {c.detail}" if c.detail else "")
                                               for c in r.checks if not c.ok],
                                       command_results=r.commands)
                        for r in suite.results],
            mop_scenarios=mop_scenarios,
            command_results=failing_cmds,
            failure_classification=suite.classification,
            report_html=generate_scenario_report(inp.uc_name, suite, duration),
            status=status,
            error="" if suite.results else "; ".join(suite.notes) or "no test cases ran",
            scenario_results=[r.to_dict() for r in suite.results],
            notes=suite.notes,
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

        # Write validation-rules.yaml if provided
        rules_path = ""
        if inp.validation_rules_yaml:
            rules_path = write_yaml_to_file(inp.validation_rules_yaml, inp.run_dir,
                                            f"{inp.uc_name}_validation-rules.yaml")

        mop_result = await run_mop_test(
            cli_jar, wf_path, json_path, inp.ciq_file_path,
            inp.run_dir, inp.java_home,
            ciq_processor_jar=inp.ciq_processor_jar,
            validation_rules_path=rules_path,
            html_template_path=inp.html_template_path,
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
            error_detail = mop_result.get("stderr", "") or f"exit {mop_result['exit_code']}"
            results = [TestCaseResult(
                id="MOP_BASE",
                description="MOP generation JAR execution",
                category="mop_generation",
                severity="critical",
                test_type="mop_test",
                expected_outcome="JAR exits successfully",
                actual_outcome="Success" if mop_result["success"] else f"Failed: {error_detail[:120]}",
                status="PASS" if mop_result["success"] else "FAIL",
                errors=[error_detail[:300]] if not mop_result["success"] else [],
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
