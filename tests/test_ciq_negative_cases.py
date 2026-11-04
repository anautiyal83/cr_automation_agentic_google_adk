"""Step 5: negative CIQ cases run on mutated copies; the JAR report is parsed (G-13)."""
from __future__ import annotations

import json

import openpyxl

from skills.ciq_validation_json_test.negative_cases import apply_mutations, rule_negative_cases
from skills.ciq_validation_json_test.result_parser import parse_jar_output


def test_jar_report_sheets_list_is_parsed(tmp_path):
    # ciq-processor 1.0.3 writes "sheets" as a list; this raised and every report was skipped
    (tmp_path / "R_VALIDATION_REPORT.json").write_text(json.dumps({
        "status": "FAILED", "globalErrors": ["Sheet X missing"],
        "sheets": [{"sheetName": "INDEX", "errors": [{"rowNumber": 2, "column": "NODE", "message": "not found"}]},
                   {"sheetName": "FILES", "errors": []}]}), encoding="utf-8")
    errs = parse_jar_output(str(tmp_path))
    assert {"sheet": "INDEX", "column": "NODE", "row": 2} == {k: errs[0][k] for k in ("sheet", "column", "row")}
    assert errs[1]["sheet"] == "workbook" and "Sheet X" in errs[1]["message"]


def test_mutation_blanks_a_cell(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "INDEX"
    ws.append(["NODE", "GROUP"])
    ws.append(["MRF1", "GRP1"])
    src = tmp_path / "ciq.xlsx"
    wb.save(src)
    out = apply_mutations(str(src), [{"sheet": "index", "row": 1, "column": "node", "value": ""}], tmp_path / "m.xlsx")
    assert openpyxl.load_workbook(out)["INDEX"]["A2"].value is None   # ws.cell(value=None) did not clear it


def test_one_negative_case_per_rule():
    rules = {"sheets": {"N": {"columns": {
        "NODE_NAME": {"type": "string", "required": True, "unique": True},
        "VER": {"type": "string", "required": False, "pattern": r"[0-9]+(\.[0-9]+)+"},
        "EMAIL": {"type": "email", "required": False},
        "GROUP": {"type": "string", "required": False, "crossRef": {"sheet": "I", "column": "GROUP"}}}}}}
    rows = {"N": [{"NODE_NAME": "MRF1", "VER": "1.0", "EMAIL": "a@b.c", "GROUP": "G"},
                  {"NODE_NAME": "MRF2", "VER": "1.1", "EMAIL": "a@b.c", "GROUP": "G"}]}
    kinds = {tc.scenario.rsplit("(", 1)[1].rstrip(")"): tc.mutations[0] for tc in rule_negative_cases(rules, rows)}
    assert set(kinds) == {"required", "unique", "pattern", "email", "crossRef"}
    assert kinds["unique"] == {"sheet": "N", "row": 2, "column": "NODE_NAME", "value": "MRF1"}
    assert kinds["required"]["value"] == ""
