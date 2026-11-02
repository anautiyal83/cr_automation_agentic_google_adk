"""Helper Script Generation Agent — Step 6b.

Runs after workflow generation (step 6). Scans the workflow YAML for
LOCAL node steps referencing Python scripts, generates them via LLM,
and writes them to the run directory.

Shares step 6 display — no separate UI step.
"""
from __future__ import annotations

import logging
from pathlib import Path

from google.adk.agents import BaseAgent

from core.logging_config import get_flow_logger

logger = logging.getLogger(__name__)
flow = get_flow_logger(__name__)


class HelperScriptGenAgent(BaseAgent):
    """Generates Python helper scripts for LOCAL workflow steps."""
    name: str = "helper_script_gen"
    description: str = "Generate Python scripts for LOCAL node steps (create_xml, parse_response, etc.)"

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(self, ctx):
        from shared.llm import get_llm_fn
        from agents.requirements_agent import requirements_store
        from skills.helper_script_gen import HelperScriptGenSkill, SkillInput

        state = ctx.session.state
        run_id = state.get("run_id", "")
        run_dir = state.get("run_dir", "")
        uc_name = state.get("uc_name", "")
        config = state.get("pipeline_config", {})
        requirements = requirements_store.get(run_id, {})
        artifacts = state.get("artifacts", {})

        workflow_yaml = artifacts.get("workflow_yaml", "")
        if not workflow_yaml:
            flow.flow("HELPER_SCRIPT_GEN: No workflow YAML — skipping")
            return
            yield

        flow.flow("HELPER_SCRIPT_GEN START — uc=%s", uc_name)

        skill = HelperScriptGenSkill(llm_fn=get_llm_fn(config))
        output = await skill.generate(SkillInput(
            uc_name=uc_name,
            workflow_yaml=workflow_yaml,
            requirements=requirements,
            run_dir=run_dir,
        ))

        # Write scripts to run directory
        if run_dir and output.scripts:
            scripts_dir = Path(run_dir) / "scripts"
            scripts_dir.mkdir(parents=True, exist_ok=True)
            for name, content in output.scripts.items():
                script_path = scripts_dir / name
                script_path.write_text(content, encoding="utf-8")
                flow.flow("HELPER_SCRIPT_GEN: Wrote %s (%d chars)", name, len(content))

            # Store script paths in artifacts for downstream use
            artifacts["helper_scripts"] = {
                name: str(scripts_dir / name) for name in output.scripts
            }
            state["artifacts"] = artifacts

        if output.status == "failed":
            # Step 6 is shared with network_config_gen: keep its error and add ours, so the
            # step 6 milestone shows failed (spec 003 H-1)
            state["step_6_status"] = "failed"
            if run_dir:
                import json
                err_file = Path(run_dir) / "step_6_error.json"
                previous = {}
                if err_file.exists():
                    try:
                        previous = json.loads(err_file.read_text(encoding="utf-8"))
                    except ValueError:
                        previous = {}
                message = f"Helper scripts: {output.error}"
                combined = "; ".join(e for e in (previous.get("error", ""), message) if e)
                err_file.write_text(json.dumps({**previous, "error": combined,
                                                "helper_script_errors": output.error}, indent=2),
                                    encoding="utf-8")

        if output.error:
            flow.flow("HELPER_SCRIPT_GEN DONE — status=%s, error=%s", output.status, output.error[:200])
        else:
            flow.flow("HELPER_SCRIPT_GEN DONE — %d scripts generated", len(output.scripts))

        return
        yield
