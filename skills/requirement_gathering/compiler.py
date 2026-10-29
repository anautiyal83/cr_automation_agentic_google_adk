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


def merge_previous_requirements(current: dict, previous: dict) -> dict:
    """Merge previously saved requirements into the current run's output.

    Strategy: for each category, fill in gaps from previous data without
    overwriting what the current LLM compilation produced. This way each
    re-run accumulates information — new runs add to/refine previous data.
    """
    if not previous:
        return current

    for cat_key in ("category_1_node_activity", "category_2_commands",
                    "category_3_orchestration", "xml_templates"):
        prev_cat = previous.get(cat_key)
        if not prev_cat or not isinstance(prev_cat, dict):
            continue
        curr_cat = current.setdefault(cat_key, {})

        for key, prev_val in prev_cat.items():
            curr_val = curr_cat.get(key)

            # Skip if current already has a non-empty value
            if curr_val and curr_val != "\u2014":
                # Special case: merge lists (nodes, commands) by appending missing items
                if isinstance(curr_val, list) and isinstance(prev_val, list):
                    curr_cat[key] = _merge_lists(curr_val, prev_val)
                continue

            # Current is empty — use previous value
            if prev_val and prev_val != "\u2014":
                curr_cat[key] = prev_val

    # Merge developer_answers from previous run
    prev_answers = previous.get("developer_answers", {})
    if prev_answers:
        curr_answers = current.setdefault("developer_answers", {})
        for k, v in prev_answers.items():
            if k not in curr_answers:
                curr_answers[k] = v

    logger.info("Merged previous requirements: %d categories checked",
                sum(1 for k in previous if k.startswith("category_")))
    return current


def _merge_lists(current: list, previous: list) -> list:
    """Merge two lists of dicts, adding items from previous that aren't in current.

    Uses 'name' or 'cmd' as identity keys for dedup.
    """
    if not previous:
        return current
    if not current:
        return list(previous)

    # Build set of identity keys from current list
    current_ids = set()
    for item in current:
        if isinstance(item, dict):
            ident = item.get("name") or item.get("cmd") or item.get("command", "")
            if ident:
                current_ids.add(str(ident).lower())

    # Add missing items from previous
    for item in previous:
        if isinstance(item, dict):
            ident = item.get("name") or item.get("cmd") or item.get("command", "")
            if ident and str(ident).lower() not in current_ids:
                current.append(item)
                current_ids.add(str(ident).lower())

    return current


