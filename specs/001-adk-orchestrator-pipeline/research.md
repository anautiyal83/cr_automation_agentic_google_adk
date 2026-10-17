# Research: Google ADK Orchestrator Pipeline

## R1: SequentialAgent vs Custom PipelineAgent

**Decision**: Use a custom `PipelineAgent(BaseAgent)` instead of `SequentialAgent`

**Rationale**: `SequentialAgent` is deprecated in ADK 2.11 and cannot jump back to earlier steps. Our pipeline requires conditional backtracking (Step 5→3, Step 7→6, Step 9→6/8). A custom BaseAgent implements a state-machine loop that reads `_retry_target` from session state and jumps to the correct step index.

**Alternatives considered**:
- `SequentialAgent` — no backtracking support, deprecated
- `Workflow` graph API — designed for DAGs with branches, not loops to arbitrary prior steps; overkill for sequential-with-retry pattern

## R2: LlmAgent vs BaseAgent for LLM Steps

**Decision**: Use BaseAgent wrappers for all 10 pipeline steps. Use LlmAgent only for the 3 reviewer agents.

**Rationale**: LlmAgent expects conversational interaction (messages → tool calls → responses). Pipeline steps need structured state→prompt→LLM→postprocess→state flow. The existing agents from cr_automation-agentic handle this well via `call_llm()`. Wrapping them in BaseAgent is simpler and reuses battle-tested code. Reviewer agents genuinely benefit from agentic tool-use (reading reports, classifying failures, generating instructions).

**Alternatives considered**:
- Native LlmAgent for steps 2,3,4,6,8 — requires complex tool wrappers for every state field read/write; doesn't leverage existing agent code
- Hybrid — adds complexity without clear benefit

## R3: Milestone / HITL Pattern

**Decision**: Use `asyncio.Event` pattern (matching reference project) instead of polling

**Rationale**: The polling pattern (sleep 0.5s, check state) wastes CPU. The `asyncio.Event` pattern from the reference project is cleaner: runner creates an Event, milestone node awaits it, `respond_milestone()` sets decision + signals the Event.

## R4: UI Approach

**Decision**: Copy and adapt NiceGUI UI from cr_automation-agentic

**Rationale**: The reference UI is well-structured with clear separation of concerns. Key changes: replace `PipelineOrchestrator` with `PipelineRunner`, replace `event_bus` subscription patterns. Same layout, CSS, navigation pattern.

## R5: Database Strategy

**Decision**: Copy SQLite schema and db.py from reference project

**Rationale**: The schema (pipeline_runs, step_results, settings, etc.) is framework-agnostic. Same tables, same queries, same persistence logic. No need to redesign.

## R6: Agent Count Revision

**Decision**: 23 agents (not 20) — 10 steps + 7 milestones + 3 reviewers + 3 routers

**Rationale**: Added milestones for Steps 5, 7, 9 (test steps) to show pass/fail results with "Skip & Continue" option. Previously only Steps 3,4,6,8 had milestones.
