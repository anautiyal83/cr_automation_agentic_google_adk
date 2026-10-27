"""HTML report generation for requirements summary and questionnaire."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

_CSS = """\
* { box-sizing: border-box; }
body { font-family: 'Segoe UI', sans-serif; font-size: 13px; line-height: 1.5;
  max-width: 960px; margin: 30px auto; color: #e2e4ed; background: #0f1117; padding: 20px; }
h1 { font-size: 1.4em; color: #6c8aff; border-bottom: 2px solid #2e3348; padding-bottom: 8px; margin-bottom: 16px; }
h2 { font-size: 1.1em; color: #38bdf8; margin-top: 24px; margin-bottom: 8px; }
h3 { font-size: 1em; color: #a78bfa; margin-top: 12px; }
p { margin: 4px 0; }
table { width: 100%; border-collapse: collapse; margin: 8px 0; font-size: 0.92em; }
th, td { text-align: left; padding: 6px 10px; border: 1px solid #2e3348; }
th { background: #1a1d27; color: #8b8fa8; font-size: 0.85em; text-transform: uppercase; }
td { background: #242836; vertical-align: top; }
code { background: #2a2d3e; padding: 1px 5px; border-radius: 3px; font-size: 0.92em; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.78em; font-weight: 600; }
.badge-complete { background: #1a3a2a; color: #22c55e; }
.badge-partial { background: #3d2f10; color: #f59e0b; }
.badge-insufficient { background: #2e1a1a; color: #ef4444; }
.badge-critical { background: #2e1a1a; color: #ef4444; }
.badge-cat { background: #1e2a40; color: #38bdf8; }
.section { background: #1a1d27; border: 1px solid #2e3348; border-radius: 8px; padding: 14px; margin: 8px 0; }
pre { background: #242836; padding: 10px; border-radius: 6px; overflow-x: auto;
  font-size: 0.85em; white-space: pre-wrap; word-break: break-word; max-height: 300px; }
.meta { color: #8b8fa8; font-size: 0.85em; }
.gap-item { background: #2e1a1a; border: 1px solid #5c2020; border-radius: 4px;
  padding: 6px 10px; margin: 4px 0; color: #fca5a5; font-size: 0.9em; }
.answer-row td:first-child { width: 30%; font-weight: 600; color: #a78bfa; }
.context { color: #8b8fa8; font-size: 0.85em; margin-top: 4px; }
.default { color: #6c8aff; font-family: monospace; font-size: 0.9em; }
"""


def _v(d: Any, *keys: str, fallback: str = "\u2014") -> Any:
    """Safe nested dict access with fallback."""
    for k in keys:
        if isinstance(d, dict):
            d = d.get(k)
        else:
            return fallback
    return d if d is not None and d != "" else fallback


def generate_summary_html(
    uc_name: str,
    ciq_sheets: dict[str, dict],
    analysis: dict,
    requirements: dict,
    developer_notes: str,
    answers: dict,
    doc_extracted: dict,
) -> str:
    """Generate a human-readable HTML requirements summary document.

    Returns the full HTML string.
    """
    cat1_req = requirements.get("category_1_node_activity", {})
    cat2_req = requirements.get("category_2_commands", {})
    cat3_req = requirements.get("category_3_orchestration", {})
    cat1_an = analysis.get("category_1_node_activity", {})
    cat2_an = analysis.get("category_2_commands", {})
    cat3_an = analysis.get("category_3_orchestration", {})

    readiness = analysis.get("overall_readiness", "INSUFFICIENT")
    badge_cls = {"COMPLETE": "badge-complete", "PARTIAL": "badge-partial"}.get(readiness, "badge-insufficient")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    lines = [
        "<!DOCTYPE html>",
        f"<html><head><meta charset='utf-8'><title>Requirements Summary \u2014 {uc_name}</title>",
        f"<style>{_CSS}</style></head><body>",
        f"<h1>Requirements Summary \u2014 {uc_name}</h1>",
        f"<p class='meta'>Generated: {now} | Answers: {len(answers)} | Readiness: "
        f"<span class='badge {badge_cls}'>{readiness}</span></p>",
    ]

    # -- CIQ Structure --
    lines.append("<h2>CIQ Structure</h2>")
    if ciq_sheets:
        lines.append("<div class='section'><table><tr><th>Sheet</th><th>Columns</th><th>Rows</th></tr>")
        for sname, sdata in ciq_sheets.items():
            headers = sdata.get("columns", sdata.get("headers", []))
            row_count = sdata.get("row_count", "\u2014")
            lines.append(
                f"<tr><td><strong>{sname}</strong></td>"
                f"<td>{', '.join(str(h) for h in headers)}</td><td>{row_count}</td></tr>"
            )
        lines.append("</table></div>")
    else:
        lines.append("<div class='section'><p>No CIQ structure available</p></div>")

    # -- Nodes --
    lines.append("<h2>Nodes</h2><div class='section'>")
    nodes = cat1_req.get("nodes", []) or cat1_an.get("nodes_detected", [])
    if nodes:
        lines.append(
            "<table><tr><th>Node</th><th>Role</th><th>SSH User</th>"
            "<th>Prompt</th><th>Escalation</th></tr>"
        )
        for n in nodes:
            if not isinstance(n, dict):
                continue
            esc = n.get("escalation", {})
            lines.append(
                f"<tr><td><strong>{_v(n, 'name')}</strong></td>"
                f"<td>{_v(n, 'role')}</td>"
                f"<td><code>{_v(n, 'ssh', 'user')}</code></td>"
                f"<td><code>{_v(n, 'prompt_regex')}</code></td>"
                f"<td><code>{_v(esc, 'method')}</code> \u2192 <code>{_v(esc, 'prompt_regex')}</code></td></tr>"
            )
        lines.append("</table>")

        # Login workflow
        lines.append("<h3>Login Workflow</h3>")
        niam_failover = cat1_req.get("niam_failover", False)
        niam_cols = cat1_req.get("niam_columns", [])
        for n in nodes:
            if not isinstance(n, dict):
                continue
            name = _v(n, "name")
            ssh_user = _v(n, "ssh", "user")
            ssh_host = _v(n, "ssh", "host")
            prompt = _v(n, "prompt_regex")
            esc = n.get("escalation", {})
            esc_method = _v(esc, "method")
            esc_prompt = _v(esc, "prompt_regex")

            steps = [f"SSH to <code>{ssh_host}</code> as <code>{ssh_user}</code>"]
            if niam_failover and niam_cols:
                steps[0] += f" (NIAM failover: try {' \u2192 '.join(niam_cols)} in order)"
            steps.append(f"Wait for prompt: <code>{prompt}</code>")
            if esc_method and esc_method != "\u2014":
                steps.append(f"Escalate: <code>{esc_method}</code> \u2192 wait for <code>{esc_prompt}</code>")
            lines.append(f"<p><strong>{name}:</strong> " + " \u2192 ".join(steps) + "</p>")
    else:
        lines.append("<p>No nodes detected</p>")

    lines.append(
        f"<p>Activity type: <strong>{_v(cat1_req, 'activity_type')}</strong> | "
        f"Data sheet: <strong>{_v(cat1_req, 'data_sheet')}</strong></p>"
    )
    lines.append("</div>")

    # -- MOP Commands --
    mop_cmds = doc_extracted.get("mop_commands", [])
    llm_cmds = cat2_req.get("commands", []) or cat2_an.get("commands_found", [])

    lines.append("<h2>MOP Commands</h2><div class='section'>")
    if mop_cmds:
        lines.append(f"<p class='meta'>Extracted directly from MOP document \u2014 {len(mop_cmds)} commands</p>")
        by_phase: dict[str, list] = {}
        for mc in mop_cmds:
            phase = mc.get("phase", "unknown") if isinstance(mc, dict) else "unknown"
            by_phase.setdefault(phase, []).append(mc)

        for phase, phase_cmds in by_phase.items():
            lines.append(f"<h3>{phase}</h3>")
            lines.append(
                "<table><tr><th>#</th><th>Command</th><th>Node</th>"
                "<th>User</th><th>Description / Validation</th></tr>"
            )
            for i, mc in enumerate(phase_cmds, 1):
                if not isinstance(mc, dict):
                    continue
                desc = mc.get("description", "") or mc.get("validation", "")
                lines.append(
                    f"<tr><td>{i}</td>"
                    f"<td><code>{mc.get('command', '\u2014')}</code></td>"
                    f"<td>{mc.get('node', '\u2014')}</td>"
                    f"<td>{mc.get('user', '\u2014')}</td>"
                    f"<td>{desc}</td></tr>"
                )
            lines.append("</table>")
    elif llm_cmds:
        lines.append(f"<p class='meta'>From LLM analysis \u2014 {len(llm_cmds)} commands</p>")
        lines.append(
            "<table><tr><th>Command</th><th>Node</th><th>Purpose</th>"
            "<th>Validation Criteria</th></tr>"
        )
        for c in llm_cmds:
            if not isinstance(c, dict):
                continue
            cmd_name = _v(c, "cmd", fallback=_v(c, "command"))
            purpose_raw = _v(c, "purpose")
            purpose = purpose_raw.replace("_", " ").title() if purpose_raw != "\u2014" else "\u2014"
            lines.append(
                f"<tr><td><code>{cmd_name}</code></td>"
                f"<td>{_v(c, 'node')}</td>"
                f"<td>{purpose}</td>"
                f"<td>{_v(c, 'validation_criteria')}</td></tr>"
            )
        lines.append("</table>")
    else:
        lines.append("<p>No commands extracted</p>")

    ldap = cat2_req.get("ldap", {})
    if ldap:
        lines.append(
            f"<p>LDAP: <code>{_v(ldap, 'host')}:{_v(ldap, 'port')}</code> | "
            f"Bind DN: <code>{_v(ldap, 'bind_dn')}</code></p>"
        )
    lines.append("</div>")

    # -- Orchestration --
    lines.append("<h2>Orchestration</h2><div class='section'>")
    grouping = cat3_req.get("grouping", {})
    lines.append(
        f"<p>Grouping: <strong>{_v(grouping, 'column', fallback=_v(cat3_an, 'grouping_column'))}</strong> | "
        f"NIAM failover: <strong>{_v(cat3_req, 'niam_failover', fallback=_v(cat3_an, 'niam_failover_needed'))}</strong></p>"
    )

    phases = cat3_req.get("phases", [])
    if phases:
        lines.append(f"<p>Phases: <strong>{' \u2192 '.join(str(p) for p in phases)}</strong></p>")

    ssh = cat3_req.get("ssh", {})
    if ssh:
        lines.append(
            f"<p>SSH timeout: {_v(ssh, 'connect_timeout_sec', fallback=_v(ssh, 'connect_timeout'))}s | "
            f"KEX: <code>{_v(ssh, 'kex_algorithms', fallback='default')}</code></p>"
        )
    lines.append("</div>")

    # -- SSH Prompts & Credentials --
    ssh_prompts = doc_extracted.get("ssh_prompts", doc_extracted.get("prompts", []))
    ssh_creds = doc_extracted.get("ssh_credentials", doc_extracted.get("credentials", []))
    if ssh_prompts or ssh_creds:
        lines.append("<h2>SSH Details (from session logs)</h2><div class='section'>")
        if ssh_prompts:
            lines.append("<p><strong>Prompts found:</strong></p><ul>")
            for p in ssh_prompts:
                lines.append(f"<li><code>{p}</code></li>")
            lines.append("</ul>")
        if ssh_creds:
            lines.append("<p><strong>LDAP credentials found:</strong></p><ul>")
            for cr in ssh_creds:
                bind_dn = cr.get("bind_dn", "?") if isinstance(cr, dict) else "?"
                lines.append(
                    f"<li>Bind DN: <code>{bind_dn}</code> | "
                    f"Port: <code>{doc_extracted.get('ldap_port', '?')}</code></li>"
                )
            lines.append("</ul>")
        lines.append("</div>")

    # -- XML Samples --
    xml_files = doc_extracted.get("xml_files", [])
    if xml_files:
        lines.append("<h2>XML Samples (from uploaded files)</h2><div class='section'>")
        for i, xf in enumerate(xml_files, 1):
            if not isinstance(xf, dict):
                continue
            fields = xf.get("fields", {})
            if fields:
                lines.append(f"<p><strong>Sample {i}:</strong> IMSI={xf.get('imsi_example', '?')}</p>")
                lines.append("<table><tr><th>XML Tag</th><th>Sample Value</th></tr>")
                for tag, val in fields.items():
                    lines.append(f"<tr><td><code>{tag}</code></td><td>{val}</td></tr>")
                lines.append("</table>")
            else:
                content = xf.get("content", "")[:500]
                lines.append(f"<p><strong>Sample {i}:</strong></p><pre>{content}</pre>")
        lines.append("</div>")

    # -- Developer Answers --
    if answers:
        lines.append("<h2>Developer Answers</h2><div class='section'><table class='answer-row'>")
        for k, v in answers.items():
            display_key = k.replace("_", " ").replace("q ", "Q")
            val_str = str(v) if not isinstance(v, bool) else ("Yes" if v else "No")
            lines.append(f"<tr><td>{display_key}</td><td>{val_str}</td></tr>")
        lines.append("</table></div>")

    # -- Remaining Gaps --
    all_gaps = []
    for cat_key in ("category_1_node_activity", "category_2_commands", "category_3_orchestration"):
        cat_label = cat_key.replace("category_", "").replace("_", " ").title()
        for gap in analysis.get(cat_key, {}).get("gaps", []):
            all_gaps.append((cat_label, gap))

    lines.append("<h2>Remaining Gaps</h2>")
    if all_gaps:
        lines.append("<div class='section'>")
        for cat_label, gap in all_gaps:
            lines.append(f"<div class='gap-item'><strong>{cat_label}:</strong> {gap}</div>")
        lines.append("</div>")
    else:
        lines.append("<div class='section'><p style='color:#22c55e'>No remaining gaps \u2014 all requirements gathered.</p></div>")

    # -- Developer Notes --
    if developer_notes:
        lines.append("<h2>Developer Notes</h2>")
        lines.append(f"<div class='section'><pre>{developer_notes}</pre></div>")

    lines.append("</body></html>")
    return "\n".join(lines)


def generate_questionnaire_html(
    uc_name: str,
    questions: list[dict],
    round_num: int,
) -> str:
    """Generate an HTML document showing the questionnaire for developer review.

    Returns the full HTML string.
    """
    cat_labels = {
        "node_activity": "Node & Activity",
        "commands": "Commands & Parameters",
        "orchestration": "Orchestration Details",
    }
    imp_badges = {
        "critical": ("badge-critical", "CRITICAL"),
        "recommended": ("badge-partial", "RECOMMENDED"),
        "optional": ("badge-cat", "OPTIONAL"),
    }

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        "<!DOCTYPE html>",
        f"<html><head><meta charset='utf-8'><title>Requirements Questionnaire \u2014 {uc_name}</title>",
        f"<style>{_CSS}</style></head><body>",
        f"<h1>Requirements Questionnaire \u2014 Round {round_num}</h1>",
        f"<p class='meta'>UC: <strong>{uc_name}</strong> | "
        f"Generated: {now} | Questions: <strong>{len(questions)}</strong></p>",
        "<p>The pipeline needs the following information to generate production-quality YAML. "
        "Answer in the UI or provide via Developer Notes.</p>",
    ]

    by_cat: dict[str, list] = {}
    for q in questions:
        if isinstance(q, dict):
            by_cat.setdefault(q.get("category", "other"), []).append(q)

    for cat, cat_questions in by_cat.items():
        label = cat_labels.get(cat, cat.replace("_", " ").title())
        lines.append(f"<h2>{label}</h2><div class='section'>")
        lines.append("<table><tr><th>#</th><th>Question</th><th>Importance</th><th>Default</th></tr>")
        for i, q in enumerate(cat_questions, 1):
            imp = q.get("importance", "optional")
            badge_cls, badge_text = imp_badges.get(imp, ("badge-cat", imp.upper()))
            default = q.get("default_value", "\u2014")
            context = q.get("context", "")
            lines.append(
                f"<tr><td>{q.get('id', i)}</td>"
                f"<td><strong>{q.get('question', '?')}</strong>"
            )
            if context:
                lines.append(f"<div class='context'>{context}</div>")
            lines.append(
                f"</td><td><span class='badge {badge_cls}'>{badge_text}</span></td>"
                f"<td><span class='default'>{default}</span></td></tr>"
            )
        lines.append("</table></div>")

    critical_count = sum(1 for q in questions if isinstance(q, dict) and q.get("importance") == "critical")
    lines.append(
        f"<p class='meta'>Total: {len(questions)} questions | Critical: {critical_count} | "
        "Use 'Continue' to accept defaults, or answer in the UI.</p>"
    )
    lines.append("</body></html>")
    return "\n".join(lines)
