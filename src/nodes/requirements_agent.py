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
# Persisted to data/knowledge/{uc_name}_answers.json
_previous_answers: dict[str, dict] = {}  # uc_name → all_answers from last run
_ANSWERS_DIR = Path("data/knowledge")


def _load_previous_answers(uc_name: str) -> dict:
    """Load cached answers from disk for a UC."""
    if uc_name in _previous_answers:
        return _previous_answers[uc_name]
    fpath = _ANSWERS_DIR / f"{uc_name}_answers.json"
    if fpath.exists():
        try:
            answers = json.loads(fpath.read_text(encoding="utf-8"))
            _previous_answers[uc_name] = answers
            logger.info("Loaded %d cached answers for UC %s from disk", len(answers), uc_name)
            return answers
        except Exception as e:
            logger.debug("Could not load answers for %s: %s", uc_name, e)
    return {}


def _save_previous_answers(uc_name: str, answers: dict):
    """Persist answers to disk for a UC."""
    _previous_answers[uc_name] = answers
    try:
        _ANSWERS_DIR.mkdir(parents=True, exist_ok=True)
        fpath = _ANSWERS_DIR / f"{uc_name}_answers.json"
        fpath.write_text(json.dumps(answers, indent=2, default=str), encoding="utf-8")
        logger.info("Saved %d answers for UC %s to %s", len(answers), uc_name, fpath)
    except Exception as e:
        logger.error("Could not save answers for %s: %s", uc_name, e)

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

IMPORTANT: Mark items as KNOWN (not gaps) if the information is visible in ANY of the provided data:
- CIQ column names and counts are KNOWN — do not list them as gaps
- NIAM column count is KNOWN from IP sheet headers
- LDAP credentials visible in SSH logs are KNOWN
- Shell prompts visible in SSH logs are KNOWN
- Command syntax in MOP is KNOWN
- XML structure in sample files is KNOWN
- Only report GENUINE gaps — information that truly cannot be found anywhere in the provided data

UC Name: {uc_name}

=== CIQ STRUCTURE ===
{ciq_summary}

=== MOP CONTENT ===
{mop_summary}

=== COMMAND OUTPUTS ===
{cmd_summary}

=== DEVELOPER NOTES ===
{developer_notes}

Respond with a JSON object covering all 3 categories.
For each category, only list items in "gaps" that are GENUINELY missing — not items visible in the data above.

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

=== DATA ALREADY AVAILABLE (DO NOT ask about these) ===
{available_data}

=== CRITICAL RULES — WHAT NOT TO ASK ===
- Do NOT ask about anything visible in the CIQ columns (sheet names, column names, column count)
- Do NOT ask about NIAM column count — it's visible in the CIQ IP sheet headers
- Do NOT ask about which NIAM to try first — always try NIAM_NAME1, then 2, then 3 in order
- Do NOT ask about XML structure/template — sample XML files are provided as input artifacts
- Do NOT ask about LDAP credentials if they're visible in the SSH session logs
- Do NOT ask about shell prompts if they're visible in the SSH session logs
- Do NOT ask about command syntax if it's in the MOP document
- Do NOT re-ask questions that have been answered in "Developer already provided" above
- Only ask about things that are GENUINELY UNKNOWN and not inferable from the input documents

=== QUESTION STYLE ===
- ALWAYS provide a default_value for every question — extract it from the documents if possible
- Write questions for a LAYMAN USER, not a developer
- Use simple everyday language, avoid technical jargon
- Keep questions short (1 sentence) — put technical details in "context"
- Use "context" field to explain WHY this is needed
- Use "helper_text" field to show an example answer
- For select/boolean, make the options self-explanatory
- MAXIMUM 10-12 questions per round. Focus on the MOST IMPORTANT gaps only.

Respond with a JSON array:
[
  {{
    "id": "na_q1",
    "category": "node_activity | commands | orchestration",
    "question": "Simple, clear question in plain language",
    "importance": "critical | recommended | optional",
    "input_type": "text | boolean | select | password",
    "options": ["Option A — explanation", "Option B — explanation"],
    "default_value": "sensible default extracted from documents",
    "context": "Technical context: why this matters",
    "helper_text": "Example answer"
  }}
]

