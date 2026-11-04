"""Step 3: a cross-sheet rule from the requirements reaches the CIQ check (DEP-60).

    python -m pytest tests/test_conditional_pattern.py -v
"""
from __future__ import annotations

import yaml

from skills.ciq_validation_json_gen.rules_baseline import conditional_pattern

CP = {"startColumn": "GROUP",
      "lookupChain": [{"sheet": "INDEX", "joinOn": "GROUP", "extract": "NODE"},
                      {"sheet": "Node_Details", "joinOn": "NODE_NAME", "extract": "VER"}],
      "rules": [{"when": r"13\..*", "pattern": r"(?i)(?!.*\.(tar|tgz|tar\.gz)$).+", "message": "plain: {value}"},
                {"when": ".*", "pattern": r"(?i).+\.(tar|tgz|tar\.gz)", "message": "tar: {value}"}]}


def _rows(files: dict[str, str]) -> dict:
    return {"INDEX": [{"NODE": "MRF1", "GROUP": "G1"}, {"NODE": "MRF3", "GROUP": "G2"}],
            "Node_Details": [{"NODE_NAME": "MRF1", "VER": "17.0.3.7"}, {"NODE_NAME": "MRF3", "VER": "13.0.3.7"}],
            "ANNOUNCEMENT_FILES": [{"GROUP": g, "INPUT_FILE": f} for g, f in files.items()]}


def test_kept_even_when_the_ciq_breaks_it(caplog):
    good = conditional_pattern(CP, "ANNOUNCEMENT_FILES", "INPUT_FILE", _rows({"G1": "a.tar", "G2": "b.3gp"}))
    assert good and good["lookupChain"] == CP["lookupChain"]
    bad = conditional_pattern(CP, "ANNOUNCEMENT_FILES", "INPUT_FILE", _rows({"G1": "a.3gp", "G2": "b.tar"}))
    assert bad == good and "2 CIQ row(s) break it" in caplog.text   # a business rule: step 5 reports them


def test_dropped_when_malformed_or_about_nothing():
    rows = _rows({"G1": "a.tar", "G2": "b.3gp"})
    wrong_col = {**CP, "lookupChain": [{"sheet": "INDEX", "joinOn": "GRP", "extract": "NODE"}]}
    bad_regex = {**CP, "rules": [{"when": "(", "pattern": ".*"}]}
    no_match = {**CP, "rules": [{"when": r"99\..*", "pattern": ".*"}]}
    for cp in (wrong_col, bad_regex, no_match, {"startColumn": "GROUP"}, "x"):
        assert conditional_pattern(cp, "ANNOUNCEMENT_FILES", "INPUT_FILE", rows) is None


def test_sanitizer_passes_it_to_the_jar(tmp_path):
    from skills.network_config_test.jar_executor import _sanitize_rules_for_jar
    noisy = {**CP, "comment": "x", "lookupChain": [{**CP["lookupChain"][0], "note": 1}, CP["lookupChain"][1]]}
    rules = {"sheets": {"ANNOUNCEMENT_FILES": {"columns": {"INPUT_FILE": {"type": "string", "conditionalPattern": noisy},
                                                         "GROUP": {"type": "string", "conditionalPattern": {"x": 1}}}}}}
    p = tmp_path / "rules.yaml"
    p.write_text(yaml.safe_dump(rules), encoding="utf-8")
    cols = yaml.safe_load(open(_sanitize_rules_for_jar(str(p)), encoding="utf-8"))["sheets"]["ANNOUNCEMENT_FILES"]["columns"]
    assert cols["INPUT_FILE"]["conditionalPattern"] == CP and "conditionalPattern" not in cols["GROUP"]


def test_rules_prompt_formats():
    """The example holds braces and backslashes: .format() must not break on them."""
    import warnings
    from skills.ciq_validation_json_gen import prompts
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        text = prompts.VALIDATION_RULES_USER.format(uc_name="U", ciq_summary="C", requirements_summary="R",
                                                    schema_summary="S")
    assert "{lookupValue}" in text and r"13\..*" in text
