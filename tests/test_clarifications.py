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


# EIR, 2026-11-11: the same question came back with a different count after every answer
c = clarifications

EIR_49 = "The requirements list 49 commands on node EIR but no workflow step runs on EIR — include its steps."
EIR_63 = "The requirements list 63 commands on node EIR but no workflow step runs on EIR — include its steps."
DELTA = ("CIQ column 'DELTA_TAC_FILE' (INPUT_FILE) is filled for every CIQ row but the workflow never uses it — use "
         "${configSequences.0.records.0.data.DELTA_TAC_FILE} where the MOP needs that value. CIQ values: SD_TAC.xlsx")


def test_one_problem_one_question_whatever_the_numbers():
    assert c.key(EIR_49) == c.key(EIR_63)
    assert c.key(DELTA) == c.key(DELTA.replace("SD_TAC.xlsx", "SD_TAC_Addition_150926.xlsx"))
    assert [q["id"] for q in c.from_errors(6, [EIR_49])] == [q["id"] for q in c.from_errors(6, [EIR_63])]


def test_answered_questions_are_recognised_and_not_asked_again():
    asked = c.from_errors(6, [EIR_49, DELTA])
    reqs = {"developer_answers": {asked[0]["id"]: "commands run on the EIR pods through the bastion server",
                                  asked[1]["id"]: ""},                     # left empty: still open
            "developer_questions": {q["id"]: q["question"] for q in asked}}
    answered = c.answered_keys(reqs)
    assert c.key(EIR_63) in answered and c.key(DELTA) not in answered
    assert [q["question"] for q in c.from_errors(6, [EIR_63, DELTA], answered=answered)] == [asked[1]["question"]]


def test_reviewer_reads_the_problem_without_technical_hints():
    assert c.plain(DELTA) == ("CIQ column 'DELTA_TAC_FILE' (INPUT_FILE) is filled for every CIQ row but the workflow "
                              "never uses it.")
    assert c.plain(c.from_errors(6, [EIR_49])[0]["question"]) == ("The requirements list 49 commands on node EIR but "
                                                                  "no workflow step runs on EIR.")
