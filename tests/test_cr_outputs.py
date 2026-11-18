"""Every validation rules file ends with the mandatory CR_LIST / CR_COUNT outputs, from the CIQ's
own INDEX sheet and CR group column (user 2026-11-18, production MRF_ANNOUNCEMENT_LOADING rules)."""
from __future__ import annotations

import yaml

from skills.ciq_validation_json_gen.validation_gen import with_cr_outputs
from skills.network_config_test.jar_executor import _sanitize_rules_for_jar

SHEETS = {"Index": {"columns": ["ZONE", "CR Group", "Email"]}, "IP": {"columns": ["Node_Name", "NIAM_NAME1"]}}
ROWS = {"Index": [{"ZONE": "NORTH1", "CR Group": "CR1", "Email": "a@b.io"}]}
RULES = "settings:\n  headerRow: 0\nsheets:\n  Index:\n    columns:\n      ZONE:\n        required: true\n"


def test_outputs_are_added_with_the_ciqs_names_and_the_production_comments():
    text = with_cr_outputs(RULES, SHEETS, ROWS)
    out = yaml.safe_load(text)["outputs"]
    assert out["CR_LIST"] == {"sheet": "Index", "column": "CR Group", "aggregate": "distinct", "separator": ","}
    assert out["CR_COUNT"] == {"sheet": "Index", "column": "CR Group", "aggregate": "distinct_count"}
    assert "# Post-validation outputs (computed only when validation passes)" in text
    assert text.index("sheets:") < text.index("outputs:")
    assert with_cr_outputs(text, SHEETS, ROWS) == text            # once only; an edited file stays


def test_other_outputs_are_kept_and_a_ciq_without_index_gets_none():
    text = with_cr_outputs(RULES + "outputs:\n  NODES:\n    sheet: IP\n    column: Node_Name\n    aggregate: count\n",
                           SHEETS, ROWS)
    assert set(yaml.safe_load(text)["outputs"]) == {"CR_LIST", "CR_COUNT", "NODES"}
    assert with_cr_outputs(RULES, {"IP": SHEETS["IP"]}, {}) == RULES


def test_the_jar_copy_keeps_the_outputs(tmp_path):
    p = tmp_path / "UC_validation-rules.yaml"
    p.write_text(with_cr_outputs(RULES, SHEETS, ROWS) + "report_output:\n  formats: [JSON]\n", encoding="utf-8")
    sanitized = yaml.safe_load(open(_sanitize_rules_for_jar(str(p)), encoding="utf-8"))
    assert "report_output" not in sanitized and set(sanitized["outputs"]) == {"CR_LIST", "CR_COUNT"}
