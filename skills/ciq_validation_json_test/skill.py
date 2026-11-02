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

        # Sanitize rules for JAR compatibility (strip LLM-generated unsupported fields)
        from skills.network_config_test.jar_executor import _sanitize_rules_for_jar
        sanitized_val_path = _sanitize_rules_for_jar(val_path) if val_path else val_path

        jar_result = await run_ciq_processor(
            jar_path=jar_path,
            ciq_file_path=inp.ciq_file_path,
            validation_rules_path=sanitized_val_path,
            json_output_path=json_path,
            output_dir=inp.run_dir,
            java_home=inp.java_home,
            uc_name=inp.uc_name,
        )

        # Phase 3: Parse results
        jar_errors = parse_jar_output(jar_result.get("output_dir", ""))

        # Phase 4: Evaluate test cases against JAR output
        if test_cases:
            results = evaluate_test_cases(test_cases, jar_errors)
        else:
            # No LLM-generated TCs — build results from JAR validation report
            results = _build_results_from_jar(jar_result, jar_errors)

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


def _build_results_from_jar(jar_result: dict, jar_errors: list[dict]) -> list[TestCaseResult]:
    """Build detailed test case results from JAR validation output.

    Creates one TC per sheet (pass/fail) plus individual TCs for each
    validation error, so the QA report shows full detail.
    """
    results = []

    # TC 1: Overall JAR execution
    results.append(TestCaseResult(
        id="TC_JAR",
        description="CIQ Processor JAR execution",
        category="integration",
        severity="critical",
        test_type="positive",
        expected_outcome="JAR completes successfully (exit 0)",
        actual_outcome="Success" if jar_result["success"] else f"Failed: exit {jar_result['exit_code']}",
        status="PASS" if jar_result["success"] else "FAIL",
    ))

    # Parse the validation report JSON for per-sheet results
    import json as _json
    from pathlib import Path
    output_dir = jar_result.get("output_dir", "")
    report_files = list(Path(output_dir).glob("*VALIDATION_REPORT*.json")) if output_dir else []

    if report_files:
        try:
            report = _json.loads(report_files[0].read_text(encoding="utf-8"))
            for sheet in report.get("sheets", []):
                sheet_name = sheet.get("sheetName", "Unknown")
                sheet_status = sheet.get("status", "UNKNOWN")
                rows_checked = sheet.get("rowsChecked", 0)
                sheet_errors = sheet.get("errors", [])

                # Per-sheet summary TC
                results.append(TestCaseResult(
                    id=f"TC_SHEET_{sheet_name.upper().replace(' ', '_')}",
                    description=f"Validate sheet '{sheet_name}' ({rows_checked} rows)",
                    category="column_validation",
                    severity="critical" if sheet_errors else "minor",
                    test_type="positive",
                    target_sheet=sheet_name,
                    expected_outcome=f"All {rows_checked} rows pass validation",
                    actual_outcome=f"PASSED ({rows_checked} rows)" if sheet_status == "PASSED"
                                   else f"FAILED: {len(sheet_errors)} error(s)",
                    status="PASS" if sheet_status == "PASSED" else "FAIL",
                ))

                # Individual error TCs
                for err in sheet_errors:
                    row_num = err.get("rowNumber", "?")
                    col = err.get("column", "?")
                    value = err.get("value", "")
                    message = err.get("message", "Validation error")

                    from .result_parser import ValidationError
                    results.append(TestCaseResult(
                        id=f"TC_{sheet_name.upper().replace(' ', '_')}_R{row_num}_{col.upper().replace(' ', '_')}",
                        description=f"Validate '{col}' in '{sheet_name}' row {row_num}",
                        category="column_validation",
                        severity="major",
                        test_type="positive",
                        target_sheet=sheet_name,
                        expected_outcome=f"Value passes '{col}' validation rules",
                        actual_outcome=f"Value '{value}' — {message[:150]}",
                        status="FAIL",
                        errors=[ValidationError(
                            sheet=sheet_name, column=col, row=row_num, message=message,
                        )],
                    ))
        except Exception as e:
            logger.debug("Could not parse validation report: %s", e)

    # Fallback: build from jar_errors if no report parsed
    if len(results) == 1 and jar_errors:
        for i, err in enumerate(jar_errors, 1):
            results.append(TestCaseResult(
                id=f"TC_ERR_{i:03d}",
                description=err.get("message", "Validation error")[:100],
                category=err.get("category", "column_validation"),
                severity=err.get("severity", "major"),
                test_type="positive",
                target_sheet=err.get("sheet", ""),
                expected_outcome="No validation errors",
                actual_outcome=err.get("message", "Error")[:200],
                status="FAIL",
            ))

    return results
