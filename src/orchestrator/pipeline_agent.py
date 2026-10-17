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

logger = logging.getLogger(__name__)


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

    class Config:
        arbitrary_types_allowed = True

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

    async def _run_async_impl(self, ctx):
        """Execute agents sequentially with backtracking on _retry_target."""
        index = 0
        total = len(self._agents_list)

        logger.info("PipelineAgent starting — %d agents to execute", total)

        while index < total:
            # Check termination flags
            if ctx.session.state.get("pipeline_cancelled"):
                logger.info("Pipeline cancelled by user")
                break
            if ctx.session.state.get("pipeline_failed"):
                logger.info("Pipeline failed — retry limit exhausted")
                break

            agent = self._agents_list[index]

            # Skip reviewer agents if preceding test step passed
            if index in self._reviewer_indices:
                test_step = self._preceding_test_step.get(index)
                if test_step and ctx.session.state.get(f"step_{test_step}_status") != "failed":
                    logger.debug("Skipping reviewer at index %d — step %d passed", index, test_step)
                    index += 1
                    continue

            # Execute the agent
            logger.info("Executing agent [%d/%d]: %s", index + 1, total, agent.name)
            try:
                async for event in agent.run_async(ctx):
                    yield event
            except Exception as e:
                logger.exception("Agent %s failed: %s", agent.name, e)
                ctx.session.state["pipeline_failed"] = True
                ctx.session.state["pipeline_error"] = str(e)
                break

            # Check for retry routing
            retry_target = ctx.session.state.pop("_retry_target", None)
            if retry_target is not None and retry_target in self._step_to_index:
                target_index = self._step_to_index[retry_target]
                logger.info("Retry routing → step %d (index %d)", retry_target, target_index)
                index = target_index
                continue

            # Advance to next agent
            index += 1

        logger.info("PipelineAgent finished at index %d", index)
        yield  # Required for async generator


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
        DocParserNode, UCAnalyzerNode, ValidationRulesNode,
        JsonTemplateNode, CiqQANode, WorkflowGenNode,
        MopTestNode, SimulatorSetupNode, ExecutionTestNode, FinalReportNode,
    )
    from src.nodes.milestone_nodes import create_milestone_node
    from src.nodes.router_nodes import (
        FailureRouterStep5, FailureRouterStep7, FailureRouterStep9,
    )
    from src.nodes.reviewer_agents import (
        create_qa_validation_reviewer,
        create_qa_mop_reviewer,
        create_qa_execution_reviewer,
    )

    # Build ordered agent list (index → agent)
    agents = [
        DocParserNode(),           # 0  - Step 1
        UCAnalyzerNode(),          # 1  - Step 2
        ValidationRulesNode(),     # 2  - Step 3
        create_milestone_node(3, "Validation Rules"),  # 3
        JsonTemplateNode(),        # 4  - Step 4
        create_milestone_node(4, "JSON Output Template"),  # 5
        CiqQANode(),               # 6  - Step 5
        create_milestone_node(5, "QA-Validation Results"),  # 7
        create_qa_validation_reviewer(model),  # 8  - Reviewer
        FailureRouterStep5(),      # 9  - Router
        WorkflowGenNode(),         # 10 - Step 6
        create_milestone_node(6, "Execution Config"),  # 11
        MopTestNode(),             # 12 - Step 7
        create_milestone_node(7, "QA-MOP Results"),  # 13
        create_qa_mop_reviewer(model),  # 14 - Reviewer
        FailureRouterStep7(),      # 15 - Router
        SimulatorSetupNode(),      # 16 - Step 8
        create_milestone_node(8, "Simulator Config"),  # 17
        ExecutionTestNode(),       # 18 - Step 9
        create_milestone_node(9, "QA-Execution Results"),  # 19
        create_qa_execution_reviewer(model),  # 20 - Reviewer
        FailureRouterStep9(),      # 21 - Router
        FinalReportNode(),         # 22 - Step 10
    ]

    # Step number → agent index (for retry routing)
    step_to_index = {
        3: 2,   # Retry Step 3 → index 2 (ValidationRulesNode)
        6: 10,  # Retry Step 6 → index 10 (WorkflowGenNode)
        8: 16,  # Retry Step 8 → index 16 (SimulatorSetupNode)
    }

    # Reviewer indices and their preceding test steps
    reviewer_indices = {8, 14, 20}
    preceding_test_step = {
        8: 5,   # QA Validation Reviewer → Step 5
        14: 7,  # QA MOP Reviewer → Step 7
        20: 9,  # QA Execution Reviewer → Step 9
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
