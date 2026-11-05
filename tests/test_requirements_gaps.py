"""Step 1: answered questions are not raised again as gaps (OB-21)."""
from __future__ import annotations

from skills.requirement_gathering.skill import RequirementGatheringSkill


def _skill(answers: dict, questions: dict, analysis: dict) -> RequirementGatheringSkill:
    s = RequirementGatheringSkill.__new__(RequirementGatheringSkill)
    s._all_answers, s._question_texts, s._analysis = answers, questions, analysis
    return s


def test_answered_gaps_dropped_and_readiness_complete():
    s = _skill(
        {"orch_q2": "ls -ltrh /var/opt/swms/clips", "cmd_q1": "sftp plugin in the framework"},
        {"orch_q2": "What is the method or command to generate a report showing the status of the "
                    "announcement file transfer?",
         "cmd_q1": "How should files be transferred to the /tmp directory on the MRF node?"},
        {"overall_readiness": "PARTIAL",
         "category_2_commands": {"gaps": ["Command to generate report showing status of announcement file transfer",
                                          "Exact SFTP command syntax for transferring files to /tmp"]}})
    s._drop_answered_gaps()
    assert s._analysis["category_2_commands"]["gaps"] == []
    assert s._analysis["overall_readiness"] == "COMPLETE"


def test_unasked_gap_kept_and_answers_sent_with_questions():
    s = _skill({"cmd_q1": "sftp plugin in the framework"},
               {"cmd_q1": "How should files be transferred to the /tmp directory on the MRF node?"},
               {"overall_readiness": "PARTIAL", "category_3_orchestration": {
                   "gaps": ["Source of announcement files (Repository server access details)"]}})
    s._drop_answered_gaps()
    assert len(s._analysis["category_3_orchestration"]["gaps"]) == 1
    assert s._analysis["overall_readiness"] == "PARTIAL"
    assert s._answers_text() == ("Q: How should files be transferred to the /tmp directory on the MRF node?\n"
                                 "A: sftp plugin in the framework")


def test_mop_condition_without_other_branch_becomes_a_question():
    # G-16: "If MRF_VERSION == 13.X.X.X" + copy/untar lines, no else — asked in step 1, not guessed in step 6
    from skills.requirement_gathering.questionnaire import mop_branch_questions
    mop = [{"phase": "Activity", "command": c} for c in (
        "For each file:", "If MRF_VERSION == 13.X.X.X", "copy each file to the directory", "Untar the tar file")]
    mop += [{"phase": "Rollback", "command": c} for c in ("Check backup", "IF YES: restore it", "IF NO: remove files")]
    sheets = {"Node_Details": {"columns": ["NODE_NAME", "VER"]}}
    rows = {"Node_Details": [{"NODE_NAME": "MRF1", "VER": "13.0.3.7"}, {"NODE_NAME": "MRF3", "VER": "17.0.3.7"}]}
    qs = mop_branch_questions(mop, sheets, rows)
    assert len(qs) == 1                                  # the rollback IF YES has its IF NO
    assert "MRF3 = 17.0.3.7" in qs[0]["question"] and "'Untar the tar file'" in qs[0]["question"]
    assert mop_branch_questions([{"phase": "A", "command": "change permission to 777 if required"}], sheets, rows) == []


def test_questions_ask_for_text_not_patterns_and_never_repeat():
    """Spec 011 / PGW Q-1..Q-4: the user cannot write patterns; no secrets, no placeholder syntax, no re-asking."""
    from skills.requirement_gathering.prompts import QUESTIONS_PROMPT, SYSTEM_PROMPT
    from skills.requirement_gathering.questionnaire import clean_questions, looks_like_regex
    assert "node_row" not in SYSTEM_PROMPT and "configData" not in SYSTEM_PROMPT
    assert "NEVER ask for a regex" in QUESTIONS_PROMPT
    asked = {"orch_q75": "What regex captures the idle CPU percentage in the sar output?"}
    answers = {"orch_q75": r"Average:\s+all\s+(?P<idle_cpu>\d+\.\d+)"}
    questions = [
        # re-asked under a new id (PGW asked it 7 times)
        {"id": "orch_q78", "question": "What is the regex to capture idle CPU percentage from sar output?"},
        # a new pattern question → asks for text, the regex default is dropped
        {"id": "orch_q80", "question": "What regex pattern confirms Updates Rejected is 0 in ndsLoadStats?",
         "default_value": r"Updates Rejected:\s*0\s*Queries Rejected:\s*0", "input_type": "text"},
        # the same thing again in this round
        {"id": "orch_q81", "question": "Which regex pattern confirms Updates Rejected is 0 in ndsLoadStats output?"},
        # a password → the secret's name
        {"id": "na_q13", "question": "What is the NIAM server password?", "input_type": "password",
         "default_value": "password123"},
        # placeholder syntax in a suggested answer → plain words
        {"id": "cmd_q19", "question": "Which script arguments build the request?",
         "default_value": "python create_xml.py --mandatory_cap '${{CIQ.Profile Modification.Mandatory Cap}}'"},
        # a prompt question may mention a pattern (prompts are matched by the framework) — kept as is
        {"id": "na_q4", "question": "Which prompt pattern does the provgw user show?"},
    ]
    out = {q["id"]: q for q in clean_questions(questions, asked, answers)}
    assert "orch_q78" not in out and "orch_q81" not in out
    q80 = out["orch_q80"]
    assert "regex" not in q80["question"].lower() and "text" in q80["question"] and "every line" in q80["question"]
    assert q80["default_value"] == ""
    assert out["na_q13"]["input_type"] == "text" and out["na_q13"]["default_value"] == ""
    assert "secret" in out["na_q13"]["question"].lower()
    assert out["cmd_q19"]["default_value"] == "python create_xml.py --mandatory_cap 'Mandatory Cap'"
    assert out["na_q4"]["question"] == "Which prompt pattern does the provgw user show?"
    assert looks_like_regex(r"(?P<x>\d+)") and not looks_like_regex("Link UP for every remote node")
