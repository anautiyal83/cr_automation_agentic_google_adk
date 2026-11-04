"""The run's final documents, grouped by category and in the order the pipeline produces them.

Categories (in this order): Requirement (steps 1-2), CIQ Validation (steps 3-5), Execution
(steps 6-9), General (step 10: the final report). Within a category the documents follow the
step that writes them; step 9's reports follow the cases as they ran (summary, then detail),
and the QA-Execution report that sums them up comes last.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

REQUIREMENT = "Requirement"
CIQ_VALIDATION = "CIQ Validation"
EXECUTION = "Execution"
GENERAL = "General"
CATEGORIES = (REQUIREMENT, CIQ_VALIDATION, EXECUTION, GENERAL)


@dataclass
class Deliverable:
    label: str
    path: Path
    category: str = GENERAL

    def rel(self, run_dir: str | Path) -> str:
        return self.path.relative_to(run_dir).as_posix()


def collect_deliverables(run_dir: str | Path, uc_name: str, include_final: bool = True) -> list[Deliverable]:
    """Requirement: requirements document. CIQ Validation: validation rules, JSON output YAML,
    CIQ JSON data, QA-Validation report. Execution: execution YAML, generated MOP(s), QA-MOP report,
    per executed case the execution-report-generator JAR's summary and success/failure report,
    QA-Execution report. General: final report (HTML, Excel) unless ``include_final`` is False
    (the final report lists the others while it is being written). Missing files are left out."""
    rd = Path(run_dir)
    if not rd.is_dir():
        return []
    out: list[Deliverable] = []

    def add(category: str, label: str, path: Path) -> None:
        if path.is_file():
            out.append(Deliverable(label, path, category))

    # Steps 1-2
    add(REQUIREMENT, "Requirements Document", rd / f"{uc_name}_requirements-summary.html")
    # Steps 3-5
    add(CIQ_VALIDATION, "Validation Rules YAML", rd / f"{uc_name}_validation-rules.yaml")
    add(CIQ_VALIDATION, "JSON Output YAML", rd / f"{uc_name}_json-output.yaml")
    for f in sorted((rd / "json_data").glob("*.json")):
        if "VALIDATION_REPORT" not in f.name.upper():
            add(CIQ_VALIDATION, "CIQ JSON Data", f)
    add(CIQ_VALIDATION, "QA-Validation Report", rd / f"{uc_name}_ciq-qa-report.html")
    # Steps 6-9
    add(EXECUTION, "Execution YAML", rd / f"{uc_name}.yaml")
    for mop in sorted((rd / "mop_output").glob("*.html")):
        add(EXECUTION, "Generated MOP", mop)
    add(EXECUTION, "QA-MOP Report", rd / f"{uc_name}_mop-qa-report.html")
    _execution_reports(rd, uc_name, add)
    add(EXECUTION, "QA-Execution Report", rd / f"{uc_name}_network-qa-report.html")
    # Step 10
    if include_final:
        add(GENERAL, "Final Report", rd / f"{uc_name}_report.html")
        add(GENERAL, "Final Report (Excel)", rd / f"{uc_name}_report.xlsx")
    return out


def group_deliverables(items: list[Deliverable]) -> list[tuple[str, list[Deliverable]]]:
    """[(category, documents)] in category order, empty categories left out."""
    return [(c, [d for d in items if d.category == c]) for c in CATEGORIES
            if any(d.category == c for d in items)]


def _execution_reports(rd: Path, uc_name: str, add) -> None:
    """Report Generator JAR (step 9): per case in the order the cases ran, its summary then its
    success/failure (detail) report. Order and status come from the test-case results."""
    reports = {f.name: f for f in (rd / "exec_report").glob("*.html")}
    ordered: list[str] = []
    status: dict[str, str] = {}
    try:
        cases = json.loads((rd / f"{uc_name}_test-cases.json").read_text(encoding="utf-8")).get("test_cases") or []
        for c in cases:
            for f in c.get("html_reports") or []:
                name = Path(f).name
                status[name] = c.get("status", "")
                if name in reports and name not in ordered:
                    ordered.append(name)
    except (OSError, ValueError):
        pass
    ordered += sorted(n for n in reports if n not in ordered)
    # a case's summary before its detail report
    run_order = list(ordered)
    for name in sorted(ordered, key=lambda n: (_case_key(n, run_order), 0 if n.endswith("_summary.html") else 1)):
        f = reports[name]
        if name.endswith("_summary.html"):
            add(EXECUTION, f"Execution Summary Report — {_case(f, uc_name, '_summary.html')}", f)
        elif name.endswith("_detail.html"):
            kind = "Failure" if status.get(name) == "FAIL" else "Success"
            add(EXECUTION, f"Execution {kind} Report — {_case(f, uc_name, '_detail.html')}", f)


def _case_key(name: str, ordered: list[str]) -> int:
    """Position of the case (first report of the same case prefix) in run order."""
    prefix = name.rsplit("_", 1)[0]
    return next(i for i, n in enumerate(ordered) if n.rsplit("_", 1)[0] == prefix)


def _case(path: Path, uc_name: str, suffix: str) -> str:
    """``<uc>_TC-P01_EAST_summary.html`` → ``TC-P01 EAST``."""
    name = path.name[: -len(suffix)]
    if name.startswith(uc_name + "_"):
        name = name[len(uc_name) + 1:]
    return name.replace("_", " ")
