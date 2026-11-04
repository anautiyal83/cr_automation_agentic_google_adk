"""Open questions of a later step, answered in step 1 (shared.clarifications)."""
from __future__ import annotations

from shared import clarifications


def test_save_load_clear_and_stable_ids(tmp_path):
    qs = clarifications.from_errors(6, ["CIQ column 'X' is never used", "CIQ column 'X' is never used", "other"])
    assert len(qs) == 2 and all(q["id"].startswith("clarify_s6_") for q in qs)
    assert clarifications.from_errors(6, ["other"])[0]["id"] == qs[1]["id"]      # same problem → same id
    clarifications.save(str(tmp_path), 6, qs)
    assert clarifications.load(str(tmp_path))["step"] == 6
    clarifications.clear(str(tmp_path))
    assert clarifications.load(str(tmp_path)) == {} and not (tmp_path / clarifications.FILE).exists()
