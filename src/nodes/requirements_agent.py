"""RequirementsGatherer Agent — pipeline gatekeeper that ensures complete
requirements before YAML generation begins.

Replaces Steps 1 (DocParser) and 2 (UCAnalyzer) with a single agent that:
1. Parses all input documents (CIQ, MOP, command outputs)
2. Analyzes UC type and identifies patterns
3. Assesses readiness across 3 categories
4. Asks developer targeted questions via HIL pause
5. Iterates until all Required + Critical items answered
6. Compiles structured requirements package for downstream steps

Three requirement categories:
  Category 1 — Node & Activity: node types, login approach, root escalation
  Category 2 — Commands & Parameters: CLI commands, CIQ param mapping, LDAP
  Category 3 — Orchestration: grouping, failover, paths, SSH, special handling
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from google.adk.agents import BaseAgent

from src.core.logging_config import get_flow_logger
from src.nodes.milestone_nodes import (
    milestone_events,
    milestone_decisions,
    milestone_waiting,
)

logger = logging.getLogger(__name__)
flow = get_flow_logger(__name__)

# Shared dict for requirements — read by step nodes via _get_step_data
requirements_store: dict[str, dict] = {}  # run_id → requirements package

# Cache of previous answers by UC name — reused on re-run if no new questions
_previous_answers: dict[str, dict] = {}  # uc_name → all_answers from last run

SYSTEM_PROMPT = """You are a Nokia telecom UC automation expert and cr-automation framework specialist.

Your job: Analyze input documents and identify ALL information needed to generate
production-quality workflow YAML, validation rules, and json-output templates.

DOMAIN EXPERTISE:
- Nokia node types: PGW (Packet Gateway), RDS (Remote Data Store), SBC (Session
  Border Controller), DSR (Diameter Signaling Router)
- NIAM login: SSH via M2M port, niamID-based node identification, failover across
  niamID-1/2/3 when multiple NIAM columns exist in CIQ
- LDAP provisioning: ldapsearch/ldapmodify on RDS nodes for subscriber management
- CIQ conventions: Index sheet (CRGroup, Zone/NodeGroup, Email), IP sheet
  (Node_Name, NIAM_NAME1/2/3), data sheets (UC-specific configuration)
- MOP patterns: pre-node-health-check, backup, activity, post-check, rollback

FRAMEWORK EXPERTISE:
- cli-automation YAML model: phases, steps, register (named capture groups),
  validation (criteria.expr), expect_reply, for_each loops
- ciq-processor json-output: tableKeys, _each directives, _row, WHERE clauses
- Node definitions: SSH config (host, port, user, auth, ne_id, kex_algorithms,
  connect_timeout_sec, elevate_to_root), prompt_regex
- Variable patterns: ${nodeGroups}, ${node_row.PGW.niamID-1}, ${configData}
"""

ANALYZE_PROMPT = """Analyze this UC's documents and identify what's available and what's missing.

UC Name: {uc_name}

=== CIQ STRUCTURE ===
{ciq_summary}

=== MOP CONTENT ===
{mop_summary}

=== COMMAND OUTPUTS ===
{cmd_summary}

=== DEVELOPER NOTES ===
{developer_notes}

Respond with a JSON object covering all 3 categories:

{{
  "uc_type": "capability_modification | license_loading | software_upgrade | config_change | other",
  "activity_type": "per_subscriber | per_node | bulk",

  "category_1_node_activity": {{
    "nodes_detected": [{{"name": "...", "type": "PGW|RDS|SBC|DSR", "role": "..."}}],
    "login_approach": "niam | direct_ssh | unknown",
    "niam_columns_count": 0,
    "root_escalation_detected": true/false,
    "root_method": "su_root | ctrl_p_e | sudo | unknown",
    "gaps": ["list of missing items"]
  }},

  "category_2_commands": {{
    "commands_found": [{{"command": "ldapsearch", "node": "RDS", "purpose": "...", "params_from_ciq": ["col1", "col2"]}}],
    "ldap_detected": true/false,
    "ldap_details_known": true/false,
    "gaps": ["list of missing items"]
  }},

  "category_3_orchestration": {{
    "grouping_column": "Zone | NodeGroup | ...",
    "niam_failover_needed": true/false,
    "backup_mentioned": true/false,
    "ssh_details_known": true/false,
    "gaps": ["list of missing items"]
  }},

  "overall_readiness": "COMPLETE | PARTIAL | INSUFFICIENT"
}}"""

QUESTIONS_PROMPT = """Based on this analysis, generate targeted questions for the developer.

