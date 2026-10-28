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
) -> str:
    """Build a text summary of already-known data so LLM doesn't re-ask.

    Summarizes CIQ columns, SSH prompts/credentials, XML samples, and MOP.
    """
    parts: list[str] = []

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
