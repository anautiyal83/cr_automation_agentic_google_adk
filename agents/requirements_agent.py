"""RequirementsGatherer Agent — thin pipeline adapter over RequirementGatheringSkill.

Replaces Steps 1 (DocParser) and 2 (UCAnalyzer) with a single agent that
delegates to the reusable RequirementGatheringSkill for document parsing, LLM analysis,
questionnaire generation, and requirements compilation.

Pipeline-specific concerns (milestone events, session state, cr_import) stay here.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from google.adk.agents import BaseAgent

from core.logging_config import get_flow_logger
from shared.state import (
    milestone_events,
    milestone_decisions,
    milestone_waiting,
)
from skills.requirement_gathering import RequirementGatheringSkill, SkillInput, SkillOutput

logger = logging.getLogger(__name__)
flow = get_flow_logger(__name__)

# Shared dict for requirements — read by step nodes via _get_step_data
requirements_store: dict[str, dict] = {}  # run_id -> requirements package


class RequirementsGathererNode(BaseAgent):
    """Gatekeeper agent that gathers complete requirements before YAML generation.

    Delegates all logic to RequirementGatheringSkill, keeping only pipeline integration.
    """
    name: str = "requirements_gatherer"
    description: str = "Parse documents, analyze UC, gather developer requirements"

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(self, ctx):
        run_id = ctx.session.state.get("run_id", "")
        run_dir = ctx.session.state.get("run_dir", "")
        uc_name = ctx.session.state.get("uc_name", "")
        doc_set = ctx.session.state.get("document_set", {})
        developer_notes = doc_set.get("developer_notes", "") or ""

        flow.flow("REQUIREMENTS START — run_id=%s, uc=%s", run_id[:8], uc_name)

        # ── Phase 1: Extract pre-parsed data from session state ──
        flow.flow("REQUIREMENTS Phase 1: Parsing documents")
        parsed = await self._parse_documents(ctx)

        # Store parsed data in session state for downstream steps
        for key in ("ciq_data", "mop_data", "intermediate_repr", "command_outputs", "constraints"):
            if parsed.get(key):
                ctx.session.state[key] = parsed[key]

        # ── Build SkillInput ──
        vendor_profile = doc_set.get("vendor_profile") or None
        flow.flow("REQUIREMENTS: vendor_profile present=%s, type=%s, doc_set_keys=%s",
                   vendor_profile is not None,
                   type(vendor_profile).__name__ if vendor_profile else "None",
                   list(doc_set.keys()) if doc_set else "empty")
        if vendor_profile:
            gw = vendor_profile.get("vendor", {}).get("access_gateway", {})
            flow.flow("REQUIREMENTS: gateway_enabled=%s, gateway_name=%s, ssh_user=%s, nodes=%d",
                       gw.get("enabled"), gw.get("name"),
                       vendor_profile.get("vendor", {}).get("ssh_defaults", {}).get("user"),
                       len(vendor_profile.get("category_1_node_activity", {}).get("nodes", [])))
        else:
            flow.flow("REQUIREMENTS: NO vendor_profile — SSH/PAM questions will NOT be filtered")
        skill_input = SkillInput(
            uc_name=uc_name,
            ciq_file_path=ctx.session.state.get("ciq_file_path", ""),
            ciq_data=parsed.get("ciq_data"),
            mop_file_path=doc_set.get("mop_file_path", ""),
            ssh_log_path=doc_set.get("command_outputs_path", ""),
            xml_sample_paths=doc_set.get("sample_io_xml_paths", []) or [],
            developer_notes=developer_notes,
            vendor_profile=vendor_profile,
        )

        # ── Create skill with local LLM wrapper ──
        from shared.llm import get_llm_fn
        config = ctx.session.state.get("pipeline_config", {})

        skill = RequirementGatheringSkill(llm_fn=get_llm_fn(config))

        # ── Phase 1+2: Parse + Analyze ──
        flow.flow("REQUIREMENTS Phase 1+2: Deterministic extraction + LLM analysis")
        output = await skill.parse_and_analyze(skill_input)

        # Store raw artifacts in session state
        ctx.session.state["raw_artifacts"] = skill._raw_artifacts

        readiness = output.readiness
        flow.flow("REQUIREMENTS Phase 2 result: readiness=%s", readiness)

        # ── Phase 3: Questionnaire loop ──
        if output.questions:
            round_num = 0
            rounds_per_batch = 3
            keep_asking = True

            while output.questions and keep_asking:
                batch_rounds = 0
                while output.questions and batch_rounds < rounds_per_batch:
                    round_num += 1
                    batch_rounds += 1
                    flow.flow("REQUIREMENTS Phase 3: Round %d — asking developer", round_num)

                    # Generate questionnaire HTML artifact
                    if run_dir:
                        q_html = skill.generate_questionnaire_doc(output.questions, round_num)
                        q_path = Path(run_dir) / f"{uc_name}_questionnaire_round{round_num}.html"
                        q_path.write_text(q_html, encoding="utf-8")
                        artifact_path = str(q_path)
                    else:
                        artifact_path = ""

                    # Ask developer via milestone
                    answers = await self._ask_developer(
                        ctx, output.questions, round_num, artifact_path, output.all_answers,
                    )

                    # Incorporate answers and re-analyze
                    output = await skill.incorporate_answers(answers)
                    readiness = output.readiness
                    flow.flow("REQUIREMENTS Phase 3: Round %d result: readiness=%s", round_num, readiness)

                    if not output.questions:
                        keep_asking = False
                        break

                # After batch, ask user to continue or proceed
                if output.questions and keep_asking:
                    remaining_gaps = skill.get_remaining_gaps()
                    user_choice = await self._ask_continue_or_clarify(
                        ctx, readiness, remaining_gaps, round_num,
                    )
                    if user_choice == "continue":
                        flow.flow("REQUIREMENTS: User chose to continue with gaps (readiness=%s)", readiness)
                        keep_asking = False
                    else:
                        flow.flow("REQUIREMENTS: User chose to answer more questions")

        # ── Phase 4: Compile final ──
        flow.flow("REQUIREMENTS Phase 4: Compiling requirements package")
        output = await skill.compile_final()

        # Store in shared dict (bypasses ADK session state)
        requirements_store[run_id] = output.requirements

        # Write JSON to disk
        if run_dir:
            req_file = Path(run_dir) / "requirements.json"
            req_file.write_text(output.requirements_json, encoding="utf-8")

        # ── Phase 5: Write summary HTML ──
        flow.flow("REQUIREMENTS Phase 5: Writing summary document")
        if run_dir:
            summary_path = Path(run_dir) / f"{uc_name}_requirements-summary.html"
            summary_path.write_text(output.requirements_html, encoding="utf-8")
            flow.flow("REQUIREMENTS: Summary doc written to %s", summary_path)

        # ── Phase 6: Milestone pause — developer reviews summary ──
        flow.flow("REQUIREMENTS: Pausing for developer review of summary")
        milestone_step = 2
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
            from shared.state import pipeline_actions
            pipeline_actions[run_id] = {"action": "cancel"}

        flow.flow(
            "REQUIREMENTS COMPLETE — readiness=%s, %d questions answered, decision=%s",
            readiness, len(output.all_answers), decision,
        )

        return
        yield  # async generator

    # ------------------------------------------------------------------
    # Pipeline-specific helpers
    # ------------------------------------------------------------------

    async def _parse_documents(self, ctx) -> dict:
        """Extract initial data from session state for the skill.

        The requirement_gathering skill handles all document parsing internally
        via its deterministic parsers (CIQ, MOP, SSH, XML). This method just
        extracts any pre-parsed data from session state.
        """
        parsed: dict[str, Any] = {}
        for key in ("intermediate_repr", "ciq_data", "mop_data", "constraints", "command_outputs"):
            if ctx.session.state.get(key):
                parsed[key] = ctx.session.state[key]
        return parsed

    async def _ask_developer(self, ctx, questions: list, round_num: int,
                             artifact_path: str,
                             previous_answers: dict | None = None) -> dict:
        """Present questions to developer via HIL milestone pause."""
        if previous_answers:
            for q in questions:
                if isinstance(q, dict):
                    q_id = q.get("id", "")
                    if q_id in previous_answers and not q.get("default_value"):
                        q["default_value"] = previous_answers[q_id]

        milestone_step = 0
        milestone_waiting[milestone_step] = {
            "waiting": True,
            "name": f"Requirements Gathering (Round {round_num})",
            "status": "questions",
            "questions": questions,
            "artifact_file": artifact_path,
        }

        if milestone_step not in milestone_events:
            milestone_events[milestone_step] = asyncio.Event()
        else:
            milestone_events[milestone_step].clear()

        flow.flow("REQUIREMENTS: Waiting for developer answers (%d questions)", len(questions))

        evt = milestone_events[milestone_step]
        await evt.wait()
        evt.clear()

        decision = milestone_decisions.pop(milestone_step, "continue")
        milestone_waiting.pop(milestone_step, None)

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

    async def _ask_continue_or_clarify(self, ctx, readiness: str,
                                        remaining_gaps: list, round_num: int) -> str:
        """Ask user whether to continue with gaps or answer more questions."""
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

        answers = await self._ask_developer(ctx, questions, round_num + 1, "")
        decision = answers.get("_continue_decision", "")
        if "answer more" in str(decision).lower() or "clarify" in str(decision).lower():
            return "clarify"
        return "continue"
