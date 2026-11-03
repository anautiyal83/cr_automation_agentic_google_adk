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