Analysis:
{analysis_json}

Developer already provided:
{developer_notes}

Rules:
- Only ask about GAPS — do NOT ask about things already known
- Mark each question as critical, recommended, or optional
- Provide sensible defaults where possible
- Group by category

CRITICAL — DEFAULTS AND QUESTION STYLE:
- ALWAYS provide a default_value for every question — the user should be able to accept defaults and move on
- For boolean: default to the most common/safe choice
- For select: default to the most likely option
- For text: default to a sensible value from MOP/CIQ or common industry practice
- The goal is: user reviews defaults, changes only what's wrong, submits
- Write questions for a LAYMAN USER, not a developer
- Use simple everyday language, avoid technical jargon
- BAD:  "What kex_algorithms should be configured for SSH?"
  GOOD: "Which SSH encryption method should be used for connecting to the nodes?"
- BAD:  "Specify the LDAP bind DN and credentials"
  GOOD: "What are the login details for the subscriber database? (username and password)"
- BAD:  "Should NIAM failover be enabled across niamID-1/2/3?"
  GOOD: "The system found 3 login IDs per node. Should it try each one until it connects successfully?"
- Keep questions short (1 sentence) — put technical details in "context"
- Use "context" field to explain WHY this is needed and WHAT happens if skipped
- Use "helper_text" field to show an example of a valid answer
- For select/boolean, make the options self-explanatory

Respond with a JSON array:
[
  {{
    "id": "q1",
    "category": "node_activity | commands | orchestration",
    "question": "Simple, clear question in plain language",
    "importance": "critical | recommended | optional",
    "input_type": "text | boolean | select | password",
    "options": ["Option A — explanation", "Option B — explanation"],
    "default_value": "sensible default",
    "context": "Technical context: why this matters and what happens without it",
    "helper_text": "Example: enter something like 'cn=adminUser' or '192.168.1.1'"
  }}
]

Only include questions for actual gaps. If developer notes already cover an item, skip it."""

COMPILE_PROMPT = """Compile the final requirements package for YAML generation.

UC: {uc_name}
Analysis: {analysis_json}
Developer Notes: {developer_notes}
Developer Answers: {answers_json}

Produce a structured requirements package as JSON:

{{
  "category_1_node_activity": {{
    "nodes": [
      {{"name": "niam_pgw_1", "type": "remote", "role": "PGW",
        "ne_id": "${{node_row.PGW.niamID-1}}", "ssh": {{...}} }}
    ],
    "niam_failover": true/false,
    "root_escalation": {{"method": "su - root", "password": "...", "prompt": "Password:"}},
    "activity_type": "per_subscriber",
    "data_sheet": "Profile Modification"
  }},
  "category_2_commands": {{
    "commands": [
      {{"cmd": "ldapsearch", "node": "RDS", "purpose": "query subscriber",
        "syntax": "ldapsearch -x -h localhost -p 16611 ...",
        "ciq_params": ["Test IMSI"], "register": ["profileName"]}}
    ],
    "ldap": {{"host": "localhost", "port": 16611, "bind_dn": "...", "password": "..."}}
  }},
  "category_3_orchestration": {{
    "grouping": {{"column": "Zone", "label": "Zone"}},
    "niam_failover": true,
    "ssh": {{"kex_algorithms": "...", "connect_timeout": 30}},
    "paths": {{"local": "/mnt/shared_data/${{CHILD_REQ_ID}}/${{NODE}}"}},
    "phases": ["preNodeHealthCheck", "backup", "activity", "postNodeHealthCheck", "rollback"]
  }},
  "generation_hints": {{
    "step_3": "specific instructions for validation rules generation",
    "step_4": "specific instructions for json-output generation",
    "step_6": "specific instructions for workflow generation",
    "step_8": "specific instructions for simulator setup"
  }}
}}

