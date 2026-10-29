"""Question generation and filtering for developer questionnaire."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Awaitable, Callable

from .prompts import QUESTIONS_PROMPT, SYSTEM_PROMPT

logger = logging.getLogger(__name__)

LlmFn = Callable[[str, str], Awaitable[str]]


async def generate_questions(
    llm_fn: LlmFn,
    analysis: dict,
    developer_notes: str,
    existing_answers: dict,
    available_data: str,
) -> list[dict[str, Any]]:
    """Generate targeted questions based on gaps in analysis.

    Returns a list of question dicts, or empty list if no gaps.
    """
    all_gaps = []
    for cat_key in ("category_1_node_activity", "category_2_commands", "category_3_orchestration"):
        cat = analysis.get(cat_key, {})
        all_gaps.extend(cat.get("gaps", []))

    if not all_gaps:
        return []

    combined_notes = developer_notes or ""
    if existing_answers:
        combined_notes += "\n\nPrevious answers (DO NOT re-ask these):\n" + json.dumps(existing_answers, indent=2)

    prompt = QUESTIONS_PROMPT.format(
        analysis_json=json.dumps(analysis, indent=2, default=str),
        developer_notes=combined_notes or "None",
        available_data=available_data,
    )

    try:
        response = await llm_fn(prompt, SYSTEM_PROMPT)
        response = response.strip()
        if response.startswith("```"):
            m = re.search(r'```(?:json)?\s*\n(.*?)```', response, re.DOTALL)
            response = m.group(1) if m else response
        questions = json.loads(response)
        return questions if isinstance(questions, list) else []
    except Exception as e:
        logger.error("Question generation failed: %s", e)
        return []


def build_available_data_summary(
    ciq_sheets: dict[str, dict],
    raw_artifacts: dict,
    ssh_parsed: dict,
    vendor_profile: dict | None = None,
) -> str:
    """Build a text summary of already-known data so LLM doesn't re-ask.

    Summarizes CIQ columns, SSH prompts/credentials, XML samples, MOP,
    and vendor profile (SSH/PAM/escalation details).
    """
    parts: list[str] = []

    # --- Vendor profile (highest priority — pre-configured by user) ---
    if vendor_profile:
        parts.append("VENDOR PROFILE (pre-configured — DO NOT ask about these):")
        vendor_info = vendor_profile.get("vendor", {})
        if vendor_info.get("name"):
            parts.append(f"  Vendor: {vendor_info['name']}")

        gw = vendor_info.get("access_gateway", {})
        if gw.get("enabled"):
            parts.append(f"  Access Gateway: {gw.get('name', 'PAM')} — ENABLED")
            parts.append(f"    Host: {gw.get('host', '?')}:{gw.get('port', 22)}")
            parts.append(f"    Username: {gw.get('username', '?')}")
            parts.append(f"    Auth: {gw.get('auth_method', 'key')}")
            if gw.get("prompt_pattern"):
                parts.append(f"    Prompt: {gw['prompt_pattern']}")
            parts.append("    ** All nodes connect via this gateway — DO NOT ask for direct SSH host/port/password **")

        ssh_defaults = vendor_info.get("ssh_defaults", {})
        if ssh_defaults:
            parts.append(f"  SSH Defaults: user={ssh_defaults.get('user', '?')}, port={ssh_defaults.get('port', 22)}")
            if ssh_defaults.get("kex_algorithms"):
                parts.append(f"    KEX: {ssh_defaults['kex_algorithms']}")
            if ssh_defaults.get("host_key_algorithms"):
                parts.append(f"    Host Key: {ssh_defaults['host_key_algorithms']}")

        nodes = vendor_profile.get("category_1_node_activity", {}).get("nodes", [])
        if nodes:
            parts.append("  Node types (pre-configured — DO NOT ask about SSH, prompts, or escalation for these):")
            for n in nodes:
                node_ssh = n.get("ssh", {})
                node_esc = n.get("escalation", {})
                via_pam = node_ssh.get("login_via_pam", False)
                access = "via PAM gateway" if via_pam else "direct SSH"
                parts.append(f"    {n.get('name', '?')}: access={access}")
                if node_ssh.get("prompt_pattern"):
                    parts.append(f"      Prompt: {node_ssh['prompt_pattern']}")
                if node_esc.get("method") and node_esc["method"] != "none":
                    parts.append(f"      Escalation: {node_esc['method']} → {node_esc.get('target_user', '?')}")
                    if node_esc.get("prompt_pattern"):
                        parts.append(f"      Escalated prompt: {node_esc['prompt_pattern']}")
                if via_pam:
                    parts.append(f"      ** Connects via gateway — DO NOT ask SSH host/port/user/password for {n.get('name', '?')} **")

        if vendor_profile.get("node_id_config"):
            nc = vendor_profile["node_id_config"]
            parts.append(f"  Node ID column: {nc.get('node_column', '?')}")
            if nc.get("gateway_id_column"):
                parts.append(f"  Gateway ID column: {nc['gateway_id_column']}")

        if vendor_profile.get("ciq_field_mappings"):
            parts.append(f"  CIQ field mappings: {len(vendor_profile['ciq_field_mappings'])} fields pre-mapped")

        parts.append("")

    if ciq_sheets:
        parts.append("CIQ SHEETS AND COLUMNS (already known — do not ask):")
        for sname, sdata in ciq_sheets.items():
            columns = sdata.get("columns", [])
            parts.append(f'  Sheet "{sname}": {columns}')
            niam_cols = [h for h in columns if "niam" in str(h).lower()]
            if niam_cols:
                parts.append(f'    -> NIAM columns found: {niam_cols} (count: {len(niam_cols)})')

    if raw_artifacts.get("ssh_logs"):
        logs = raw_artifacts["ssh_logs"]
        parts.append(f"\nSSH SESSION LOGS available ({len(logs)} chars) — contains:")
        parts.append("  - Real shell prompts (extract from logs, don't ask user)")
        parts.append("  - Real command outputs with actual data patterns")
        creds = ssh_parsed.get("credentials", [])
        if creds:
            parts.append(f"  - LDAP credentials visible: user={creds[0].get('bind_dn', '?')}, password=***")
        prompts = ssh_parsed.get("prompts", [])
        if prompts:
            parts.append(f"  - Shell prompts found: {prompts[:5]}")

    if raw_artifacts.get("xml_samples"):
        parts.append(f"\nSAMPLE XML FILES provided ({len(raw_artifacts['xml_samples'])} chars)")
        parts.append("  - XML structure/template is available — do not ask about it")

    if raw_artifacts.get("mop_text"):
        parts.append(f"\nFULL MOP DOCUMENT available ({len(raw_artifacts['mop_text'])} chars)")
        parts.append("  - Command syntax and procedures are in MOP — do not ask about them")

    return "\n".join(parts) if parts else "No additional data available"


def filter_answered_questions(questions: list[dict], all_answers: dict) -> list[dict]:
    """Filter out questions already answered via ID or fuzzy topic match.

    Returns only unanswered questions. Modifies ``default_value`` in-place
    if a fuzzy match is found.
    """
    unanswered = []
    for q in questions:
        if not isinstance(q, dict):
            continue
        q_id = q.get("id", "")
        q_text = q.get("question", "").lower().strip()

        # Direct ID match
        if q_id in all_answers:
            continue

        # Fuzzy: check if any previous answer covers this question's topic
        already_covered = False
        for ak, av in all_answers.items():
            if q_text and any(
                kw in q_text
                for kw in ak.lower().replace("_", " ").split()
                if len(kw) > 3
            ):
                already_covered = True
                q["default_value"] = av
                break

        if not already_covered:
            unanswered.append(q)

    return unanswered


# Words/prefixes that indicate connection/access topics
# Uses startswith matching so "escalation" matches "escalat"
_CONNECTION_PREFIXES = (
    "ssh", "port", "username", "password", "hostname",
    "credential", "login", "logging", "connect",
    "authenticat", "keypair", "key-based",
    "kex", "host_key", "hostkey",
    "timeout",
    "prompt", "shell", "escalat", "privileg", "sudo",
    "niam", "gateway", "pam", "jumpbox", "bastion",
)


def filter_vendor_profile_questions(
    questions: list[dict],
    vendor_profile: dict | None,
) -> list[dict]:
    """Deterministically remove questions about SSH/PAM/connection/escalation
    when a vendor profile already provides them.

    Uses aggressive word-level matching — if ANY connection-related word appears
    in a question AND a vendor profile covers that topic, the question is dropped.
    """
    if not vendor_profile:
        return questions

    vendor = vendor_profile.get("vendor", {})
    gw = vendor.get("access_gateway", {})
    ssh = vendor.get("ssh_defaults", {})
    nodes = vendor_profile.get("category_1_node_activity", {}).get("nodes", [])

    has_gateway = bool(gw.get("enabled"))
    has_ssh_defaults = bool(ssh.get("user"))
    has_any_node_config = bool(nodes)

    if not (has_gateway or has_ssh_defaults or has_any_node_config):
        return questions

    # Build set of known names to detect in questions
    known_names = set()
    if gw.get("name"):
        known_names.add(gw["name"].lower())
    for n in nodes:
        if n.get("name"):
            known_names.add(n["name"].lower())

    filtered = []
    for q in questions:
        if not isinstance(q, dict):
            filtered.append(q)
            continue

        text = q.get("question", "").lower()
        words = text.replace("?", " ").replace(",", " ").replace(".", " ").split()

        # Check if question contains any connection-related word (prefix match)
        has_connection_word = any(
            w.startswith(pfx) for w in words for pfx in _CONNECTION_PREFIXES
        )

        # Also check for known names (NIAM, PGW, RDS etc.) mentioned with
        # connection context words like "for the", "to the", "of the"
        mentions_known_entity = any(name in text for name in known_names)

        should_drop = False

        if has_connection_word:
            # If gateway is configured, drop ANY question about gateway/access/login
            if has_gateway and (
                mentions_known_entity
                or any(w.startswith(p) for w in words for p in ("niam", "gateway", "pam", "jumpbox", "bastion"))
            ):
                should_drop = True

            # If SSH defaults configured, drop ANY ssh/port/user/pass question
            if has_ssh_defaults and any(
                w.startswith(p) for w in words for p in ("ssh", "port", "username", "password", "kex", "hostkey", "timeout")
            ):
                should_drop = True

            # If nodes configured, drop connection questions mentioning those nodes
            if has_any_node_config and mentions_known_entity:
                should_drop = True

            # Drop any question about prompt/escalation when nodes have those configured
            if any(w.startswith(p) for w in words for p in ("prompt", "escalat", "privileg", "sudo")):
                for n in nodes:
                    n_ssh = n.get("ssh", {})
                    n_esc = n.get("escalation", {})
                    name = n.get("name", "").lower()
                    if name and name in text:
                        if n_ssh.get("prompt_pattern") or (
                            n_esc.get("method") and n_esc["method"] != "none"
                        ):
                            should_drop = True
                            break
                # If gateway covers all access, also drop generic prompt/escalation questions
                if has_gateway and not should_drop:
                    should_drop = True

        if should_drop:
            logger.debug("Filtered vendor-profile question: %s", q.get("question", "")[:80])
        else:
            filtered.append(q)

    if len(filtered) < len(questions):
        logger.info(
            "Vendor profile filter: dropped %d/%d connection questions",
            len(questions) - len(filtered), len(questions),
        )

    return filtered
