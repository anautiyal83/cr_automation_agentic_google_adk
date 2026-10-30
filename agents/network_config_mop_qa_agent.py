"""Network Config MOP QA Agent — Step 7.

Runs MOP generation test BEFORE simulator setup (step 8).
Uses test_mop_only() which doesn't need a running simulator.

On MOP failure, sets _retry_target=6 to re-run NetworkConfigGen.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from google.adk.agents import BaseAgent

from core.logging_config import get_flow_logger

logger = logging.getLogger(__name__)
flow = get_flow_logger(__name__)


class NetworkConfigMopQAAgent(BaseAgent):
    """Step 7: MOP generation test (no simulator needed)."""
    name: str = "network_config_mop_qa"
    description: str = "Step 7: MOP generation test against workflow YAML"

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(self, ctx):
        from shared.llm import get_llm_fn
        from agents.requirements_agent import requirements_store
        from skills.network_config_test import (
            NetworkConfigTestSkill, SkillInput,
        )

        state = ctx.session.state
        run_id = state.get("run_id", "")
        run_dir = state.get("run_dir", "")
        uc_name = state.get("uc_name", "")
        config = state.get("pipeline_config", {})
        requirements = requirements_store.get(run_id, {})
        artifacts = state.get("artifacts", {})

        flow.flow("NETWORK_CONFIG_MOP_QA START — uc=%s", uc_name)

        skill = NetworkConfigTestSkill(
            llm_fn=get_llm_fn(config),
            cli_automation_jar=config.get("cli_automation_jar",
                "lib/cli-automation-standalone.jar"),
        )

        inp = SkillInput(
            uc_name=uc_name,
            workflow_yaml=artifacts.get("workflow_yaml", ""),
            json_output_yaml=artifacts.get("json_template_yaml", ""),
            validation_rules_yaml=artifacts.get("validation_rules_yaml", ""),
            ciq_file_path=state.get("ciq_file_path", ""),
            requirements=requirements,
            run_dir=run_dir,
            cli_automation_jar=config.get("cli_automation_jar",
                "lib/cli-automation-standalone.jar"),
            ciq_processor_jar=config.get("ciq_processor_jar",
                "lib/ciq-processor-1.0.3-cli.jar"),
            html_template_path=config.get("mop_template", ""),
            java_home=config.get("java_home"),
        )

        output = await skill.test_mop_only(inp)

        # Save MOP QA report
        if run_dir and output.report_html:
            report_path = Path(run_dir) / f"{uc_name}_mop-qa-report.html"
            report_path.write_text(output.report_html, encoding="utf-8")

        # Save step 7 status
        mop_failed = any(s.status == "FAIL" for s in output.mop_scenarios)
        state["step_7_status"] = "failed" if mop_failed else "passed"

        # On MOP failure, retry workflow generation (step 6)
        if output.status == "failed":
            state["_retry_target"] = 6
            state["mop_feedback"] = [
                {"scenario": s.name, "status": s.status, "error": s.error[:200]}
                for s in output.mop_scenarios if s.status == "FAIL"
            ]

            if run_dir:
                err_file = Path(run_dir) / "step_7_error.json"
                err_file.write_text(json.dumps({
                    "error": f"MOP test: {output.failed}/{output.total} failed",
                    "retry_target": 6,
                }, indent=2))

        flow.flow("NETWORK_CONFIG_MOP_QA DONE — %d/%d passed",
                   output.passed, output.total)

        return
        yield
