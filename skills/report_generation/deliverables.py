"""The run's deliverables, in the order the final step (10) lists them."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Deliverable:
    label: str
    path: Path

    def rel(self, run_dir: str | Path) -> str:
        return self.path.relative_to(run_dir).as_posix()


def collect_deliverables(run_dir: str | Path, uc_name: str) -> list[Deliverable]:
    """Validation rules, JSON output, execution workflow, generated MOP, QA-Validation report,
    QA-Execution report, then per executed case the execution-report-generator JAR's summary
    and execution (detail) report. Missing files are left out."""
    rd = Path(run_dir)
    if not rd.is_dir():
        return []
    out: list[Deliverable] = []

    def add(label: str, path: Path) -> None:
        if path.is_file():
            out.append(Deliverable(label, path))

    add("Validation Rules YAML", rd / f"{uc_name}_validation-rules.yaml")
    add("JSON Output YAML", rd / f"{uc_name}_json-output.yaml")
    add("Execution YAML", rd / f"{uc_name}.yaml")
    for mop in sorted((rd / "mop_output").glob("*.html")):
        add("Generated MOP", mop)
    add("QA-Validation Report", rd / f"{uc_name}_ciq-qa-report.html")
    add("QA-Execution Report", rd / f"{uc_name}_network-qa-report.html")

    # Execution reports (Report Generator JAR, step 9): case status from the test-case results
    status: dict[str, str] = {}
    try:
        cases = json.loads((rd / f"{uc_name}_test-cases.json").read_text(encoding="utf-8")).get("test_cases") or []
        status = {Path(f).name: c.get("status", "") for c in cases for f in c.get("html_reports") or []}
    except (OSError, ValueError):
        pass
    reports = sorted((rd / "exec_report").glob("*.html"))
    for f in reports:
        if f.name.endswith("_summary.html"):
            add(f"Execution Summary Report — {_case(f, uc_name, '_summary.html')}", f)
    for f in reports:
        if f.name.endswith("_detail.html"):
            kind = "Failure" if status.get(f.name) == "FAIL" else "Success"
            add(f"Execution {kind} Report — {_case(f, uc_name, '_detail.html')}", f)
    return out


def _case(path: Path, uc_name: str, suffix: str) -> str:
    """``<uc>_TC-P01_EAST_summary.html`` → ``TC-P01 EAST``."""
    name = path.name[: -len(suffix)]
    if name.startswith(uc_name + "_"):
        name = name[len(uc_name) + 1:]
    return name.replace("_", " ")
