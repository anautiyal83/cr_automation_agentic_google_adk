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

# Current context — set by the pipeline before each agent runs
_current_context: dict[str, str] = {"run_id": "", "uc_name": "", "step": ""}

# Approximate cost per 1M tokens (input/output) for common models
_MODEL_COSTS: dict[str, tuple[float, float]] = {
    "gemini-2.5-flash": (0.15, 0.60),
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-1.5-pro": (1.25, 5.00),
    "gemini-2.5-pro": (1.25, 10.00),
    "claude-sonnet": (3.00, 15.00),
    "claude-haiku": (0.25, 1.25),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
}


def set_llm_context(run_id: str = "", uc_name: str = "", step: str = ""):
    """Set the current context for LLM usage tracking."""
    _current_context["run_id"] = run_id
    _current_context["uc_name"] = uc_name
    _current_context["step"] = step


def _estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Estimate cost in USD based on model and token counts."""
    model_lower = model.lower()
    for key, (input_cost, output_cost) in _MODEL_COSTS.items():
        if key in model_lower:
            return (prompt_tokens * input_cost + completion_tokens * output_cost) / 1_000_000
    # Default: assume cheap model pricing
    return (prompt_tokens * 0.15 + completion_tokens * 0.60) / 1_000_000


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

        # Track usage with context
        usage = response.usage
        prompt_tok = getattr(usage, "prompt_tokens", 0)
        comp_tok = getattr(usage, "completion_tokens", 0)
        total_tok = getattr(usage, "total_tokens", 0)
        cost = _estimate_cost(model, prompt_tok, comp_tok)

        _usage_log.append({
            "model": model,
            "prompt_tokens": prompt_tok,
            "completion_tokens": comp_tok,
            "total_tokens": total_tok,
            "cost_usd": round(cost, 6),
            "elapsed_sec": round(elapsed, 1),
            "timestamp": time.time(),
            "run_id": _current_context.get("run_id", ""),
            "uc_name": _current_context.get("uc_name", ""),
            "step": _current_context.get("step", ""),
        })

        logger.debug("LLM call: model=%s, %d prompt chars, %d response chars, %.1fs, $%.4f",
                      model, len(user_prompt), len(text), elapsed, cost)
        return text

    except Exception as e:
        elapsed = time.monotonic() - t0
        logger.error("LLM call failed after %.1fs: %s", elapsed, e)
        raise


def get_llm_fn(config: dict | None = None):
    """Return an llm_fn compatible with skill constructors.

    Usage::
        from shared.llm import get_llm_fn
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
