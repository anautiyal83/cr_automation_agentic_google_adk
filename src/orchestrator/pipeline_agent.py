"""Custom PipelineAgent — state-machine orchestrator with backtracking support.

Replaces ADK's deprecated SequentialAgent with a loop that:
1. Executes agents in index order (0..N)
2. Checks session state for _retry_target after each agent
3. Jumps to the target index if set (backtracking)
4. Skips reviewer agents if the preceding test step passed
5. Stops on pipeline_cancelled or pipeline_failed
"""
from __future__ import annotations

import logging
from typing import Any

from google.adk.agents import BaseAgent

from src.core.logging_config import get_flow_logger
from src.nodes.milestone_nodes import pipeline_actions

logger = logging.getLogger(__name__)
flow = get_flow_logger(__name__)


class PipelineAgent(BaseAgent):
    """Custom orchestrator with conditional backtracking for retry loops.

    Agents are indexed 0..N. The _step_to_index mapping connects logical
    step numbers (3, 6, 8) to agent indices so failure routers can set
    _retry_target=6 and the loop jumps to the correct agent.
    """
    name: str = "cr_automation_pipeline"
    description: str = "10-step CR Automation pipeline with backtracking retry loops"

    # Set by build_pipeline() after construction
    _agents_list: list[BaseAgent] = []
    _step_to_index: dict[int, int] = {}
    _reviewer_indices: set[int] = set()    # indices of reviewer agents (conditional)
    _preceding_test_step: dict[int, int] = {}  # reviewer_index → test step number

    model_config = {"arbitrary_types_allowed": True}

    def _configure(
        self,
        agents_list: list[BaseAgent],
        step_to_index: dict[int, int],
        reviewer_indices: set[int] | None = None,
        preceding_test_step: dict[int, int] | None = None,
    ):
        """Configure the pipeline after construction (Pydantic model limitation)."""
        self._agents_list = agents_list
        self._step_to_index = step_to_index
        self._reviewer_indices = reviewer_indices or set()
        self._preceding_test_step = preceding_test_step or {}
        # Register sub_agents for ADK framework
        self.sub_agents = list(agents_list)

    @staticmethod
    def _parse_step_number(agent_name: str) -> int | None:
        """Extract step number from agent name like 'step1_doc_parser' → 1."""
        if agent_name.startswith("step"):
            try:
                return int(agent_name.split("_")[0][4:])
            except (ValueError, IndexError):
                pass
        return None

    async def _run_async_impl(self, ctx):
        """Execute agents sequentially with backtracking on _retry_target."""
        from uuid import UUID
        from pathlib import Path as _Path
        from src.core.events import PipelineEvent, event_bus

        index = 0
        total = len(self._agents_list)
        run_id_str = ctx.session.state.get("run_id", "")

        import time as _time

        flow.flow("PIPELINE START — %d agents, run_id=%s", total, run_id_str[:8] if run_id_str else "?")
        pipeline_t0 = _time.monotonic()

        while index < total:
            agent = self._agents_list[index]
            step_num = self._parse_step_number(agent.name)

            # ----- CHECK PIPELINE ACTIONS (cancel/rerun from milestones) -----
            action = pipeline_actions.pop(run_id_str, None)
            if action:
                act = action.get("action")
                if act == "cancel":
                    flow.flow("PIPELINE CANCELLED by user at index %d", index)
                    ctx.session.state["pipeline_cancelled"] = True
                    break
                elif act == "rerun":
                    target = action.get("target_step")
                    if target and target in self._step_to_index:
                        target_index = self._step_to_index[target]
                        flow.flow("RERUN — jumping to step %d (index %d)", target, target_index)
                        if run_id_str:
                            try:
                                await event_bus.publish(PipelineEvent(
                                    run_id=UUID(run_id_str), event_type="retry_started",
                                    step_number=target,
                                ))
                            except Exception:
                                pass
                        index = target_index
                        continue

            # ----- CHECK TERMINATION FLAGS -----
            if ctx.session.state.get("pipeline_cancelled"):
                flow.flow("PIPELINE CANCELLED (state flag) at index %d", index)
                break
            if ctx.session.state.get("pipeline_failed"):
                flow.flow("PIPELINE FAILED at index %d", index)
                break

            # ----- SKIP REVIEWERS if preceding test passed -----
            if index in self._reviewer_indices:
                test_step = self._preceding_test_step.get(index)
                if test_step:
                    # Check error file on disk (reliable) — ADK state may not reflect sub-agent writes
                    _run_dir = ctx.session.state.get("run_dir", "")
                    _err_file = _Path(_run_dir) / f"step_{test_step}_error.json" if _run_dir else None
                    step_failed = _err_file and _err_file.exists()
                    if not step_failed:
                        logger.debug("Skipping reviewer at index %d — step %d passed (no error file)", index, test_step)
                        index += 1
                        continue

            # ----- CLEAR old error file before re-run -----
            if step_num:
                _run_dir = ctx.session.state.get("run_dir", "")
                if _run_dir:
                    _old_err = _Path(_run_dir) / f"step_{step_num}_error.json"
                    if _old_err.exists():
                        _old_err.unlink()

            # ----- PUBLISH step_started -----
            if step_num and run_id_str:
                try:
                    await event_bus.publish(PipelineEvent(
                        run_id=UUID(run_id_str), event_type="step_started",
                        step_number=step_num,
                    ))
                except Exception:
                    pass

            # ----- EXECUTE THE AGENT -----
            agent_t0 = _time.monotonic()
            flow.flow("AGENT START [%d/%d] %s%s", index + 1, total, agent.name,
                       f" (step {step_num})" if step_num else "")
            try:
                async for event in agent.run_async(ctx):
                    yield event
            except Exception as e:
                agent_elapsed = _time.monotonic() - agent_t0
                logger.error("Agent %s failed after %.1fs: %s", agent.name, agent_elapsed, e)
                logger.debug("Agent %s traceback:", agent.name, exc_info=True)
                flow.flow("AGENT FAILED [%d/%d] %s — %.1fs — %s", index + 1, total, agent.name, agent_elapsed, e)
                ctx.session.state["pipeline_failed"] = True
                ctx.session.state["pipeline_error"] = str(e)
                if step_num and run_id_str:
                    try:
                        await event_bus.publish(PipelineEvent(
                            run_id=UUID(run_id_str), event_type="step_failed",
                            step_number=step_num,
                            payload={"error": str(e)},
                        ))
                    except Exception:
                        pass
                break

            agent_elapsed = _time.monotonic() - agent_t0

            # Determine step status — check disk first (reliable), fallback to session state
            if step_num:
                _run_dir = ctx.session.state.get("run_dir", "")
                _err = _Path(_run_dir) / f"step_{step_num}_error.json" if _run_dir else None
                status = "failed" if (_err and _err.exists()) else \
                         ctx.session.state.get(f"step_{step_num}_status", "passed")
            else:
                status = "ok"
            flow.flow("AGENT DONE  [%d/%d] %s — %.1fs — status=%s",
                       index + 1, total, agent.name, agent_elapsed, status)

            # ----- PUBLISH step_completed -----
            if step_num and run_id_str:
                try:
                    evt_type = "step_completed" if status != "failed" else "step_failed"
                    await event_bus.publish(PipelineEvent(
                        run_id=UUID(run_id_str), event_type=evt_type,
                        step_number=step_num,
                        payload={"status": status},
                    ))
                except Exception:
                    pass

            # ----- CHECK FOR ACTIONS SET DURING AGENT EXECUTION -----
            # (milestone nodes set pipeline_actions during their run)
            action = pipeline_actions.pop(run_id_str, None)
            if action:
                act = action.get("action")
                if act == "cancel":
                    flow.flow("PIPELINE CANCELLED after agent %s", agent.name)
                    ctx.session.state["pipeline_cancelled"] = True
                    break
                elif act == "rerun":
                    target = action.get("target_step")
                    if target and target in self._step_to_index:
                        target_index = self._step_to_index[target]
                        flow.flow("RERUN after milestone — jumping to step %d (index %d)", target, target_index)
                        if run_id_str:
                            try:
                                await event_bus.publish(PipelineEvent(
                                    run_id=UUID(run_id_str), event_type="retry_started",
                                    step_number=target,
                                ))
                            except Exception:
                                pass
                        index = target_index
                        continue

            # ----- ALSO CHECK session state _retry_target (from router agents) -----
            retry_target = ctx.session.state.pop("_retry_target", None)
            if retry_target is not None and retry_target in self._step_to_index:
                target_index = self._step_to_index[retry_target]
                flow.flow("RETRY ROUTING — step %d (index %d)", retry_target, target_index)
                if run_id_str:
                    try:
                        await event_bus.publish(PipelineEvent(
                            run_id=UUID(run_id_str), event_type="retry_started",
                            step_number=retry_target,
                        ))
                    except Exception:
                        pass
                index = target_index
                continue

            # Advance to next agent
            index += 1

        pipeline_elapsed = _time.monotonic() - pipeline_t0
        final_state = "cancelled" if ctx.session.state.get("pipeline_cancelled") else \
                      "failed" if ctx.session.state.get("pipeline_failed") else "completed"
        flow.flow("PIPELINE END — %s at index %d, total %.1fs", final_state, index, pipeline_elapsed)
        # Must be async generator but don't yield None
        return
        yield  # unreachable — makes this an async generator


