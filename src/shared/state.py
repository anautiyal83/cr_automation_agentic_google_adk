"""Module-level shared state dicts — used across agents, runner, and UI.

These bypass ADK session state which may use copies/deltas and
cannot reliably share data between agents.
"""
from __future__ import annotations

import asyncio

# Milestone pause signaling
milestone_events: dict[int, asyncio.Event] = {}   # step -> Event (signaled by runner)
milestone_decisions: dict[int, str] = {}            # step -> decision
milestone_waiting: dict[int, dict] = {}             # step -> {waiting, name, status}

# Pipeline-level actions — set by milestone nodes, read by PipelineAgent
pipeline_actions: dict[str, dict] = {}  # run_id -> {"action": "rerun"/"cancel", "target_step": N}

# Reviewer feedback — set by MilestoneReviewerNode, read by step nodes on retry
reviewer_feedback: dict[str, str] = {}  # "run_id:step_num" -> feedback text
