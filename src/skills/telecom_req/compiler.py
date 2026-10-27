"""Merge doc-extracted + LLM-compiled + user answers into final requirements.

Also injects discovered schema summaries into generation_hints so downstream
LLM agents (Steps 3 & 4) produce schema-compliant YAML.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from .schema_discovery import (
    DiscoveredSchemas,
    build_json_output_summary,
    build_validation_rules_summary,
    discover_schemas,
)

logger = logging.getLogger(__name__)


def merge_doc_extracted(requirements: dict, doc_extracted: dict) -> dict:
    """Merge deterministic document extractions into LLM-compiled requirements.

    ``doc_extracted`` has authoritative data parsed directly from files.
    It overrides LLM where available (LLM may miss some commands).
    """
    requirements["doc_extracted"] = doc_extracted

    mop_commands = doc_extracted.get("mop_commands", [])
    if mop_commands:
        existing_cmds = requirements.get("category_2_commands", {}).get("commands", [])
        existing_names = {
            c.get("cmd", c.get("command", "")).lower()
            for c in existing_cmds
            if isinstance(c, dict)
        }
        for mc in mop_commands:
            cmd_text = mc.get("command", mc) if isinstance(mc, dict) else str(mc)
            if cmd_text.lower() not in existing_names:
                existing_cmds.append({
                    "cmd": cmd_text,
                    "node": mc.get("node", "") if isinstance(mc, dict) else "",
                    "purpose": mc.get("description", "") if isinstance(mc, dict) else "",
                    "syntax": cmd_text,
                    "validation_criteria": mc.get("validation", "") if isinstance(mc, dict) else "",
                    "target_user": mc.get("user", "") if isinstance(mc, dict) else "",
                })
        if "category_2_commands" not in requirements:
            requirements["category_2_commands"] = {}
        requirements["category_2_commands"]["commands"] = existing_cmds

    return requirements


def enforce_answers(
    requirements: dict,
    answers: dict,
    raw_artifacts: dict,
    ssh_prompts: list[str],
) -> dict:
    """Override LLM-compiled requirements with explicit user answers.

    Ensures questionnaire answers are respected — LLM may hallucinate or ignore them.
    Handles:
    - Phase enforcement (backup/rollback removal)
    - Prompt regex from SSH logs
    - Raw MOP/XML in generation hints
    """
    if not answers:
        return requirements

    orch = requirements.get("category_3_orchestration", {})
    nodes = requirements.get("category_1_node_activity", {})
    hints = requirements.get("generation_hints", {})

    # -- Phase enforcement --
    phases = orch.get("phases", [])

    no_backup = any(
        "no backup" in str(v).lower() or "not required" in str(v).lower()
        for k, v in answers.items()
        if "backup" in k.lower() or "backup" in str(v).lower()
    )
    no_rollback = any(
        "no rollback" in str(v).lower() or "not required" in str(v).lower()
        for k, v in answers.items()
        if "rollback" in k.lower() or "rollback" in str(v).lower()
    )

    if no_backup and "backup" in [p.lower() for p in phases]:
        phases = [p for p in phases if p.lower() != "backup"]
        logger.info("Enforced: removed 'backup' phase per user answer")
    if no_rollback and "rollback" in [p.lower().replace("rollbackconfiguration", "rollback") for p in phases]:
        phases = [p for p in phases if "rollback" not in p.lower()]
        logger.info("Enforced: removed 'rollback' phase per user answer")

    orch["phases"] = phases
    requirements["category_3_orchestration"] = orch

    # -- Prompt regex from SSH logs --
    if ssh_prompts:
        node_list = nodes.get("nodes", [])
        for node in node_list:
            node_name = node.get("name", "").upper()
            for prompt_str in ssh_prompts:
                if node_name.lower() in prompt_str.lower():
                    if not any(
                        "prompt" in k.lower() and node_name.lower() in k.lower()
                        for k in answers
                    ):
                        node["prompt_regex_from_logs"] = prompt_str
        nodes["nodes"] = node_list
        requirements["category_1_node_activity"] = nodes

    # -- Generation hints with raw artifacts --
    wf_hints = hints.get("workflow_yaml", {}) if isinstance(hints.get("workflow_yaml"), dict) else {}

    if no_backup:
        wf_hints.setdefault("phase_overrides", []).append(
            "DO NOT generate a backup phase. User explicitly stated backup is not required."
        )
    if no_rollback:
        wf_hints.setdefault("phase_overrides", []).append(
            "DO NOT generate a rollback phase. User explicitly stated rollback is not required."
        )

    if raw_artifacts.get("mop_text"):
        wf_hints["raw_mop_text"] = raw_artifacts["mop_text"]
    if raw_artifacts.get("xml_samples"):
        wf_hints["xml_samples"] = raw_artifacts["xml_samples"]

    hints["workflow_yaml"] = wf_hints
    requirements["generation_hints"] = hints

    # -- Store raw answers for downstream access --
    requirements["developer_answers"] = answers

    return requirements


def inject_schema_hints(
    requirements: dict,
    schemas: DiscoveredSchemas | None = None,
    schema_dir: str | None = None,
) -> dict:
    """Inject schema summaries into generation_hints for Steps 3 & 4.

    Discovers schemas from disk if not provided, then appends concise
    schema references into ``generation_hints.step_3`` and
    ``generation_hints.step_4`` so the downstream LLM agents produce
    YAML that conforms to the project's authoritative schemas.

    Also records ``generation_hints.schema_dir`` and any missing schemas.
    """
    if schemas is None:
        schemas = discover_schemas(schema_dir)

    hints = requirements.get("generation_hints", {})
    if not isinstance(hints, dict):
        hints = {}

    # Step 3: validation rules
    if schemas.validation_rules.found:
        existing = hints.get("step_3", "")
        vr_summary = build_validation_rules_summary(schemas.validation_rules)
        hints["step_3"] = (existing + "\n\n" + vr_summary).strip() if existing else vr_summary
        logger.info("Injected validation-rules schema summary into generation_hints.step_3")
    else:
        logger.warning("Validation rules schema not found: %s", schemas.validation_rules.error)

    # Step 4: json output
    if schemas.json_output.found:
        existing = hints.get("step_4", "")
        jo_summary = build_json_output_summary(schemas.json_output)
        hints["step_4"] = (existing + "\n\n" + jo_summary).strip() if existing else jo_summary
        logger.info("Injected json-output schema summary into generation_hints.step_4")
    else:
        logger.warning("JSON output schema not found: %s", schemas.json_output.error)

    # Record discovery metadata
    hints["schema_dir"] = schemas.schema_dir
    if schemas.missing:
        hints["schema_missing"] = schemas.missing

    requirements["generation_hints"] = hints
    return requirements
