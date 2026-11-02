"""Step 8 simulator report (HTML) — milestone artifact for Simulator Setup.

Sections: 1. Summary, 2. Instances, 3. Sanity checks, 4. Issues & known limitations.
Styled like the other QA reports (network_config_test/report.py).
"""
from __future__ import annotations

import html
from datetime import datetime

# MockWire gaps that every run inherits (specs/004-simulator-setup/mockwire-gaps.md)
_KNOWN_LIMITATIONS = [
    ("MW-01", "NIAM root elevation (Ctrl+P, Ctrl+E) is not simulated: nodes log in on their root "
              "prompt and step 9 runs with elevate_to_root disabled."),
    ("MW-02", "Every simulated command exits with code 0; failure paths are not exercised."),
    ("MW-04", "Responses do not change after modifications (no pre/post state)."),
]


def _e(text) -> str:
    return html.escape(str(text if text is not None else ""))


def _badge(status: str) -> str:
    cls = {"PASS": "b-pass", "FAIL": "b-fail"}.get(status, "b-skip")
    return f'<span class="badge {cls}">{_e(status)}</span>'


def generate_simulator_report(
    uc_name: str,
    nodes: list,
    simulator=None,
    sanity=None,
    errors: list[str] | None = None,
    sim_dir: str = "",
) -> str:
    """Render the step 8 report from topology, running instances and sanity results."""
    errors = errors or []
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    verdict = "PASS" if not errors else "FAIL"
    verdict_color = "#16A34A" if verdict == "PASS" else "#DC2626"
    sanity_by_node = {nr.node: nr for nr in (sanity.node_results if sanity else [])}
    running = bool(simulator and simulator.is_running)

    instance_rows = []
    for n in nodes:
        port = simulator.port_for(n.name) if simulator else None
        inst = next((i for i in simulator.instances if i.name == n.instance), None) if simulator else None
        esc = "<br>".join(f"<code>{_e(k)}</code> &rarr; <code>{_e(v.rstrip())}</code>"
                          for k, v in n.escalations.items()) or "&mdash;"
        instance_rows.append(
            f"<tr><td><b>{_e(n.name)}</b></td><td>{_e(port or '—')}</td>"
            f"<td><code>{_e(n.prompt.rstrip())}</code></td><td>{_e(n.user)}</td>"
            f"<td>{len(n.ne_ids) or '—'}</td><td>{esc}</td><td>{len(n.commands)}</td>"
            f"<td>{'yes' if n.transfer else '—'}</td>"
            f"<td><small>{_e(inst.log_file if inst else '')}</small></td></tr>")

    sanity_rows = []
    transcripts = []
    for n in nodes:
        nr = sanity_by_node.get(n.name)
        if not nr:
            sanity_rows.append(f"<tr><td><b>{_e(n.name)}</b></td>" + "<td>—</td>" * 6
                               + "<td>not run</td></tr>")
            continue
        sanity_rows.append(
            f"<tr><td><b>{_e(nr.node)}</b></td><td>{_badge(nr.ssh_connect)}</td><td>{_badge(nr.niam)}</td>"
            f"<td>{_badge(nr.prompt_match)}</td><td>{_badge(nr.basic_command)}</td>"
            f"<td>{_badge(nr.escalation)}</td><td>{_badge(nr.uc_command)}</td>"
            f"<td>{'<br>'.join(_e(x) for x in nr.errors) or 'OK'}</td></tr>")
        if nr.transcript:
            transcripts.append(f"<details><summary>{_e(nr.node)} session transcript</summary>"
                               f"<pre>{_e(nr.transcript)}</pre></details>")

    warnings = [f"{_e(n.name)}: {_e(w)}" for n in nodes for w in n.warnings]
    issues = "".join(f'<div class="rec crit">{_e(e)}</div>' for e in errors)
    issues += "".join(f'<div class="rec">{w}</div>' for w in warnings)
    if not issues:
        issues = '<div class="rec ok">No issues — simulator ready for execution testing.</div>'
    limitations = "".join(f"<li><b>{k}</b> — {_e(v)}</li>" for k, v in _KNOWN_LIMITATIONS)

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Simulator Setup Report — {_e(uc_name)}</title>
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
  code {{ font-size: 0.78rem; background: #f1f5f9; padding: 1px 4px; border-radius: 4px; }}
  pre {{ font-size: 0.72rem; background: #0f172a; color: #e2e8f0; padding: 12px; border-radius: 8px; overflow-x: auto; white-space: pre-wrap; margin-top: 8px; }}
  details {{ margin-top: 10px; }} summary {{ cursor: pointer; font-weight: 600; color: #475569; font-size: 0.85rem; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 0.68rem; font-weight: 600; }}
  .b-pass {{ background: #dcfce7; color: #166534; }}
  .b-fail {{ background: #fce4ec; color: #991b1b; }}
  .b-skip {{ background: #f1f5f9; color: #64748b; }}
  .rec {{ padding: 10px; border-radius: 6px; margin-bottom: 8px; border-left: 3px solid #f59e0b; background: #fffbeb; font-size: 0.85rem; }}
  .rec.crit {{ border-left-color: #ef4444; background: #fef2f2; }}
  .rec.ok {{ border-left-color: #16a34a; background: #f0fdf4; }}
  ul {{ margin-left: 18px; font-size: 0.85rem; }}
  .footer {{ text-align: center; font-size: 0.72rem; color: #94a3b8; margin-top: 24px; padding-top: 12px; border-top: 1px solid #e2e8f0; }}
</style>
</head>
<body>
<div class="container">

<div class="header">
  <h1>Simulator Setup — Step 8 Report</h1>
  <div class="sub">{_e(uc_name)} &mdash; {timestamp}</div>
</div>

<div class="card">
  <h2>1. Summary</h2>
  <div class="grid">
    <div class="metric"><div class="val"><span class="verdict" style="background:{verdict_color}">{verdict}</span></div><div class="lbl">Verdict</div></div>
    <div class="metric"><div class="val">{len(nodes)}</div><div class="lbl">Simulated Nodes</div></div>
    <div class="metric"><div class="val">{'Running' if running else 'Stopped'}</div><div class="lbl">MockWire</div></div>
    <div class="metric"><div class="val" style="color:#16A34A">{sanity.nodes_passed if sanity else 0}</div><div class="lbl">Sanity Passed</div></div>
    <div class="metric"><div class="val" style="color:#DC2626">{sanity.nodes_failed if sanity else 0}</div><div class="lbl">Sanity Failed</div></div>
  </div>
  <p style="margin-top:12px;font-size:0.8rem;color:#64748b">Simulator folder: <code>{_e(sim_dir)}</code></p>
</div>

<div class="card">
  <h2>2. Instances</h2>
  <table>
    <tr><th>Node</th><th>Port</th><th>Login prompt</th><th>User</th><th>NIAM neIds</th><th>Escalation</th><th>Workflow cmds</th><th>Transfer</th><th>Log</th></tr>
    {''.join(instance_rows)}
  </table>
</div>

<div class="card">
  <h2>3. Sanity Checks (real SSH session per node)</h2>
  <table>
    <tr><th>Node</th><th>SSH</th><th>NIAM</th><th>Prompt</th><th>Command</th><th>Escalation</th><th>UC cmd</th><th>Details</th></tr>
    {''.join(sanity_rows)}
  </table>
  {''.join(transcripts)}
</div>

<div class="card">
  <h2>4. Issues &amp; Known Limitations</h2>
  {issues}
  <p style="margin:12px 0 6px;font-weight:600;font-size:0.85rem">Simulator limitations (MockWire gaps)</p>
  <ul>{limitations}</ul>
</div>

<div class="footer">NetConfig Pipeline &mdash; Step 8 Simulator Setup</div>
</div>
</body>
</html>
"""
