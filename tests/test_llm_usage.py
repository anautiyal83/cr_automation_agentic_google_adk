"""LLM usage is stored with the run in the pipeline DB (table llm_usage) and survives restarts."""
from __future__ import annotations

import asyncio

import shared.llm as llm
from storage.db import Database


def test_usage_stored_per_run_and_cleared(tmp_path, monkeypatch):
    async def scenario():
        db = Database(str(tmp_path / "pipeline.db"))
        await db.initialize()
        monkeypatch.setattr(llm, "_usage_log", [])
        monkeypatch.setattr(llm, "_usage_db", None)
        llm.set_usage_db(db)
        await llm._record_usage({"run_id": "r1", "uc_name": "UC", "step": "network_config_gen", "model": "m",
                                 "prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10,
                                 "cost_usd": 0.001, "elapsed_sec": 2.0, "timestamp": 1700000000.0})
        await llm._record_usage({"run_id": "r2", "step": "helper_script_gen", "model": "m", "total_tokens": 5})
        # A restarted server: empty memory, same DB
        monkeypatch.setattr(llm, "_usage_log", [])
        every = await llm.load_usage_stats()
        one = await llm.load_usage_stats("r1")
        await llm.clear_usage_stats()
        return every, one, await llm.load_usage_stats()

    every, one, after_clear = asyncio.run(scenario())
    assert [e["total_tokens"] for e in every] == [10, 5]
    assert len(one) == 1 and one[0]["step"] == "network_config_gen" and one[0]["timestamp"] == 1700000000.0
    assert after_clear == []


def test_a_hanging_llm_call_times_out_and_is_retried(monkeypatch):
    """E030 run 2: a step 6 call hung 43 minutes until OpenRouter reset the connection."""
    import asyncio
    import types

    import litellm

    from shared import llm
    calls = {"n": 0}
    real_sleep = asyncio.sleep

    async def acompletion(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            await real_sleep(30)                         # hangs
        msg = types.SimpleNamespace(content="ok")
        usage = types.SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)], usage=usage)

    async def no_wait(seconds, *a, **kw):
        await real_sleep(0 if seconds >= 5 else seconds)  # skip the retry back-off, keep the hang
    monkeypatch.setattr(litellm, "acompletion", acompletion)
    monkeypatch.setattr(asyncio, "sleep", no_wait)
    text = asyncio.run(llm.call_llm("s", "u", model="openrouter/x",
                                    config={"llm_timeout_sec": 0.2, "openrouter_api_key": "k"}))
    assert text == "ok" and calls["n"] == 2


def test_a_refused_model_stops_step_1_instead_of_asking_questions(monkeypatch):
    """2026-11-17: a retired model (404) was logged, the analysis came back empty and step 1 asked
    10 questions about it — the run must stop with OpenRouter's reason."""
    import httpx
    import litellm
    import pytest

    from shared import llm
    from shared.state import llm_setup_errors
    from skills.requirement_gathering import analyzer
    calls = {"n": 0}

    async def acompletion(**kw):
        calls["n"] += 1
        raise litellm.NotFoundError(
            message='OpenrouterException - {"error":{"message":"Qwen3 235B A22B was deprecated on Oct 9, 2026.",'
                    '"code":404}}', model=kw["model"], llm_provider="openrouter",
            response=httpx.Response(404, request=httpx.Request("POST", "https://openrouter.ai")))

    monkeypatch.setattr(litellm, "acompletion", acompletion)
    llm.set_llm_context(run_id="r404", step="requirements_gatherer")
    try:
        with pytest.raises(llm.LlmSetupError) as err:
            asyncio.run(analyzer.analyze_uc(llm.get_llm_fn({"openrouter_api_key": "k"}), "UC", "", "", "", ""))
    finally:
        llm.set_llm_context()
    assert calls["n"] == 1                                   # no retries
    assert "deprecated on Oct 9, 2026" in str(err.value) and "Settings" in str(err.value)
    assert "deprecated on Oct 9, 2026" in llm_setup_errors.pop("r404")
