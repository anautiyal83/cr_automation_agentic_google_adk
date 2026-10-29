"""Validation rules YAML generation — LLM generation + auto-fix."""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from . import prompts
from .autofix import clean_yaml_response, fix_validation_rules, parse_yaml, dump_yaml, _ciq_prompt_summary

logger = logging.getLogger(__name__)

LlmFn = Callable[[str, str], Awaitable[str]]


async def generate_validation_rules(
    llm_fn: LlmFn,
    uc_name: str,
    ciq_sheets: dict,
    requirements: dict,
    schema_summary: str,
) -> tuple[str, list[str]]:
    """Generate validation-rules.yaml via LLM and auto-fix.

    Args:
        ciq_sheets: CIQ structure from requirements.doc_extracted.ciq_sheets.

    Returns:
        (yaml_text, errors) — the generated YAML and any validation errors.
    """
    requirements_summary = _build_requirements_summary(requirements)
    ciq_summary = _ciq_prompt_summary(ciq_sheets)

    user_prompt = prompts.VALIDATION_RULES_USER.format(
        uc_name=uc_name,
        ciq_summary=ciq_summary,
        requirements_summary=requirements_summary,
        schema_summary=schema_summary,
    )

    logger.info("Generating validation-rules.yaml via LLM (%d char prompt)", len(user_prompt))
    raw_response = await llm_fn(prompts.VALIDATION_RULES_SYSTEM, user_prompt)
    yaml_text = clean_yaml_response(raw_response)

    yaml_text = fix_validation_rules(yaml_text, ciq_sheets)

    # Re-dump through parse→dump to ensure valid YAML on disk
    parsed = parse_yaml(yaml_text)
    if parsed:
        yaml_text = dump_yaml(parsed)

    errors = _validate_structure(yaml_text)

    logger.info("Validation rules generated: %d chars, %d errors", len(yaml_text), len(errors))
    return yaml_text, errors


async def regenerate_validation_rules(
    llm_fn: LlmFn,
    current_yaml: str,
    ciq_sheets: dict,
    test_failures: str,
    reviewer_analysis: str,
    schema_summary: str,
) -> tuple[str, list[str]]:
    """Regenerate validation-rules.yaml using test feedback."""
    ciq_summary = _ciq_prompt_summary(ciq_sheets)

    user_prompt = prompts.VALIDATION_RULES_REGENERATE.format(
        current_yaml=current_yaml[:8000],
        test_failures=test_failures[:4000],
        reviewer_analysis=reviewer_analysis[:3000],
        ciq_summary=ciq_summary,
        schema_summary=schema_summary,
    )

    logger.info("Regenerating validation-rules.yaml with feedback (%d char prompt)", len(user_prompt))
    raw_response = await llm_fn(prompts.VALIDATION_RULES_SYSTEM, user_prompt)
    yaml_text = clean_yaml_response(raw_response)

    yaml_text = fix_validation_rules(yaml_text, ciq_sheets)
    errors = _validate_structure(yaml_text)

    logger.info("Validation rules regenerated: %d chars, %d errors", len(yaml_text), len(errors))
    return yaml_text, errors


def _build_requirements_summary(requirements: dict) -> str:
    """Build a concise requirements summary for the LLM prompt."""
    parts = []

    cat1 = requirements.get("category_1_node_activity", {})
    nodes = cat1.get("nodes", [])
    if nodes:
        node_names = [n.get("name", "?") for n in nodes if isinstance(n, dict)]
        parts.append(f"Nodes: {', '.join(node_names)}")
        parts.append(f"Activity type: {cat1.get('activity_type', 'per_subscriber')}")
        data_sheet = cat1.get("data_sheet", "")
        if data_sheet:
            parts.append(f"Data sheet: {data_sheet}")

    cat2 = requirements.get("category_2_commands", {})
    commands = cat2.get("commands", [])
    if commands:
        cmd_names = [c.get("cmd", "?") for c in commands[:10] if isinstance(c, dict)]
        parts.append(f"Commands ({len(commands)}): {', '.join(cmd_names)}")

    cat3 = requirements.get("category_3_orchestration", {})
    grouping = cat3.get("grouping", {})
    if grouping:
        parts.append(f"Grouping column: {grouping.get('column', '?')}")

    answers = requirements.get("developer_answers", {})
    if answers:
        parts.append(f"Developer answers: {len(answers)} responses")

    return "\n".join(parts) if parts else "No requirements available"


def _validate_structure(yaml_text: str) -> list[str]:
    """Basic structural validation of the generated YAML."""
    errors = []
    parsed = parse_yaml(yaml_text)
    if parsed is None:
        errors.append("Invalid YAML syntax")
        return errors

    if "sheets" not in parsed:
        errors.append("Missing 'sheets' section")

    sheets = parsed.get("sheets", {})
    if isinstance(sheets, dict):
        for sname, sconfig in sheets.items():
            if not isinstance(sconfig, dict):
                errors.append(f"Sheet '{sname}' config is not a dict")
                continue
            cols = sconfig.get("columns", {})
            if not cols:
                errors.append(f"Sheet '{sname}' has no columns defined")

    return errors
