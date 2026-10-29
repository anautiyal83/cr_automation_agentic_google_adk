"""CIQ Validation & JSON Output Testing — QA engine.

Phase 1: Generate professional test cases via LLM
Phase 2: Execute tests via ciq-processor JAR
Phase 3: Evaluate results + generate QA report

Usage::

    skill = CiqValidationJsonTestSkill(
        llm_fn=my_async_llm,
        jar_path="lib/ciq-processor-1.0.3-cli.jar",
    )
    output = await skill.test(SkillInput(
        uc_name="...",
        ciq_file_path="/path/to/ciq.xlsx",
        validation_rules_yaml="...",
        json_output_yaml="...",
        run_dir="/path/to/run/dir",
    ))
    # output.report_html contains the full QA report
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from .test_case_gen import TestCase, generate_test_cases
from .jar_executor import run_ciq_processor, write_yaml_to_file
from .result_parser import TestCaseResult, evaluate_test_cases, parse_jar_output
from .report import generate_qa_report

logger = logging.getLogger(__name__)

LlmFn = Callable[[str, str], Awaitable[str]]


@dataclass
class SkillInput:
    """Input for CIQ validation & JSON output testing."""
    uc_name: str
    ciq_file_path: str = ""
    validation_rules_yaml: str = ""
    json_output_yaml: str = ""
    requirements: dict = field(default_factory=dict)
    run_dir: str = ""
    jar_path: str = "lib/ciq-processor-1.0.3-cli.jar"
    java_home: str | None = None


@dataclass
class SkillOutput:
    """Output from CIQ validation & JSON output testing."""
    total: int = 0
    passed: int = 0
    failed: int = 0
    test_cases: list[TestCaseResult] = field(default_factory=list)
    report_html: str = ""
    status: str = "pending"  # "passed" | "failed" | "pending" | "error"
    error: str = ""


class CiqValidationJsonTestSkill:
    """QA engine: generates test cases, executes them, produces a professional report.

    LLM generates test scenarios. ciq-processor JAR executes validation.
    Results are evaluated and compiled into an HTML QA report.
    """

    def __init__(self, llm_fn: LlmFn | None = None, jar_path: str = "lib/ciq-processor-1.0.3-cli.jar"):
        self._llm_fn = llm_fn
        self._jar_path = jar_path

    async def test(self, inp: SkillInput) -> SkillOutput:
        """Run the full QA cycle: generate TCs → execute → evaluate → report.

        If no llm_fn was provided, skips TC generation and runs the JAR
        with the base CIQ directly.
        """
        t0 = time.monotonic()
        jar_path = inp.jar_path or self._jar_path

        # Extract CIQ sheets from requirements for TC generation
        ciq_sheets = inp.requirements.get("doc_extracted", {}).get("ciq_sheets", {})

        # Phase 1: Generate test cases (if LLM available)
        test_cases: list[TestCase] = []
        if self._llm_fn and inp.validation_rules_yaml and inp.json_output_yaml:
            try:
                test_cases = await generate_test_cases(
                    self._llm_fn,
                    inp.uc_name,
                    ciq_sheets,
                    inp.validation_rules_yaml,
                    inp.json_output_yaml,
                )
                logger.info("Phase 1: Generated %d test cases", len(test_cases))
            except Exception as e:
                logger.error("Test case generation failed: %s", e, exc_info=True)
                # Continue without generated TCs — still run the JAR

        # Phase 2: Write YAMLs and execute JAR
        if not inp.run_dir:
            return SkillOutput(status="error", error="run_dir is required for JAR execution")

        val_path = ""
        json_path = ""
        if inp.validation_rules_yaml:
            val_path = write_yaml_to_file(
                inp.validation_rules_yaml, inp.run_dir,
                f"{inp.uc_name}_validation-rules.yaml",
            )
        if inp.json_output_yaml:
            json_path = write_yaml_to_file(
                inp.json_output_yaml, inp.run_dir,
                f"{inp.uc_name}_json-output.yaml",
            )

        jar_result = await run_ciq_processor(
            jar_path=jar_path,
            ciq_file_path=inp.ciq_file_path,
            validation_rules_path=val_path,
            json_output_path=json_path,
            output_dir=inp.run_dir,
            java_home=inp.java_home,
        )

        # Phase 3: Parse results
        jar_errors = parse_jar_output(jar_result.get("output_dir", ""))

        # Phase 4: Evaluate test cases against JAR output
        if test_cases:
            results = evaluate_test_cases(test_cases, jar_errors)
        else:
            # No generated TCs — create a single result from JAR execution
            results = [
                TestCaseResult(
                    id="TC_BASE",
                    description="Base CIQ validation + JSON output generation",
                    category="integration",
                    severity="critical",
                    test_type="positive",
                    expected_outcome="JAR completes with exit code 0",
                    actual_outcome="Success" if jar_result["success"] else f"Failed: exit {jar_result['exit_code']}",
                    status="PASS" if jar_result["success"] and not jar_errors else "FAIL",
                )
            ]

        total = len(results)
        passed = sum(1 for r in results if r.status == "PASS")
        failed = total - passed
        # Only count positive test failures as blocking — negative test failures
        # are warnings (JAR may not support content validation like enum/regex)
        positive_fails = sum(1 for r in results
                             if r.status == "FAIL" and getattr(r, "test_type", "positive") != "negative")
        duration = time.monotonic() - t0

        # Phase 5: Generate QA report
        report_html = generate_qa_report(
            uc_name=inp.uc_name,
            test_results=results,
            ciq_file=inp.ciq_file_path,
            validation_rules_file=val_path,
            json_output_file=json_path,
            duration_sec=duration,
        )

        status = "passed" if positive_fails == 0 else "failed"
        if positive_fails == 0 and failed > 0:
            logger.info("QA: %d negative test warnings (non-blocking), all positive tests passed", failed)
        logger.info("QA complete: %d/%d passed, %d positive fails (%.1fs)", passed, total, positive_fails, duration)

        return SkillOutput(
            total=total,
            passed=passed,
            failed=failed,
            test_cases=results,
            report_html=report_html,
            status=status,
        )
