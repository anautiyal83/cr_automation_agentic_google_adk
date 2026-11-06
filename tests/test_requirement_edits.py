"""Edit requirements at the step 2 review: the hand-off file, the skill applying an edit, and the
document's Requirement Updates section."""
from __future__ import annotations

from shared import requirement_edits
from skills.requirement_gathering import skill as skill_mod
from skills.requirement_gathering.html_report import generate_summary_html
from skills.requirement_gathering.skill import RequirementGatheringSkill


def test_edit_is_handed_over_once(tmp_path):
    edit = requirement_edits.save(str(tmp_path), {"q1": "tar"}, "  VER 13 loads plain files  ", "dev@x.io")
    assert edit["note"] == "VER 13 loads plain files" and edit["by"] == "dev@x.io" and edit["at"]
    assert requirement_edits.take(str(tmp_path)) == edit
    assert requirement_edits.take(str(tmp_path)) == {}          # applied once


def test_apply_edit_replaces_saved_answers_and_adds_an_authoritative_note(monkeypatch):
    saved = {}
    monkeypatch.setattr(skill_mod, "_save_answers", lambda uc, answers: saved.update({uc: dict(answers)}))
    sk = RequirementGatheringSkill(llm_fn=None)
    sk._uc_name = "UC_X"
    sk._all_answers = {"q1": "plain files for every version", "q2": "Yes"}
    sk._developer_notes = "Existing note"
    sk._ciq_only_round = True

    changed = sk.apply_edit({"q1": "13.0.3.7 plain files, every other version tar", "q2": "Yes"},
                            "File type depends on VER", "dev@x.io", "2026-11-06 10:00 UTC")

    assert changed == {"q1": {"from": "plain files for every version",
                              "to": "13.0.3.7 plain files, every other version tar"}}   # q2 unchanged
    assert saved["UC_X"]["q1"].startswith("13.0.3.7")                                    # next run gets it
    assert sk._developer_notes.startswith("Existing note\n\n=== Requirement update (2026-11-06 10:00 UTC, dev@x.io)")
    assert sk._developer_notes.endswith("File type depends on VER")
    assert sk._ciq_only_round is False                                                   # analysed in full
    assert sk._edits == [{"at": "2026-11-06 10:00 UTC", "by": "dev@x.io", "note": "File type depends on VER",
                          "answers": changed}]


def test_document_lists_the_updates():
    reqs = {"requirement_updates": [{"at": "2026-11-06 10:00 UTC", "by": "dev@x.io", "note": "use <tar>",
                                     "answers": {"q1": {"from": "plain", "to": "tar"}}}]}
    html = generate_summary_html("UC_X", {}, {}, reqs, "", {"q1": "tar"}, {},
                                 question_texts={"q1": "Which file type?"})
    assert "Requirement Updates (1)" in html
    assert "use &lt;tar&gt;" in html                                                    # escaped
    assert "<td>Which file type?</td><td class='meta'>plain</td><td>tar</td>" in html


def test_step2_edit_compiles_again_and_pauses_for_review_again(tmp_path, monkeypatch):
    """The agent's review loop: edit → apply + re-analyse + compile again → review again → continue."""
    import asyncio
    import uuid
    from types import SimpleNamespace

    import agents.requirements_agent as ra
    from shared.state import milestone_decisions, milestone_events, milestone_waiting

    calls = []

    def out(**kw):
        base = dict(readiness="COMPLETE", questions=[], all_answers={"q1": "x"}, requirements={"n": len(calls)},
                    requirements_json="{}", requirements_html="<html>doc</html>")
        return SimpleNamespace(**{**base, **kw})

    class FakeSkill:
        _raw_artifacts = {}

        def __init__(self, llm_fn=None):
            pass

        async def parse_and_analyze(self, inp):
            calls.append("analyze")
            return out()

        async def compile_final(self):
            calls.append("compile")
            return out()

        def apply_edit(self, answers, note, by, at, ciq_file_path="", excluded_sheets=None):
            calls.append(("edit", dict(answers), note))
            return {k: {"from": "", "to": v} for k, v in answers.items()}

        async def incorporate_answers(self, answers):
            calls.append("reanalyze")
            return out(questions=[{"id": "q9", "question": "?"}])   # open points: no questionnaire

    monkeypatch.setattr(ra, "RequirementGatheringSkill", FakeSkill)
    run_id = str(uuid.uuid4())
    ctx = SimpleNamespace(session=SimpleNamespace(state={
        "run_id": run_id, "run_dir": str(tmp_path), "uc_name": "UC_X", "document_set": {},
        "review_milestones": True}))

    async def wait_for_review():
        async def poll():
            while not (milestone_waiting.get(2) or {}).get("waiting"):
                await asyncio.sleep(0.01)
        await asyncio.wait_for(poll(), 5)

    async def scenario():
        task = asyncio.create_task(_drain(ra.RequirementsGathererNode()._run_async_impl(ctx)))
        await wait_for_review()
        requirement_edits.save(str(tmp_path), {"q1": "tar"}, "VER decides", "dev@x.io")
        milestone_decisions[2] = "edit"
        milestone_events[2].set()
        await asyncio.sleep(0.05)
        await wait_for_review()                       # paused for review again
        milestone_decisions[2] = "continue"
        milestone_events[2].set()
        await asyncio.wait_for(task, 5)

    async def _drain(gen):
        async for _ in gen:
            pass

    try:
        asyncio.run(scenario())
    finally:
        milestone_waiting.pop(2, None)
        milestone_events.pop(2, None)
    assert calls == ["analyze", "compile", ("edit", {"q1": "tar"}, "VER decides"), "reanalyze", "compile"]
    assert not (tmp_path / requirement_edits.FILE).exists()                    # taken once
    assert (tmp_path / "UC_X_requirements-summary.html").read_text(encoding="utf-8") == "<html>doc</html>"


