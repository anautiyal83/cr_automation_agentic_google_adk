"""Final Report Generator Agent.

Wraps report_generation skill. Generates the final HTML + Excel
pipeline report (Step 10). No LLM required.
"""
from __future__ import annotations

import logging

from google.adk.agents import BaseAgent

from core.logging_config import get_flow_logger

logger = logging.getLogger(__name__)
flow = get_flow_logger(__name__)


class ReportAgent(BaseAgent):
    """Generates final HTML + Excel pipeline report."""
    name: str = "report_generator"
    description: str = "Step 10: Generate final HTML + Excel pipeline report"

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(self, ctx):
        from agents.requirements_agent import requirements_store
        from skills.report_generation import (
            ReportGenerationSkill, SkillInput,
        )

        state = ctx.session.state
        run_id = state.get("run_id", "")
        uc_name = state.get("uc_name", "")
        requirements = requirements_store.get(run_id, {})

        flow.flow("REPORT START — uc=%s", uc_name)

        skill = ReportGenerationSkill()
        inp = SkillInput(
            uc_name=uc_name,
            run_dir=state.get("run_dir", ""),
            step_results=state.get("step_results", []),
            artifacts=state.get("artifacts", {}),
            requirements=requirements,
        )

        output = await skill.generate(inp)
        state["step_10_status"] = output.status

        if output.status == "failed":
            state["step_10_error"] = output.error

        flow.flow("REPORT DONE — status=%s, html=%s, excel=%s",
                   output.status, output.html_path, output.excel_path)

        return
        yield
