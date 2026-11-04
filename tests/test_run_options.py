"""Run phase options (spec 010): the nine phases, their selection in step 6, the cache key."""
from __future__ import annotations

import pytest

from shared import run_options
from shared.run_options import canonical_phase, normalize, selected, summary


def test_options_normalize_and_summary():
    assert selected(None) == run_options.KEYS and run_options.is_default({})
    opts = normalize({"phases": {"backup": False, "rollbackPrecheck": False, "nonsense": True},
                      "backup_via_repo": True})
    assert opts["backup_via_repo"] is False                                   # via repo only with Backup
    assert "backup" not in selected(opts) and "rollbackPrecheck" not in selected(opts)
    assert summary(opts) == "Without Backup, RollbackPrecheck"
    assert summary({"backup_via_repo": True}) == "All phases · backup via repo server"
    assert summary({}) == "All phases · backup kept on the node"


@pytest.mark.parametrize("key,name,canon", [
    ("preNodeHealthCheck", "Pre-activity health checks", "preNodeHealthCheck"),
    ("backup", "Backup existing files", "backup"),
    ("activity_configuration", "Announcement file loading", "activityConfiguration"),
    ("activity", "", "activityConfiguration"),
    ("activityPrecheck", "", "activityPrecheck"),
    ("activity_postcheck", "Verify loaded files", "activityPostCheck"),
    ("postNodeHealthCheck", "", "postNodeHealthCheck"),
    ("rollback", "Rollback announcement files", "rollbackConfiguration"),
    ("rollbackPostCheck", "", "rollbackPostCheck"),
    ("rollback_precheck", "Check the backup exists", "rollbackPrecheck"),
])
def test_generated_phases_map_to_the_model(key, name, canon):
    assert canonical_phase(key, {"name": name}) == canon


def test_step6_keeps_only_the_selected_phases_in_model_order():
    from skills.network_config_gen.structure import select_phases
    wf = {"phases": {
        "postNodeHealthCheck": {"steps": [{"node": "A", "send": "post"}]},
        "rollback": {"name": "Rollback", "steps": [{"node": "A", "send": "rb"}]},
        "activity_configuration": {"steps": [{"node": "A", "send": "change"}]},
        "activity": {"steps": [{"node": "A", "send": "more"}]},          # merged into the same phase
        "backup": {"steps": [{"node": "A", "send": "bk"}]},
        "preNodeHealthCheck": {"steps": [{"node": "A", "send": "pre"}]},
    }}
    select_phases(wf, {"run_options": {"phases": {"backup": False, "postNodeHealthCheck": False}}})
    assert list(wf["phases"]) == ["preNodeHealthCheck", "activityConfiguration", "rollbackConfiguration"]
    assert [s["send"] for s in wf["phases"]["activityConfiguration"]["steps"]] == ["change", "more"]
    full = {"phases": {"rollback": {"steps": []}, "preNodeHealthCheck": {"steps": []}}}
    select_phases(full, {})                                                    # no options: all kept
    assert list(full["phases"]) == ["preNodeHealthCheck", "rollbackConfiguration"]


def test_options_change_only_the_step6_cache():
    from storage.artifact_cache import inputs_fingerprint, options_fingerprint
    base = {"uc_name": "UC", "developer_notes": "n"}
    changed = {**base, "run_options": {"phases": {"backup": False}}}
    assert inputs_fingerprint(base) == inputs_fingerprint(changed)             # steps 1-4 keep their cache
    assert options_fingerprint(base) == "" == options_fingerprint({**base, "run_options": run_options.default()})
    assert options_fingerprint(changed) != ""


def test_cache_entry_of_step6_needs_the_same_options(tmp_path):
    from storage.artifact_cache import ArtifactCache
    cache = ArtifactCache(str(tmp_path))
    cache.save_cache("UC", "network_config_gen", {"workflow_yaml": "phases: {}"}, inputs="i", options="")
    assert cache.has_cache("UC", "network_config_gen", inputs="i", options="")
    assert not cache.has_cache("UC", "network_config_gen", inputs="i", options="abc")
