"""Spec 012: generated files edited at a review — versions, restore, re-runs, reading the run folder."""
from __future__ import annotations

import pytest

from shared import run_files


def _run(tmp_path, text="steps: [a]\n"):
    (tmp_path / "UC.yaml").write_text(text, encoding="utf-8")
    return str(tmp_path)


def test_first_save_keeps_the_generated_file_as_v1(tmp_path):
    run_dir = _run(tmp_path)
    assert run_files.versions(run_dir, "UC.yaml") == []
    v = run_files.save(run_dir, "UC.yaml", "steps: [b]\n", "dev@x.io", problems=0, expected="steps: [a]\n")
    assert v == 2
    assert [(m["version"], m["source"], m["by"]) for m in run_files.versions(run_dir, "UC.yaml")] == [
        (1, "generated", ""), (2, "user", "dev@x.io")]
    assert run_files.current(run_dir, "UC.yaml") == "steps: [b]\n"          # the run folder holds the edit
    assert run_files.generated(run_dir, "UC.yaml") == "steps: [a]\n"
    assert run_files.edited_files(run_dir) == ["UC.yaml"]


def test_restore_is_a_new_version(tmp_path):
    run_dir = _run(tmp_path)
    run_files.save(run_dir, "UC.yaml", "steps: [b]\n", "dev@x.io")
    assert run_files.restore(run_dir, "UC.yaml", 1, "dev@x.io") == 3
    assert run_files.current(run_dir, "UC.yaml") == "steps: [a]\n"
    assert run_files.versions(run_dir, "UC.yaml")[-1]["source"] == "restored"


def test_save_refused_when_the_file_changed_since_it_was_opened(tmp_path):
    run_dir = _run(tmp_path)
    (tmp_path / "UC.yaml").write_text("steps: [rerun]\n", encoding="utf-8")       # a re-run wrote meanwhile
    with pytest.raises(run_files.ChangedSinceOpened):
        run_files.save(run_dir, "UC.yaml", "steps: [b]\n", "dev@x.io", expected="steps: [a]\n")
    assert run_files.current(run_dir, "UC.yaml") == "steps: [rerun]\n"


def test_rerun_after_an_edit_is_recorded_and_the_users_version_kept(tmp_path):
    run_dir = _run(tmp_path)
    assert run_files.record_generated(run_dir, "UC.yaml") is None                # never edited: nothing to record
    run_files.save(run_dir, "UC.yaml", "steps: [mine]\n", "dev@x.io")
    (tmp_path / "UC.yaml").write_text("steps: [regenerated]\n", encoding="utf-8")  # the step wrote again
    assert run_files.record_generated(run_dir, "UC.yaml") == 3
    assert run_files.replaced_user_version(run_dir, "UC.yaml") == 2
    assert run_files.read_version(run_dir, "UC.yaml", 2) == "steps: [mine]\n"
    assert run_files.edited_files(run_dir) == []                                  # current = generated again


def test_steps_read_the_run_folder_first(tmp_path):
    _run(tmp_path, "edited: true\n")
    state = {"run_dir": str(tmp_path), "uc_name": "UC", "artifacts": {"workflow_yaml": "stale: true\n"}}
    assert run_files.read(state, "workflow_yaml") == "edited: true\n"
    assert run_files.read({**state, "run_dir": ""}, "workflow_yaml") == "stale: true\n"


def test_editable_files_per_review(tmp_path):
    for name in ("UC_validation-rules.yaml", "UC_json-output.yaml", "UC.yaml"):
        (tmp_path / name).write_text("x: 1\n", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "compare_tac.py").write_text("print(1)\n", encoding="utf-8")
    assert [f.rel for f in run_files.editable_files(str(tmp_path), "UC", 4)] == ["UC_validation-rules.yaml",
                                                                                "UC_json-output.yaml"]
    assert [(f.rel, f.kind) for f in run_files.editable_files(str(tmp_path), "UC", 6)] == [
        ("UC.yaml", "workflow"), ("scripts/compare_tac.py", "script")]
    assert run_files.editable_files(str(tmp_path), "UC", 9) == []


def test_line_diff():
    assert run_files.diff("a\nb\n", "a\nc\n") == [("", "a"), ("-", "b"), ("+", "c")]
