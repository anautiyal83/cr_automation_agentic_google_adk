# CLAUDE.md

## Project Overview
Google ADK-based orchestrator for the CR Automation pipeline.
Wraps existing agents from `D:/my_workspace/cr_automation-agentic` using ADK's SequentialAgent with conditional routing.

## Key Files
- `src/orchestrator/pipeline_graph.py` — ADK agent graph (10 steps + milestones + routers)
- `src/orchestrator/runner.py` — PipelineRunner with session management, milestone handling
- `src/nodes/step_nodes.py` — Step node implementations wrapping existing agents
- `main.py` — CLI entry point

## Architecture
- Each pipeline step is a `BaseAgent` subclass that calls the existing agent's `execute()` method
- State is shared via ADK session state (dict)
- Milestones pause execution via polling loop (wait for `milestone_N_decision` in state)
- Failure routers classify errors and set `_retry_target` in state
- Smart retry: Step 9 failures go to Step 6 (workflow) or Step 8 (simulator) based on error patterns

## Dependencies
- Imports agents from `D:/my_workspace/cr_automation-agentic/src/agents/`
- Requires `google-adk>=2.11.0`
