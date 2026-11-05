"""Local LLM wrapper — calls LiteLlm/OpenRouter without cr_bridge dependency.

All skills use ``llm_fn(system_prompt, user_prompt) -> str``.
This module provides that function using the same LiteLlm backend
that the pipeline already depends on.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# LLM usage: one row per call in the pipeline DB (table llm_usage, keyed by run_id with the run's
# data) once the runner registers it; the in-memory list alone was lost on every server restart.
# Without a DB (CLI step tests) usage stays in memory only.
_usage_log: list[dict] = []
_usage_db = None  # storage.db.Database


def set_usage_db(db) -> None:
    """Persist LLM usage in this database from now on (PipelineRunner registers its DB)."""
    global _usage_db
    _usage_db = db


_USAGE_COLUMNS = ("run_id", "uc_name", "step", "model", "prompt_tokens", "completion_tokens",
                  "total_tokens", "cost_usd", "elapsed_sec")


async def _record_usage(entry: dict) -> None:
    _usage_log.append(entry)
    if _usage_db is None:
        return
    try:
        from datetime import datetime, timezone
        created = datetime.fromtimestamp(entry.get("timestamp") or time.time(), tz=timezone.utc).isoformat()
        await _usage_db.execute_insert(
            f"INSERT INTO llm_usage ({', '.join(_USAGE_COLUMNS)}, created_at) "
            f"VALUES ({', '.join('?' * (len(_USAGE_COLUMNS) + 1))})",
            tuple(entry.get(c) or (0 if c not in ("run_id", "uc_name", "step", "model") else "")
                  for c in _USAGE_COLUMNS) + (created,))
    except Exception as e:  # usage tracking must never fail an LLM call
        logger.warning("LLM usage not stored in the database: %s", e)

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


LLM_TIMEOUT_SEC = 300  # one LLM call; config llm_timeout_sec / env LLM_TIMEOUT_SEC override
_TRANSIENT = ("payload is not completed", "connection", "timed out", "timeout", "reset", "temporarily", "502",
              "503", "504")


def _transient(error: Exception) -> bool:
    text = str(error).lower()
    return any(t in text for t in _TRANSIENT)


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
    # A call that never returns stalled a step 6 for 43 minutes (E030: OpenRouter reset the
    # connection only then) — every call has a hard limit and a timeout is retried
    timeout = float(config.get("llm_timeout_sec") or os.environ.get("LLM_TIMEOUT_SEC") or LLM_TIMEOUT_SEC)

    t0 = time.monotonic()
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            response = await asyncio.wait_for(litellm.acompletion(
                model=model,
                messages=messages,
                temperature=0.2,
                max_tokens=16000,
                timeout=timeout,
            ), timeout=timeout * 1.05 + 1)
            break  # success
        except (litellm.ServiceUnavailableError, litellm.RateLimitError, litellm.Timeout,
                litellm.APIConnectionError, litellm.InternalServerError, asyncio.TimeoutError) as e:
            last_error = e
            if attempt < max_retries:
                delay = retry_delays[attempt]
                logger.warning("LLM call failed (attempt %d/%d): %s — retrying in %ds",
                               attempt + 1, max_retries + 1, type(e).__name__, delay)
                await asyncio.sleep(delay)
            else:
                logger.error("LLM call failed after %d attempts: %s", max_retries + 1, e)
                raise
        except litellm.APIError as e:
            # A broken / cut-off response ("payload is not completed") is transient too
            if attempt < max_retries and _transient(e):
                last_error = e
                logger.warning("LLM call failed (attempt %d/%d): %s — retrying in %ds",
                               attempt + 1, max_retries + 1, str(e)[:120], retry_delays[attempt])
                await asyncio.sleep(retry_delays[attempt])
                continue
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
    await _record_usage(entry)

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
    """LLM calls of this process (in memory). The UI reads ``load_usage_stats`` (all runs, DB)."""
    return list(_usage_log)


async def load_usage_stats(run_id: str = "", owner: str | None = None) -> list[dict]:
    """Every recorded LLM call from the DB (all runs, or one run), oldest first; the in-memory
    log when no DB is registered. Same keys as the in-memory entries (``timestamp`` = epoch).
    ``owner``: only the runs that user started (spec 006; None = all runs)."""
    if _usage_db is None:
        return [e for e in _usage_log if (not run_id or e.get("run_id") == run_id) and owner is None]
    from datetime import datetime
    where, params = [], []
    if run_id:
        where.append("run_id = ?")
        params.append(run_id)
    if owner is not None:
        where.append("run_id IN (SELECT id FROM pipeline_runs WHERE started_by = ?)")
        params.append(owner)
    rows = await _usage_db.execute(
        f"SELECT {', '.join(_USAGE_COLUMNS)}, created_at FROM llm_usage "
        + (f"WHERE {' AND '.join(where)} " if where else "") + "ORDER BY id", tuple(params))
    for r in rows:
        try:
            r["timestamp"] = datetime.fromisoformat(str(r.pop("created_at"))).timestamp()
        except (TypeError, ValueError):
            r["timestamp"] = 0.0
    return rows


async def clear_usage_stats(owner: str | None = None) -> None:
    """Delete recorded LLM usage (memory and DB): all of it, or only that of the runs ``owner``
    started (spec 006: a user clears only their own)."""
    if owner is not None:
        if _usage_db is not None:
            await _usage_db.execute_insert(
                "DELETE FROM llm_usage WHERE run_id IN (SELECT id FROM pipeline_runs WHERE started_by = ?)", (owner,))
        return
    _usage_log.clear()
    if _usage_db is not None:
        await _usage_db.execute_insert("DELETE FROM llm_usage")