def _workbook(path, sheets, hidden=()):
    import openpyxl
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
        if name in hidden:
            ws.sheet_state = "hidden"
    wb.save(path)
    return str(path)


def test_hidden_ciq_sheets_are_not_read(tmp_path):
    """EIR: CRFRoutePriorityList was hidden in the workbook, yet listed as a CIQ sheet of the activity."""
    from skills.requirement_gathering.parsers import parse_ciq
    path = _workbook(tmp_path / "ciq.xlsx", {"INDEX": [["ZONE", "CRGROUP"], ["EAST", "CR1"]],
                                              "CRFRoutePriorityList": [["A"], ["1"]]},
                     hidden=("CRFRoutePriorityList",))
    assert list(parse_ciq(ciq_file_path=path)) == ["INDEX"]


def test_edit_replaces_the_ciq_and_leaves_out_sheets_also_for_later_runs(tmp_path, monkeypatch):
    docs: dict = {}
    monkeypatch.setattr(skill_mod, "_load_uc_doc", lambda uc: dict(docs.get(uc, {})))
    monkeypatch.setattr(skill_mod, "_save_uc_doc", lambda uc, doc: docs.update({uc: dict(doc)}))
    monkeypatch.setattr(skill_mod, "_save_answers", lambda uc, answers: None)
    new = _workbook(tmp_path / "fixed.xlsx", {"INDEX": [["ZONE"], ["EAST"]], "INPUT_FILE": [["FILE"], ["a.zip"]],
                                               "Extra": [["X"], ["1"]]})
    sk = RequirementGatheringSkill(llm_fn=None)
    sk._uc_name, sk._doc_extracted = "UC_X", {"ciq_sheets": {"INDEX": {}}}

    sk.apply_edit({}, "", "dev@x.io", "now", ciq_file_path=new, excluded_sheets=["Extra"])

    assert list(sk._ciq_sheets) == ["INDEX", "INPUT_FILE"] and sk._doc_extracted["ciq_sheets"] is sk._ciq_sheets
    assert docs["UC_X"]["excluded_ciq_sheets"] == ["Extra"]                         # next runs leave it out
    assert sk._edits[-1]["ciq_file"] == "fixed.xlsx" and sk._edits[-1]["excluded_sheets"] == ["Extra"]
    assert sk._ciq_replaced and sk._ciq_file_path == new

    sk.apply_edit({}, "", "dev@x.io", "later", excluded_sheets=[])                   # ticked again
    assert list(sk._ciq_sheets) == ["INDEX", "INPUT_FILE", "Extra"] and docs["UC_X"]["excluded_ciq_sheets"] == []


def test_later_steps_read_the_corrected_ciq():
    from agents.requirements_agent import ciq_file_path, requirements_store
    state = {"run_id": "run-c", "ciq_file_path": "old.xlsx"}
    assert ciq_file_path(state) == "old.xlsx"
    requirements_store["run-c"] = {"ciq_file_path": "fixed.xlsx"}
    try:
        assert ciq_file_path(state) == "fixed.xlsx"
    finally:
        requirements_store.pop("run-c", None)


def test_vendor_profile_merges_into_a_node_the_llm_left_without_ssh():
    """EIR steps 1-2 crashed: the LLM compiled `"ssh": null` for a node; setdefault returned None."""
    from skills.requirement_gathering.compiler import merge_vendor_profile
    reqs = {"category_1_node_activity": {"nodes": [{"name": "EIR", "ssh": None, "escalation": None}]},
            "category_3_orchestration": None}
    profile = {"vendor": {"ssh_defaults": {"user": "oamsys", "port": 22}},
               "category_1_node_activity": {"nodes": [{"name": "EIR", "ssh": {"user": "oamsys", "file_transfer": "scp"}}]}}
    out = merge_vendor_profile(reqs, profile)
    node = out["category_1_node_activity"]["nodes"][0]
    assert node["ssh"]["user"] == "oamsys" and node["ssh"]["file_transfer"] == "scp"
    assert out["category_3_orchestration"]["ssh"]["user"] == "oamsys"