def merge_vendor_profile(requirements: dict, vendor_profile: dict) -> dict:
    """Merge vendor profile data into compiled requirements.

    Ensures nodes, SSH config, escalation, gateway, and CIQ mappings
    from the vendor profile appear in the final requirements even if
    the LLM compiler omitted them.
    """
    if not vendor_profile:
        return requirements

    vendor = vendor_profile.get("vendor", {})
    vp_cat1 = vendor_profile.get("category_1_node_activity", {})
    vp_cat3 = vendor_profile.get("category_3_orchestration", {})

    cat1 = requirements.setdefault("category_1_node_activity", {})
    cat3 = requirements.setdefault("category_3_orchestration", {})

    # -- Nodes: merge vendor profile nodes into requirements --
    vp_nodes = vp_cat1.get("nodes", [])
    existing_nodes = cat1.get("nodes", [])
    existing_names = {
        n.get("name", "").upper() for n in existing_nodes if isinstance(n, dict)
    }

    for vn in vp_nodes:
        name = vn.get("name", "")
        if name.upper() not in existing_names:
            existing_nodes.append(vn)
            existing_names.add(name.upper())
        else:
            # Existing node — vendor profile OVERRIDES LLM values
            for en in existing_nodes:
                if isinstance(en, dict) and en.get("name", "").upper() == name.upper():
                    vn_ssh = vn.get("ssh", {})
                    vn_esc = vn.get("escalation", {})
                    if vn_ssh:
                        en_ssh = en.setdefault("ssh", {})
                        # Profile wins for user, prompt, port, login_via_pam
                        for k in ("user", "port", "prompt_pattern", "login_via_pam"):
                            if vn_ssh.get(k):
                                en_ssh[k] = vn_ssh[k]
                    if vn_esc and vn_esc.get("method", "none") != "none":
                        en["escalation"] = vn_esc
                    break

    cat1["nodes"] = existing_nodes

    # -- Gateway config --
    gw = vendor.get("access_gateway", {})
    if gw.get("enabled"):
        cat1["access_gateway"] = gw
        cat1.setdefault("niam_failover", True)

    # -- SSH defaults --
    ssh_defaults = vendor.get("ssh_defaults", {})
    if ssh_defaults:
        cat3_ssh = cat3.setdefault("ssh", {})
        for key in ("user", "port", "kex_algorithms", "host_key_algorithms", "connect_timeout_sec"):
            if ssh_defaults.get(key) and not cat3_ssh.get(key):
                cat3_ssh[key] = ssh_defaults[key]
        if ssh_defaults.get("user"):
            default_user = ssh_defaults["user"]
            default_port = ssh_defaults.get("port", 22)
            for node in existing_nodes:
                if isinstance(node, dict):
                    node_ssh = node.setdefault("ssh", {})
                    # Override LLM hallucinations (e.g. "niamuser") with profile user
                    current_user = node_ssh.get("user", "")
                    if not current_user or current_user.lower() in ("niamuser", "niam_user", ""):
                        node_ssh["user"] = default_user
                    if not node_ssh.get("port"):
                        node_ssh["port"] = default_port

    # -- Phases from vendor profile --
    vp_phases = vp_cat3.get("phases", [])
    if vp_phases and not cat3.get("phases"):
        cat3["phases"] = vp_phases

    # -- Grouping from vendor profile --
    vp_grouping = vp_cat3.get("grouping", {})
    if vp_grouping and not cat3.get("grouping"):
        cat3["grouping"] = vp_grouping

    # -- CIQ field mappings --
    if vendor_profile.get("ciq_field_mappings"):
        cat1.setdefault("ciq_field_mappings", vendor_profile["ciq_field_mappings"])

    # -- Node ID config --
    if vendor_profile.get("node_id_config"):
        cat1.setdefault("node_id_config", vendor_profile["node_id_config"])

    # -- Data sheet --
    if vp_cat1.get("data_sheet") and not cat1.get("data_sheet"):
        cat1["data_sheet"] = vp_cat1["data_sheet"]

    requirements["category_1_node_activity"] = cat1
    requirements["category_3_orchestration"] = cat3

    logger.info("Merged vendor profile: %d nodes, gateway=%s",
                len(existing_nodes), bool(gw.get("enabled")))

    return requirements


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

    # -- Map prompt/escalation answers to node config --
    node_list = nodes.get("nodes", [])
    for node in node_list:
        if not isinstance(node, dict):
            continue
        node_name = node.get("name", "").lower()
        node_ssh = node.setdefault("ssh", {})
        node_esc = node.setdefault("escalation", {})

        for k, v in answers.items():
            v_str = str(v).strip()
            if not v_str:
                continue
            # Match answers containing node name + prompt/escalation context
            k_lower = k.lower()
            if node_name and node_name in v_str.lower():
                # "rdsggn01:~ #" contains "rds" → this is the RDS prompt
                if any(pw in k_lower for pw in ("prompt", "root_prompt", "escalat")):
                    if not node_esc.get("prompt_pattern"):
                        node_esc["prompt_pattern"] = v_str
                        logger.info("Enforced escalation prompt for %s: %s", node_name, v_str[:40])

        # Also check SSH log prompts
        if ssh_prompts:
            for prompt_str in ssh_prompts:
                if node_name in prompt_str.lower():
                    if not node_ssh.get("prompt_pattern"):
                        node_ssh["prompt_pattern"] = prompt_str
                        logger.info("Set prompt from SSH logs for %s: %s", node_name, prompt_str[:40])

    # Infer activity_type from MOP if missing
    if not nodes.get("activity_type"):
        mop_text = raw_artifacts.get("mop_text", "").lower()
        if any(kw in mop_text for kw in ("imsi", "subscriber", "per subscriber", "ldapsearch")):
            nodes["activity_type"] = "per_subscriber"
        elif any(kw in mop_text for kw in ("per node", "node-level", "software")):
            nodes["activity_type"] = "per_node"

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
