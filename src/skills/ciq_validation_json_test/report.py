"""Professional QA report generation — HTML with executive summary, coverage, and details."""
from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any

from .result_parser import TestCaseResult


def generate_qa_report(
    uc_name: str,
    test_results: list[TestCaseResult],
    ciq_file: str = "",
    validation_rules_file: str = "",
    json_output_file: str = "",
    jar_version: str = "",
    duration_sec: float = 0,
) -> str:
    """Generate a professional HTML QA report.

    Sections:
    1. Executive Summary — verdict, pass rate, risk level
    2. Test Environment — files, versions, timestamp
    3. Test Coverage Matrix — categories × severity
    4. Test Results — full table with status, details
    5. Failure Analysis — grouped by root cause
    6. Recommendations — prioritized fix suggestions
    """
    total = len(test_results)
    passed = sum(1 for r in test_results if r.status == "PASS")
    failed = sum(1 for r in test_results if r.status == "FAIL")
    errors = sum(1 for r in test_results if r.status == "ERROR")
    skipped = sum(1 for r in test_results if r.status == "SKIPPED")
    pass_rate = (passed / total * 100) if total > 0 else 0

    # Risk assessment
    critical_fails = sum(1 for r in test_results if r.status == "FAIL" and r.severity == "critical")
    if critical_fails > 0:
        risk_level, risk_color = "HIGH", "#DC2626"
    elif failed > total * 0.3:
        risk_level, risk_color = "MEDIUM", "#D97706"
    elif failed > 0:
        risk_level, risk_color = "LOW", "#2563EB"
    else:
        risk_level, risk_color = "NONE", "#16A34A"

    verdict = "PASS" if failed == 0 and errors == 0 else "FAIL"
    verdict_color = "#16A34A" if verdict == "PASS" else "#DC2626"

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # Coverage matrix
    categories = sorted(set(r.category for r in test_results if r.category))
    severities = ["critical", "major", "minor"]

    # Failure groups
    failure_groups: dict[str, list[TestCaseResult]] = {}
    for r in test_results:
        if r.status == "FAIL":
            key = r.category or "uncategorized"
            failure_groups.setdefault(key, []).append(r)

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>QA Report — {_e(uc_name)}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: 'Segoe UI', -apple-system, sans-serif; background: #f8fafc; color: #1e293b; line-height: 1.5; }}
  .container {{ max-width: 1200px; margin: 0 auto; padding: 24px; }}

  /* Header */
  .header {{ background: linear-gradient(135deg, #1e1b4b 0%, #312e81 100%); color: #fff; padding: 32px; border-radius: 12px; margin-bottom: 24px; }}
  .header h1 {{ font-size: 1.5rem; font-weight: 700; margin-bottom: 4px; }}
  .header .subtitle {{ font-size: 0.85rem; color: rgba(255,255,255,0.6); }}

  /* Cards */
  .card {{ background: #fff; border: 1px solid #e2e8f0; border-radius: 10px; padding: 24px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.04); }}
  .card h2 {{ font-size: 1.1rem; font-weight: 600; color: #334155; margin-bottom: 16px; padding-bottom: 8px; border-bottom: 2px solid #e2e8f0; }}

  /* Executive Summary */
  .summary-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 16px; }}
  .metric {{ text-align: center; padding: 16px; border-radius: 8px; background: #f8fafc; }}
  .metric .value {{ font-size: 1.8rem; font-weight: 700; }}
  .metric .label {{ font-size: 0.75rem; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; }}

  /* Verdict badge */
  .verdict {{ display: inline-block; padding: 6px 20px; border-radius: 20px; font-weight: 700; font-size: 0.9rem; letter-spacing: 1px; color: #fff; }}

  /* Donut chart (pure CSS) */
  .donut-container {{ display: flex; align-items: center; gap: 24px; }}
  .donut {{ width: 120px; height: 120px; border-radius: 50%; background: conic-gradient(
    #16A34A 0deg {passed / total * 360 if total else 0}deg,
    #DC2626 {passed / total * 360 if total else 0}deg {(passed + failed) / total * 360 if total else 0}deg,
    #D97706 {(passed + failed) / total * 360 if total else 0}deg {(passed + failed + errors) / total * 360 if total else 0}deg,
    #94A3B8 {(passed + failed + errors) / total * 360 if total else 0}deg 360deg
  ); position: relative; }}
  .donut::after {{ content: '{pass_rate:.0f}%'; position: absolute; inset: 20px; background: #fff; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 1.3rem; font-weight: 700; color: #334155; }}
  .legend {{ font-size: 0.8rem; }}
  .legend-item {{ display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }}
  .legend-dot {{ width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }}

  /* Environment table */
  .env-table {{ width: 100%; font-size: 0.85rem; }}
  .env-table td {{ padding: 6px 12px; }}
  .env-table td:first-child {{ font-weight: 600; color: #64748b; width: 180px; }}

  /* Coverage matrix */
  .coverage-table {{ width: 100%; border-collapse: collapse; font-size: 0.8rem; }}
  .coverage-table th, .coverage-table td {{ padding: 8px 12px; border: 1px solid #e2e8f0; text-align: center; }}
  .coverage-table th {{ background: #f1f5f9; font-weight: 600; color: #475569; }}
  .coverage-cell {{ font-weight: 600; }}

  /* Results table */
  .results-table {{ width: 100%; border-collapse: collapse; font-size: 0.8rem; }}
  .results-table th {{ background: #f1f5f9; padding: 10px 12px; text-align: left; font-weight: 600; color: #475569; border-bottom: 2px solid #e2e8f0; white-space: nowrap; }}
  .results-table td {{ padding: 10px 12px; border-bottom: 1px solid #f1f5f9; vertical-align: top; }}
  .results-table tr:hover {{ background: #fafbfd; }}

  /* Status badges */
  .badge {{ display: inline-block; padding: 2px 10px; border-radius: 12px; font-size: 0.7rem; font-weight: 600; letter-spacing: 0.3px; }}
  .badge-pass {{ background: #dcfce7; color: #166534; }}
  .badge-fail {{ background: #fce4ec; color: #991b1b; }}
  .badge-error {{ background: #fef3c7; color: #92400e; }}
  .badge-skip {{ background: #f1f5f9; color: #64748b; }}
  .badge-critical {{ background: #fce4ec; color: #991b1b; }}
  .badge-major {{ background: #fff7ed; color: #9a3412; }}
  .badge-minor {{ background: #f0f9ff; color: #1e40af; }}

  /* Failure details */
  .failure-group {{ margin-bottom: 16px; }}
  .failure-group h3 {{ font-size: 0.9rem; font-weight: 600; color: #DC2626; margin-bottom: 8px; }}
  .error-list {{ font-size: 0.78rem; color: #64748b; padding-left: 16px; }}
  .error-list li {{ margin-bottom: 4px; }}

  /* Recommendations */
  .rec-item {{ display: flex; gap: 12px; padding: 12px; border-radius: 8px; background: #fffbeb; margin-bottom: 8px; border-left: 3px solid #f59e0b; }}
  .rec-item.critical {{ background: #fef2f2; border-left-color: #ef4444; }}
  .rec-priority {{ font-weight: 700; font-size: 0.75rem; text-transform: uppercase; white-space: nowrap; }}
  .rec-text {{ font-size: 0.85rem; }}

  /* Footer */
  .footer {{ text-align: center; font-size: 0.75rem; color: #94a3b8; margin-top: 32px; padding-top: 16px; border-top: 1px solid #e2e8f0; }}
</style>
</head>
<body>
<div class="container">

  <!-- Header -->
  <div class="header">
    <h1>CIQ Validation & JSON Output — QA Report</h1>
    <div class="subtitle">{_e(uc_name)} &mdash; {timestamp}</div>
  </div>

  <!-- 1. Executive Summary -->
  <div class="card">
    <h2>1. Executive Summary</h2>
    <div class="donut-container" style="margin-bottom: 20px;">
      <div class="donut"></div>
      <div class="legend">
        <div class="legend-item"><div class="legend-dot" style="background:#16A34A"></div> Passed: {passed}</div>
        <div class="legend-item"><div class="legend-dot" style="background:#DC2626"></div> Failed: {failed}</div>
        <div class="legend-item"><div class="legend-dot" style="background:#D97706"></div> Errors: {errors}</div>
        <div class="legend-item"><div class="legend-dot" style="background:#94A3B8"></div> Skipped: {skipped}</div>
      </div>
    </div>
    <div class="summary-grid">
      <div class="metric">
        <div class="value" style="color:{verdict_color}"><span class="verdict" style="background:{verdict_color}">{verdict}</span></div>
        <div class="label">Overall Verdict</div>
      </div>
      <div class="metric">
        <div class="value">{total}</div>
        <div class="label">Total Test Cases</div>
      </div>
      <div class="metric">
        <div class="value" style="color:#16A34A">{passed}</div>
        <div class="label">Passed</div>
      </div>
      <div class="metric">
        <div class="value" style="color:#DC2626">{failed}</div>
        <div class="label">Failed</div>
      </div>
      <div class="metric">
        <div class="value">{pass_rate:.1f}%</div>
        <div class="label">Pass Rate</div>
      </div>
      <div class="metric">
        <div class="value" style="color:{risk_color}">{risk_level}</div>
        <div class="label">Risk Level</div>
      </div>
    </div>
  </div>

  <!-- 2. Test Environment -->
  <div class="card">
    <h2>2. Test Environment</h2>
    <table class="env-table">
      <tr><td>Use Case</td><td>{_e(uc_name)}</td></tr>
      <tr><td>CIQ File</td><td><code>{_e(ciq_file)}</code></td></tr>
      <tr><td>Validation Rules</td><td><code>{_e(validation_rules_file)}</code></td></tr>
      <tr><td>JSON Output Config</td><td><code>{_e(json_output_file)}</code></td></tr>
      <tr><td>CIQ Processor</td><td>{_e(jar_version) or 'ciq-processor JAR'}</td></tr>
      <tr><td>Execution Time</td><td>{duration_sec:.1f}s</td></tr>
      <tr><td>Report Generated</td><td>{timestamp}</td></tr>
    </table>
  </div>

  <!-- 3. Test Coverage Matrix -->
  <div class="card">
    <h2>3. Test Coverage Matrix</h2>
    <table class="coverage-table">
      <tr>
        <th>Category</th>
        {_th_cells(severities)}
        <th>Total</th>
        <th>Pass Rate</th>
      </tr>
      {_coverage_rows(test_results, categories, severities)}
    </table>
  </div>

  <!-- 4. Test Results -->
  <div class="card">
    <h2>4. Test Results</h2>
    <table class="results-table">
      <tr>
        <th>ID</th>
        <th>Status</th>
        <th>Severity</th>
        <th>Category</th>
        <th>Description</th>
        <th>Target</th>
        <th>Expected</th>
        <th>Actual</th>
      </tr>
      {_result_rows(test_results)}
    </table>
  </div>

  <!-- 5. Failure Analysis -->
  {_failure_analysis_section(failure_groups) if failure_groups else ''}

  <!-- 6. Recommendations -->
  {_recommendations_section(test_results, failure_groups)}

  <div class="footer">
    Generated by NetConfig QA Engine &mdash; {timestamp}
  </div>
</div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def _e(text: str) -> str:
    return html.escape(str(text)) if text else ""


def _status_badge(status: str) -> str:
    cls = {"PASS": "badge-pass", "FAIL": "badge-fail", "ERROR": "badge-error", "SKIPPED": "badge-skip"}.get(status, "badge-skip")
    return f'<span class="badge {cls}">{status}</span>'


def _severity_badge(severity: str) -> str:
    cls = {"critical": "badge-critical", "major": "badge-major", "minor": "badge-minor"}.get(severity, "badge-minor")
    return f'<span class="badge {cls}">{severity.upper()}</span>'


def _th_cells(items: list[str]) -> str:
    return "".join(f"<th>{_e(s).title()}</th>" for s in items)


def _coverage_rows(results: list[TestCaseResult], categories: list[str], severities: list[str]) -> str:
    rows = []
    for cat in categories:
        cat_results = [r for r in results if r.category == cat]
        cells = []
        for sev in severities:
            sev_results = [r for r in cat_results if r.severity == sev]
            total = len(sev_results)
            passed = sum(1 for r in sev_results if r.status == "PASS")
            color = "#16A34A" if passed == total and total > 0 else "#DC2626" if passed < total else "#94A3B8"
            cells.append(f'<td class="coverage-cell" style="color:{color}">{passed}/{total}</td>')

        cat_total = len(cat_results)
        cat_passed = sum(1 for r in cat_results if r.status == "PASS")
        rate = (cat_passed / cat_total * 100) if cat_total > 0 else 0
        rate_color = "#16A34A" if rate == 100 else "#DC2626" if rate < 70 else "#D97706"

        rows.append(
            f'<tr><td style="text-align:left;font-weight:600">{_e(cat)}</td>'
            f'{"".join(cells)}'
            f'<td>{cat_passed}/{cat_total}</td>'
            f'<td style="color:{rate_color};font-weight:600">{rate:.0f}%</td></tr>'
        )
    return "\n      ".join(rows)


def _result_rows(results: list[TestCaseResult]) -> str:
    rows = []
    for r in results:
        error_detail = ""
        if r.errors:
            msgs = [f"{e.sheet}:{e.column} — {e.message}" for e in r.errors[:3]]
            if len(r.errors) > 3:
                msgs.append(f"... +{len(r.errors) - 3} more")
            error_detail = "<br>".join(_e(m) for m in msgs)

        actual_cell = _e(r.actual_outcome)
        if error_detail:
            actual_cell += f'<div style="font-size:0.72rem;color:#94a3b8;margin-top:4px">{error_detail}</div>'

        rows.append(
            f'<tr>'
            f'<td><strong>{_e(r.id)}</strong></td>'
            f'<td>{_status_badge(r.status)}</td>'
            f'<td>{_severity_badge(r.severity)}</td>'
            f'<td>{_e(r.category)}</td>'
            f'<td>{_e(r.description)}</td>'
            f'<td>{_e(r.target_sheet)}</td>'
            f'<td style="font-size:0.78rem">{_e(r.expected_outcome)}</td>'
            f'<td style="font-size:0.78rem">{actual_cell}</td>'
            f'</tr>'
        )
    return "\n      ".join(rows)


def _failure_analysis_section(failure_groups: dict[str, list[TestCaseResult]]) -> str:
    groups_html = []
    for category, failures in sorted(failure_groups.items()):
        items = []
        for f in failures:
            error_msgs = [f"{e.sheet}:{e.column} row {e.row} — {e.message}" for e in f.errors[:5]]
            error_list = "".join(f"<li>{_e(m)}</li>" for m in error_msgs) if error_msgs else "<li>No detailed error info</li>"
            items.append(
                f'<div style="margin-bottom:8px">'
                f'<strong>{_e(f.id)}</strong> {_severity_badge(f.severity)} — {_e(f.description)}'
                f'<ul class="error-list">{error_list}</ul>'
                f'</div>'
            )
        groups_html.append(
            f'<div class="failure-group">'
            f'<h3>{_e(category).title()} ({len(failures)} failure{"s" if len(failures) != 1 else ""})</h3>'
            f'{"".join(items)}'
            f'</div>'
        )

    return (
        f'<div class="card">'
        f'<h2>5. Failure Analysis</h2>'
        f'{"".join(groups_html)}'
        f'</div>'
    )


def _recommendations_section(results: list[TestCaseResult], failure_groups: dict) -> str:
    recs = []

    critical_fails = [r for r in results if r.status == "FAIL" and r.severity == "critical"]
    if critical_fails:
        sheets = sorted(set(r.target_sheet for r in critical_fails if r.target_sheet))
        recs.append(("critical", f"Fix {len(critical_fails)} critical failure(s) in: {', '.join(sheets) or 'multiple areas'}. These block production use."))

    for cat, failures in sorted(failure_groups.items()):
        if cat == "column_validation":
            cols = sorted(set(e.column for f in failures for e in f.errors if e.column))[:5]
            recs.append(("major", f"Review column validation rules for: {', '.join(cols) or 'multiple columns'}. Column names or types may not match the actual CIQ."))
        elif cat == "cross_sheet":
            recs.append(("major", f"{len(failures)} cross-sheet reference failure(s). Verify workbook_rules Sheet.Column references match actual sheet and column names."))
        elif cat == "json_mapping":
            recs.append(("major", f"{len(failures)} JSON output mapping failure(s). Verify _each, segregate_by, and dotted references resolve to real CIQ data."))

    if not failure_groups:
        recs.append(("info", "All tests passed. The validation rules and JSON output configuration are ready for production use."))

    items = []
    for priority, text in recs:
        cls = "critical" if priority == "critical" else ""
        items.append(
            f'<div class="rec-item {cls}">'
            f'<div class="rec-priority" style="color:{"#DC2626" if priority == "critical" else "#D97706" if priority == "major" else "#16A34A"}">{_e(priority)}</div>'
            f'<div class="rec-text">{_e(text)}</div>'
            f'</div>'
        )

    return (
        f'<div class="card">'
        f'<h2>6. Recommendations</h2>'
        f'{"".join(items)}'
        f'</div>'
    )
