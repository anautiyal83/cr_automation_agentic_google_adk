"""Spec 012 H1–H4, H8: the workflow as rows — add by phase + step number, edit, delete; the
platform's own steps rebuilt from the rows."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from shared import workflow_table as wt

MRF = Path("data/runs/46e5fee1-b0ab-4e9c-af36-01b1798d93a0")


def _mrf():
    """The MRF workflow rebuilt with the requirements given here (the run folder's copy was built with
    the run's own options, e.g. backup via the repo server)."""
    if not (MRF / "MRF_ANNOUNCEMENT_LOADING.yaml").exists():
        pytest.skip("MRF run folder not present")
    reqs = json.loads((MRF / "requirements.json").read_text(encoding="utf-8"))
    return wt.rebuild((MRF / "MRF_ANNOUNCEMENT_LOADING.yaml").read_text(encoding="utf-8"), reqs), reqs


def test_rows_numbered_per_phase_and_automatic_steps_counted_not_listed():
    text, _ = _mrf()
    phases = wt.table(text)
    backup = next(p for p in phases if p.key == "backup")
    assert [r.number for r in backup.rows] == list(range(1, len(backup.rows) + 1))
    assert backup.automatic > 0                                   # backups by convention, checksums
    assert not any(r.send.startswith(("mkdir -p /tmp/${ORDERNO}", "sha256sum")) for r in backup.rows)


def test_add_at_a_step_number_then_delete_gives_the_same_file():
    text, reqs = _mrf()
    assert wt.rebuild(text, reqs) == text                         # nothing changed: nothing moves
    phase = wt.table(text)[0]
    new = wt.change(text, "add", phase.key, 2, wt.Row(phase=phase.key, number=2, node=phase.rows[0].node,
                                                     send="echo hello", success="hello"), reqs)
    rows = wt.table(new)[0].rows
    assert rows[1].send == "echo hello" and rows[1].success == "hello" and len(rows) == len(phase.rows) + 1
    assert rows[2].send == phase.rows[1].send                     # the old step 2 is now step 3
    assert wt.change(new, "delete", phase.key, 2, None, reqs) == text


def test_edit_keeps_what_the_form_does_not_show():
    text, reqs = _mrf()
    phase = wt.table(text)[0]
    row = phase.rows[0]
    row.success = "active"
    new = wt.change(text, "edit", phase.key, 1, row, reqs)
    edited = wt.table(new)[0].rows[0]
    assert edited.success == "active" and edited.send == phase.rows[0].send and edited.node == phase.rows[0].node


def test_wrong_positions_are_refused():
    text, reqs = _mrf()
    phase = wt.table(text)[0]
    with pytest.raises(ValueError):
        wt.change(text, "add", phase.key, len(phase.rows) + 2, wt.Row(phase=phase.key, number=0), reqs)
    with pytest.raises(ValueError):
        wt.change(text, "delete", "nope", 1, None, reqs)


def test_ciq_values_shown_by_column_name():
    assert wt.show("cp /tmp/${configSequences.0.records.0.data.INPUT_FILE} x") == "cp /tmp/<INPUT_FILE> x"
