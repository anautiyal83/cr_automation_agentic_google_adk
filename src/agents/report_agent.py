"""Final Report Generator Agent.

Generates the final HTML + Excel pipeline report (Step 10).
Currently delegates to the existing report generator via cr_bridge
until a dedicated report skill is created.
"""
from __future__ import annotations

import logging
from pathlib import Path

from google.adk.agents import BaseAgent

from src.core.logging_config import get_flow_logger

logger = logging.getLogger(__name__)
flow = get_flow_logger(__name__)


class ReportAgent(BaseAgent):
    """Generates final HTML + Excel pipeline report."""
    name: str = "report_generator"
    description: str = "Step 10: Generate final HTML + Excel pipeline report"

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(self, ctx):
        from src.shared.cr_bridge import get_agent_class

        state = ctx.session.state
        uc_name = state.get("uc_name", "")
        config = state.get("pipeline_config", {})

        flow.flow("REPORT START — uc=%s", uc_name)

        # Build step_data from state
        step_data = self._build_step_data(state)

        try:
            ReportGenerator = get_agent_class("src.agents.report_generator", "ReportGenerator")
            agent = ReportGenerator()

            # Call the existing agent's execute method
            from src.shared.cr_bridge import import_cr_module
            models_mod = import_cr_module("src.core.models")
            output = await agent.execute(
                input_data=step_data,
                config=config,
            )
            result = output.model_dump()

            state["step_10_status"] = result.get("status", "passed")
            flow.flow("REPORT DONE — status=%s", result.get("status"))

        except Exception as e:
            logger.error("Report generation failed: %s", e, exc_info=True)
            state["step_10_status"] = "failed"
            state["step_10_error"] = str(e)
            flow.flow("REPORT FAILED — %s", e)

        return
        yield

    @staticmethod
    def _build_step_data(state: dict) -> dict:
        """Build input dict for the report generator."""
        from src.agents.requirements_agent import requirements_store
        run_id = state.get("run_id", "")
        requirements = requirements_store.get(run_id, {})

        doc_set = dict(state.get("document_set", {}))
        return {
            "uc_name": state.get("uc_name", ""),
            "run_dir": state.get("run_dir", ""),
            "ciq_file_path": state.get("ciq_file_path", ""),
            "document_set": doc_set,
            "artifacts": state.get("artifacts", {}),
            "step_results": state.get("step_results", []),
            "requirements": requirements,
            "run_data": state.get("run_data", {}),
        }
