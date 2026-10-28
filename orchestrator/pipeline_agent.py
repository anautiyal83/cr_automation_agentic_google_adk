"""PipelineAgent — 7-agent orchestrator with built-in milestones and retry routing.

Executes 7 agents sequentially:
1. RequirementsGatherer — parse docs, questionnaire, compile requirements
2. CiqValidationJsonGen — generate validation-rules.yaml + json-output.yaml
3. CiqValidationJsonQA — test generated YAMLs via JAR
4. NetworkConfigGen — generate workflow YAML
5. NetworkSimulatorSetup — generate simulator config, start MockWire, sanity check
6. NetworkConfigQA — MOP test + execution test
7. ReportGenerator — final HTML + Excel report

After each agent (except requirements and report), the pipeline:
- Publishes step events for the UI progress tracker
- Pauses for milestone review (built-in, not a separate agent)
- Handles user decisions (continue/rerun/cancel)
- On QA failure, reads _retry_target from state and jumps back
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any
from uuid import UUID

from google.adk.agents import BaseAgent

from core.events import PipelineEvent, event_bus
from core.logging_config import get_flow_logger
from shared.state import (
    pipeline_actions,
    milestone_events,
    milestone_decisions,
    milestone_waiting,
)

logger = logging.getLogger(__name__)
flow = get_flow_logger(__name__)

# Maps agent name → display step numbers for the UI (10-step tracker)
_AGENT_DISPLAY_STEPS: dict[str, list[int]] = {
    "requirements_gatherer": [1, 2],
    "ciq_validation_json_gen": [3, 4],
    "ciq_validation_json_qa": [5],
    "network_config_gen": [6],
    "network_simulator_setup": [8],
    "network_config_qa": [7, 9],
    "report_generator": [10],
}

# Maps agent name → milestone config (step_num, name) for pause after execution
_AGENT_MILESTONES: dict[str, tuple[int, str]] = {
    "ciq_validation_json_gen": (4, "CIQ Validation & JSON Output"),
    "ciq_validation_json_qa": (5, "QA-Validation Results"),
    "network_config_gen": (6, "Execution Workflow"),
    "network_simulator_setup": (8, "Simulator Setup"),
    "network_config_qa": (9, "Network Configuration QA"),
}

# Maps agent name → index for retry routing
_AGENT_NAMES = [
    "requirements_gatherer",       # 0
    "ciq_validation_json_gen",     # 1
    "ciq_validation_json_qa",      # 2
    "network_config_gen",          # 3
    "network_simulator_setup",     # 4
    "network_config_qa",           # 5
    "report_generator",            # 6
]

# Retry target step → agent index
_RETRY_STEP_TO_INDEX: dict[int, int] = {
    3: 1,   # retry CiqValidationJsonGen
    4: 1,   # retry CiqValidationJsonGen
    6: 3,   # retry NetworkConfigGen
    8: 4,   # retry NetworkSimulatorSetup
}


class PipelineAgent(BaseAgent):
    """7-agent orchestrator with built-in milestone pauses and retry routing."""

    name: str = "netconfig_pipeline"
    description: str = "NetConfig pipeline: 7 agents with milestone review and retry loops"

    _agents_list: list[BaseAgent] = []

    model_config = {"arbitrary_types_allowed": True}

    def _configure(self, agents_list: list[BaseAgent]):
        """Configure after construction (Pydantic model limitation)."""
        self._agents_list = agents_list
        self.sub_agents = list(agents_list)

    async def _run_async_impl(self, ctx):
        index = 0
        total = len(self._agents_list)
        run_id_str = ctx.session.state.get("run_id", "")

        flow.flow("PIPELINE START — %d agents, run_id=%s", total, run_id_str[:8] if run_id_str else "?")
        pipeline_t0 = time.monotonic()

        while index < total:
            agent = self._agents_list[index]

            # ----- CHECK PIPELINE ACTIONS -----
            action = pipeline_actions.pop(run_id_str, None)
            if action:
                result = await self._handle_action(action, ctx, index, run_id_str)
                if result == "break":
                    break
                elif isinstance(result, int):
                    index = result
                    continue

            # ----- CHECK TERMINATION -----
            if ctx.session.state.get("pipeline_cancelled") or ctx.session.state.get("pipeline_failed"):
                break

            # ----- CLEAR old error files before re-run -----
            run_dir = ctx.session.state.get("run_dir", "")
            for step_num in _AGENT_DISPLAY_STEPS.get(agent.name, []):
                if run_dir:
                    err = Path(run_dir) / f"step_{step_num}_error.json"
                    if err.exists():
                        err.unlink()

            # ----- PUBLISH step_started -----
            display_steps = _AGENT_DISPLAY_STEPS.get(agent.name, [])
            for sn in display_steps:
                await self._publish(run_id_str, "step_started", sn)

            # ----- EXECUTE AGENT -----
            agent_t0 = time.monotonic()
            flow.flow("AGENT START [%d/%d] %s", index + 1, total, agent.name)

            try:
                async for evt in agent.run_async(ctx):
                    yield evt
            except Exception as e:
                elapsed = time.monotonic() - agent_t0
                logger.error("Agent %s failed after %.1fs: %s", agent.name, elapsed, e, exc_info=True)
                flow.flow("AGENT FAILED [%d/%d] %s — %.1fs", index + 1, total, agent.name, elapsed)
                ctx.session.state["pipeline_failed"] = True
                ctx.session.state["pipeline_error"] = str(e)
                for sn in display_steps:
                    await self._publish(run_id_str, "step_failed", sn, {"error": str(e)})
                break

            elapsed = time.monotonic() - agent_t0
            flow.flow("AGENT DONE [%d/%d] %s — %.1fs", index + 1, total, agent.name, elapsed)

            # ----- PUBLISH step_completed -----
            for sn in display_steps:
                status = ctx.session.state.get(f"step_{sn}_status", "passed")
                evt_type = "step_completed" if status != "failed" else "step_failed"
                await self._publish(run_id_str, evt_type, sn, {"status": status})

            # ----- CHECK post-agent actions (set during agent execution) -----
            action = pipeline_actions.pop(run_id_str, None)
            if action:
                result = await self._handle_action(action, ctx, index, run_id_str)
                if result == "break":
                    break
                elif isinstance(result, int):
                    index = result
                    continue

            # ----- MILESTONE PAUSE (built-in) -----
            milestone_cfg = _AGENT_MILESTONES.get(agent.name)
            if milestone_cfg:
                ms_step, ms_name = milestone_cfg
                decision = await self._milestone_pause(ctx, ms_step, ms_name, run_id_str)

                if decision == "cancel":
                    ctx.session.state["pipeline_cancelled"] = True
                    pipeline_actions[run_id_str] = {"action": "cancel"}
                    break
                elif decision == "rerun":
                    # Determine retry target from the agent's output
                    retry_target = ctx.session.state.pop("_retry_target", None)
                    if retry_target and retry_target in _RETRY_STEP_TO_INDEX:
                        target_index = _RETRY_STEP_TO_INDEX[retry_target]
                    else:
                        target_index = index  # re-run same agent
                    flow.flow("RERUN — jumping to agent index %d (retry_target=%s)", target_index, retry_target)
                    await self._publish(run_id_str, "retry_started", retry_target or display_steps[0] if display_steps else 0)
                    index = target_index
                    continue

            # ----- CHECK _retry_target from QA agents (auto-retry without milestone) -----
            retry_target = ctx.session.state.pop("_retry_target", None)
            if retry_target is not None and retry_target in _RETRY_STEP_TO_INDEX:
                target_index = _RETRY_STEP_TO_INDEX[retry_target]
                retry_count_key = f"retry_count_{retry_target}"
                ctx.session.state[retry_count_key] = ctx.session.state.get(retry_count_key, 0) + 1
                flow.flow("AUTO-RETRY — step %d → agent index %d (count=%d)",
                           retry_target, target_index, ctx.session.state[retry_count_key])
                await self._publish(run_id_str, "retry_started", retry_target)
                index = target_index
                continue

            index += 1

        pipeline_elapsed = time.monotonic() - pipeline_t0
        final = "cancelled" if ctx.session.state.get("pipeline_cancelled") else \
                "failed" if ctx.session.state.get("pipeline_failed") else "completed"
        flow.flow("PIPELINE END — %s, %.1fs", final, pipeline_elapsed)
        return
        yield  # async generator

    # ------------------------------------------------------------------
    # Built-in milestone pause
    # ------------------------------------------------------------------

    async def _milestone_pause(self, ctx, step_num: int, name: str, run_id_str: str) -> str:
        """Pause for user review. Returns 'continue', 'rerun', or 'cancel'."""
        run_dir = ctx.session.state.get("run_dir", "")
        err_file = Path(run_dir) / f"step_{step_num}_error.json" if run_dir else None
        status = "failed" if (err_file and err_file.exists()) else "passed"

        flow.flow("MILESTONE Step %d (%s): status=%s — awaiting review", step_num, name, status)

        # Signal to UI
        milestone_waiting[step_num] = {"waiting": True, "name": name, "status": status}
        ctx.session.state[f"milestone_{step_num}_waiting"] = True
        ctx.session.state[f"milestone_{step_num}_step_name"] = name
        ctx.session.state[f"milestone_{step_num}_step_status"] = status

        # Publish milestone_reached event for the UI
        artifact_file = ""
        extra_files = []
        if run_dir:
            artifact_file, extra_files = self._resolve_artifacts(
                ctx.session.state, step_num
            )
        await self._publish(run_id_str, "milestone_reached", step_num, {
            "milestone": name,
            "artifact_file": artifact_file,
            "test_status": status,
            "extra_artifact_files": extra_files,
        })

        # Wait for user decision via asyncio.Event
        evt = milestone_events.get(step_num)
        if evt:
            await evt.wait()
            evt.clear()
        else:
            while step_num not in milestone_decisions:
                await asyncio.sleep(0.5)

        decision = milestone_decisions.pop(step_num, "continue")
        milestone_waiting.pop(step_num, None)
        flow.flow("MILESTONE Step %d: decision=%s", step_num, decision)
        return decision

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _handle_action(self, action: dict, ctx, index: int, run_id_str: str):
        """Handle a pipeline action. Returns 'break', target_index (int), or None."""
        act = action.get("action")
        if act == "cancel":
            flow.flow("PIPELINE CANCELLED at index %d", index)
            ctx.session.state["pipeline_cancelled"] = True
            return "break"
        elif act == "rerun":
            target = action.get("target_step")
            if target and target in _RETRY_STEP_TO_INDEX:
                target_index = _RETRY_STEP_TO_INDEX[target]
                flow.flow("RERUN — jumping to step %d (index %d)", target, target_index)
                await self._publish(run_id_str, "retry_started", target)
                return target_index
        return None

    async def _publish(self, run_id_str: str, event_type: str, step_num: int = 0, payload: dict | None = None):
        """Publish a pipeline event to the event bus."""
        if not run_id_str:
            return
        try:
            await event_bus.publish(PipelineEvent(
                run_id=UUID(run_id_str),
                event_type=event_type,
                step_number=step_num,
                payload=payload or {},
            ))
        except Exception:
            pass

    @staticmethod
    def _resolve_artifacts(state: dict, step_num: int) -> tuple[str, list[str]]:
        """Find the primary artifact file and extra files for milestone review."""
        run_dir = state.get("run_dir", "")
        uc_name = state.get("uc_name", "")
        if not run_dir or not uc_name:
            return "", []

        _ARTIFACT_MAP = {
            4: (f"{uc_name}_validation-rules.yaml", [f"{uc_name}_json-output.yaml"]),
            5: (f"{uc_name}_ciq-qa-report.html", []),
            6: (f"{uc_name}.yaml", []),
            8: (f"{uc_name}_simulator-config.yaml", []),
            9: (f"{uc_name}_network-qa-report.html", []),
        }

        primary, extras = _ARTIFACT_MAP.get(step_num, ("", []))
        primary_path = str(Path(run_dir) / primary) if primary else ""
        extra_paths = [str(Path(run_dir) / e) for e in extras if (Path(run_dir) / e).exists()]

        return primary_path, extra_paths


def build_pipeline(config: dict | None = None) -> PipelineAgent:
    """Build the 7-agent pipeline.

    Returns a configured PipelineAgent ready for execution.
    """
    import os

    config = config or {}
    api_key = config.get("openrouter_api_key", os.environ.get("OPENROUTER_API_KEY", ""))
    if api_key:
        os.environ["OPENROUTER_API_KEY"] = api_key

    from agents.requirements_agent import RequirementsGathererNode
    from agents.ciq_validation_json_gen_agent import CiqValidationJsonGenAgent
    from agents.ciq_validation_json_qa_agent import CiqValidationJsonQAAgent
    from agents.network_config_gen_agent import NetworkConfigGenAgent
    from agents.network_simulator_setup_agent import NetworkSimulatorSetupAgent
    from agents.network_config_qa_agent import NetworkConfigQAAgent
    from agents.report_agent import ReportAgent

    agents = [
        RequirementsGathererNode(),       # 0 - Steps 1+2
        CiqValidationJsonGenAgent(),      # 1 - Steps 3+4
        CiqValidationJsonQAAgent(),       # 2 - Step 5
        NetworkConfigGenAgent(),          # 3 - Step 6
        NetworkSimulatorSetupAgent(),     # 4 - Step 8
        NetworkConfigQAAgent(),           # 5 - Steps 7+9
        ReportAgent(),                    # 6 - Step 10
    ]

    pipeline = PipelineAgent()
    pipeline._configure(agents)

    logger.info("Pipeline built: %d agents", len(agents))
    return pipeline
