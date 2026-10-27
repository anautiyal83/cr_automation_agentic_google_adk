"""LLM-powered test case generation for CIQ validation & JSON output."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from . import prompts

logger = logging.getLogger(__name__)

LlmFn = Callable[[str, str], Awaitable[str]]


@dataclass
class TestCase:
    """A single generated test case."""
    id: str = ""
    category: str = ""          # column_validation, cross_sheet, json_mapping, edge_case, negative, integration
    severity: str = "major"     # critical, major, minor
    description: str = ""
    preconditions: str = ""
    test_type: str = "positive" # positive, negative
    target_sheet: str = ""
    target_columns: list[str] = field(default_factory=list)
    scenario: str = ""
    expected_outcome: str = ""
    validation_points: list[str] = field(default_factory=list)


async def generate_test_cases(
    llm_fn: LlmFn,
    uc_name: str,
    ciq_sheets: dict,
    validation_rules_yaml: str,
    json_output_yaml: str,
) -> list[TestCase]:
    """Generate test cases via LLM from CIQ structure + generated YAMLs.

    Returns a list of TestCase objects ready for execution.
    """
    ciq_summary = _build_ciq_summary(ciq_sheets)

    user_prompt = prompts.TEST_CASE_GEN_USER.format(
        uc_name=uc_name,
        ciq_summary=ciq_summary,
        validation_rules_yaml=validation_rules_yaml[:6000],
        json_output_yaml=json_output_yaml[:4000],
    )

    logger.info("Generating test cases via LLM (%d char prompt)", len(user_prompt))
    raw_response = await llm_fn(prompts.TEST_CASE_GEN_SYSTEM, user_prompt)

    test_cases = _parse_test_cases(raw_response)
    logger.info("Generated %d test cases", len(test_cases))
    return test_cases


def _parse_test_cases(response: str) -> list[TestCase]:
    """Parse LLM JSON response into TestCase objects."""
    # Strip markdown fencing
    text = re.sub(r"^```(?:json)?\s*\n?", "", response, flags=re.MULTILINE)
    text = re.sub(r"\n?```\s*$", "", text, flags=re.MULTILINE).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON array in the response
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                logger.error("Could not parse test cases from LLM response")
                return []
        else:
            return []

    if not isinstance(data, list):
        data = [data]

    test_cases = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        tc = TestCase(
            id=item.get("id", f"TC_{i+1:03d}"),
            category=item.get("category", ""),
            severity=item.get("severity", "major"),
            description=item.get("description", ""),
            preconditions=item.get("preconditions", ""),
            test_type=item.get("test_type", "positive"),
            target_sheet=item.get("target_sheet", ""),
            target_columns=item.get("target_columns", []),
            scenario=item.get("scenario", ""),
            expected_outcome=item.get("expected_outcome", ""),
            validation_points=item.get("validation_points", []),
        )
        test_cases.append(tc)

    return test_cases


def _build_ciq_summary(ciq_sheets: dict) -> str:
    """Build CIQ summary for test case generation prompt."""
    if not ciq_sheets:
        return "No CIQ data"
    parts = []
    for sname, sdata in ciq_sheets.items():
        cols = sdata.get("columns", sdata.get("headers", []))
        parts.append(f'Sheet "{sname}": columns=[{", ".join(cols[:20])}]')
        samples = sdata.get("sample_rows", [])
        if samples and isinstance(samples[0], dict):
            vals = ", ".join(f"{k}={v!r}" for k, v in list(samples[0].items())[:6])
            parts.append(f"  sample: {vals}")
    return "\n".join(parts)
