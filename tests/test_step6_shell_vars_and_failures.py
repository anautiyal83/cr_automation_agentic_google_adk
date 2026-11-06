"""EIR step 6 (2026-11-06): shell variables written ${NAME}, JavaScript in criteria.expr, and the
review showing why a step failed."""
from __future__ import annotations

import json

from skills.network_config_gen.autofix import expr_without_parentheses, parse_yaml, shell_variables_unbraced
from skills.network_config_gen.validator import check_expr_vars


def _wf(send: str, expr: str = "") -> dict:
    step = {"node": "EIR", "send": send}
    if expr:
        step["validation"] = {"success": {"criteria": {"expr": expr}}}
    return parse_yaml(json.dumps({"nodes": {}, "phases": {"backup": {"steps": [step]}}}))


def _send(wf: dict) -> str:
    return wf["phases"]["backup"]["steps"][0]["send"]


def test_shell_variable_set_by_the_command_is_written_unbraced():
    """The JAR fills ${DATE} with '' before the node sees it (probed): `$DATE` reaches the shell."""
    wf = _wf("DATE=$(date +%Y%m%d); org -c eirWhiteRng >/opt/sdf/processing/predump_white_${DATE}.txt")
    assert shell_variables_unbraced(wf) == 1
    assert _send(wf) == "DATE=$(date +%Y%m%d); org -c eirWhiteRng >/opt/sdf/processing/predump_white_$DATE.txt"


def test_unbraced_keeps_the_name_apart_from_a_following_word():
    wf = _wf('for i in a b; do echo ${i}_x "${i}y" ${ORDERNO}; done')
    shell_variables_unbraced(wf)
    assert _send(wf) == "for i in a b; do echo $i''_x \"$i\"\"y\" ${ORDERNO}; done"   # ORDERNO: not a shell var


def test_javascript_in_expr_is_reported_with_what_to_write_and_not_mangled():
    js = "counts.every(val => val === counts[0]) && counts.length > 0"
    wf = _wf("echo 1", js)
    assert expr_without_parentheses(wf) == 0                                    # brackets left alone
    problems = check_expr_vars(wf, runtime_params=[])
    assert len(problems) == 1 and "sort -u | wc -l" in problems[0] and "distinctCount == 1" in problems[0]


def test_review_lists_why_a_step_failed(tmp_path):
    from ui.pages.progress_section import _failure_reasons
    (tmp_path / "step_6_error.json").write_text(json.dumps({"error": "Step 'a' uses x.; For CIQ rows like NG1, "
                                                                     "step 'DATE=$(date); b' uses ${DATE}."}))
    assert _failure_reasons(str(tmp_path), 6) == ["Step 'a' uses x.", "For CIQ rows like NG1, step 'DATE=$(date); b' "
                                                                      "uses ${DATE}."]
    (tmp_path / "step_9_error.json").write_text(json.dumps({"error": "1/2 tests failed", "failed_test_cases": [
        {"id": "TC-P01", "scenario": "Sunny day", "errors": ["step x failed", "run FAIL"]}]}))
    assert _failure_reasons(str(tmp_path), 9) == ["1/2 tests failed", "TC-P01 Sunny day: step x failed; run FAIL"]
    assert _failure_reasons(str(tmp_path), 5) == []
