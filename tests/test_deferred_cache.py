"""OB-48: step 6's artifacts wait for step 9 (DEP-55) — a failure the simulator caused keeps them, and a pass after a
server restart (nothing deferred in memory) still caches them from the run folder."""
from __future__ import annotations

import json

import pytest

from orchestrator import pipeline_agent
from orchestrator.pipeline_agent import _settle_deferred_cache


@pytest.fixture(autouse=True)
def _deferred_step6(monkeypatch):
    """The deferral mechanism with step 6 deferred, as it was (unused since 2026-11-12: step 6 caches at review)."""
    monkeypatch.setattr(pipeline_agent, "_CACHE_AFTER_STEP", {"network_config_gen": 9, "helper_script_gen": 9})


class FakeCache:
    def __init__(self):
        self.saved, self.cleared = {}, []

    def save_cache(self, uc, name, data, inputs="", options=""):
        self.saved[name] = data

    def clear_cache(self, uc, name):
        self.cleared.append(name)


def _pending():
    return {"network_config_gen": ({"workflow_yaml": "w"}, "in", "opt"),
            "helper_script_gen": ({"helper_scripts": {}}, "in", "opt")}


def test_simulator_failure_keeps_the_workflow_waiting(tmp_path):
    (tmp_path / "step_9_error.json").write_text(json.dumps({"classification": "simulator", "retry_target": 8}))
    cache, pending = FakeCache(), _pending()
    _settle_deferred_cache(cache, "UC", pending, [9], {"step_9_status": "failed"}, str(tmp_path))
    assert not cache.cleared and set(pending) == {"network_config_gen", "helper_script_gen"}


def test_workflow_failure_saves_nothing_and_clears_nothing(tmp_path):
    """Only the user's Rerun clears a step's cache."""
    (tmp_path / "step_9_error.json").write_text(json.dumps({"classification": "workflow", "retry_target": 6}))
    cache, pending = FakeCache(), _pending()
    _settle_deferred_cache(cache, "UC", pending, [9], {"step_9_status": "failed"}, str(tmp_path))
    assert not cache.cleared and not cache.saved and not pending


def test_rerun_clears_only_the_rerun_steps_cache(monkeypatch):
    from orchestrator import pipeline_agent
    from storage import artifact_cache
    cleared = []
    monkeypatch.setattr(artifact_cache.ArtifactCache, "clear_cache", lambda self, uc, name: cleared.append(name))
    assert pipeline_agent._clear_step_cache("UC", 3) == ["network_config_gen", "helper_script_gen"]
    assert cleared == ["network_config_gen", "helper_script_gen"]
    cleared.clear()
    assert pipeline_agent._clear_step_cache("UC", 1) == ["ciq_validation_json_gen"]  # not step 6 after it
    assert pipeline_agent._clear_step_cache("UC", 6) == []  # step 8 has no cache


def test_pass_after_a_restart_caches_from_the_run(tmp_path):
    cache = FakeCache()
    state = {"step_9_status": "passed", "artifacts": {"workflow_yaml": "w"}, "document_set": {}}
    _settle_deferred_cache(cache, "UC", {}, [9], state, str(tmp_path))
    assert set(cache.saved) == {"network_config_gen", "helper_script_gen"}
