"""Parse ciq-processor JAR output into structured test results."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .test_case_gen import TestCase

logger = logging.getLogger(__name__)


@dataclass
class ValidationError:
    """A single validation error from the JAR."""
    sheet: str = ""
    row: int = 0
    column: str = ""
    message: str = ""
    rule_type: str = ""
    severity: str = "error"


@dataclass
class TestCaseResult:
    """Result of executing a single test case."""
    id: str = ""
    description: str = ""
    category: str = ""
    severity: str = "major"
    test_type: str = "positive"
    target_sheet: str = ""
    expected_outcome: str = ""
    actual_outcome: str = ""
    status: str = "PASS"        # PASS | FAIL | ERROR | SKIPPED
    errors: list[ValidationError] = field(default_factory=list)
    validation_points_met: list[str] = field(default_factory=list)
    validation_points_missed: list[str] = field(default_factory=list)
    duration_ms: int = 0


def parse_jar_output(qa_dir: str) -> list[dict]:
    """Parse the ciq-processor output directory for validation results.

    Reads JSON result files from qa/results/ and returns raw error data.
    """
    results_dir = Path(qa_dir) / "results"
    all_errors: list[dict] = []

    if not results_dir.exists():
        # Try qa_dir directly
        results_dir = Path(qa_dir)

    for jf in sorted(results_dir.glob("**/*.json")):
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                # Extract validation errors from various JAR output formats
                errors = _extract_errors_from_report(data, jf.name)
                all_errors.extend(errors)
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        errors = _extract_errors_from_report(item, jf.name)
                        all_errors.extend(errors)
        except Exception as e:
            logger.debug("Could not parse JAR output %s: %s", jf, e)

    logger.info("Parsed JAR output: %d total errors from %s", len(all_errors), qa_dir)
    return all_errors


def evaluate_test_cases(
    test_cases: list[TestCase],
    jar_errors: list[dict],
) -> list[TestCaseResult]:
    """Evaluate generated test cases against actual JAR output.

    For each test case:
    - Positive tests: PASS if no errors on target sheet/columns, FAIL if errors found
    - Negative tests: PASS if errors correctly caught, FAIL if errors missed
    """
    results = []

    # Group JAR errors by sheet
    errors_by_sheet: dict[str, list[dict]] = {}
    for err in jar_errors:
        sheet = err.get("sheet", "unknown")
        errors_by_sheet.setdefault(sheet, []).append(err)

    for tc in test_cases:
        sheet_errors = errors_by_sheet.get(tc.target_sheet, [])

        # Filter to relevant columns if specified
        relevant_errors = sheet_errors
        if tc.target_columns:
            tc_cols = {c.lower() for c in tc.target_columns}
            relevant_errors = [
                e for e in sheet_errors
                if e.get("column", "").lower() in tc_cols
            ]

        validation_errors = [
            ValidationError(
                sheet=e.get("sheet", ""),
                row=e.get("row", 0),
                column=e.get("column", ""),
                message=e.get("message", ""),
                rule_type=e.get("rule_type", ""),
                severity=e.get("severity", "error"),
            )
            for e in relevant_errors
        ]

        # Determine status based on test type
        if tc.test_type == "positive":
            # Positive test: should have NO errors
            status = "PASS" if not relevant_errors else "FAIL"
            actual = "No validation errors" if not relevant_errors else f"{len(relevant_errors)} validation error(s)"
        elif tc.test_type == "negative":
            # Negative test: should HAVE errors (invalid data caught)
            status = "PASS" if relevant_errors else "FAIL"
            actual = f"{len(relevant_errors)} error(s) correctly caught" if relevant_errors else "No errors — invalid data not caught"
        else:
            status = "PASS" if not relevant_errors else "FAIL"
            actual = f"{len(relevant_errors)} error(s)" if relevant_errors else "Clean"

        # Check validation points
        points_met = []
        points_missed = []
        for vp in tc.validation_points:
            vp_lower = vp.lower()
            if any(vp_lower in e.get("message", "").lower() for e in relevant_errors):
                points_met.append(vp)
            elif tc.test_type == "negative":
                points_missed.append(vp)
            else:
                points_met.append(vp)

        results.append(TestCaseResult(
            id=tc.id,
            description=tc.description,
            category=tc.category,
            severity=tc.severity,
            test_type=tc.test_type,
            target_sheet=tc.target_sheet,
            expected_outcome=tc.expected_outcome,
            actual_outcome=actual,
            status=status,
            errors=validation_errors,
            validation_points_met=points_met,
            validation_points_missed=points_missed,
        ))

    return results


def _extract_errors_from_report(data: dict, source_file: str = "") -> list[dict]:
    """Extract validation error entries from a JAR output JSON report."""
    errors = []

    # Format 1: {"errors": [{"sheet", "row", "column", "message"}]}
    for err in data.get("errors", []):
        if isinstance(err, dict):
            errors.append(err)

    # Format 2: {"sheets": {"SheetName": {"errors": [...]}}}
    for sheet_name, sheet_data in data.get("sheets", {}).items():
        if isinstance(sheet_data, dict):
            for err in sheet_data.get("errors", []):
                if isinstance(err, dict):
                    err.setdefault("sheet", sheet_name)
                    errors.append(err)

    # Format 3: {"validationResults": [{"sheet", "column", "row", "message", "status"}]}
    for vr in data.get("validationResults", []):
        if isinstance(vr, dict) and vr.get("status", "").lower() in ("fail", "error", "failed"):
            errors.append({
                "sheet": vr.get("sheet", ""),
                "row": vr.get("row", 0),
                "column": vr.get("column", ""),
                "message": vr.get("message", vr.get("description", "")),
                "rule_type": vr.get("ruleType", vr.get("rule_type", "")),
                "severity": vr.get("severity", "error"),
            })

    # Format 4: test case results directory structure
    for tc_key in ("testCases", "test_cases", "results"):
        for tc in data.get(tc_key, []):
            if isinstance(tc, dict) and tc.get("status", "").upper() in ("FAIL", "FAILED", "ERROR"):
                for err in tc.get("errors", []):
                    if isinstance(err, dict):
                        errors.append(err)

    return errors