Use STABLE question IDs with category prefix: na_q1, na_q2 for node_activity; cmd_q1, cmd_q2 for commands; orch_q1, orch_q2 for orchestration.
Only include questions for GENUINE gaps that cannot be answered from the provided data."""

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
  }},
  "xml_templates": {{
    "description": "Mapping of CIQ columns to XML modification templates",
    "ciq_to_xml_mapping": [
      {{"ciq_column": "CIQ column name", "xml_tag": "XML element/attribute to set",
        "xml_sample_file": "which sample XML this comes from",
        "spml_objectclass": "Subscriber type in SPML"}}
    ],
    "base_xml_structure": "The SPML batchRequest template with placeholders"
  }}
}}

CRITICAL for xml_templates:
- Analyze the sample XML files provided and map each CIQ column to its XML tag
- Use ACTUAL SPML format from the samples — do NOT invent dummy XML
- Replace hardcoded values with ${{configData.COLUMN_NAME}} references
- The IMSI field should always use ${{configData.TEST_IMSI}} or similar CIQ reference
- Include the full SPML namespace and version from the samples

Be concrete — use actual values from the answers and sample files, not placeholders."""


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

        # ── Phase 1b: Extract raw artifact content ──
        # Read actual file contents (MOP text, SSH logs, XML samples) for richer context
        raw_artifacts = self._extract_raw_artifacts(ctx.session.state)
        ctx.session.state["raw_artifacts"] = raw_artifacts
        if raw_artifacts:
            flow.flow("REQUIREMENTS: Extracted raw artifacts — %s",
                       {k: f"{len(v)} chars" for k, v in raw_artifacts.items() if v})

        # ── Phase 2: Analyze UC type ──
        flow.flow("REQUIREMENTS Phase 2: Analyzing UC type")
        parsed["_raw_artifacts"] = raw_artifacts  # attach for richer context

        # Pre-populate with answers from previous run of same UC (if any)
        all_answers = dict(_load_previous_answers(uc_name))
        if all_answers:
            flow.flow("REQUIREMENTS: Loaded %d previous answers for UC %s", len(all_answers), uc_name)

        # Include previous answers in analysis so LLM evaluates readiness WITH them
        analysis_notes = developer_notes
        if all_answers:
            analysis_notes += "\n\n=== Previously Answered Questions ===\n" + json.dumps(all_answers, indent=2)

        analysis = await self._analyze_uc(parsed, uc_name, analysis_notes)

        readiness = analysis.get("overall_readiness", "INSUFFICIENT")
        flow.flow("REQUIREMENTS Phase 2 result: readiness=%s", readiness)

        # ── Phase 3: Gather missing requirements (iterate) ──
        # Only skip questionnaire if readiness is COMPLETE (all requirements met)
        # PARTIAL still has gaps — let the user decide via the continue/clarify dialog
        if all_answers and readiness == "COMPLETE":
            flow.flow("REQUIREMENTS: Reusing %d previous answers — readiness=COMPLETE, skipping questionnaire",
                       len(all_answers))
        else:
            round_num = 0
            rounds_per_batch = 3
            keep_asking = True

            while readiness != "COMPLETE" and keep_asking:
                # Run a batch of up to 3 rounds
                batch_rounds = 0
                while readiness != "COMPLETE" and batch_rounds < rounds_per_batch:
                    round_num += 1
                    batch_rounds += 1
                    flow.flow("REQUIREMENTS Phase 3: Round %d — asking developer", round_num)

                    # Generate questions
                    questions = await self._generate_questions(
                        analysis, developer_notes, all_answers,
                        raw_artifacts=raw_artifacts, parsed=parsed,
                    )

                    if not questions:
                        flow.flow("REQUIREMENTS: No questions to ask — proceeding with defaults")
                        keep_asking = False
                        break

                    # Filter out already-answered questions (fuzzy match)
                    unanswered = self._filter_answered_questions(questions, all_answers)

                    if not unanswered and all_answers:
                        flow.flow("REQUIREMENTS: All %d questions already covered — skipping",
                                  len(questions))
                        keep_asking = False
                        break

                    if unanswered and len(unanswered) < len(questions):
                        flow.flow("REQUIREMENTS: %d/%d already answered, showing %d remaining",
                                  len(questions) - len(unanswered), len(questions), len(unanswered))
                        questions = unanswered

                    # Ask developer (HIL pause)
                    answers = await self._ask_developer(ctx, questions, round_num, all_answers)
                    all_answers.update(answers)
                    _save_previous_answers(uc_name, all_answers)

                    # Re-analyze with new answers
                    combined_notes = developer_notes
                    if all_answers:
                        combined_notes += "\n\n=== Developer Answers ===\n" + json.dumps(all_answers, indent=2)
                    analysis = await self._analyze_uc(parsed, uc_name, combined_notes)
                    readiness = analysis.get("overall_readiness", "INSUFFICIENT")
                    flow.flow("REQUIREMENTS Phase 3: Round %d result: readiness=%s", round_num, readiness)

                # After batch of 3 rounds, if still not COMPLETE, ask user what to do
                if readiness != "COMPLETE" and keep_asking:
                    # Collect remaining gaps for display
                    remaining_gaps = []
                    for cat_key in ("category_1_node_activity", "category_2_commands", "category_3_orchestration"):
                        cat = analysis.get(cat_key, {})
                        remaining_gaps.extend(cat.get("gaps", []))

                    user_choice = await self._ask_continue_or_clarify(
                        ctx, readiness, remaining_gaps, round_num,
                    )

                    if user_choice == "continue":
                        flow.flow("REQUIREMENTS: User chose to continue with gaps (readiness=%s)", readiness)
                        keep_asking = False
                    else:
                        flow.flow("REQUIREMENTS: User chose to answer more questions")
                        # Continue with another batch of 3 rounds

        # Save final answers for re-runs
        if all_answers:
            _save_previous_answers(uc_name, all_answers)

        # ── Phase 4: Compile requirements ──
        flow.flow("REQUIREMENTS Phase 4: Compiling requirements package")
        requirements = await self._compile_requirements(
            uc_name, analysis, developer_notes, all_answers,
            raw_artifacts=raw_artifacts,
        )

        # ── Phase 4b: Enforce questionnaire answers as hard overrides ──
        # LLM compilation may hallucinate — override with explicit user answers
        requirements = self._enforce_answers(requirements, all_answers, raw_artifacts)
        flow.flow("REQUIREMENTS Phase 4b: Enforced answer overrides")

        # Store in shared dict (bypasses ADK session state)
        requirements_store[run_id] = requirements

        # Write JSON to disk
        if run_dir:
            req_file = Path(run_dir) / "requirements.json"
            req_file.write_text(json.dumps(requirements, indent=2, default=str), encoding="utf-8")

        # ── Phase 5: Generate requirements summary document ──
        flow.flow("REQUIREMENTS Phase 5: Generating summary document")
        parsed["_ciq_file_path"] = ctx.session.state.get("ciq_file_path", "")
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

    @staticmethod
    def _filter_answered_questions(questions: list, all_answers: dict) -> list:
        """Filter out questions already answered via ID or fuzzy topic match."""
        unanswered = []
        for q in questions:
            if not isinstance(q, dict):
                continue
            q_id = q.get("id", "")
            q_text = q.get("question", "").lower().strip()
            # Direct ID match
            if q_id in all_answers:
                continue
            # Fuzzy: check if any previous answer covers this question's topic
            already_covered = False
            for ak, av in all_answers.items():
                if q_text and any(
                    kw in q_text for kw in ak.lower().replace("_", " ").split()
                    if len(kw) > 3
                ):
                    already_covered = True
                    q["default_value"] = av
                    break
            if not already_covered:
                unanswered.append(q)
        return unanswered

    async def _ask_continue_or_clarify(self, ctx, readiness: str,
                                        remaining_gaps: list, round_num: int) -> str:
        """Ask user whether to continue with gaps or answer more questions.

        Uses milestone step 0 with a special question format.
        Returns 'continue' or 'clarify'.
        """
        gap_text = "\n".join(f"  - {g}" for g in remaining_gaps[:10]) if remaining_gaps else "  (none identified)"

        questions = [{
            "id": "_continue_decision",
            "category": "orchestration",
            "question": (
                f"After {round_num} rounds of questions, readiness is '{readiness}'. "
                "Would you like to continue with the current information, or answer more questions to fill the gaps?"
            ),
            "importance": "critical",
            "input_type": "select",
            "options": [
                "Continue with current information — proceed to YAML generation",
                "Answer more questions — I want to clarify the remaining gaps",
            ],
            "default_value": "Continue with current information — proceed to YAML generation",
            "context": f"Remaining gaps:\n{gap_text}",
            "helper_text": "Choose 'Continue' to proceed or 'Answer more' to provide more details",
        }]

        answers = await self._ask_developer(ctx, questions, round_num + 1)
        decision = answers.get("_continue_decision", "")
        if "answer more" in str(decision).lower() or "clarify" in str(decision).lower():
            return "clarify"
        return "continue"

    def _generate_summary_doc(self, run_dir: Path, uc_name: str, parsed: dict,
                               analysis: dict, requirements: dict,
                               developer_notes: str, answers: dict) -> str:
        """Generate a human-readable HTML requirements summary document."""
        from datetime import datetime, timezone

        ciq_data = parsed.get("ciq_data", {})
        # ciq_data may be a JSON string from IntermediateRepresentation
        if isinstance(ciq_data, str):
            try:
                ciq_data = json.loads(ciq_data)
            except (json.JSONDecodeError, TypeError):
                ciq_data = {}

        per_sheet = ciq_data.get("per_sheet", {})

        # Fallback: if per_sheet is empty, build it from raw CIQ data
        if not per_sheet and ciq_data:
            # Try headers/rows/sheet_names at top level
            sheet_names = ciq_data.get("sheet_names", [])
            headers = ciq_data.get("headers", [])
            rows = ciq_data.get("rows", [])
            if sheet_names and headers:
                for sn in sheet_names:
                    per_sheet[sn] = {"headers": headers, "row_count": len(rows)}
            elif headers:
                per_sheet["Data"] = {"headers": headers, "row_count": len(rows)}

        # Last resort: read CIQ Excel directly for sheet/column info
        if not per_sheet:
            try:
                ciq_path = parsed.get("_ciq_file_path") or ""
                if ciq_path and Path(ciq_path).exists():
                    import openpyxl
                    wb = openpyxl.load_workbook(ciq_path, read_only=True, data_only=True)
                    for sn in wb.sheetnames:
                        ws = wb[sn]
                        hdrs = [str(c.value or "") for c in next(ws.iter_rows(min_row=1, max_row=1))]
                        per_sheet[sn] = {"headers": [h for h in hdrs if h], "row_count": ws.max_row - 1}
                    wb.close()
            except Exception as e:
                logger.debug("Could not read CIQ for summary: %s", e)

        # Helper to safely get nested values without "?"
        def _v(d, *keys, fallback="—"):
            """Get nested dict value, return fallback if missing/empty."""
            for k in keys:
                if isinstance(d, dict):
                    d = d.get(k)
                else:
                    return fallback
            return d if d is not None and d != "" else fallback

        # Primary data sources: requirements (compiled), analysis (LLM), answers (user)
        cat1_req = requirements.get("category_1_node_activity", {})
        cat2_req = requirements.get("category_2_commands", {})
        cat3_req = requirements.get("category_3_orchestration", {})
        cat1_an = analysis.get("category_1_node_activity", {})
        cat2_an = analysis.get("category_2_commands", {})
        cat3_an = analysis.get("category_3_orchestration", {})

        # Build HTML
        lines = [
            "<!DOCTYPE html>",
            "<html><head>",
            "<meta charset='utf-8'>",
            f"<title>Requirements Summary — {uc_name}</title>",
            "<style>",
            "* { box-sizing: border-box; }",
            "body { font-family: 'Segoe UI', sans-serif; font-size: 13px; line-height: 1.5; "
            "max-width: 960px; margin: 30px auto; color: #e2e4ed; background: #0f1117; padding: 20px; }",
            "h1 { font-size: 1.4em; color: #6c8aff; border-bottom: 2px solid #2e3348; padding-bottom: 8px; margin-bottom: 16px; }",
            "h2 { font-size: 1.1em; color: #38bdf8; margin-top: 24px; margin-bottom: 8px; }",
            "h3 { font-size: 1em; color: #a78bfa; margin-top: 12px; }",
            "p { margin: 4px 0; }",
            "table { width: 100%; border-collapse: collapse; margin: 8px 0; font-size: 0.92em; }",
            "th, td { text-align: left; padding: 6px 10px; border: 1px solid #2e3348; }",
            "th { background: #1a1d27; color: #8b8fa8; font-size: 0.85em; text-transform: uppercase; }",
            "td { background: #242836; vertical-align: top; }",
            "code { background: #2a2d3e; padding: 1px 5px; border-radius: 3px; font-size: 0.92em; }",
            ".badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.78em; font-weight: 600; }",
            ".badge-complete { background: #1a3a2a; color: #22c55e; }",
            ".badge-partial { background: #3d2f10; color: #f59e0b; }",
            ".badge-insufficient { background: #2e1a1a; color: #ef4444; }",
            ".section { background: #1a1d27; border: 1px solid #2e3348; border-radius: 8px; padding: 14px; margin: 8px 0; }",
            "pre { background: #242836; padding: 10px; border-radius: 6px; overflow-x: auto; "
            "font-size: 0.85em; white-space: pre-wrap; word-break: break-word; max-height: 300px; }",
            ".meta { color: #8b8fa8; font-size: 0.85em; }",
            ".gap-item { background: #2e1a1a; border: 1px solid #5c2020; border-radius: 4px; "
            "padding: 6px 10px; margin: 4px 0; color: #fca5a5; font-size: 0.9em; }",
            ".answer-row td:first-child { width: 30%; font-weight: 600; color: #a78bfa; }",
            "</style>",
            "</head><body>",
            f"<h1>Requirements Summary — {uc_name}</h1>",
            f"<p class='meta'>Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
            f" | Answers: {len(answers)} | Readiness: ",
        ]

        readiness = analysis.get("overall_readiness", "INSUFFICIENT")
        badge_cls = {"COMPLETE": "badge-complete", "PARTIAL": "badge-partial"}.get(readiness, "badge-insufficient")
        lines.append(f"<span class='badge {badge_cls}'>{readiness}</span></p>")

        # ── CIQ Structure ──
        lines.append("<h2>CIQ Structure</h2>")
        if per_sheet:
            lines.append("<div class='section'><table><tr><th>Sheet</th><th>Columns</th><th>Rows</th></tr>")
            for sname, sdata in per_sheet.items():
                headers = sdata.get("headers", []) if isinstance(sdata, dict) else []
                row_count = sdata.get("row_count", "—") if isinstance(sdata, dict) else "—"
                lines.append(f"<tr><td><strong>{sname}</strong></td>"
                             f"<td>{', '.join(str(h) for h in headers)}</td><td>{row_count}</td></tr>")
            lines.append("</table></div>")
        else:
            lines.append("<div class='section'><p>No CIQ structure available</p></div>")

        # ── Nodes ──
        lines.append("<h2>Nodes</h2><div class='section'>")
        nodes = cat1_req.get("nodes", []) or cat1_an.get("nodes_detected", [])
        if nodes:
            lines.append("<table><tr><th>Node</th><th>Role</th><th>SSH User</th>"
                         "<th>Prompt</th><th>Escalation</th></tr>")
            for n in nodes:
                if isinstance(n, dict):
                    esc = n.get("escalation", {})
                    lines.append(
                        f"<tr><td><strong>{_v(n, 'name')}</strong></td>"
                        f"<td>{_v(n, 'role')}</td>"
                        f"<td><code>{_v(n, 'ssh', 'user')}</code></td>"
                        f"<td><code>{_v(n, 'prompt_regex')}</code></td>"
                        f"<td><code>{_v(esc, 'method')}</code> → <code>{_v(esc, 'prompt_regex')}</code></td></tr>"
                    )
            lines.append("</table>")
        else:
            lines.append("<p>No nodes detected</p>")

        # Login workflow summary per node
        if nodes:
            lines.append("<h3>Login Workflow</h3>")
            niam_failover = cat1_req.get("niam_failover", False)
            niam_cols = cat1_req.get("niam_columns", [])
            for n in nodes:
                if not isinstance(n, dict):
                    continue
                name = _v(n, 'name')
                ssh_user = _v(n, 'ssh', 'user')
                ssh_host = _v(n, 'ssh', 'host')
                prompt = _v(n, 'prompt_regex')
                esc = n.get("escalation", {})
                esc_method = _v(esc, 'method')
                esc_prompt = _v(esc, 'prompt_regex')

                steps = [f"SSH to <code>{ssh_host}</code> as <code>{ssh_user}</code>"]
                if niam_failover and niam_cols:
                    steps[0] += f" (NIAM failover: try {' → '.join(niam_cols)} in order)"
                steps.append(f"Wait for prompt: <code>{prompt}</code>")
                if esc_method and esc_method != "—":
                    steps.append(f"Escalate: <code>{esc_method}</code> → wait for <code>{esc_prompt}</code>")
                lines.append(f"<p><strong>{name}:</strong> " + " → ".join(steps) + "</p>")

        lines.append(f"<p>Activity type: <strong>{_v(cat1_req, 'activity_type')}</strong> | "
                     f"Data sheet: <strong>{_v(cat1_req, 'data_sheet')}</strong></p>")
        lines.append("</div>")

        # ── Commands ──
        lines.append("<h2>Commands</h2><div class='section'>")
        # Merge commands from both sources (requirements has richer data, analysis has more entries)
        cmds = cat2_req.get("commands", [])
        if not cmds:
            cmds = cat2_an.get("commands_found", [])
        if cmds:
            lines.append("<table><tr><th>Command</th><th>Node</th><th>Purpose</th>"
                         "<th>Syntax</th><th>Validation Criteria</th></tr>")
            for c in cmds:
                if not isinstance(c, dict):
                    continue
                cmd_name = _v(c, 'cmd', fallback=_v(c, 'command'))
                syntax = _v(c, 'syntax')
                # Clean up purpose: convert snake_case to readable text
                purpose_raw = _v(c, 'purpose')
                purpose = purpose_raw.replace("_", " ").replace("-", " ").title() if purpose_raw != "—" else "—"

                # Build validation description from multiple possible formats
                val_desc = "—"
                val_rules = c.get('validation_rules', c.get('validation_success_patterns', []))
                if isinstance(val_rules, list) and val_rules:
                    parts = []
                    for vr in val_rules:
                        if isinstance(vr, dict):
                            msg = vr.get('description', '') or vr.get('failure_message', '')
                            if msg:
                                parts.append(str(msg))
                        elif isinstance(vr, str):
                            parts.append(vr[:100])
                    val_desc = "; ".join(parts) if parts else "—"

                # CIQ params and register as sub-text
                ciq_params = c.get('ciq_params', c.get('params_from_ciq', []))
                register = c.get('register', [])
                sub_parts = []
                if ciq_params:
                    sub_parts.append(f"CIQ: {', '.join(str(p) for p in ciq_params)}")
                if register:
                    sub_parts.append(f"Captures: {', '.join(str(r) for r in register)}")
                target_user = c.get('target_user')
                if target_user:
                    sub_parts.append(f"User: {target_user}")
                sub_text = f"<br><em style='color:#8b8fa8;font-size:0.85em'>{' | '.join(sub_parts)}</em>" if sub_parts else ""

                lines.append(
                    f"<tr><td><code>{cmd_name}</code>{sub_text}</td>"
                    f"<td>{_v(c, 'node')}</td>"
                    f"<td>{purpose}</td>"
                    f"<td><code>{syntax}</code></td>"
                    f"<td>{val_desc}</td></tr>"
                )
            lines.append("</table>")

        ldap = cat2_req.get("ldap", {})
        if ldap:
            lines.append(f"<p>LDAP: <code>{_v(ldap, 'host')}:{_v(ldap, 'port')}</code> | "
                         f"Bind DN: <code>{_v(ldap, 'bind_dn')}</code></p>")
        lines.append("</div>")

        # ── Orchestration ──
        lines.append("<h2>Orchestration</h2><div class='section'>")
        grouping = cat3_req.get("grouping", {})
        lines.append(f"<p>Grouping: <strong>{_v(grouping, 'column', fallback=_v(cat3_an, 'grouping_column'))}</strong> | "
                     f"NIAM failover: <strong>{_v(cat3_req, 'niam_failover', fallback=_v(cat3_an, 'niam_failover_needed'))}</strong></p>")

        phases = cat3_req.get("phases", [])
        if phases:
            lines.append(f"<p>Phases: <strong>{' → '.join(str(p) for p in phases)}</strong></p>")

        ssh = cat3_req.get("ssh", {})
        if ssh:
            lines.append(f"<p>SSH timeout: {_v(ssh, 'connect_timeout_sec', fallback=_v(ssh, 'connect_timeout'))}s | "
                         f"KEX: <code>{_v(ssh, 'kex_algorithms', fallback='default')}</code></p>")
        lines.append("</div>")

        # ── Developer Answers ──
        if answers:
            lines.append("<h2>Developer Answers</h2><div class='section'>")
            lines.append("<table class='answer-row'>")
            for k, v in answers.items():
                # Clean up key for display
                display_key = k.replace("_", " ").replace("q ", "Q")
                val_str = str(v) if not isinstance(v, bool) else ("Yes" if v else "No")
                lines.append(f"<tr><td>{display_key}</td><td>{val_str}</td></tr>")
            lines.append("</table></div>")

        # ── Remaining Gaps ──
        all_gaps = []
        for cat_key in ("category_1_node_activity", "category_2_commands", "category_3_orchestration"):
            cat_label = cat_key.replace("category_", "").replace("_", " ").title()
            cat = analysis.get(cat_key, {})
            for gap in cat.get("gaps", []):
                all_gaps.append((cat_label, gap))

        lines.append("<h2>Remaining Gaps</h2>")
        if all_gaps:
            lines.append("<div class='section'>")
            for cat_label, gap in all_gaps:
                lines.append(f"<div class='gap-item'><strong>{cat_label}:</strong> {gap}</div>")
            lines.append("</div>")
        else:
            lines.append("<div class='section'><p style='color:#22c55e'>No remaining gaps — all requirements gathered.</p></div>")

        # ── Developer Notes ──
        if developer_notes:
            lines.append("<h2>Developer Notes</h2>")
            lines.append(f"<div class='section'><pre>{developer_notes}</pre></div>")

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

        # Include raw MOP text if available (much richer than parsed templates)
        raw_arts = parsed.get("_raw_artifacts", {})
        if raw_arts.get("mop_text"):
            mop_summary = (
                "=== RAW MOP DOCUMENT ===\n" + raw_arts["mop_text"][:8000]
                + "\n\n=== PARSED MOP STEPS ===\n" + mop_summary
            )

        cmd_data = parsed.get("command_outputs", {})
        cmd_summary = json.dumps(cmd_data, indent=2, default=str)[:2000] if cmd_data else "No command outputs"

        # Include raw SSH session logs (contains real prompts, real outputs)
        if raw_arts.get("ssh_logs"):
            cmd_summary = (
                "=== RAW SSH SESSION LOGS ===\n" + raw_arts["ssh_logs"][:12000]
                + "\n\n=== PARSED COMMAND OUTPUTS ===\n" + cmd_summary
            )

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

    async def _generate_questions(self, analysis: dict, developer_notes: str,
                                   existing_answers: dict,
                                   raw_artifacts: dict | None = None,
                                   parsed: dict | None = None) -> list:
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
            combined_notes += "\n\nPrevious answers (DO NOT re-ask these):\n" + json.dumps(existing_answers, indent=2)

        # Build "available data" summary so LLM knows what NOT to ask about
        available_parts = []
        raw_artifacts = raw_artifacts or {}
        parsed = parsed or {}

        # CIQ columns are visible — don't ask about them
        ciq_data = parsed.get("ciq_data", {})
        per_sheet = ciq_data.get("per_sheet", {})
        if per_sheet:
            available_parts.append("CIQ SHEETS AND COLUMNS (already known — do not ask):")
            for sname, sdata in per_sheet.items():
                headers = sdata.get("headers", []) if isinstance(sdata, dict) else []
                available_parts.append(f'  Sheet "{sname}": {headers}')
                niam_cols = [h for h in headers if "niam" in h.lower()]
                if niam_cols:
                    available_parts.append(f'    → NIAM columns found: {niam_cols} (count: {len(niam_cols)})')

        # SSH logs contain prompts and credentials — don't ask about them
        if raw_artifacts.get("ssh_logs"):
            logs = raw_artifacts["ssh_logs"]
            available_parts.append(f"\nSSH SESSION LOGS available ({len(logs)} chars) — contains:")
            available_parts.append("  - Real shell prompts (extract from logs, don't ask user)")
            available_parts.append("  - Real command outputs with actual data patterns")
            # Extract visible credentials
            import re
            ldap_creds = re.findall(r"-D\s+'([^']+)'\s+-w\s+(\S+)", logs)
            if ldap_creds:
                available_parts.append(f"  - LDAP credentials visible: user={ldap_creds[0][0]}, password=***")
            prompts_found = _extract_prompts_from_logs(logs)
            if prompts_found:
                available_parts.append(f"  - Shell prompts found: {prompts_found[:5]}")

        # XML samples are provided — don't ask about XML structure
        if raw_artifacts.get("xml_samples"):
            available_parts.append(f"\nSAMPLE XML FILES provided ({len(raw_artifacts['xml_samples'])} chars)")
            available_parts.append("  - XML structure/template is available — do not ask about it")

        # MOP text is provided — don't ask about command syntax
        if raw_artifacts.get("mop_text"):
            available_parts.append(f"\nFULL MOP DOCUMENT available ({len(raw_artifacts['mop_text'])} chars)")
            available_parts.append("  - Command syntax and procedures are in MOP — do not ask about them")

        available_data = "\n".join(available_parts) if available_parts else "No additional data available"

        prompt = QUESTIONS_PROMPT.format(
            analysis_json=json.dumps(analysis, indent=2, default=str),
            developer_notes=combined_notes or "None",
            available_data=available_data,
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
                                     developer_notes: str, answers: dict,
                                     raw_artifacts: dict | None = None) -> dict:
        """Compile final requirements package."""
        from src.nodes.cr_import import import_cr_module
        agents_mod = import_cr_module("src.agents")
        call_llm = agents_mod.call_llm

        # Include XML samples and MOP text for CIQ→XML mapping
        extra_context = ""
        raw_artifacts = raw_artifacts or {}
        if raw_artifacts.get("xml_samples"):
            extra_context += (
                "\n\n=== SAMPLE XML FILES (use these as templates — map CIQ columns to XML tags) ===\n"
                + raw_artifacts["xml_samples"][:6000]
            )
        if raw_artifacts.get("mop_text"):
            extra_context += (
                "\n\n=== RAW MOP DOCUMENT (extract exact commands and conditional logic) ===\n"
                + raw_artifacts["mop_text"][:6000]
            )

        prompt = COMPILE_PROMPT.format(
            uc_name=uc_name,
            analysis_json=json.dumps(analysis, indent=2, default=str),
            developer_notes=developer_notes or "None",
            answers_json=json.dumps(answers, indent=2, default=str) if answers else "No answers needed — all requirements auto-detected",
        ) + extra_context

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

    def _extract_raw_artifacts(self, state: dict) -> dict:
        """Read raw file contents from uploaded artifacts for richer downstream context.

        Extracts:
        - mop_text: Full MOP document text (conditional logic, step descriptions)
        - ssh_logs: Full SSH session logs (real prompts, real command outputs)
        - xml_samples: Sample XML file contents (SPML schema, field mappings)
        """
        raw = {}
        doc_set = state.get("document_set", {})

        # Read MOP file
        mop_path = doc_set.get("mop_file_path", "")
        if mop_path:
            try:
                raw["mop_text"] = Path(mop_path).read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                logger.debug("Could not read MOP file %s: %s", mop_path, e)

        # Read command output files (SSH session logs)
        cmd_path = doc_set.get("command_outputs_path", "")
        if cmd_path:
            p = Path(cmd_path)
            if p.is_file():
                try:
                    raw["ssh_logs"] = p.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    pass
            elif p.is_dir():
                parts = []
                for f in sorted(p.iterdir()):
                    if f.is_file() and f.suffix in (".txt", ".log", ""):
                        try:
                            content = f.read_text(encoding="utf-8", errors="replace")
                            parts.append(f"=== {f.name} ===\n{content}")
                        except Exception:
                            pass
                raw["ssh_logs"] = "\n\n".join(parts)

        # Read sample I/O files (XML, text, etc.)
        sample_paths = doc_set.get("sample_io_xml_paths", [])
        if sample_paths:
            parts = []
            for sp in sample_paths:
                try:
                    content = Path(sp).read_text(encoding="utf-8", errors="replace")
                    parts.append(f"=== {Path(sp).name} ===\n{content}")
                except Exception:
                    pass
            raw["xml_samples"] = "\n\n".join(parts)

        return raw

    @staticmethod
    def _enforce_answers(requirements: dict, answers: dict, raw_artifacts: dict) -> dict:
        """Override LLM-compiled requirements with explicit user answers.

        Ensures questionnaire answers are respected — LLM may hallucinate or ignore them.
        """
        if not answers:
            return requirements

        orch = requirements.get("category_3_orchestration", {})
        nodes = requirements.get("category_1_node_activity", {})
        cmds = requirements.get("category_2_commands", {})
        hints = requirements.get("generation_hints", {})

        # ── Phase enforcement ──
        # Check for backup/rollback answers and enforce
        answer_text = json.dumps(answers, default=str).lower()
        phases = orch.get("phases", [])

        no_backup = any(
            "no backup" in str(v).lower() or "not required" in str(v).lower()
            for k, v in answers.items()
            if "backup" in k.lower() or "backup" in str(v).lower()
        )
        no_rollback = any(
            "no rollback" in str(v).lower() or "not required" in str(v).lower()
            for k, v in answers.items()
            if "rollback" in k.lower() or "rollback" in str(v).lower()
        )

        if no_backup and "backup" in [p.lower() for p in phases]:
            phases = [p for p in phases if p.lower() != "backup"]
            logger.info("Enforced: removed 'backup' phase per user answer")
        if no_rollback and "rollback" in [p.lower().replace("rollbackconfiguration", "rollback")
                                           for p in phases]:
            phases = [p for p in phases if "rollback" not in p.lower()]
            logger.info("Enforced: removed 'rollback' phase per user answer")

        orch["phases"] = phases
        requirements["category_3_orchestration"] = orch

        # ── Prompt regex enforcement ──
        # Extract real prompts from SSH logs if available
        ssh_logs = raw_artifacts.get("ssh_logs", "")
        if ssh_logs:
            extracted_prompts = _extract_prompts_from_logs(ssh_logs)
            if extracted_prompts:
                node_list = nodes.get("nodes", [])
                for node in node_list:
                    node_name = node.get("name", "").upper()
                    # Match extracted prompts to nodes
                    for prompt_str in extracted_prompts:
                        prompt_lower = prompt_str.lower()
                        if node_name.lower() in prompt_lower:
                            # Don't override if user already provided a specific prompt
                            if not any("prompt" in k.lower() and node_name.lower() in k.lower()
                                       for k in answers):
                                node["prompt_regex_from_logs"] = prompt_str
                nodes["nodes"] = node_list
                requirements["category_1_node_activity"] = nodes

        # ── Include raw MOP and XML samples in generation hints ──
        wf_hints = hints.get("workflow_yaml", {}) if isinstance(hints.get("workflow_yaml"), dict) else {}

        if no_backup:
            wf_hints["phase_overrides"] = wf_hints.get("phase_overrides", [])
            wf_hints["phase_overrides"].append(
                "DO NOT generate a backup phase. User explicitly stated backup is not required."
            )
        if no_rollback:
            wf_hints["phase_overrides"] = wf_hints.get("phase_overrides", [])
            wf_hints["phase_overrides"].append(
                "DO NOT generate a rollback phase. User explicitly stated rollback is not required."
            )

        # Attach raw MOP text for downstream workflow generation
        if raw_artifacts.get("mop_text"):
            wf_hints["raw_mop_text"] = raw_artifacts["mop_text"]
        if raw_artifacts.get("xml_samples"):
            wf_hints["xml_samples"] = raw_artifacts["xml_samples"]

        hints["workflow_yaml"] = wf_hints
        requirements["generation_hints"] = hints

        # ── Store raw answers for direct downstream access ──
        requirements["developer_answers"] = answers

        return requirements


def _extract_prompts_from_logs(ssh_logs: str) -> list[str]:
    """Extract shell prompt patterns from raw SSH session logs.

    Looks for lines that look like prompts: [user@host ~]$ or user@host:~$
    """
    import re
    prompts = set()
    # Match common prompt patterns at start of lines
    for m in re.finditer(
        r'^(\[?\w+@[\w.-]+\s*[~\w/]*\]?\s*[$#>]\s*)$',
        ssh_logs, re.MULTILINE,
    ):
        prompt = m.group(1).strip()
        if len(prompt) > 3:
            prompts.add(prompt)
    # Also match prompts like "hostname$" or "hostname:~"
    for m in re.finditer(
        r'^([\w.-]+[$:#~>]+\s*)$',
        ssh_logs, re.MULTILINE,
    ):
        prompt = m.group(1).strip()
        if len(prompt) > 3:
            prompts.add(prompt)
    return sorted(prompts)
