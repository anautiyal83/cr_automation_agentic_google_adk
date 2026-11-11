"""Network Configuration Generator Agent.

Wraps network_config_gen skill. Generates the execution workflow YAML (Step 6)
with ${...} runtime placeholders.
"""
from __future__ import annotations

import logging
from pathlib import Path

from google.adk.agents import BaseAgent

from core.logging_config import get_flow_logger

logger = logging.getLogger(__name__)
flow = get_flow_logger(__name__)


class NetworkConfigGenAgent(BaseAgent):
    """Generates execution workflow YAML from requirements."""
    name: str = "network_config_gen"
    description: str = "Step 6: Generate execution workflow YAML via LLM"

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(self, ctx):
        from shared.llm import get_llm_fn
        from agents.requirements_agent import requirements_store
        from skills.network_config_gen import (
            NetworkConfigGenSkill, SkillInput,
        )

        state = ctx.session.state
        run_id = state.get("run_id", "")
        run_dir = state.get("run_dir", "")
        uc_name = state.get("uc_name", "")
        config = state.get("pipeline_config", {})
        requirements = requirements_store.get(run_id, {})
        # The run's phases and backup route (spec 010) — from the run, not the cached requirements
        requirements = {**requirements,
                        "run_options": (state.get("document_set") or {}).get("run_options") or {}}

        flow.flow("NETWORK_CONFIG_GEN START — uc=%s", uc_name)

        skill = NetworkConfigGenSkill(llm_fn=get_llm_fn(config))

        # Check for retry context (feedback from QA agent)
        retry_count = state.get("retry_count_6", 0)
        if retry_count > 0 and state.get("_network_config_gen_skill"):
            skill = state["_network_config_gen_skill"]
            feedback_data = state.get("exec_feedback", [])
            reviewer_analysis = state.get("_reviewer_analysis_9", state.get("_reviewer_analysis_7", ""))

            from skills.network_config_gen import RegenerateFeedback
            feedback = RegenerateFeedback(
                test_results=feedback_data,
                reviewer_analysis=reviewer_analysis,
                attempt_number=retry_count + 1,
            )
            flow.flow("NETWORK_CONFIG_GEN: Regenerating (attempt %d)", retry_count + 1)
            output = await skill.regenerate(feedback)
        else:
            inp = SkillInput(
                uc_name=uc_name,
                requirements=requirements,
                generation_hints=requirements.get("generation_hints", {}),
            )
            output = await skill.generate(inp)
            state["_network_config_gen_skill"] = skill

        # Save artifacts
        artifacts = state.get("artifacts", {})
        if output.workflow_yaml:
            artifacts["workflow_yaml"] = output.workflow_yaml
            if run_dir:
                p = Path(run_dir) / f"{uc_name}.yaml"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(output.workflow_yaml, encoding="utf-8")

        state["artifacts"] = artifacts

        # Problems the developer has already answered do not block step 6 again (EIR: the same question
        # came back after every answer): the review lists them as notes to check
        from shared import clarifications
        answered = clarifications.answered_keys(requirements)
        accepted = [i for i in output.developer_issues if clarifications.key(i) in answered]
        if (output.status == "failed" and accepted and not output.error
                and all(e in accepted for e in output.validation_errors)):
            output.status = "passed"
            flow.flow("NETWORK_CONFIG_GEN: %d problem(s) the developer already answered — not blocking", len(accepted))
        if run_dir:
            import json
            notes_file = Path(run_dir) / "step_6_notes.json"
            if accepted and output.status == "passed":
                notes_file.write_text(json.dumps({"notes": accepted}, indent=2), encoding="utf-8")
            elif notes_file.exists():
                notes_file.unlink()
        state["step_6_status"] = output.status

        if output.status == "failed":
            state["step_6_error"] = output.error or "; ".join(output.validation_errors)
            if run_dir:
                import json
                err_file = Path(run_dir) / "step_6_error.json"
                # failed_agents: a milestone rerun restarts from the workflow, not just the scripts
                err_file.write_text(json.dumps({"error": state["step_6_error"],
                                                "failed_agents": ["network_config_gen"]}, indent=2))

        # What the correction rounds could not fix AND a developer can answer: offered as step 1
        # questions at the milestone. Framework errors (placeholders, regexes, …) are not asked (DEP-53)
        clarifications.save(run_dir, 6, clarifications.from_errors(6, output.developer_issues, answered=answered)
                            if output.status == "failed" else [])

        flow.flow("NETWORK_CONFIG_GEN DONE — status=%s, errors=%d",
                   output.status, len(output.validation_errors))

        return
        yield
