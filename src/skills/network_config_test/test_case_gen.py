"""LLM-powered test case generation for network configuration testing."""
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
    """A single generated test case for workflow testing."""
    id: str = ""
    category: str = ""          # mop_generation, command_execution, validation_criteria, etc.
    severity: str = "major"     # critical, major, minor
    phase: str = ""             # workflow phase this targets
    description: str = ""
    test_type: str = "both"     # mop_test, execution_test, both
    target_node: str = ""
    target_command: str = ""
    scenario: str = ""
    expected_outcome: str = ""
    validation_points: list[str] = field(default_factory=list)


async def generate_test_cases(
    llm_fn: LlmFn,
    uc_name: str,
    workflow_yaml: str,
    simulator_config_yaml: str,
    requirements: dict,
) -> list[TestCase]:
    """Generate test cases via LLM from workflow + simulator config."""
    cat1 = requirements.get("category_1_node_activity", {})
    nodes = cat1.get("nodes", [])
    node_names = [n.get("name", "?") for n in nodes if isinstance(n, dict)]

    cat3 = requirements.get("category_3_orchestration", {})
    phases = cat3.get("phases", [])

    cat2 = requirements.get("category_2_commands", {})
    commands_count = len(cat2.get("commands", []))

    user_prompt = prompts.TEST_CASE_GEN_USER.format(
        uc_name=uc_name,
        workflow_yaml=workflow_yaml[:8000],
        simulator_summary=simulator_config_yaml[:3000],
        nodes_summary=", ".join(node_names),
        phases=", ".join(phases) if phases else "unknown",
        commands_count=commands_count,
    )

    logger.info("Generating network config test cases via LLM (%d char prompt)", len(user_prompt))
    raw = await llm_fn(prompts.TEST_CASE_GEN_SYSTEM, user_prompt)
    test_cases = _parse_test_cases(raw)
    logger.info("Generated %d test cases", len(test_cases))
    return test_cases


def _parse_test_cases(response: str) -> list[TestCase]:
    """Parse LLM JSON response into TestCase objects."""
    text = re.sub(r"^```(?:json)?\s*\n?", "", response, flags=re.MULTILINE)
    text = re.sub(r"\n?```\s*$", "", text, flags=re.MULTILINE).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
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
            phase=item.get("phase", ""),
            description=item.get("description", ""),
            test_type=item.get("test_type", "both"),
            target_node=item.get("target_node", ""),
            target_command=item.get("target_command", ""),
            scenario=item.get("scenario", ""),
            expected_outcome=item.get("expected_outcome", ""),
            validation_points=item.get("validation_points", []),
        )
        test_cases.append(tc)

    return test_cases
