"""CIQ Validation & JSON Output QA Agent.

Wraps ciq_validation_json_test skill. Runs test cases against the generated
validation-rules.yaml and json-output.yaml (Step 5).

On failure, sets feedback for the gen agent's regenerate() loop.
"""
from __future__ import annotations

import logging
from pathlib import Path

from google.adk.agents import BaseAgent

from src.core.logging_config import get_flow_logger

logger = logging.getLogger(__name__)
flow = get_flow_logger(__name__)


class CiqValidationJsonQAAgent(BaseAgent):
    """QA engine: generates test cases, runs JAR, produces QA report."""
    name: str = "ciq_validation_json_qa"
    description: str = "Step 5: QA test validation rules + JSON output via ciq-processor JAR"

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(self, ctx):
        from src.shared.llm import get_llm_fn
        from src.agents.requirements_agent import requirements_store
        from src.skills.ciq_validation_json_test import (
            CiqValidationJsonTestSkill, SkillInput,
        )

        state = ctx.session.state
        run_id = state.get("run_id", "")
        run_dir = state.get("run_dir", "")
        uc_name = state.get("uc_name", "")
        config = state.get("pipeline_config", {})
        requirements = requirements_store.get(run_id, {})
        artifacts = state.get("artifacts", {})

        flow.flow("CIQ_VAL_JSON_QA START — uc=%s", uc_name)

        skill = CiqValidationJsonTestSkill(
            llm_fn=get_llm_fn(config),
            jar_path=config.get("ciq_processor_jar", "lib/ciq-processor-1.0.3-cli.jar"),
        )

        inp = SkillInput(
            uc_name=uc_name,
            ciq_file_path=state.get("ciq_file_path", ""),
            validation_rules_yaml=artifacts.get("validation_rules_yaml", ""),
            json_output_yaml=artifacts.get("json_template_yaml", ""),
            requirements=requirements,
            run_dir=run_dir,
            jar_path=config.get("ciq_processor_jar", "lib/ciq-processor-1.0.3-cli.jar"),
            java_home=config.get("java_home"),
        )

        output = await skill.test(inp)

        # Save QA report
        state["step_5_status"] = output.status
        if run_dir and output.report_html:
            report_path = Path(run_dir) / f"{uc_name}_ciq-qa-report.html"
            report_path.write_text(output.report_html, encoding="utf-8")

        # On failure, store feedback for the gen agent's regenerate() loop
        if output.status == "failed":
            failed_tcs = [
                {"id": tc.id, "description": tc.description, "status": tc.status,
                 "expected": tc.expected_outcome, "actual": tc.actual_outcome,
                 "errors": [str(e) for e in tc.errors[:5]]}
                for tc in output.test_cases if tc.status == "FAIL"
            ]
            state["qa_feedback"] = failed_tcs
            state["step_5_error"] = f"{output.failed}/{output.total} test cases failed"
            state["_retry_target"] = 3  # retry gen agent

            if run_dir:
                import json
                err_file = Path(run_dir) / "step_5_error.json"
                err_file.write_text(json.dumps({
                    "error": state["step_5_error"],
                    "failed_test_cases": failed_tcs[:10],
                }, indent=2))

        flow.flow("CIQ_VAL_JSON_QA DONE — %d/%d passed, status=%s",
                   output.passed, output.total, output.status)

        return
        yield
