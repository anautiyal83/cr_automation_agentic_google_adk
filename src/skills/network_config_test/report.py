"""Professional QA report for network configuration testing.

Two test phases reported:
- MOP Generation Test — can the JAR parse the workflow into MOP scenarios?
- Execution Test — do commands execute and validate correctly against the simulator?
"""
from __future__ import annotations

import html as html_mod
from datetime import datetime, timezone

from .result_parser import TestCaseResult, MopScenario, CommandResult


def generate_qa_report(
    uc_name: str,
    test_results: list[TestCaseResult],
    mop_scenarios: list[MopScenario],
    command_results: list[CommandResult],
    failure_classification: dict | None = None,
    workflow_file: str = "",
    simulator_config_file: str = "",
    duration_sec: float = 0,
) -> str:
    """Generate a professional HTML QA report for network config testing."""
    total = len(test_results)
    passed = sum(1 for r in test_results if r.status == "PASS")
    failed = sum(1 for r in test_results if r.status == "FAIL")
    skipped = sum(1 for r in test_results if r.status == "SKIPPED")
    pass_rate = (passed / total * 100) if total > 0 else 0

    mop_total = len(mop_scenarios)
    mop_passed = sum(1 for s in mop_scenarios if s.status == "PASS")
    cmd_total = len(command_results)
    cmd_passed = sum(1 for c in command_results if c.success and c.validation_status != "failed")
    val_passed = sum(1 for c in command_results if c.validation_status == "success")
    val_failed = sum(1 for c in command_results if c.validation_status == "failed")

    critical_fails = sum(1 for r in test_results if r.status == "FAIL" and r.severity == "critical")
    risk_level = "HIGH" if critical_fails > 0 else "MEDIUM" if failed > total * 0.3 else "LOW" if failed > 0 else "NONE"
    risk_color = {"HIGH": "#DC2626", "MEDIUM": "#D97706", "LOW": "#2563EB", "NONE": "#16A34A"}[risk_level]

    verdict = "PASS" if failed == 0 else "FAIL"
    verdict_color = "#16A34A" if verdict == "PASS" else "#DC2626"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    classification = failure_classification or {}
    cls_text = classification.get("classification", "")
    retry_target = classification.get("retry_target", "")

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Network Config QA Report — {_e(uc_name)}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: 'Segoe UI', -apple-system, sans-serif; background: #f8fafc; color: #1e293b; line-height: 1.5; }}
  .container {{ max-width: 1200px; margin: 0 auto; padding: 24px; }}
  .header {{ background: linear-gradient(135deg, #1e1b4b 0%, #312e81 100%); color: #fff; padding: 32px; border-radius: 12px; margin-bottom: 24px; }}
  .header h1 {{ font-size: 1.5rem; font-weight: 700; }}
  .header .sub {{ font-size: 0.85rem; color: rgba(255,255,255,0.6); }}
  .card {{ background: #fff; border: 1px solid #e2e8f0; border-radius: 10px; padding: 24px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.04); }}
  .card h2 {{ font-size: 1.1rem; font-weight: 600; color: #334155; margin-bottom: 16px; border-bottom: 2px solid #e2e8f0; padding-bottom: 8px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; }}
  .metric {{ text-align: center; padding: 14px; background: #f8fafc; border-radius: 8px; }}
  .metric .val {{ font-size: 1.6rem; font-weight: 700; }}
  .metric .lbl {{ font-size: 0.7rem; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; }}
  .verdict {{ display: inline-block; padding: 4px 16px; border-radius: 16px; font-weight: 700; font-size: 0.85rem; color: #fff; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.8rem; }}
  th {{ background: #f1f5f9; padding: 10px 12px; text-align: left; font-weight: 600; color: #475569; border-bottom: 2px solid #e2e8f0; }}
  td {{ padding: 8px 12px; border-bottom: 1px solid #f1f5f9; vertical-align: top; }}
  tr:hover {{ background: #fafbfd; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 0.68rem; font-weight: 600; }}
  .b-pass {{ background: #dcfce7; color: #166534; }}
  .b-fail {{ background: #fce4ec; color: #991b1b; }}
  .b-skip {{ background: #f1f5f9; color: #64748b; }}
  .b-crit {{ background: #fce4ec; color: #991b1b; }}
  .b-major {{ background: #fff7ed; color: #9a3412; }}
  .b-minor {{ background: #f0f9ff; color: #1e40af; }}
  .phase-header {{ background: #f8fafc; padding: 8px 12px; font-weight: 600; color: #6C63FF; border-left: 3px solid #6C63FF; }}
  .rec {{ padding: 10px; border-radius: 6px; margin-bottom: 8px; border-left: 3px solid #f59e0b; background: #fffbeb; font-size: 0.85rem; }}
  .rec.crit {{ border-left-color: #ef4444; background: #fef2f2; }}
  .footer {{ text-align: center; font-size: 0.72rem; color: #94a3b8; margin-top: 24px; padding-top: 12px; border-top: 1px solid #e2e8f0; }}
</style>
</head>
<body>
<div class="container">

<div class="header">
  <h1>Network Configuration — QA Report</h1>
  <div class="sub">{_e(uc_name)} &mdash; {timestamp}</div>
</div>

<!-- 1. Executive Summary -->
<div class="card">
  <h2>1. Executive Summary</h2>
  <div class="grid">
    <div class="metric"><div class="val"><span class="verdict" style="background:{verdict_color}">{verdict}</span></div><div class="lbl">Verdict</div></div>
    <div class="metric"><div class="val">{total}</div><div class="lbl">Test Cases</div></div>
    <div class="metric"><div class="val" style="color:#16A34A">{passed}</div><div class="lbl">Passed</div></div>
    <div class="metric"><div class="val" style="color:#DC2626">{failed}</div><div class="lbl">Failed</div></div>
    <div class="metric"><div class="val">{pass_rate:.0f}%</div><div class="lbl">Pass Rate</div></div>
    <div class="metric"><div class="val" style="color:{risk_color}">{risk_level}</div><div class="lbl">Risk</div></div>
  </div>
</div>

<!-- 2. Test Environment -->
<div class="card">
  <h2>2. Test Environment</h2>
  <table>
    <tr><td style="font-weight:600;width:180px">Use Case</td><td>{_e(uc_name)}</td></tr>
    <tr><td style="font-weight:600">Workflow</td><td><code>{_e(workflow_file)}</code></td></tr>
    <tr><td style="font-weight:600">Simulator Config</td><td><code>{_e(simulator_config_file)}</code></td></tr>
    <tr><td style="font-weight:600">Duration</td><td>{duration_sec:.1f}s</td></tr>
    <tr><td style="font-weight:600">Generated</td><td>{timestamp}</td></tr>
  </table>
</div>

<!-- 3. MOP Generation Results -->
<div class="card">
  <h2>3. MOP Generation Test</h2>
  <div class="grid" style="margin-bottom:12px">
    <div class="metric"><div class="val">{mop_total}</div><div class="lbl">Scenarios</div></div>
    <div class="metric"><div class="val" style="color:#16A34A">{mop_passed}</div><div class="lbl">Passed</div></div>
    <div class="metric"><div class="val" style="color:#DC2626">{mop_total - mop_passed}</div><div class="lbl">Failed</div></div>
  </div>
  {_mop_table(mop_scenarios)}
</div>

<!-- 4. Execution Results -->
<div class="card">
  <h2>4. Execution Test</h2>
  <div class="grid" style="margin-bottom:12px">
    <div class="metric"><div class="val">{cmd_total}</div><div class="lbl">Commands</div></div>
    <div class="metric"><div class="val" style="color:#16A34A">{cmd_passed}</div><div class="lbl">Cmd OK</div></div>
    <div class="metric"><div class="val" style="color:#DC2626">{cmd_total - cmd_passed}</div><div class="lbl">Cmd Fail</div></div>
    <div class="metric"><div class="val" style="color:#16A34A">{val_passed}</div><div class="lbl">Val OK</div></div>
    <div class="metric"><div class="val" style="color:#DC2626">{val_failed}</div><div class="lbl">Val Fail</div></div>
  </div>
  {_command_table(command_results)}
</div>

<!-- 5. Test Case Results -->
<div class="card">
  <h2>5. Test Case Results</h2>
  {_test_case_table(test_results)}
</div>

<!-- 6. Failure Classification -->
{_classification_section(classification) if cls_text else ""}

<!-- 7. Recommendations -->
{_recommendations(test_results, classification, mop_scenarios, command_results)}

<div class="footer">Generated by NetConfig QA Engine &mdash; {timestamp}</div>
</div>
</body>
</html>"""


def _e(t: str) -> str:
    return html_mod.escape(str(t)) if t else ""


def _sb(status: str) -> str:
    c = {"PASS": "b-pass", "FAIL": "b-fail"}.get(status, "b-skip")
    return f'<span class="badge {c}">{status}</span>'


def _sv(severity: str) -> str:
    c = {"critical": "b-crit", "major": "b-major", "minor": "b-minor"}.get(severity, "b-minor")
    return f'<span class="badge {c}">{severity.upper()}</span>'


def _mop_table(scenarios: list[MopScenario]) -> str:
    if not scenarios:
        return "<p style='color:#94a3b8'>No MOP scenarios</p>"
    rows = "".join(
        f"<tr><td>{_e(s.name)}</td><td>{_sb(s.status)}</td><td>{_e(s.error[:100])}</td></tr>"
        for s in scenarios
    )
    return f"<table><tr><th>Scenario</th><th>Status</th><th>Error</th></tr>{rows}</table>"


def _command_table(commands: list[CommandResult]) -> str:
    if not commands:
        return "<p style='color:#94a3b8'>No execution results</p>"
    rows = []
    for c in commands:
        cs = _sb("PASS") if c.success else _sb("FAIL")
        vs = _sb(c.validation_status.upper()) if c.validation_status != "skipped" else '<span class="badge b-skip">SKIP</span>'
        detail = _e(c.failure_reason or c.validation_conclusion or "")[:80]
        rows.append(f"<tr><td>{_e(c.phase)}</td><td>{_e(c.node)}</td><td><code>{_e(c.command[:50])}</code></td><td>{cs}</td><td>{vs}</td><td style='font-size:0.72rem;color:#64748b'>{detail}</td></tr>")
    return f"<table><tr><th>Phase</th><th>Node</th><th>Command</th><th>Cmd</th><th>Val</th><th>Detail</th></tr>{''.join(rows)}</table>"


def _test_case_table(results: list[TestCaseResult]) -> str:
    if not results:
        return "<p style='color:#94a3b8'>No test cases</p>"
    rows = []
    for r in results:
        errs = "<br>".join(_e(e[:80]) for e in r.errors[:3])
        if len(r.errors) > 3:
            errs += f"<br>... +{len(r.errors) - 3} more"
        actual = _e(r.actual_outcome)
        if errs:
            actual += f"<div style='font-size:0.7rem;color:#94a3b8;margin-top:2px'>{errs}</div>"
        rows.append(
            f"<tr><td><strong>{_e(r.id)}</strong></td><td>{_sb(r.status)}</td><td>{_sv(r.severity)}</td>"
            f"<td>{_e(r.category)}</td><td>{_e(r.phase)}</td><td>{_e(r.description)}</td>"
            f"<td style='font-size:0.78rem'>{actual}</td></tr>"
        )
    return f"<table><tr><th>ID</th><th>Status</th><th>Sev</th><th>Category</th><th>Phase</th><th>Description</th><th>Result</th></tr>{''.join(rows)}</table>"


def _classification_section(cls: dict) -> str:
    if not cls.get("classification"):
        return ""
    wf = cls.get("workflow_issues", [])
    sim = cls.get("simulator_issues", [])
    target = cls.get("retry_target", "?")
    return (
        f'<div class="card"><h2>6. Failure Classification</h2>'
        f'<p>Primary failure source: <strong>{_e(cls["classification"]).upper()}</strong> '
        f'&mdash; suggested retry target: Step {target}</p>'
        f'<p>Workflow issues ({len(wf)}): {", ".join(_e(w) for w in wf[:5]) or "none"}</p>'
        f'<p>Simulator issues ({len(sim)}): {", ".join(_e(s) for s in sim[:5]) or "none"}</p>'
        f'</div>'
    )


def _recommendations(results, cls, mop_scenarios, cmd_results) -> str:
    recs = []
    critical = [r for r in results if r.status == "FAIL" and r.severity == "critical"]
    if critical:
        recs.append(("crit", f"{len(critical)} critical test failure(s). Fix before production."))

    mop_fails = [s for s in mop_scenarios if s.status == "FAIL"]
    if mop_fails:
        recs.append(("crit", f"MOP generation failed for {len(mop_fails)} scenario(s). The workflow YAML likely has structural issues the JAR cannot parse."))

    if cls.get("classification") == "simulator":
        recs.append(("rec", "Failures are primarily simulator-related. Regenerate simulator config with corrected command-response mappings."))
    elif cls.get("classification") == "workflow":
        recs.append(("rec", "Failures are primarily workflow-related. Regenerate workflow YAML with reviewer feedback."))

    cmd_fails = [c for c in cmd_results if not c.success or c.validation_status == "failed"]
    if cmd_fails and not mop_fails:
        recs.append(("rec", f"{len(cmd_fails)} command(s) failed during execution. Check validation criteria expressions and simulator responses."))

    if not any(r.status == "FAIL" for r in results):
        recs.append(("pass", "All tests passed. Workflow and simulator configurations are ready for production."))

    items = []
    for cls_name, text in recs:
        css = "rec crit" if cls_name == "crit" else "rec"
        items.append(f'<div class="{css}">{_e(text)}</div>')
    return f'<div class="card"><h2>7. Recommendations</h2>{"".join(items)}</div>'
