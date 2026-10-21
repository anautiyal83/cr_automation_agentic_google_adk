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

Respond with a JSON array:
[
  {{
    "id": "q1",
    "category": "node_activity | commands | orchestration",
    "question": "Human-readable question",
    "importance": "critical | recommended | optional",
    "input_type": "text | boolean | select | password",
    "options": ["opt1", "opt2"],
    "default_value": "sensible default",
    "context": "Why this is needed"
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
        all_answers = {}
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

            # Ask developer (HIL pause)
            answers = await self._ask_developer(ctx, questions, round_num)
            all_answers.update(answers)

            # Re-analyze with new answers
            combined_notes = developer_notes
            if all_answers:
                combined_notes += "\n\n=== Developer Answers ===\n" + json.dumps(all_answers, indent=2)
            analysis = await self._analyze_uc(parsed, uc_name, combined_notes)
            readiness = analysis.get("overall_readiness", "INSUFFICIENT")
            flow.flow("REQUIREMENTS Phase 3: Round %d result: readiness=%s", round_num, readiness)

        # ── Phase 4: Compile requirements ──
        flow.flow("REQUIREMENTS Phase 4: Compiling requirements package")
        requirements = await self._compile_requirements(
            uc_name, analysis, developer_notes, all_answers
        )

        # Store in shared dict (bypasses ADK session state)
        requirements_store[run_id] = requirements

        # Also write to disk for debugging
        if run_dir:
            req_file = Path(run_dir) / "requirements.json"
            req_file.write_text(json.dumps(requirements, indent=2, default=str), encoding="utf-8")

        flow.flow("REQUIREMENTS COMPLETE — readiness=%s, %d questions answered",
                   readiness, len(all_answers))

        return
        yield  # async generator

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

    async def _ask_developer(self, ctx, questions: list, round_num: int) -> dict:
        """Present questions to developer via HIL pause, return answers."""
        run_id = ctx.session.state.get("run_id", "")

        # Use milestone step 0 for requirements gathering
        milestone_step = 0

        # Store questions for UI to display
        milestone_waiting[milestone_step] = {
            "waiting": True,
            "name": f"Requirements Gathering (Round {round_num})",
            "status": "questions",
            "questions": questions,
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
