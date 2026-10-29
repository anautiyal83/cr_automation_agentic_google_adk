"""JSON output YAML generation — LLM generation + auto-fix."""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from . import prompts
from .autofix import clean_yaml_response, fix_json_output, parse_yaml, dump_yaml, _ciq_prompt_summary

logger = logging.getLogger(__name__)

LlmFn = Callable[[str, str], Awaitable[str]]


async def generate_json_output(
    llm_fn: LlmFn,
    uc_name: str,
    ciq_sheets: dict,
    requirements: dict,
    schema_summary: str,
) -> tuple[str, list[str]]:
    """Generate json-output.yaml via LLM and auto-fix.

    Args:
        ciq_sheets: CIQ structure from requirements.doc_extracted.ciq_sheets.

    Returns:
        (yaml_text, errors) — the generated YAML and any validation errors.
    """
    requirements_summary = _build_requirements_summary(requirements)
    ciq_summary = _ciq_prompt_summary(ciq_sheets)

    user_prompt = prompts.JSON_OUTPUT_USER.format(
        uc_name=uc_name,
        ciq_summary=ciq_summary,
        requirements_summary=requirements_summary,
        schema_summary=schema_summary,
    )

    logger.info("Generating json-output.yaml via LLM (%d char prompt)", len(user_prompt))
    raw_response = await llm_fn(prompts.JSON_OUTPUT_SYSTEM, user_prompt)
    yaml_text = clean_yaml_response(raw_response)

    yaml_text = fix_json_output(yaml_text, ciq_sheets)

    # Re-dump through parse→dump to ensure valid YAML on disk
    parsed = parse_yaml(yaml_text)
    if parsed:
        yaml_text = dump_yaml(parsed)

    errors = _validate_structure(yaml_text)

    logger.info("JSON output generated: %d chars, %d errors", len(yaml_text), len(errors))
    return yaml_text, errors


async def regenerate_json_output(
    llm_fn: LlmFn,
    current_yaml: str,
    ciq_sheets: dict,
    test_failures: str,
    reviewer_analysis: str,
    schema_summary: str,
) -> tuple[str, list[str]]:
    """Regenerate json-output.yaml using test feedback."""
    ciq_summary = _ciq_prompt_summary(ciq_sheets)

    user_prompt = prompts.JSON_OUTPUT_REGENERATE.format(
        current_yaml=current_yaml[:8000],
        test_failures=test_failures[:4000],
        reviewer_analysis=reviewer_analysis[:3000],
        ciq_summary=ciq_summary,
        schema_summary=schema_summary,
    )

    logger.info("Regenerating json-output.yaml with feedback (%d char prompt)", len(user_prompt))
    raw_response = await llm_fn(prompts.JSON_OUTPUT_SYSTEM, user_prompt)
    yaml_text = clean_yaml_response(raw_response)

    yaml_text = fix_json_output(yaml_text, ciq_sheets)
    errors = _validate_structure(yaml_text)

    logger.info("JSON output regenerated: %d chars, %d errors", len(yaml_text), len(errors))
    return yaml_text, errors


def _build_requirements_summary(requirements: dict) -> str:
    """Build requirements summary focused on JSON output generation needs."""
    parts = []

    cat1 = requirements.get("category_1_node_activity", {})
    nodes = cat1.get("nodes", [])
    if nodes:
        node_names = [n.get("name", "?") for n in nodes if isinstance(n, dict)]
        parts.append(f"Nodes: {', '.join(node_names)}")
        data_sheet = cat1.get("data_sheet", "")
        if data_sheet:
            parts.append(f"Data sheet: {data_sheet}")
        parts.append(f"NIAM failover: {cat1.get('niam_failover', False)}")

    cat2 = requirements.get("category_2_commands", {})
    commands = cat2.get("commands", [])
    if commands:
        ciq_params = set()
        for c in commands:
            if isinstance(c, dict):
                ciq_params.update(c.get("ciq_params", []))
        if ciq_params:
            parts.append(f"CIQ params referenced by commands: {', '.join(sorted(ciq_params))}")

    cat3 = requirements.get("category_3_orchestration", {})
    grouping = cat3.get("grouping", {})
    if grouping:
        parts.append(f"Grouping: column={grouping.get('column', '?')}, label={grouping.get('label', '?')}")
    phases = cat3.get("phases", [])
    if phases:
        parts.append(f"Phases: {', '.join(phases)}")

    xml = requirements.get("xml_templates") or {}
    mappings = xml.get("ciq_to_xml_mapping", [])
    if mappings:
        cols = [m.get("ciq_column", "?") for m in mappings if isinstance(m, dict)]
        parts.append(f"XML-mapped CIQ columns: {', '.join(cols)}")

    answers = requirements.get("developer_answers", {})
    if answers:
        parts.append(f"Developer answers: {len(answers)} responses")

    return "\n".join(parts) if parts else "No requirements available"


def _validate_structure(yaml_text: str) -> list[str]:
    """Basic structural validation of json-output YAML."""
    errors = []
    parsed = parse_yaml(yaml_text)
    if parsed is None:
        errors.append("Invalid YAML syntax")
        return errors

    if "output_mode" not in parsed:
        errors.append("Missing 'output_mode' (required: 'individual' or 'single')")

    if parsed.get("output_mode") == "individual":
        seg = parsed.get("segregate_by", {})
        if not isinstance(seg, dict) or not seg.get("sheet") or not seg.get("column"):
            errors.append("output_mode=individual requires segregate_by with sheet and column")

    if "data" not in parsed:
        errors.append("Missing 'data' section")
    elif not isinstance(parsed.get("data"), dict):
        errors.append("'data' must be a mapping")

    return errors