Be concrete — use actual values from the answers, not placeholders."""


class RequirementsGathererNode(BaseAgent):
    """Gatekeeper agent that gathers complete requirements before YAML generation.

    Replaces Steps 1 (DocParser) and 2 (UCAnalyzer).
    Blocks pipeline until readiness = COMPLETE.
    """
    name: str = "requirements_gatherer"
    description: str = "Parse documents, analyze UC, gather developer requirements"

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(self, ctx):
        run_id = ctx.session.state.get("run_id", "")
        run_dir = ctx.session.state.get("run_dir", "")
        uc_name = ctx.session.state.get("uc_name", "")
        developer_notes = ctx.session.state.get("document_set", {}).get("developer_notes", "") or ""

        flow.flow("REQUIREMENTS START — run_id=%s, uc=%s", run_id[:8], uc_name)

        # ── Phase 1: Parse documents ──
        flow.flow("REQUIREMENTS Phase 1: Parsing documents")
        parsed = await self._parse_documents(ctx)

        # Store parsed data in session state for downstream steps
        if parsed.get("ciq_data"):
            ctx.session.state["ciq_data"] = parsed["ciq_data"]
        if parsed.get("mop_data"):
            ctx.session.state["mop_data"] = parsed["mop_data"]
        if parsed.get("intermediate_repr"):
            ctx.session.state["intermediate_repr"] = parsed["intermediate_repr"]
        if parsed.get("command_outputs"):
            ctx.session.state["command_outputs"] = parsed["command_outputs"]
        if parsed.get("constraints"):
            ctx.session.state["constraints"] = parsed["constraints"]

        # ── Phase 2: Analyze UC type ──
        flow.flow("REQUIREMENTS Phase 2: Analyzing UC type")
        analysis = await self._analyze_uc(parsed, uc_name, developer_notes)

        readiness = analysis.get("overall_readiness", "INSUFFICIENT")
        flow.flow("REQUIREMENTS Phase 2 result: readiness=%s", readiness)

        # ── Phase 3: Gather missing requirements (iterate) ──
        # Pre-populate with answers from previous run of same UC (if any)
        all_answers = dict(_previous_answers.get(uc_name, {}))
        if all_answers:
            flow.flow("REQUIREMENTS: Loaded %d previous answers for UC %s", len(all_answers), uc_name)
        round_num = 0
        max_rounds = 3

        while readiness != "COMPLETE" and round_num < max_rounds:
            round_num += 1
            flow.flow("REQUIREMENTS Phase 3: Round %d — asking developer", round_num)

            # Generate questions
            questions = await self._generate_questions(analysis, developer_notes, all_answers)

            if not questions:
                flow.flow("REQUIREMENTS: No questions to ask — proceeding with defaults")
                break

            # Check if all questions already have answers from previous run
            unanswered = [q for q in questions if isinstance(q, dict) and q.get("id") not in all_answers]
            if not unanswered and all_answers:
                flow.flow("REQUIREMENTS: All %d questions already answered from previous run — skipping questionnaire",
                          len(questions))
                break

            # Ask developer (HIL pause) — pass previous answers for pre-filling defaults
            answers = await self._ask_developer(ctx, questions, round_num, all_answers)
            all_answers.update(answers)

            # Cache answers for potential re-runs
            _previous_answers[uc_name] = dict(all_answers)

            # Re-analyze with new answers
            combined_notes = developer_notes
            if all_answers:
                combined_notes += "\n\n=== Developer Answers ===\n" + json.dumps(all_answers, indent=2)
            analysis = await self._analyze_uc(parsed, uc_name, combined_notes)
            readiness = analysis.get("overall_readiness", "INSUFFICIENT")
            flow.flow("REQUIREMENTS Phase 3: Round %d result: readiness=%s", round_num, readiness)

        # Save final answers for re-runs
        if all_answers:
            _previous_answers[uc_name] = dict(all_answers)

        # ── Phase 4: Compile requirements ──
        flow.flow("REQUIREMENTS Phase 4: Compiling requirements package")
        requirements = await self._compile_requirements(
            uc_name, analysis, developer_notes, all_answers
        )

        # Store in shared dict (bypasses ADK session state)
        requirements_store[run_id] = requirements

        # Write JSON to disk
        if run_dir:
            req_file = Path(run_dir) / "requirements.json"
            req_file.write_text(json.dumps(requirements, indent=2, default=str), encoding="utf-8")

        # ── Phase 5: Generate requirements summary document ──
        flow.flow("REQUIREMENTS Phase 5: Generating summary document")
        summary_path = ""
        if run_dir:
            summary_path = self._generate_summary_doc(
                Path(run_dir), uc_name, parsed, analysis, requirements, developer_notes, all_answers
            )

        # ── Phase 6: Milestone pause — developer reviews summary ──
        flow.flow("REQUIREMENTS: Pausing for developer review of summary")
        milestone_step = 2  # Use step 2 for requirements review milestone
        milestone_waiting[milestone_step] = {
            "waiting": True,
            "name": "Requirements Summary",
            "status": "passed",
        }

        if milestone_step not in milestone_events:
            milestone_events[milestone_step] = asyncio.Event()
        else:
            milestone_events[milestone_step].clear()

        evt = milestone_events[milestone_step]
        await evt.wait()
        evt.clear()

        decision = milestone_decisions.pop(milestone_step, "continue")
        milestone_waiting.pop(milestone_step, None)

        if decision == "cancel":
            from src.nodes.milestone_nodes import pipeline_actions
            pipeline_actions[run_id] = {"action": "cancel"}

        flow.flow("REQUIREMENTS COMPLETE — readiness=%s, %d questions answered, decision=%s",
                   readiness, len(all_answers), decision)

        return
        yield  # async generator

    def _generate_questionnaire_doc(self, run_dir: Path, uc_name: str,
                                     questions: list, round_num: int) -> str:
        """Generate an HTML document showing the questionnaire for developer review."""
        from datetime import datetime, timezone

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

        lines = [
            "<!DOCTYPE html>",
            "<html><head>",
            "<meta charset='utf-8'>",
            f"<title>Requirements Questionnaire — {uc_name}</title>",
            "<style>",
            "body { font-family: 'Segoe UI', sans-serif; max-width: 900px; margin: 40px auto; color: #e2e4ed; background: #0f1117; padding: 20px; }",
            "h1 { color: #6c8aff; border-bottom: 2px solid #2e3348; padding-bottom: 10px; }",
            "h2 { color: #38bdf8; margin-top: 30px; }",
            "table { width: 100%; border-collapse: collapse; margin: 10px 0; }",
            "th, td { text-align: left; padding: 10px 14px; border: 1px solid #2e3348; }",
            "th { background: #1a1d27; color: #8b8fa8; font-size: 0.85em; text-transform: uppercase; }",
            "td { background: #242836; vertical-align: top; }",
            ".badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.75em; font-weight: 600; }",
            ".badge-critical { background: #2e1a1a; color: #ef4444; }",
            ".badge-partial { background: #3d2f10; color: #f59e0b; }",
            ".badge-cat { background: #1e2a40; color: #38bdf8; }",
            ".section { background: #1a1d27; border: 1px solid #2e3348; border-radius: 8px; padding: 16px; margin: 12px 0; }",
            ".context { color: #8b8fa8; font-size: 0.85em; margin-top: 4px; }",
            ".default { color: #6c8aff; font-family: monospace; font-size: 0.9em; }",
            ".meta { color: #8b8fa8; font-size: 0.85em; }",
            "</style>",
            "</head><body>",
            f"<h1>Requirements Questionnaire — Round {round_num}</h1>",
            f"<p class='meta'>UC: <strong>{uc_name}</strong> | "
            f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} | "
            f"Questions: <strong>{len(questions)}</strong></p>",
            f"<p>The pipeline needs the following information to generate production-quality YAML. "
            f"Answer in the UI or provide via Developer Notes.</p>",
        ]

        # Group questions by category
        by_cat: dict[str, list] = {}
        for q in questions:
            if isinstance(q, dict):
                cat = q.get("category", "other")
                by_cat.setdefault(cat, []).append(q)

        for cat, cat_questions in by_cat.items():
            label = cat_labels.get(cat, cat.replace("_", " ").title())
            lines.append(f"<h2>{label}</h2>")
            lines.append("<div class='section'>")
            lines.append("<table><tr><th>#</th><th>Question</th><th>Importance</th><th>Default</th></tr>")

            for i, q in enumerate(cat_questions, 1):
                imp = q.get("importance", "optional")
                badge_cls, badge_text = imp_badges.get(imp, ("badge-cat", imp.upper()))
                default = q.get("default_value", "—")
                context = q.get("context", "")

                lines.append(f"<tr>")
                lines.append(f"<td>{q.get('id', i)}</td>")
                lines.append(f"<td><strong>{q.get('question', '?')}</strong>")
                if context:
                    lines.append(f"<div class='context'>{context}</div>")
                lines.append(f"</td>")
                lines.append(f"<td><span class='badge {badge_cls}'>{badge_text}</span></td>")
                lines.append(f"<td><span class='default'>{default}</span></td>")
                lines.append(f"</tr>")

            lines.append("</table></div>")

        # Summary
        critical_count = sum(1 for q in questions if isinstance(q, dict) and q.get("importance") == "critical")
        lines.append(f"<p class='meta'>Total: {len(questions)} questions | "
                     f"Critical: {critical_count} | "
                     f"Use 'Continue' to accept defaults, or answer in the UI.</p>")

        lines.append("</body></html>")

        doc_path = run_dir / f"{uc_name}_questionnaire_round{round_num}.html"
        doc_path.write_text("\n".join(lines), encoding="utf-8")
        flow.flow("REQUIREMENTS: Questionnaire doc written to %s", doc_path)
        return str(doc_path)

    def _generate_summary_doc(self, run_dir: Path, uc_name: str, parsed: dict,
                               analysis: dict, requirements: dict,
                               developer_notes: str, answers: dict) -> str:
        """Generate a human-readable HTML requirements summary document."""
        from datetime import datetime, timezone

        ciq_data = parsed.get("ciq_data", {})
        per_sheet = ciq_data.get("per_sheet", {})

        # Build HTML
        lines = [
            "<!DOCTYPE html>",
            "<html><head>",
            "<meta charset='utf-8'>",
            f"<title>Requirements Summary — {uc_name}</title>",
            "<style>",
            "body { font-family: 'Segoe UI', sans-serif; max-width: 900px; margin: 40px auto; color: #e2e4ed; background: #0f1117; padding: 20px; }",
            "h1 { color: #6c8aff; border-bottom: 2px solid #2e3348; padding-bottom: 10px; }",
            "h2 { color: #38bdf8; margin-top: 30px; }",
            "h3 { color: #a78bfa; }",
            "table { width: 100%; border-collapse: collapse; margin: 10px 0; }",
            "th, td { text-align: left; padding: 8px 12px; border: 1px solid #2e3348; }",
            "th { background: #1a1d27; color: #8b8fa8; font-size: 0.85em; text-transform: uppercase; }",
            "td { background: #242836; }",
            ".badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.75em; font-weight: 600; }",
            ".badge-complete { background: #1a3a2a; color: #22c55e; }",
            ".badge-partial { background: #3d2f10; color: #f59e0b; }",
            ".badge-critical { background: #2e1a1a; color: #ef4444; }",
            ".badge-cat { background: #1e2a40; color: #38bdf8; }",
            ".section { background: #1a1d27; border: 1px solid #2e3348; border-radius: 8px; padding: 16px; margin: 12px 0; }",
            "pre { background: #242836; padding: 12px; border-radius: 6px; overflow-x: auto; font-size: 0.85em; }",
            ".meta { color: #8b8fa8; font-size: 0.85em; }",
            "</style>",
            "</head><body>",
            f"<h1>Requirements Summary</h1>",
            f"<p class='meta'>UC: <strong>{uc_name}</strong> | "
            f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</p>",
        ]

        # Readiness status
        readiness = analysis.get("overall_readiness", "?")
        badge_cls = "badge-complete" if readiness == "COMPLETE" else "badge-partial"
        lines.append(f"<p>Readiness: <span class='badge {badge_cls}'>{readiness}</span> | "
                     f"UC Type: <strong>{analysis.get('uc_type', '?')}</strong> | "
                     f"Activity: <strong>{analysis.get('activity_type', '?')}</strong></p>")

        # CIQ Structure
        lines.append("<h2>CIQ Structure</h2>")
        lines.append("<div class='section'><table><tr><th>Sheet</th><th>Columns</th><th>Rows</th></tr>")
        for sname, sdata in per_sheet.items():
            headers = sdata.get("headers", []) if isinstance(sdata, dict) else []
            row_count = sdata.get("row_count", "?") if isinstance(sdata, dict) else "?"
            lines.append(f"<tr><td><strong>{sname}</strong></td><td>{', '.join(str(h) for h in headers)}</td><td>{row_count}</td></tr>")
        lines.append("</table></div>")

        # Category 1: Node & Activity
        cat1 = analysis.get("category_1_node_activity", {})
        cat1_req = requirements.get("category_1_node_activity", {})
        lines.append("<h2>Category 1: Node & Activity</h2>")
        lines.append("<div class='section'>")

        nodes_detected = cat1.get("nodes_detected", [])
        if nodes_detected:
            lines.append("<table><tr><th>Node</th><th>Type</th><th>Role</th></tr>")
            for n in nodes_detected:
                if isinstance(n, dict):
                    lines.append(f"<tr><td>{n.get('name','?')}</td><td>{n.get('type','?')}</td><td>{n.get('role','?')}</td></tr>")
                else:
                    lines.append(f"<tr><td colspan='3'>{n}</td></tr>")
            lines.append("</table>")

        lines.append(f"<p>Login approach: <strong>{cat1.get('login_approach', '?')}</strong> | "
                     f"NIAM columns: <strong>{cat1.get('niam_columns_count', '?')}</strong> | "
                     f"Root escalation: <strong>{cat1.get('root_method', '?')}</strong></p>")

        root_esc = cat1_req.get("root_escalation", {})
        if root_esc:
            lines.append(f"<p>Root method: <code>{root_esc.get('method', '?')}</code> | "
                         f"Password: <code>{'***' if root_esc.get('password') else 'not set'}</code></p>")
        lines.append("</div>")

        # Category 2: Commands & Parameters
        cat2 = analysis.get("category_2_commands", {})
        cat2_req = requirements.get("category_2_commands", {})
        lines.append("<h2>Category 2: Commands & Parameters</h2>")
        lines.append("<div class='section'>")

        cmds = cat2.get("commands_found", []) or cat2_req.get("commands", [])
        if cmds:
            lines.append("<table><tr><th>Command</th><th>Node</th><th>Purpose</th><th>CIQ Params</th></tr>")
            for c in cmds:
                if isinstance(c, dict):
                    params = ", ".join(c.get("ciq_params", c.get("params_from_ciq", [])))
                    lines.append(f"<tr><td><code>{c.get('cmd', c.get('command', '?'))}</code></td>"
                                 f"<td>{c.get('node', '?')}</td><td>{c.get('purpose', '?')}</td>"
                                 f"<td>{params}</td></tr>")
            lines.append("</table>")

        ldap = cat2_req.get("ldap", {})
        if ldap:
            lines.append(f"<p>LDAP: <code>{ldap.get('host', '?')}:{ldap.get('port', '?')}</code> | "
                         f"Bind DN: <code>{ldap.get('bind_dn', '?')}</code> | "
                         f"Password: <code>{'***' if ldap.get('password') else 'not set'}</code></p>")
        lines.append("</div>")

        # Category 3: Orchestration
        cat3 = analysis.get("category_3_orchestration", {})
        cat3_req = requirements.get("category_3_orchestration", {})
        lines.append("<h2>Category 3: Orchestration</h2>")
        lines.append("<div class='section'>")
        lines.append(f"<p>Grouping: <strong>{cat3.get('grouping_column', cat3_req.get('grouping', {}).get('column', '?'))}</strong> | "
                     f"NIAM failover: <strong>{cat3.get('niam_failover_needed', cat3_req.get('niam_failover', '?'))}</strong> | "
                     f"Backup: <strong>{cat3.get('backup_mentioned', '?')}</strong></p>")

        ssh = cat3_req.get("ssh", {})
        if ssh:
            lines.append(f"<p>SSH: connect={ssh.get('connect_timeout', '?')}s | "
                         f"read={ssh.get('read_timeout', '?')}s | "
                         f"kex={ssh.get('kex_algorithms', 'default')}</p>")

        phases = cat3_req.get("phases", [])
        if phases:
            lines.append(f"<p>Phases: {' → '.join(phases)}</p>")
        lines.append("</div>")

        # Generation Hints
        hints = requirements.get("generation_hints", {})
        if hints:
            lines.append("<h2>Generation Hints (per step)</h2>")
            lines.append("<div class='section'>")
            for step_key, hint in hints.items():
                lines.append(f"<p><strong>{step_key}:</strong> {hint}</p>")
            lines.append("</div>")

        # Developer Notes & Answers
        if developer_notes or answers:
            lines.append("<h2>Developer Input</h2>")
            lines.append("<div class='section'>")
            if developer_notes:
                lines.append(f"<h3>Notes</h3><pre>{developer_notes}</pre>")
            if answers:
                lines.append(f"<h3>Questionnaire Answers</h3><pre>{json.dumps(answers, indent=2, default=str)}</pre>")
            lines.append("</div>")

        # Gaps (if any remain)
        all_gaps = []
        for cat_key in ("category_1_node_activity", "category_2_commands", "category_3_orchestration"):
            cat = analysis.get(cat_key, {})
            for gap in cat.get("gaps", []):
                all_gaps.append(f"{cat_key}: {gap}")
        if all_gaps:
            lines.append("<h2>Remaining Gaps</h2>")
            lines.append("<div class='section'>")
            for g in all_gaps:
                lines.append(f"<p class='badge badge-critical'>{g}</p>")
            lines.append("</div>")

        lines.append("</body></html>")

        summary_path = run_dir / f"{uc_name}_requirements-summary.html"
        summary_path.write_text("\n".join(lines), encoding="utf-8")
        flow.flow("REQUIREMENTS: Summary doc written to %s", summary_path)
        return str(summary_path)

    async def _parse_documents(self, ctx) -> dict:
        """Parse CIQ, MOP, and command outputs using existing agents."""
        from src.nodes.cr_import import get_agent_class
        from src.nodes.step_nodes import _get_step_data, _call_existing_agent, _get_config_from_state

        step_data = _get_step_data(ctx.session.state)
        config = _get_config_from_state(ctx.session.state)

        # Run DocParser
        try:
            DocParserAgent = get_agent_class("src.agents.doc_parser_agent", "DocParserAgent")
            parse_result = await _call_existing_agent(DocParserAgent(), step_data, config)
            parsed = parse_result.get("output_data", {})

            # Merge parsed data back into step_data for UCAnalyzer
            for key in ("intermediate_repr", "ciq_data", "mop_data", "constraints", "command_outputs"):
                if key in parsed:
                    step_data[key] = parsed[key]
                    ctx.session.state[key] = parsed[key]

            flow.flow("REQUIREMENTS: DocParser — %s", parse_result.get("status", "?"))
        except Exception as e:
            logger.error("DocParser failed: %s", e)
            parsed = {}

        # Run UCAnalyzer
        try:
            UCAnalyzerAgent = get_agent_class("src.agents.uc_analyzer_agent", "UCAnalyzerAgent")
            analyze_result = await _call_existing_agent(UCAnalyzerAgent(), step_data, config)
            analyzer_output = analyze_result.get("output_data", {})

            for key in ("intermediate_repr", "ciq_data", "mop_data"):
                if key in analyzer_output:
                    parsed[key] = analyzer_output[key]

            flow.flow("REQUIREMENTS: UCAnalyzer — %s", analyze_result.get("status", "?"))
        except Exception as e:
            logger.error("UCAnalyzer failed: %s", e)

        return parsed

    async def _analyze_uc(self, parsed: dict, uc_name: str, developer_notes: str) -> dict:
        """Analyze UC type and assess readiness using LLM."""
        from src.nodes.cr_import import import_cr_module
        agents_mod = import_cr_module("src.agents")
        call_llm = agents_mod.call_llm

        # Build summaries for the prompt
        ciq_data = parsed.get("ciq_data", {})
        per_sheet = ciq_data.get("per_sheet", {})
        ciq_summary = ""
        if per_sheet:
            parts = []
            for sname, sdata in per_sheet.items():
                headers = sdata.get("headers", []) if isinstance(sdata, dict) else []
                samples = sdata.get("sample_rows", []) if isinstance(sdata, dict) else []
                parts.append(f'Sheet "{sname}": columns={headers}')
                if samples:
                    parts.append(f"  Sample: {samples[:2]}")
            ciq_summary = "\n".join(parts)
        else:
            ciq_summary = json.dumps(ciq_data, indent=2, default=str)[:3000]

        mop_data = parsed.get("mop_data", {})
        mop_summary = json.dumps(mop_data, indent=2, default=str)[:4000] if mop_data else "No MOP data"

        cmd_data = parsed.get("command_outputs", {})
        cmd_summary = json.dumps(cmd_data, indent=2, default=str)[:2000] if cmd_data else "No command outputs"

        prompt = ANALYZE_PROMPT.format(
            uc_name=uc_name,
            ciq_summary=ciq_summary,
            mop_summary=mop_summary,
            cmd_summary=cmd_summary,
            developer_notes=developer_notes or "None provided",
        )

        try:
            response = await call_llm(prompt, system_prompt=SYSTEM_PROMPT)
            # Extract JSON from response
            response = response.strip()
            if response.startswith("```"):
                import re
                m = re.search(r'```(?:json)?\s*\n(.*?)```', response, re.DOTALL)
                response = m.group(1) if m else response
            return json.loads(response)
        except Exception as e:
            logger.error("UC analysis failed: %s", e)
            return {"overall_readiness": "INSUFFICIENT", "error": str(e)}

    async def _generate_questions(self, analysis: dict, developer_notes: str, existing_answers: dict) -> list:
        """Generate targeted questions based on gaps in analysis."""
        from src.nodes.cr_import import import_cr_module
        agents_mod = import_cr_module("src.agents")
        call_llm = agents_mod.call_llm

        # Collect all gaps
        all_gaps = []
        for cat_key in ("category_1_node_activity", "category_2_commands", "category_3_orchestration"):
            cat = analysis.get(cat_key, {})
            gaps = cat.get("gaps", [])
            all_gaps.extend(gaps)

        if not all_gaps:
            return []

        combined_notes = developer_notes or ""
        if existing_answers:
            combined_notes += "\n\nPrevious answers:\n" + json.dumps(existing_answers, indent=2)

        prompt = QUESTIONS_PROMPT.format(
            analysis_json=json.dumps(analysis, indent=2, default=str),
            developer_notes=combined_notes or "None",
        )

        try:
            response = await call_llm(prompt, system_prompt=SYSTEM_PROMPT)
            response = response.strip()
            if response.startswith("```"):
                import re
                m = re.search(r'```(?:json)?\s*\n(.*?)```', response, re.DOTALL)
                response = m.group(1) if m else response
            questions = json.loads(response)
            return questions if isinstance(questions, list) else []
        except Exception as e:
            logger.error("Question generation failed: %s", e)
            return []

    async def _ask_developer(self, ctx, questions: list, round_num: int,
                             previous_answers: dict | None = None) -> dict:
        """Present questions to developer via HIL pause, return answers."""
        run_id = ctx.session.state.get("run_id", "")
        run_dir = ctx.session.state.get("run_dir", "")
        uc_name = ctx.session.state.get("uc_name", "UC")

        # Pre-fill default_value from previous answers for re-runs
        if previous_answers:
            for q in questions:
                if isinstance(q, dict):
                    q_id = q.get("id", "")
                    if q_id in previous_answers and not q.get("default_value"):
                        q["default_value"] = previous_answers[q_id]

        # Generate questionnaire HTML as artifact
        artifact_path = ""
        if run_dir:
            artifact_path = self._generate_questionnaire_doc(
                Path(run_dir), uc_name, questions, round_num
            )

        # Use milestone step 0 for requirements gathering
        milestone_step = 0

        # Store questions for UI to display
        milestone_waiting[milestone_step] = {
            "waiting": True,
            "name": f"Requirements Gathering (Round {round_num})",
            "status": "questions",
            "questions": questions,
            "artifact_file": artifact_path,
        }

        # Create event if not exists
        if milestone_step not in milestone_events:
            milestone_events[milestone_step] = asyncio.Event()
        else:
            milestone_events[milestone_step].clear()

        flow.flow("REQUIREMENTS: Waiting for developer answers (%d questions)", len(questions))

        # Wait for developer response
        evt = milestone_events[milestone_step]
        await evt.wait()
        evt.clear()

        # Read decision/answers
        decision = milestone_decisions.pop(milestone_step, "continue")
        milestone_waiting.pop(milestone_step, None)

        # Answers are stored by the UI in the milestone_decisions as JSON
        answers = {}
        if isinstance(decision, str) and decision.startswith("{"):
            try:
                answers = json.loads(decision)
            except json.JSONDecodeError:
                pass
        elif isinstance(decision, dict):
            answers = decision

        flow.flow("REQUIREMENTS: Developer answered %d questions", len(answers))
        return answers

    async def _compile_requirements(self, uc_name: str, analysis: dict,
                                     developer_notes: str, answers: dict) -> dict:
        """Compile final requirements package."""
        from src.nodes.cr_import import import_cr_module
        agents_mod = import_cr_module("src.agents")
        call_llm = agents_mod.call_llm

        prompt = COMPILE_PROMPT.format(
            uc_name=uc_name,
            analysis_json=json.dumps(analysis, indent=2, default=str),
            developer_notes=developer_notes or "None",
            answers_json=json.dumps(answers, indent=2, default=str) if answers else "No answers needed — all requirements auto-detected",
        )

        try:
            response = await call_llm(prompt, system_prompt=SYSTEM_PROMPT)
            response = response.strip()
            if response.startswith("```"):
                import re
                m = re.search(r'```(?:json)?\s*\n(.*?)```', response, re.DOTALL)
                response = m.group(1) if m else response
            return json.loads(response)
        except Exception as e:
            logger.error("Requirements compilation failed: %s", e)
            # Return minimal requirements from analysis
            return {
                "analysis": analysis,
                "developer_notes": developer_notes,
                "developer_answers": answers,
                "generation_hints": {},
            }
