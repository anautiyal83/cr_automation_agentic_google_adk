"""Step 9 test-case report: scenarios, expected vs actual, evidence (spec 005)."""
from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path

from .scenarios import ScenarioResult, SuiteResult

_CSS = """
:root{--bg:#F1F5F9;--card:#fff;--text:#1E293B;--dim:#64748B;--border:#E2E8F0;--ok:#16A34A;--bad:#DC2626;
--accent:#4F46E5;--soft:#F8FAFC}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font:13px/1.45 Inter,Segoe UI,system-ui,sans-serif}
.wrap{max-width:1280px;margin:0 auto;padding:24px}
h1{font-size:20px;margin:0}h2{font-size:14px;margin:22px 0 10px}
.sub{color:var(--dim);margin-top:2px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin-top:16px}
.kpi{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:12px 14px}
.kpi b{display:block;font-size:20px}.kpi span{color:var(--dim);font-size:11px}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:14px;margin-top:12px}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--border);
vertical-align:top}th{font-size:11px;color:var(--dim);background:var(--soft);font-weight:600}
.pill{display:inline-block;padding:2px 10px;border-radius:999px;font-size:11px;font-weight:600}
.PASS,.ok{background:rgba(22,163,74,.12);color:var(--ok)}.FAIL,.bad{background:rgba(220,38,38,.12);color:var(--bad)}
.skip{background:rgba(148,163,184,.18);color:var(--dim)}
details{background:var(--card);border:1px solid var(--border);border-radius:12px;margin-top:10px}
summary{cursor:pointer;padding:12px 14px;display:flex;gap:10px;align-items:center;font-weight:600}
details .body{padding:0 14px 14px}
.mono{font-family:Consolas,Menlo,monospace;font-size:12px;word-break:break-word}
pre{background:var(--soft);border:1px solid var(--border);border-radius:8px;padding:8px;white-space:pre-wrap;
max-height:220px;overflow:auto;margin:4px 0}
.dim{color:var(--dim)}.verdict{font-size:12px;padding:4px 12px}
"""


def generate_scenario_report(uc_name: str, suite: SuiteResult, duration_sec: float = 0.0) -> str:
    results = suite.results
    verdict = "PASS" if results and suite.failed == 0 else "FAIL"
    positives = [r for r in results if r.scenario.kind == "positive"]
    negatives = [r for r in results if r.scenario.kind == "negative"]
    rollbacks = [r for r in results if r.scenario.kind == "rollback"]
    parts = [
        f"<!doctype html><html><head><meta charset='utf-8'><title>{escape(uc_name)} — Test Case Report</title>",
        f"<style>{_CSS}</style></head><body><div class='wrap'>",
        f"<div style='display:flex;justify-content:space-between;align-items:flex-end;gap:12px'>"
        f"<div><h1>Execution Test Case Report</h1><div class='sub'>{escape(uc_name)} · "
        f"{datetime.now():%Y-%m-%d %H:%M} · workflow executed by cli-automation against the simulator</div></div>"
        f"<span class='pill verdict {verdict}'>{verdict}</span></div>",
        "<div class='kpis'>",
        _kpi(len(results), "Test cases"), _kpi(suite.passed, "Passed"), _kpi(suite.failed, "Failed"),
        _kpi(len(positives), "Sunny day"), _kpi(len(rollbacks), "Rollback only"), _kpi(len(negatives), "Negative"),
        _kpi(f"{duration_sec:.0f}s", "Duration"),
        "</div>",
    ]
    if suite.notes:
        parts.append("<div class='card'><b>Notes</b><ul>"
                     + "".join(f"<li>{escape(n)}</li>" for n in suite.notes) + "</ul></div>")
    if suite.classification:
        c = suite.classification
        parts.append(f"<div class='card'><b>Failure routing:</b> {escape(c.get('classification', ''))} "
                     f"→ retry step {escape(str(c.get('retry_target', '')))}</div>")

    parts.append("<h2>Test cases</h2><div class='card' style='padding:0;overflow:auto'><table>"
                 "<tr><th>ID</th><th>Scenario</th><th>CR / node group</th><th>Expected</th>"
                 "<th>Actual</th><th>Result</th></tr>")
    for r in results:
        s = r.scenario
        parts.append(f"<tr><td class='mono'>{escape(s.id)}</td><td>{escape(s.title)}</td>"
                     f"<td>{escape(s.cr_group)} / {escape(s.node_group)}</td><td>{escape(s.expected)}</td>"
                     f"<td>{escape(r.actual)}</td><td><span class='pill {r.status}'>{r.status}</span></td></tr>")
    parts.append("</table></div><h2>Details</h2>")
    parts += [_details(r) for r in results]
    parts.append("</div></body></html>")
    return "".join(parts)


