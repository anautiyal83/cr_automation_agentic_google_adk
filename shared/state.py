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

# Why a run stopped when a step crashed — set by PipelineAgent, read by the runner as the run's failure
# reason (ADK session state written there does not reach the runner reliably)
run_failures: dict[str, str] = {}  # run_id -> plain message

# Running MockWire simulators — registered by step 8, stopped by step 9, on step 8
# re-run, when the pipeline ends (any outcome), and on app shutdown
simulator_processes: dict[str, object] = {}  # run_id -> SimulatorProcess


async def stop_simulator(run_id: str | None = None) -> None:
    """Stop the simulator of one run, or of every run when run_id is None."""
    run_ids = [run_id] if run_id else list(simulator_processes)
    for rid in run_ids:
        sim = simulator_processes.pop(rid, None)
        if sim is not None:
            await sim.stop()
