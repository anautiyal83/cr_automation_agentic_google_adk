"""Professional HTML pipeline report generation."""
from __future__ import annotations

import html as html_mod
from datetime import datetime, timezone
from pathlib import Path


def generate_pipeline_report(
    uc_name: str,
    step_results: list[dict],
    artifacts: dict,
    run_dir: str = "",
    requirements: dict | None = None,
    duration_sec: float = 0,
) -> str:
    """Generate a professional HTML pipeline report.

    Sections:
    1. Executive Summary — overall status, duration, step pass/fail counts
    2. Step Results — per-step status with error details
    3. Generated Artifacts — list of YAML/HTML/Excel files produced
    4. Requirements Summary — nodes, commands, phases from requirements
    5. Test Results — links to QA reports if they exist
    """
    total = len(step_results)
    passed = sum(1 for s in step_results if s.get("status") == "passed")
    failed = total - passed
    verdict = "PASSED" if failed == 0 else "FAILED"
    verdict_color = "#16A34A" if verdict == "PASSED" else "#DC2626"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    req = requirements or {}
    nodes = req.get("category_1_node_activity", {}).get("nodes", [])
    node_names = [n.get("name", "?") for n in nodes if isinstance(n, dict)]
    phases = req.get("category_3_orchestration", {}).get("phases", [])
    commands = req.get("category_2_commands", {}).get("commands", [])

    # Find artifact files on disk
    artifact_files = []
    if run_dir:
        rd = Path(run_dir)
        if rd.exists():
            for f in sorted(rd.iterdir()):
                if f.is_file() and f.suffix in (".yaml", ".html", ".json", ".xlsx", ".txt"):
                    artifact_files.append({"name": f.name, "size": f.stat().st_size, "path": str(f)})

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Pipeline Report - {_e(uc_name)}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: 'Segoe UI', -apple-system, sans-serif; background: #f8fafc; color: #1e293b; line-height: 1.5; }}
  .container {{ max-width: 1100px; margin: 0 auto; padding: 24px; }}
  .header {{ background: linear-gradient(135deg, #1e1b4b 0%, #312e81 100%); color: #fff; padding: 32px; border-radius: 12px; margin-bottom: 24px; }}
  .header h1 {{ font-size: 1.5rem; font-weight: 700; }}
  .header .sub {{ font-size: 0.85rem; color: rgba(255,255,255,0.6); }}
  .card {{ background: #fff; border: 1px solid #e2e8f0; border-radius: 10px; padding: 24px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.04); }}
  .card h2 {{ font-size: 1.1rem; font-weight: 600; color: #334155; margin-bottom: 16px; border-bottom: 2px solid #e2e8f0; padding-bottom: 8px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 16px; }}
  .metric {{ text-align: center; padding: 14px; background: #f8fafc; border-radius: 8px; }}
  .metric .val {{ font-size: 1.6rem; font-weight: 700; }}
  .metric .lbl {{ font-size: 0.7rem; color: #64748b; text-transform: uppercase; }}
  .verdict {{ display: inline-block; padding: 4px 16px; border-radius: 16px; font-weight: 700; font-size: 0.85rem; color: #fff; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
  th {{ background: #f1f5f9; padding: 10px 12px; text-align: left; font-weight: 600; color: #475569; border-bottom: 2px solid #e2e8f0; }}
  td {{ padding: 8px 12px; border-bottom: 1px solid #f1f5f9; }}
  tr:hover {{ background: #fafbfd; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 0.7rem; font-weight: 600; }}
  .b-pass {{ background: #dcfce7; color: #166534; }}
  .b-fail {{ background: #fce4ec; color: #991b1b; }}
  .footer {{ text-align: center; font-size: 0.72rem; color: #94a3b8; margin-top: 24px; padding-top: 12px; border-top: 1px solid #e2e8f0; }}
</style>
</head>
<body>
<div class="container">

<div class="header">
  <h1>NetConfig Pipeline Report</h1>
  <div class="sub">{_e(uc_name)} &mdash; {timestamp}</div>
</div>

<!-- 1. Executive Summary -->
<div class="card">
  <h2>1. Executive Summary</h2>
  <div class="grid">
    <div class="metric"><div class="val"><span class="verdict" style="background:{verdict_color}">{verdict}</span></div><div class="lbl">Verdict</div></div>
    <div class="metric"><div class="val">{total}</div><div class="lbl">Steps</div></div>
    <div class="metric"><div class="val" style="color:#16A34A">{passed}</div><div class="lbl">Passed</div></div>
    <div class="metric"><div class="val" style="color:#DC2626">{failed}</div><div class="lbl">Failed</div></div>
    <div class="metric"><div class="val">{duration_sec:.0f}s</div><div class="lbl">Duration</div></div>
  </div>
</div>

<!-- 2. Step Results -->
<div class="card">
  <h2>2. Step Results</h2>
  <table>
    <tr><th>#</th><th>Step</th><th>Status</th><th>Error</th></tr>
    {_step_rows(step_results)}
  </table>
</div>

<!-- 3. Generated Artifacts -->
<div class="card">
  <h2>3. Generated Artifacts</h2>
  {_artifact_table(artifact_files) if artifact_files else '<p style="color:#94a3b8">No artifacts found</p>'}
</div>

<!-- 4. Requirements Summary -->
<div class="card">
  <h2>4. Requirements Summary</h2>
  <table>
    <tr><td style="font-weight:600;width:150px">Nodes</td><td>{_e(', '.join(node_names)) or 'None'}</td></tr>
    <tr><td style="font-weight:600">Phases</td><td>{_e(', '.join(phases)) or 'None'}</td></tr>
    <tr><td style="font-weight:600">Commands</td><td>{len(commands)} defined</td></tr>
    <tr><td style="font-weight:600">Activity Type</td><td>{_e(req.get('category_1_node_activity', {}).get('activity_type', 'N/A'))}</td></tr>
  </table>
</div>

<!-- 5. Test Reports -->
<div class="card">
  <h2>5. Test Reports</h2>
  {_test_report_links(artifact_files, uc_name)}
</div>

<div class="footer">Generated by NetConfig Pipeline &mdash; {timestamp}</div>
</div>
</body>
</html>"""


def _e(t: str) -> str:
    return html_mod.escape(str(t)) if t else ""


def _sb(status: str) -> str:
    cls = "b-pass" if status == "passed" else "b-fail"
    return f'<span class="badge {cls}">{status.upper()}</span>'


def _step_rows(results: list[dict]) -> str:
    rows = []
    for r in results:
        err = _e(r.get("error_message", ""))[:120]
        rows.append(
            f'<tr><td>{r.get("step_number", "?")}</td>'
            f'<td>{_e(r.get("step_name", ""))}</td>'
            f'<td>{_sb(r.get("status", "?"))}</td>'
            f'<td style="font-size:0.78rem;color:#64748b">{err}</td></tr>'
        )
    return "\n    ".join(rows)


def _artifact_table(files: list[dict]) -> str:
    rows = []
    for f in files:
        size = f["size"]
        size_str = f"{size:,} bytes" if size < 10240 else f"{size/1024:.1f} KB"
        rows.append(f'<tr><td><code>{_e(f["name"])}</code></td><td>{size_str}</td></tr>')
    return f'<table><tr><th>File</th><th>Size</th></tr>{"".join(rows)}</table>'


def _test_report_links(files: list[dict], uc_name: str) -> str:
    reports = [f for f in files if f["name"].endswith("-report.html") or f["name"].endswith("qa-report.html")]
    if not reports:
        return '<p style="color:#94a3b8">No QA reports generated</p>'
    items = [f'<li><code>{_e(f["name"])}</code></li>' for f in reports]
    return f'<ul style="padding-left:20px">{"".join(items)}</ul>'
