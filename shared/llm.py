"""Local LLM wrapper — calls LiteLlm/OpenRouter without cr_bridge dependency.

All skills use ``llm_fn(system_prompt, user_prompt) -> str``.
This module provides that function using the same LiteLlm backend
that the pipeline already depends on.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Track LLM usage for the usage stats page. Kept in data/llm_usage.jsonl as well: the in-memory
# list alone was empty after every server restart (LLM Usage page / dashboard showed nothing)
_USAGE_FILE = Path(__file__).resolve().parent.parent / "data" / "llm_usage.jsonl"


def _load_usage() -> list[dict]:
    try:
        lines = _USAGE_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


_usage_log: list[dict] = _load_usage()


def _record_usage(entry: dict) -> None:
    _usage_log.append(entry)
    try:
        _USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_USAGE_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError as e:
        logger.debug("LLM usage not persisted: %s", e)

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
    import asyncio
    import litellm

    config = config or {}

    # Always read live config to pick up settings changes during pipeline run
    try:
        from core.config import ConfigManager
        cm = ConfigManager._live_instance
        if cm and cm._config:
            live = cm._config.model_dump() if hasattr(cm._config, "model_dump") else {}
            for k in ("default_model", "openrouter_api_key", "step_models"):
                if k in live and live[k]:
                    config[k] = live[k]
    except Exception:
        pass

    # Fallback: read API key and model from DB if not in config or env
    if not config.get("openrouter_api_key") and not os.environ.get("OPENROUTER_API_KEY"):
        try:
            import sqlite3
            from pathlib import Path
            db_path = Path(__file__).resolve().parent.parent / "data" / "pipeline.db"
            if db_path.exists():
                conn = sqlite3.connect(str(db_path))
                for key, cfg_key in [("api_key", "openrouter_api_key"), ("default_model", "default_model")]:
                    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
                    if row and row[0] and not config.get(cfg_key):
                        config[cfg_key] = row[0]
                conn.close()
        except Exception:
            pass

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

    max_retries = 3
    retry_delays = [5, 15, 30]  # seconds

    t0 = time.monotonic()
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            response = await litellm.acompletion(
                model=model,
                messages=messages,
                temperature=0.2,
                max_tokens=16000,
            )
            break  # success
        except (litellm.ServiceUnavailableError, litellm.RateLimitError,
                litellm.Timeout, litellm.APIConnectionError) as e:
            last_error = e
            if attempt < max_retries:
                delay = retry_delays[attempt]
                logger.warning("LLM call failed (attempt %d/%d): %s — retrying in %ds",
                               attempt + 1, max_retries + 1, type(e).__name__, delay)
                await asyncio.sleep(delay)
            else:
                logger.error("LLM call failed after %d attempts: %s", max_retries + 1, e)
                raise
        except Exception:
            raise  # Non-retryable errors propagate immediately

    elapsed = time.monotonic() - t0
    text = response.choices[0].message.content or ""

    # Track usage with context
    usage = response.usage
    prompt_tok = getattr(usage, "prompt_tokens", 0)
    comp_tok = getattr(usage, "completion_tokens", 0)
    total_tok = getattr(usage, "total_tokens", 0)
    cost = _estimate_cost(model, prompt_tok, comp_tok)

    entry = {
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
    }
    _record_usage(entry)

    if attempt > 0:
        logger.info("LLM call succeeded after %d retries (%.1fs total)", attempt, elapsed)
    logger.debug("LLM call: model=%s, %d prompt chars, %d response chars, %.1fs, $%.4f",
                  model, len(user_prompt), len(text), elapsed, cost)
    return text


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
    """Clear the usage log (memory and data/llm_usage.jsonl)."""
    _usage_log.clear()
    try:
        _USAGE_FILE.unlink(missing_ok=True)
    except OSError as e:
        logger.debug("LLM usage file not removed: %s", e)
