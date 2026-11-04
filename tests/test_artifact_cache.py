"""ArtifactCache (spec 002 T012: C-1, C-3, C-5, OB-7).

    python -m pytest tests/test_artifact_cache.py -v
"""
from __future__ import annotations

from storage.artifact_cache import ArtifactCache, _ROOT


def test_round_trip_and_scripts_by_content(tmp_path):
    cache = ArtifactCache(str(tmp_path / "cache"))
    cache.save_cache("UC", "ciq_validation_json_gen",
                     {"validation_rules_yaml": "rules: []", "json_template_yaml": "sheets: {}"})
    assert cache.has_cache("UC", "ciq_validation_json_gen")
    assert cache.load_cache("UC", "ciq_validation_json_gen") == \
        {"validation_rules_yaml": "rules: []", "json_template_yaml": "sheets: {}"}
    script = tmp_path / "run" / "scripts" / "create_xml.py"  # helper_scripts are run-dir paths (OB-7)
    script.parent.mkdir(parents=True)
    script.write_text("print('xml')", encoding="utf-8")
    cache.save_cache("UC", "helper_script_gen", {"helper_scripts": {"create_xml.py": str(script)}})
    assert cache.load_cache("UC", "helper_script_gen") == {"helper_scripts": {"create_xml.py": "print('xml')"}}


def test_incomplete_save_is_never_a_hit(tmp_path):
    """C-3: an entry missing or empty marks the agent incomplete, not passed."""
    cache = ArtifactCache(str(tmp_path / "cache"))
    cache.save_cache("UC", "ciq_validation_json_gen", {"validation_rules_yaml": "rules: []", "json_template_yaml": " "})
    assert not cache.has_cache("UC", "ciq_validation_json_gen")
    assert cache._read_meta("UC")["agent_status"]["ciq_validation_json_gen"] == "incomplete"
    cache.save_cache("UC", "helper_script_gen", {"helper_scripts": {}})
    assert not cache.has_cache("UC", "helper_script_gen")


def test_clear_from_step_for_every_agent(tmp_path):
    """C-1: clears the agent and the cached ones after it; agents without a cache clear nothing."""
    cache = ArtifactCache(str(tmp_path / "cache"))
    full = {"requirements_gatherer": {"requirements_json": "{}"},
            "ciq_validation_json_gen": {"validation_rules_yaml": "r", "json_template_yaml": "j"},
            "network_config_gen": {"workflow_yaml": "w"},
            "helper_script_gen": {"helper_scripts": {"a.py": "x"}}}
    order = list(full)

    def fill():
        for agent, artifacts in full.items():
            cache.save_cache("UC", agent, artifacts)
        return [a for a in order if cache.has_cache("UC", a)]

    assert fill() == order
    for i, agent in enumerate(order):
        cache.clear_from_step("UC", agent)
        assert [a for a in order if cache.has_cache("UC", a)] == order[:i]
        assert fill() == order
    for agent in ("ciq_validation_json_qa", "network_simulator_setup", "network_config_qa", "report_generator"):
        cache.clear_from_step("UC", agent)
        assert [a for a in order if cache.has_cache("UC", a)] == order


def test_cache_dir_is_relative_to_the_project(tmp_path, monkeypatch):
    """C-5: a CLI started in another directory uses the same cache."""
    monkeypatch.chdir(tmp_path)
    assert ArtifactCache().uc_cache_dir("UC") == _ROOT / "data" / "cache" / "UC"
    assert ArtifactCache(str(tmp_path / "c")).uc_cache_dir("UC") == tmp_path / "c" / "UC"
