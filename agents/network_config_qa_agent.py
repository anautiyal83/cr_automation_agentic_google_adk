"""Network Configuration Execution QA Agent — Step 9.

Runs execution test AFTER simulator setup (step 8).
MOP test (step 7) is handled separately by NetworkConfigMopQAAgent.

On failure, classifies as workflow vs simulator issue and sets
_retry_target so PipelineAgent jumps back to the appropriate agent.
"""
from __future__ import annotations

import logging
from pathlib import Path

from google.adk.agents import BaseAgent

from agents.network_simulator_setup_agent import mockwire_jar
from core.logging_config import get_flow_logger

logger = logging.getLogger(__name__)
flow = get_flow_logger(__name__)


class _SimulatorControl:
    """Scenario runs restart MockWire; keep it registered so every exit path stops it."""

    def __init__(self, run_id: str):
        self.run_id = run_id

    async def stop(self) -> None:
        from shared.state import stop_simulator
        await stop_simulator(self.run_id)

    def register(self, sim) -> None:
        from shared.state import simulator_processes
        simulator_processes[self.run_id] = sim


class NetworkConfigQAAgent(BaseAgent):
    """Step 9: Execution test against simulator + failure classification."""
    name: str = "network_config_exec_qa"
    description: str = "Step 9: Execution test against simulator"

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

        flow.flow("NETWORK_CONFIG_QA START — uc=%s", uc_name)

        # Step 8 writes the workflow copy pointed at the running simulator (read from disk:
        # ADK session state is unreliable across agents)
        overlay = Path(run_dir) / "simulator" / f"{uc_name}.yaml" if run_dir else None

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
            execution_workflow_path=str(overlay) if overlay and overlay.exists() else "",
            mockwire_jar=mockwire_jar(config),
            execution_report_jar=config.get("execution_report_jar") or SkillInput.execution_report_jar,
            simulator_control=_SimulatorControl(run_id),
        )

        output = await skill.test(inp)

        # Save the test-case report (HTML for review, JSON for the final report / tooling)
        if run_dir and output.report_html:
            report_path = Path(run_dir) / f"{uc_name}_network-qa-report.html"
            report_path.write_text(output.report_html, encoding="utf-8")
        if run_dir and output.scenario_results:
            import json
            (Path(run_dir) / f"{uc_name}_test-cases.json").write_text(json.dumps({
                "uc_name": uc_name, "total": output.total, "passed": output.passed,
                "failed": output.failed, "notes": output.notes,
                "test_cases": output.scenario_results}, indent=2), encoding="utf-8")

        # Save execution test status (Step 9): every test case must behave as expected
        exec_failed = output.status != "passed" or not inp.execution_workflow_path
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
                    "failed_test_cases": [
                        {"id": t.id, "scenario": t.description, "target": t.target_node, "errors": t.errors}
                        for t in output.test_cases if t.status != "PASS"],
                    "classification": classification,
                    "retry_target": retry_target,
                    "simulator_issues": output.failure_classification.get("simulator_issues", []),
                    "workflow_issues": output.failure_classification.get("workflow_issues", []),
                    # exact commands MockWire had no response for (from its event log)
                    "unmatched_commands": output.failure_classification.get("unmatched_commands", []),
                }, indent=2), encoding="utf-8")
        elif exec_failed and run_dir:
            # Failed without a routable cause (nothing ran): the error file is the step status
            import json
            (Path(run_dir) / "step_9_error.json").write_text(json.dumps({
                "error": output.error or f"{output.failed}/{output.total} tests failed",
                "notes": output.notes}, indent=2), encoding="utf-8")

        # Stop simulator after testing (a step 8 retry starts a fresh one)
        try:
            from shared.state import stop_simulator
            await stop_simulator(run_id)
            flow.flow("NETWORK_CONFIG_QA: Simulator stopped")
        except Exception as e:
            logger.warning("Simulator stop error: %s", e)

        flow.flow("NETWORK_CONFIG_EXEC_QA DONE — %d/%d passed, exec=%s",
                   output.passed, output.total,
                   "FAIL" if exec_failed else "PASS")

        return
        yield
