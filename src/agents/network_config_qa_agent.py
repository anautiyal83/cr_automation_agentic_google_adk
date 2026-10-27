"""Network Configuration QA Agent.

Wraps network_config_test skill. Runs MOP generation test (Step 7) and
execution test (Step 9) in a single agent. Classifies failures as
workflow vs simulator for retry routing.

On failure, sets _retry_target so PipelineAgent jumps back to the
appropriate gen agent.
"""
from __future__ import annotations

import logging
from pathlib import Path

from google.adk.agents import BaseAgent

from src.core.logging_config import get_flow_logger

logger = logging.getLogger(__name__)
flow = get_flow_logger(__name__)


class NetworkConfigQAAgent(BaseAgent):
    """QA engine: MOP generation test + execution test + failure classification."""
    name: str = "network_config_qa"
    description: str = "Steps 7+9: MOP generation test + execution test against simulator"

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(self, ctx):
        from src.shared.llm import get_llm_fn
        from src.agents.requirements_agent import requirements_store
        from src.skills.network_config_test import (
            NetworkConfigTestSkill, SkillInput,
        )

        state = ctx.session.state
        run_id = state.get("run_id", "")
        run_dir = state.get("run_dir", "")
        uc_name = state.get("uc_name", "")
        config = state.get("pipeline_config", {})
        requirements = requirements_store.get(run_id, {})
        artifacts = state.get("artifacts", {})

        flow.flow("NETWORK_CONFIG_QA START — uc=%s", uc_name)

        # Determine simulator connection
        simulator_ports = state.get("_simulator_ports", [])
        simulator_port = simulator_ports[0] if simulator_ports else 2222

        skill = NetworkConfigTestSkill(
            llm_fn=get_llm_fn(config),
            cli_automation_jar=config.get("cli_automation_jar",
                "lib/cli-automation-standalone.jar"),
        )

        inp = SkillInput(
            uc_name=uc_name,
            workflow_yaml=artifacts.get("workflow_yaml", ""),
            simulator_config_yaml=artifacts.get("simulator_config_yaml", ""),
            json_output_yaml=artifacts.get("json_template_yaml", ""),
            ciq_file_path=state.get("ciq_file_path", ""),
            requirements=requirements,
            run_dir=run_dir,
            cli_automation_jar=config.get("cli_automation_jar",
                "lib/cli-automation-standalone.jar"),
            java_home=config.get("java_home"),
            simulator_host="localhost",
            simulator_port=simulator_port,
        )

        output = await skill.test(inp)

        # Save QA report
        if run_dir and output.report_html:
            report_path = Path(run_dir) / f"{uc_name}_network-qa-report.html"
            report_path.write_text(output.report_html, encoding="utf-8")

        # Save MOP test status (Step 7)
        mop_failed = any(s.status == "FAIL" for s in output.mop_scenarios)
        state["step_7_status"] = "failed" if mop_failed else "passed"

        # Save execution test status (Step 9)
        exec_failed = any(
            not c.success or c.validation_status == "failed"
            for c in output.command_results
        )
        state["step_9_status"] = "failed" if exec_failed else "passed"

        # On failure, set retry target based on failure classification
        if output.status == "failed" and output.failure_classification:
            classification = output.failure_classification.get("classification", "")
            retry_target = output.failure_classification.get("retry_target", 6)

            state["exec_feedback"] = [
                {"command": c.command, "phase": c.phase, "success": c.success,
                 "output": c.output[:200], "failure": c.failure_reason,
                 "validation_status": c.validation_status}
                for c in output.command_results
                if not c.success or c.validation_status == "failed"
            ]
            state["_retry_target"] = retry_target
            state[f"_reviewer_analysis_{retry_target}"] = (
                f"Failure classification: {classification}. "
                f"Workflow issues: {output.failure_classification.get('workflow_issues', [])}. "
                f"Simulator issues: {output.failure_classification.get('simulator_issues', [])}."
            )

            flow.flow("NETWORK_CONFIG_QA: classification=%s, retry_target=%d",
                       classification, retry_target)

            if run_dir:
                import json
                err_file = Path(run_dir) / "step_9_error.json"
                err_file.write_text(json.dumps({
                    "error": f"{output.failed}/{output.total} tests failed",
                    "classification": classification,
                    "retry_target": retry_target,
                }, indent=2))

        # Stop simulator after testing
        sim_process = state.get("_simulator_process")
        if sim_process:
            try:
                await sim_process.stop()
                flow.flow("NETWORK_CONFIG_QA: Simulator stopped")
            except Exception as e:
                logger.debug("Simulator stop error: %s", e)

        flow.flow("NETWORK_CONFIG_QA DONE — %d/%d passed, mop=%s, exec=%s",
                   output.passed, output.total,
                   "FAIL" if mop_failed else "PASS",
                   "FAIL" if exec_failed else "PASS")

        return
        yield
