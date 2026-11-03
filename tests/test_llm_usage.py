"""LLM usage survives a server restart (data/llm_usage.jsonl)."""
from __future__ import annotations

import shared.llm as llm


def test_usage_persists_and_clears(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_USAGE_FILE", tmp_path / "llm_usage.jsonl")
    monkeypatch.setattr(llm, "_usage_log", [])
    llm._record_usage({"model": "m", "total_tokens": 10, "step": "network_config_gen"})
    llm._record_usage({"model": "m", "total_tokens": 5, "step": "helper_script_gen"})
    assert [e["total_tokens"] for e in llm._load_usage()] == [10, 5]   # what a restarted server loads
    llm.clear_usage_stats()
    assert llm.get_usage_stats() == [] and llm._load_usage() == []
