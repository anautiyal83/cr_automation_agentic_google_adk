"""Step 1 keeps every answer with its question: saved together, and a new question never takes the
id of another saved question (the LLM numbers its questions itself)."""
from __future__ import annotations

from skills.requirement_gathering import skill as skill_mod
from skills.requirement_gathering.questionnaire import fresh_ids


def test_answers_are_saved_with_their_questions(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_mod, "_ANSWERS_DIR", tmp_path)
    texts = {"cmd_q1": "Which file is uploaded?", "cmd_q2": "Asked, not answered yet"}
    skill_mod._save_answers("UC_X", {"cmd_q1": "bulk.json"}, texts)
    doc = skill_mod._load_uc_doc("UC_X")
    assert doc["developer_answers"] == {"cmd_q1": "bulk.json"}
    assert doc["question_texts"] == {"cmd_q1": "Which file is uploaded?"}

    skill_mod._save_answers("UC_X", {"cmd_q1": "bulk.json", "cmd_q2": "yes"}, {"cmd_q2": "Asked, not answered yet"})
    assert skill_mod._load_uc_doc("UC_X")["question_texts"] == texts      # earlier texts kept


def test_a_new_question_does_not_take_an_answered_questions_id():
    saved = {"cmd_q1": "Which file is uploaded to the node?", "cmd_q2": "Where is the backup kept?"}
    out = fresh_ids([
        {"id": "cmd_q1", "question": "Which file is uploaded to the node?"},       # same question: kept
        {"id": "cmd_q2", "question": "How many pods must show the new TAC count?"},  # another one
        {"id": "orch_q1", "question": "Retry how often?"},
        {"id": "orch_q1", "question": "Stop on the first failure?"},                # twice in the round
    ], saved)
    assert [q["id"] for q in out] == ["cmd_q1", "cmd_q3", "orch_q1", "orch_q2"]
