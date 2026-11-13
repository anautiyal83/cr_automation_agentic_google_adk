"""The confirmed CIQ cell rules (shared/ciq_cells): one table for step 6's scripts and step 8's simulator."""
from shared import ciq_cells

ANSWER = ("If 'UNCHANGED' or blank, the corresponding XML attribute for that field should be omitted for 'UNCHANGED' "
          "indicating no change, and create empty tag in case of blank to delete or remove the value,  Otherwise, use "
          "the provided CIQ value.")


def test_a_sentence_about_one_kind_of_cell_decides_it():
    """Run 6570694d's answer names both kinds in one sentence and blank alone in the next: blank deletes."""
    assert ciq_cells.derive({"developer_answers": {"q": ANSWER}}) == {"unchanged": "keep", "blank": "delete"}


def test_a_later_note_overrides_and_defaults_keep():
    req = {"developer_answers": {"q": ANSWER}, "developer_notes": "Blank cells mean no change."}
    assert ciq_cells.derive(req)["blank"] == "keep"
    assert ciq_cells.derive({}) == {"unchanged": "keep", "blank": "keep"}


def test_stored_rules_win_and_kinds():
    assert ciq_cells.rules({"ciq_cell_rules": {"blank": "delete"}, "developer_notes": "blank means no change"}) == \
        {"unchanged": "keep", "blank": "delete"}
    assert [ciq_cells.kind(v) for v in ("", None, "None", " UNCHANGED ", "10")] == \
        ["blank", "blank", "blank", "unchanged", "value"]


def test_the_requirements_document_shows_the_rules():
    from skills.requirement_gathering.html_report import summary_html_from_requirements
    html = summary_html_from_requirements("UC", {"developer_answers": {"q": ANSWER}})
    assert "CIQ Cell Rules" in html and "delete the attribute (absent after the activity)" in html