def _kpi(value, label: str) -> str:
    return f"<div class='kpi'><b>{escape(str(value))}</b><span>{escape(label)}</span></div>"


def _details(r: ScenarioResult) -> str:
    s = r.scenario
    out = [f"<details{' open' if r.status == 'FAIL' else ''}><summary>"
           f"<span class='pill {r.status}'>{r.status}</span><span class='mono'>{escape(s.id)}</span>"
           f"<span>{escape(s.title)}</span><span class='dim' style='margin-left:auto'>{r.duration_sec:.1f}s</span>"
           "</summary><div class='body'>"]
    if s.fault:
        out.append(f"<p><b>Injected fault:</b> {escape(s.fault.description)}</p>")
        if s.fault.lines:
            out.append("<pre class='mono'>" + escape("\n".join(s.fault.lines)) + "</pre>")
    if r.error:
        out.append("<p><b>Error:</b></p><pre class='mono'>" + escape(r.error) + "</pre>")
    if r.html_reports:
        out.append("<p><b>Execution report:</b> " + ", ".join(
            f"<span class='mono'>exec_report/{escape(Path(f).name)}</span>" for f in r.html_reports) + "</p>")
    out.append("<table><tr><th>Check</th><th>Result</th><th>Detail</th></tr>")
    for c in r.checks:
        out.append(f"<tr><td>{escape(c.description)}</td><td><span class='pill {'ok' if c.ok else 'bad'}'>"
                   f"{'OK' if c.ok else 'FAILED'}</span></td><td class='mono'>{escape(c.detail)}</td></tr>")
    out.append("</table>")
    if r.commands:
        out.append("<p><b>Steps executed</b> <span class='dim'>(execution report)</span></p>"
                   "<table><tr><th>#</th><th>Node</th><th>Phase</th><th>Command</th><th>Validation</th></tr>")
        for i, c in enumerate(r.commands, 1):
            state = "FAIL" if (not c.success or c.validation_status == "failed") else \
                    ("PASS" if c.validation_status == "success" else "skip")
            label = "failed" if state == "FAIL" else c.validation_status
            reason = c.validation_conclusion if state == "FAIL" else ""
            out.append(f"<tr><td>{i}</td><td>{escape(c.node)}</td><td>{escape(c.phase)}</td>"
                       f"<td class='mono'>{escape(c.command[:200])}"
                       + (f"<pre>{escape(c.output[:1200])}</pre>" if state == "FAIL" and c.output else "")
                       + f"</td><td><span class='pill {state}'>{escape(label)}</span>"
                       f"<div class='dim'>{escape(reason)}</div></td></tr>")
        out.append("</table>")
    received = [e for e in r.events if not str(e.get("source", "")).startswith(("builtin:stty", "ctrl"))]
    if received:
        out.append("<p><b>Commands received by the simulator</b></p><table><tr><th>Node</th><th>Matched by</th>"
                   "<th>Command</th></tr>")
        for e in received[:200]:
            src = str(e.get("source", ""))
            out.append(f"<tr><td>{escape(str(e.get('instance', '')))}</td><td><span class='pill "
                       f"{'bad' if src == 'unmatched' else 'skip'}'>{escape(src)}</span></td>"
                       f"<td class='mono'>{escape(str(e.get('input', ''))[:200])}</td></tr>")
        out.append("</table>")
    out.append("</div></details>")
    return "".join(out)
