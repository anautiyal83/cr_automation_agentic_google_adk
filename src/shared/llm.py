"""Local LLM wrapper — calls LiteLlm/OpenRouter without cr_bridge dependency.

All skills use ``llm_fn(system_prompt, user_prompt) -> str``.
This module provides that function using the same LiteLlm backend
that the pipeline already depends on.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

# Track LLM usage for the usage stats page
_usage_log: list[dict] = []


async def call_llm(
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
    config: dict | None = None,
) -> str:
    """Call LLM via LiteLlm/OpenRouter.

    Args:
        system_prompt: System instruction for the LLM.
        user_prompt: User prompt with the actual request.
        model: Model name override. Defaults to config or env.
        config: Pipeline config dict (for model/api_key).

    Returns:
        LLM response text.
    """
    import litellm

    config = config or {}
    model = model or config.get("default_model", os.environ.get(
        "LLM_MODEL", "openrouter/google/gemini-2.5-flash"
    ))

    api_key = config.get("openrouter_api_key") or os.environ.get("OPENROUTER_API_KEY", "")
    if api_key:
        os.environ["OPENROUTER_API_KEY"] = api_key

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})

    t0 = time.monotonic()
    try:
        response = await litellm.acompletion(
            model=model,
            messages=messages,
            temperature=0.2,
            max_tokens=16000,
        )
        elapsed = time.monotonic() - t0

        text = response.choices[0].message.content or ""

        # Track usage
        usage = response.usage
        _usage_log.append({
            "model": model,
            "prompt_tokens": getattr(usage, "prompt_tokens", 0),
            "completion_tokens": getattr(usage, "completion_tokens", 0),
            "total_tokens": getattr(usage, "total_tokens", 0),
            "elapsed_sec": round(elapsed, 1),
            "timestamp": time.time(),
        })

        logger.debug("LLM call: model=%s, %d prompt chars, %d response chars, %.1fs",
                      model, len(user_prompt), len(text), elapsed)
        return text

    except Exception as e:
        elapsed = time.monotonic() - t0
        logger.error("LLM call failed after %.1fs: %s", elapsed, e)
        raise


def get_llm_fn(config: dict | None = None):
    """Return an llm_fn compatible with skill constructors.

    Usage::
        from src.shared.llm import get_llm_fn
        skill = SomeSkill(llm_fn=get_llm_fn(config))
    """
    cfg = config or {}

    async def _llm_fn(system_prompt: str, user_prompt: str) -> str:
        return await call_llm(system_prompt, user_prompt, config=cfg)

    return _llm_fn


def get_usage_stats() -> list[dict]:
    """Return LLM usage log for the stats page."""
    return list(_usage_log)


def clear_usage_stats():
    """Clear the usage log."""
    _usage_log.clear()
