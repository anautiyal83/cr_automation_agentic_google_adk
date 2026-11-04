"""Step 10 deliverables list (order + labels)."""
from __future__ import annotations

import json

from skills.report_generation.deliverables import collect_deliverables, group_deliverables


def test_deliverables_in_order(tmp_path):
    uc = "UC_1_X"
    for name in (f"{uc}_requirements-summary.html", f"{uc}.yaml", f"{uc}_json-output.yaml", f"{uc}_validation-rules.yaml", f"{uc}_ciq-qa-report.html",
                 f"{uc}_network-qa-report.html", f"mop_output/{uc}_mop-CR1.html",
                 f"exec_report/{uc}_TC-P01_EAST_summary.html", f"exec_report/{uc}_TC-P01_EAST_detail.html",
                 f"exec_report/{uc}_TC-P02_NORTH_detail.html"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("x", encoding="utf-8")
    (tmp_path / f"{uc}_test-cases.json").write_text(json.dumps({"test_cases": [
        {"status": "PASS", "html_reports": [f"exec_report/{uc}_TC-P01_EAST_detail.html"]},
        {"status": "FAIL", "html_reports": [f"exec_report/{uc}_TC-P02_NORTH_detail.html"]}]}), encoding="utf-8")
    (tmp_path / f"{uc}_mop-qa-report.html").write_text("x", encoding="utf-8")
    (tmp_path / "json_data").mkdir()
    (tmp_path / "json_data" / f"{uc}.json").write_text("{}", encoding="utf-8")
    (tmp_path / f"{uc}_report.html").write_text("x", encoding="utf-8")
    items = collect_deliverables(tmp_path, uc)
    # grouped by category, in the order the pipeline produces the documents
    assert [(d.category, d.label) for d in items] == [
        ("Requirement", "Requirements Document"),
        ("CIQ Validation", "Validation Rules YAML"), ("CIQ Validation", "JSON Output YAML"),
        ("CIQ Validation", "CIQ JSON Data"), ("CIQ Validation", "QA-Validation Report"),
        ("Execution", "Execution YAML"), ("Execution", "Generated MOP"), ("Execution", "QA-MOP Report"),
        ("Execution", "Execution Summary Report — TC-P01 EAST"), ("Execution", "Execution Success Report — TC-P01 EAST"),
        ("Execution", "Execution Failure Report — TC-P02 NORTH"), ("Execution", "QA-Execution Report"),
        ("General", "Final Report")]
    assert [c for c, _ in group_deliverables(items)] == ["Requirement", "CIQ Validation", "Execution", "General"]
    assert "Final Report" not in [d.label for d in collect_deliverables(tmp_path, uc, include_final=False)]
