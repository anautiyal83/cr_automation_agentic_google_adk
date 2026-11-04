"""Step 3 validation rules derived from the CIQ rows (rules_baseline)."""
from __future__ import annotations

import openpyxl

from skills.ciq_validation_json_gen.rules_baseline import baseline_rules, merge_llm_rules, read_ciq_rows

SHEETS = {
    "INDEX": {"columns": ["NODE", "GROUP", "CRGROUP", "EMAIL"]},
    "FILES": {"columns": ["GROUP", "INPUT_FILE", "DEST_PATH"]},
    "Node_Details": {"columns": ["NODE TYPE", "VER", "NODE_NAME", "NIAM NAME"]},
}


def _ciq(tmp_path):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    data = {
        "INDEX": [("NODE", "GROUP", "CRGROUP", "EMAIL"), ("MRF1", "GRP1", "CR1", "a@x.com;b@x.com"),
                  ("MRF2", "GRP2", "CR1", "a@x.com")],
        "FILES": [("GROUP", "INPUT_FILE", "DEST_PATH"), ("GRP1", "f1.tar", "/var/clips"), ("GRP2", "f2.3gp", "/var/c2")],
        "Node_Details": [("NODE TYPE", "VER", "NODE_NAME", "NIAM NAME"), ("MRF", "13.0.3.7", "MRF1", "n1"),
                         ("MRF", "17.0.3.7", "MRF2", "n2"), ("MRF", "17.0.3.7", "MRF3", "n3")],
    }
    for name, rows in data.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    path = tmp_path / "ciq.xlsx"
    wb.save(path)
    return str(path)


def test_rules_from_ciq_rows(tmp_path):
    rows = read_ciq_rows(_ciq(tmp_path), SHEETS)
    s = baseline_rules(SHEETS, rows)["sheets"]
    assert s["INDEX"]["columns"]["EMAIL"] == {"type": "email", "required": True, "multi": True}
    assert s["INDEX"]["columns"]["NODE"]["crossRef"] == {"sheet": "Node_Details", "column": "NODE_NAME"}
    assert s["FILES"]["columns"]["GROUP"]["crossRef"] == {"sheet": "INDEX", "column": "GROUP"}
    assert "crossRef" not in s["Node_Details"]["columns"]["NODE_NAME"]      # master list refs nothing
    assert s["Node_Details"]["columns"]["NODE_NAME"]["unique"] is True
    ver = s["Node_Details"]["columns"]["VER"]
    assert ver["type"] == "string" and ver["pattern"] == r"[0-9]+(\.[0-9]+)+"   # not an IP address
    assert s["Node_Details"]["columns"]["NODE TYPE"] == {"type": "enum", "required": True, "values": ["MRF"]}
    assert s["FILES"]["columns"]["DEST_PATH"]["pattern"] == r"/\S*"


def test_llm_extras_kept_only_when_the_ciq_passes_them(tmp_path):
    rows = read_ciq_rows(_ciq(tmp_path), SHEETS)
    llm = {"sheets": {
        "FILES": {"columns": {"INPUT_FILE": {"type": "string", "pattern": r".+\.tar"}}},   # f2.3gp fails
        "INDEX": {"columns": {"CRGROUP": {"pattern": "CR[0-9]+", "patternMessage": "CR1, CR2…"},
                              "EMAIL": {"type": "string"}}},                               # no downgrade
    }}
    s = merge_llm_rules(baseline_rules(SHEETS, rows), llm, rows)["sheets"]
    assert "pattern" not in s["FILES"]["columns"]["INPUT_FILE"]
    assert s["INDEX"]["columns"]["CRGROUP"]["pattern"] == "CR[0-9]+"
    assert s["INDEX"]["columns"]["EMAIL"]["type"] == "email"
