"""CIQ Validation & JSON Output Generator Agent.

Wraps ciq_validation_json_gen skill. Generates validation-rules.yaml (Step 3)
and json-output.yaml (Step 4) in a single agent execution.
"""
from __future__ import annotations

import logging
from pathlib import Path

from google.adk.agents import BaseAgent

from src.core.logging_config import get_flow_logger

logger = logging.getLogger(__name__)
flow = get_flow_logger(__name__)


class CiqValidationJsonGenAgent(BaseAgent):
    """Generates validation-rules.yaml + json-output.yaml from requirements."""
    name: str = "ciq_validation_json_gen"
    description: str = "Steps 3+4: Generate CIQ validation rules and JSON output template"

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(self, ctx):
        from src.shared.llm import get_llm_fn
        from src.agents.requirements_agent import requirements_store
        from src.skills.ciq_validation_json_gen import (
            CiqValidationJsonGenSkill, SkillInput,
        )

        state = ctx.session.state
        run_id = state.get("run_id", "")
        run_dir = state.get("run_dir", "")
        uc_name = state.get("uc_name", "")
        config = state.get("pipeline_config", {})
        requirements = requirements_store.get(run_id, {})

        flow.flow("CIQ_VAL_JSON_GEN START — uc=%s", uc_name)

        skill = CiqValidationJsonGenSkill(llm_fn=get_llm_fn(config))

        # Check for retry context (feedback from QA agent)
        retry_count = state.get("retry_count_3", 0)
        if retry_count > 0 and state.get("_ciq_gen_skill"):
            # Reuse stateful skill for regenerate()
            skill = state["_ciq_gen_skill"]
            feedback_data = state.get("qa_feedback", [])
            reviewer_analysis = state.get("_reviewer_analysis_5", "")

            from src.skills.ciq_validation_json_gen import RegenerateFeedback
            feedback = RegenerateFeedback(
                test_results=feedback_data,
                reviewer_analysis=reviewer_analysis,
                attempt_number=retry_count + 1,
            )
            flow.flow("CIQ_VAL_JSON_GEN: Regenerating (attempt %d)", retry_count + 1)
            output = await skill.regenerate(feedback)
        else:
            # First run
            inp = SkillInput(
                uc_name=uc_name,
                requirements=requirements,
                generation_hints=requirements.get("generation_hints", {}),
            )
            output = await skill.generate(inp)
            # Store skill instance for potential regenerate()
            state["_ciq_gen_skill"] = skill

        # Save artifacts
        artifacts = state.get("artifacts", {})
        if output.validation_rules_yaml:
            artifacts["validation_rules_yaml"] = output.validation_rules_yaml
            state["step_3_status"] = "passed" if not output.validation_errors else "failed"
            if run_dir:
                p = Path(run_dir) / f"{uc_name}_validation-rules.yaml"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(output.validation_rules_yaml, encoding="utf-8")

        if output.json_output_yaml:
            artifacts["json_template_yaml"] = output.json_output_yaml
            state["step_4_status"] = "passed" if not output.json_output_errors else "failed"
            if run_dir:
                p = Path(run_dir) / f"{uc_name}_json-output.yaml"
                p.write_text(output.json_output_yaml, encoding="utf-8")

        state["artifacts"] = artifacts

        if output.status == "failed":
            state["step_3_status"] = "failed"
            state["step_3_error"] = output.error or "; ".join(output.validation_errors + output.json_output_errors)
            if run_dir:
                import json
                err_file = Path(run_dir) / "step_3_error.json"
                err_file.write_text(json.dumps({"error": state["step_3_error"]}, indent=2))

        flow.flow("CIQ_VAL_JSON_GEN DONE — status=%s, val_errors=%d, json_errors=%d",
                   output.status, len(output.validation_errors), len(output.json_output_errors))

        return
        yield
