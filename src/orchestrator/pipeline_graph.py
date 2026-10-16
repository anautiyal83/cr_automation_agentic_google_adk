"""CR Automation Pipeline — Google ADK Agent Graph.

Agent Architecture:
  - 6 LlmAgents (Steps 2,3,4,6,8 + UC Analyzer) with tools for post-processing
  - 4 Deterministic BaseAgents (Steps 1,5,7,9,10) wrapping existing JAR-based logic
  - 3 Peer Reviewer LlmAgents (QA Validation, QA MOP, QA Execution)
  - 7 Milestone nodes (pause for user review)
  - 3 Failure Router nodes (conditional retry routing)

Feedback Loops:
  Step 5 fail → QA Validation Reviewer → fix instructions → retry Step 3
  Step 7 fail → QA MOP Reviewer → fix instructions → retry Step 6
  Step 9 fail → QA Execution Reviewer → classify → retry Step 6 or Step 8
"""
from __future__ import annotations

import logging
import os

from google.adk.agents import SequentialAgent, BaseAgent
from google.adk.models.lite_llm import LiteLlm

from src.nodes.llm_agents import (
    create_model,
    create_uc_analyzer_agent,
    create_validation_rules_agent,
    create_json_template_agent,
    create_workflow_gen_agent,
    create_simulator_setup_agent,
)
from src.nodes.reviewer_agents import (
    create_qa_validation_reviewer,
    create_qa_mop_reviewer,
    create_qa_execution_reviewer,
)
from src.nodes.step_nodes import (
    doc_parser_node,
    ciq_qa_node,
    mop_test_node,
    execution_test_node,
    final_report_node,
    milestone_review_node,
    failure_router_step5_node,
    failure_router_step7_node,
    failure_router_step9_node,
)

logger = logging.getLogger(__name__)


def build_pipeline_agent(
    model_name: str = "openrouter/google/gemini-2.5-flash",
    api_key: str = "",
) -> BaseAgent:
    """Build the CR Automation pipeline as a Google ADK agent graph.

    Returns a SequentialAgent with 10 pipeline steps, 3 reviewers,
    7 milestones, and 3 failure routers.
    """
    if api_key:
        os.environ["OPENROUTER_API_KEY"] = api_key

    model = LiteLlm(model=model_name)

    # --- LLM Agents (native ADK LlmAgent with tools) ---
    uc_analyzer = create_uc_analyzer_agent(model)
    validation_rules = create_validation_rules_agent(model)
    json_template = create_json_template_agent(model)
    workflow_gen = create_workflow_gen_agent(model)
    simulator_setup = create_simulator_setup_agent(model)

    # --- Peer Reviewer Agents ---
    qa_val_reviewer = create_qa_validation_reviewer(model)
    qa_mop_reviewer = create_qa_mop_reviewer(model)
    qa_exec_reviewer = create_qa_execution_reviewer(model)

    # --- Pipeline ---
    pipeline = SequentialAgent(
        name="cr_automation_pipeline",
        description=(
            "10-step CR Automation pipeline with LLM agents, deterministic test steps, "
            "3 peer reviewer agents for failure analysis, and conditional retry loops."
        ),
        sub_agents=[
            # ── Phase 1: Document Parsing ──
            doc_parser_node,                    # Step 1 (deterministic)
            uc_analyzer,                        # Step 2 (LlmAgent)

            # ── Phase 2: Config Generation ──
            validation_rules,                   # Step 3 (LlmAgent + tools)
            milestone_review_node("milestone_step3", step_num=3, name="Validation Rules"),
            json_template,                      # Step 4 (LlmAgent + tools)
            milestone_review_node("milestone_step4", step_num=4, name="JSON Output Template"),

            # ── Phase 3: CIQ QA ──
            ciq_qa_node,                        # Step 5 (deterministic JAR)
            qa_val_reviewer,                    # Reviewer: analyze Step 5 failures
            failure_router_step5_node,          # Router: continue or retry Step 3

            # ── Phase 4: Workflow & MOP ──
            workflow_gen,                        # Step 6 (LlmAgent + tools)
            milestone_review_node("milestone_step6", step_num=6, name="Workflow"),
            mop_test_node,                      # Step 7 (deterministic JAR)
            qa_mop_reviewer,                    # Reviewer: analyze Step 7 failures
            failure_router_step7_node,          # Router: continue or retry Step 6

            # ── Phase 5: Simulator & Execution ──
            simulator_setup,                    # Step 8 (LlmAgent + tools)
            milestone_review_node("milestone_step8", step_num=8, name="Simulator Config"),
            execution_test_node,                # Step 9 (deterministic JAR + MockWire)
            qa_exec_reviewer,                   # Reviewer: classify & analyze Step 9 failures
            failure_router_step9_node,          # Router: continue, retry Step 6, or retry Step 8

            # ── Phase 6: Final Report ──
            final_report_node,                  # Step 10 (deterministic)
        ],
    )

    logger.info(
        "Pipeline built: %d agents (%d LLM, %d deterministic, %d reviewers, %d milestones, %d routers)",
        len(pipeline.sub_agents), 5, 5, 3, 4, 3,
    )

    return pipeline