def build_pipeline(config: dict | None = None) -> PipelineAgent:
    """Build the complete pipeline with all 23 agents.

    Returns a configured PipelineAgent ready for execution.
    """
    import os
    from google.adk.models.lite_llm import LiteLlm

    config = config or {}
    model_name = config.get("default_model", "openrouter/google/gemini-2.5-flash")
    api_key = config.get("openrouter_api_key", os.environ.get("OPENROUTER_API_KEY", ""))
    if api_key:
        os.environ["OPENROUTER_API_KEY"] = api_key

    model = LiteLlm(model=model_name)

    # Import node factories
    from src.nodes.step_nodes import (
        ValidationRulesNode,
        JsonTemplateNode, CiqQANode, WorkflowGenNode,
        MopTestNode, SimulatorSetupNode, ExecutionTestNode, FinalReportNode,
    )
    from src.nodes.requirements_agent import RequirementsGathererNode
    from src.nodes.milestone_nodes import create_milestone_node
    from src.nodes.router_nodes import (
        FailureRouterStep5, FailureRouterStep7, FailureRouterStep9,
    )
    from src.nodes.reviewer_agents import (
        create_milestone_reviewer,
        create_qa_validation_reviewer,
        create_qa_mop_reviewer,
        create_qa_execution_reviewer,
    )

    # Build ordered agent list (index → agent)
    # RequirementsGatherer replaces Steps 1+2 (DocParser + UCAnalyzer)
    # Pattern: Step → MilestoneReviewer (skipped if passed) → Milestone (pause)
    agents = [
        RequirementsGathererNode(),               #  0 - Requirements (replaces Steps 1+2)
        ValidationRulesNode(),                    #  1 - Step 3
        create_milestone_reviewer(model, 3),      #  2 - Reviewer (if step 3 failed)
        create_milestone_node(3, "Validation Rules"),  #  3 - Milestone
        JsonTemplateNode(),                       #  4 - Step 4
        create_milestone_reviewer(model, 4),      #  5 - Reviewer (if step 4 failed)
        create_milestone_node(4, "JSON Output Template"),  #  6 - Milestone
        CiqQANode(),                              #  7 - Step 5
        create_qa_validation_reviewer(model),     #  8 - QA Reviewer (if step 5 failed)
        create_milestone_node(5, "QA-Validation Results"),  #  9 - Milestone
        FailureRouterStep5(),                     # 10 - Router
        WorkflowGenNode(),                        # 11 - Step 6
        create_milestone_reviewer(model, 6),      # 12 - Reviewer (if step 6 failed)
        create_milestone_node(6, "Execution Config"),  # 13 - Milestone
        MopTestNode(),                            # 14 - Step 7
        create_qa_mop_reviewer(model),            # 15 - QA Reviewer (if step 7 failed)
        create_milestone_node(7, "QA-MOP Results"),  # 16 - Milestone
        FailureRouterStep7(),                     # 17 - Router
        SimulatorSetupNode(),                     # 18 - Step 8
        create_milestone_reviewer(model, 8),      # 19 - Reviewer (if step 8 failed)
        create_milestone_node(8, "Simulator Config"),  # 20 - Milestone
        ExecutionTestNode(),                      # 21 - Step 9
        create_qa_execution_reviewer(model),      # 22 - QA Reviewer (if step 9 failed)
        create_milestone_node(9, "QA-Execution Results"),  # 23 - Milestone
        FailureRouterStep9(),                     # 24 - Router
        FinalReportNode(),                        # 25 - Step 10
    ]

    # Step number → agent index (for retry routing)
    step_to_index = {
        3: 1,    # ValidationRulesNode
        4: 4,    # JsonTemplateNode
        5: 7,    # CiqQANode
        6: 11,   # WorkflowGenNode
        7: 14,   # MopTestNode
        8: 18,   # SimulatorSetupNode
        9: 21,   # ExecutionTestNode
    }

    # Reviewer indices and which step they review
    # Skipped if that step passed
    reviewer_indices = {2, 5, 8, 12, 15, 19, 22}
    preceding_test_step = {
        2: 3,    # Milestone reviewer step 3 → skip if step 3 passed
        5: 4,    # Milestone reviewer step 4 → skip if step 4 passed
        8: 5,    # QA Validation Reviewer → skip if step 5 passed
        12: 6,   # Milestone reviewer step 6 → skip if step 6 passed
        15: 7,   # QA MOP Reviewer → skip if step 7 passed
        19: 8,   # Milestone reviewer step 8 → skip if step 8 passed
        22: 9,   # QA Execution Reviewer → skip if step 9 passed
    }

    pipeline = PipelineAgent()
    pipeline._configure(
        agents_list=agents,
        step_to_index=step_to_index,
        reviewer_indices=reviewer_indices,
        preceding_test_step=preceding_test_step,
    )

    logger.info("Pipeline built: %d agents (step_to_index=%s)", len(agents), step_to_index)
    return pipeline
