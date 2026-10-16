"""CR Automation Pipeline — Google ADK Workflow Graph.

Defines the 10-step pipeline as a Workflow with conditional routing:
- LLM steps (1,2,3,4,6,8): Use call_llm via FunctionNode
- Deterministic steps (5,7,9,10): Run JARs, compare results
- Milestones (3,4,5,6,7,8,9): Pause for user review
- Feedback loops:
  - Step 5 failure → retry Step 3 (validation rules issue)
  - Step 7 failure → retry Step 6 (workflow YAML issue)
  - Step 9 failure → retry Step 6 (workflow issue) OR Step 8 (simulator issue)
"""
from __future__ import annotations

import logging
from google.adk.agents import SequentialAgent, BaseAgent
from google.adk.models import LiteLlm

from src.nodes.step_nodes import (
    doc_parser_node,
    uc_analyzer_node,
    validation_rules_node,
    json_template_node,
    ciq_qa_node,
    milestone_review_node,
    workflow_gen_node,
    mop_test_node,
    simulator_setup_node,
    execution_test_node,
    final_report_node,
    failure_router_step5_node,
    failure_router_step7_node,
    failure_router_step9_node,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Route constants
# ---------------------------------------------------------------------------
CONTINUE = "continue"
RETRY_STEP3 = "retry_step3"
RETRY_STEP6 = "retry_step6"
RETRY_STEP8 = "retry_step8"
SKIP = "skip"
CANCEL = "cancel"


def build_pipeline_agent(
    model_name: str = "openrouter/google/gemini-2.5-flash",
    api_key: str = "",
) -> BaseAgent:
    """Build the CR Automation pipeline as a Google ADK agent graph.

    The pipeline is structured as a SequentialAgent with sub-agents for each
    step. Conditional routing is handled within each step's logic — the step
    sets state flags that downstream milestone/router nodes read to decide
    whether to continue, retry from an earlier step, or cancel.

    Returns a BaseAgent that can be run via google.adk.runners.Runner.
    """

    # --- LLM Model ---
    llm = LiteLlm(model=model_name)
    if api_key:
        import os
        os.environ["OPENROUTER_API_KEY"] = api_key

    # --- Build pipeline as SequentialAgent ---
    # Each step is a sub-agent (FunctionNode-based or LlmAgent-based).
    # Milestone nodes pause for user review.
    # Router nodes check state and decide retry vs continue.

    pipeline = SequentialAgent(
        name="cr_automation_pipeline",
        description="10-step CR Automation pipeline with milestones and feedback loops",
        sub_agents=[
            # Phase 1: Document Parsing & Analysis
            doc_parser_node,        # Step 1
            uc_analyzer_node,       # Step 2

            # Phase 2: Config Generation & Validation
            validation_rules_node,  # Step 3 (LLM)
            milestone_review_node("milestone_step3", step_num=3, name="Validation Rules"),
            json_template_node,     # Step 4 (LLM)
            milestone_review_node("milestone_step4", step_num=4, name="JSON Output Template"),

            # Phase 3: CIQ QA Cycle
            ciq_qa_node,            # Step 5 (deterministic)
            failure_router_step5_node,  # Routes: continue or retry_step3

            # Phase 4: Workflow & MOP
            workflow_gen_node,      # Step 6 (LLM)
            milestone_review_node("milestone_step6", step_num=6, name="Workflow"),
            mop_test_node,          # Step 7 (deterministic)
            failure_router_step7_node,  # Routes: continue or retry_step6

            # Phase 5: Simulator & Execution
            simulator_setup_node,   # Step 8 (LLM)
            milestone_review_node("milestone_step8", step_num=8, name="Simulator Config"),
            execution_test_node,    # Step 9 (deterministic)
            failure_router_step9_node,  # Routes: continue, retry_step6, or retry_step8

            # Phase 6: Final Report
            final_report_node,      # Step 10 (deterministic)
        ],
    )

    return pipeline
