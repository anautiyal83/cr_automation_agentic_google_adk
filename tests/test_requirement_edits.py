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

        def apply_edit(self, answers, note, by, at):
            calls.append(("edit", dict(answers), note))
            return {k: {"from": "", "to": v} for k, v in answers.items()}

        async def incorporate_answers(self, answers):
            calls.append("reanalyze")
            return out()

    monkeypatch.setattr(ra, "RequirementGatheringSkill", FakeSkill)
    run_id = str(uuid.uuid4())
    ctx = SimpleNamespace(session=SimpleNamespace(state={
        "run_id": run_id, "run_dir": str(tmp_path), "uc_name": "UC_X", "document_set": {},
        "review_milestones": True}))

    async def wait_for_review():
        while not (milestone_waiting.get(2) or {}).get("waiting"):
            await asyncio.sleep(0.01)

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
