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
