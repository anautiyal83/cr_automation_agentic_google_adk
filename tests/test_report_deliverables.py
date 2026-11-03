"""Step 10 deliverables list (order + labels)."""
from __future__ import annotations

import json

from skills.report_generation.deliverables import collect_deliverables


def test_deliverables_in_order(tmp_path):
    uc = "UC_1_X"
    for name in (f"{uc}.yaml", f"{uc}_json-output.yaml", f"{uc}_validation-rules.yaml", f"{uc}_ciq-qa-report.html",
                 f"{uc}_network-qa-report.html", f"mop_output/{uc}_mop-CR1.html",
                 f"exec_report/{uc}_TC-P01_EAST_summary.html", f"exec_report/{uc}_TC-P01_EAST_detail.html",
                 f"exec_report/{uc}_TC-P02_NORTH_detail.html"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("x", encoding="utf-8")
    (tmp_path / f"{uc}_test-cases.json").write_text(json.dumps({"test_cases": [
        {"status": "PASS", "html_reports": [f"exec_report/{uc}_TC-P01_EAST_detail.html"]},
        {"status": "FAIL", "html_reports": [f"exec_report/{uc}_TC-P02_NORTH_detail.html"]}]}), encoding="utf-8")
    labels = [d.label for d in collect_deliverables(tmp_path, uc)]
    assert labels == ["Validation Rules YAML", "JSON Output YAML", "Execution YAML", "Generated MOP",
                      "QA-Validation Report", "QA-Execution Report", "Execution Summary Report — TC-P01 EAST",
                      "Execution Success Report — TC-P01 EAST", "Execution Failure Report — TC-P02 NORTH"]
