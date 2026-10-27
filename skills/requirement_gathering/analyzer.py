"""LLM-based UC analysis and requirements compilation."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Awaitable, Callable

from .prompts import ANALYZE_PROMPT, COMPILE_PROMPT, SYSTEM_PROMPT

logger = logging.getLogger(__name__)

LlmFn = Callable[[str, str], Awaitable[str]]


def _extract_json(response: str) -> str:
    """Strip markdown fences from an LLM response to get raw JSON."""
    response = response.strip()
    if response.startswith("```"):
        m = re.search(r'```(?:json)?\s*\n(.*?)```', response, re.DOTALL)
        if m:
            return m.group(1)
    return response


async def analyze_uc(
    llm_fn: LlmFn,
    uc_name: str,
    ciq_summary: str,
    mop_summary: str,
    cmd_summary: str,
    developer_notes: str,
) -> dict[str, Any]:
    """Analyze UC type and assess readiness across 3 categories.

    Returns a dict with ``overall_readiness`` and per-category gap lists.
    """
    prompt = ANALYZE_PROMPT.format(
        uc_name=uc_name,
        ciq_summary=ciq_summary,
        mop_summary=mop_summary,
        cmd_summary=cmd_summary,
        developer_notes=developer_notes or "None provided",
    )

    try:
        response = await llm_fn(prompt, SYSTEM_PROMPT)
        return json.loads(_extract_json(response))
    except Exception as e:
        logger.error("UC analysis failed: %s", e)
        return {"overall_readiness": "INSUFFICIENT", "error": str(e)}


async def compile_requirements(
    llm_fn: LlmFn,
    uc_name: str,
    analysis: dict,
    developer_notes: str,
    answers: dict,
    raw_artifacts: dict | None = None,
) -> dict[str, Any]:
    """Compile final requirements package via LLM.

    Merges analysis, developer notes, answers, and raw document context.
    """
    extra_context = ""
    raw_artifacts = raw_artifacts or {}
    if raw_artifacts.get("xml_samples"):
        extra_context += (
            "\n\n=== SAMPLE XML FILES (use these as templates — map CIQ columns to XML tags) ===\n"
            + raw_artifacts["xml_samples"][:6000]
        )
    if raw_artifacts.get("mop_text"):
        extra_context += (
            "\n\n=== RAW MOP DOCUMENT (extract exact commands and conditional logic) ===\n"
            + raw_artifacts["mop_text"][:6000]
        )

    prompt = COMPILE_PROMPT.format(
        uc_name=uc_name,
        analysis_json=json.dumps(analysis, indent=2, default=str),
        developer_notes=developer_notes or "None",
        answers_json=(
            json.dumps(answers, indent=2, default=str)
            if answers
            else "No answers needed — all requirements auto-detected"
        ),
    ) + extra_context

    try:
        response = await llm_fn(prompt, SYSTEM_PROMPT)
        return json.loads(_extract_json(response))
    except Exception as e:
        logger.error("Requirements compilation failed: %s", e)
        return {
            "analysis": analysis,
            "developer_notes": developer_notes,
            "developer_answers": answers,
            "generation_hints": {},
        }
