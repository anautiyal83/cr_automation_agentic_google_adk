# Research: Google ADK Orchestrator Pipeline

## R1: SequentialAgent vs Custom PipelineAgent

**Decision**: Use a custom `PipelineAgent(BaseAgent)` instead of `SequentialAgent`

**Rationale**: `SequentialAgent` is deprecated in ADK 2.11 and cannot jump back to earlier steps. Our pipeline requires conditional backtracking (Step 5→3, Step 7→6, Step 9→6/8). A custom BaseAgent implements a state-machine loop that reads `_retry_target` from session state and jumps to the correct step index.

**Alternatives considered**:
- `SequentialAgent` — no backtracking support, deprecated
- `Workflow` graph API — designed for DAGs with branches, not loops to arbitrary prior steps; overkill for sequential-with-retry pattern

## R2: LlmAgent vs BaseAgent for LLM Steps

**Decision**: Use BaseAgent wrappers for all 10 pipeline steps. Use LlmAgent only for the 3 reviewer agents. *(Reviewers later became BaseAgents (21d8864) and were then folded into QA skills (bd35015) — see R7. No LlmAgent remains.)*

**Rationale**: LlmAgent expects conversational interaction (messages → tool calls → responses). Pipeline steps need structured state→prompt→LLM→postprocess→state flow. The existing agents from cr_automation-agentic handle this well via `call_llm()`. Wrapping them in BaseAgent is simpler and reuses battle-tested code. Reviewer agents genuinely benefit from agentic tool-use (reading reports, classifying failures, generating instructions).

**Alternatives considered**:
- Native LlmAgent for steps 2,3,4,6,8 — requires complex tool wrappers for every state field read/write; doesn't leverage existing agent code
- Hybrid — adds complexity without clear benefit

## R3: Milestone / HITL Pattern

**Decision**: Use `asyncio.Event` pattern (matching reference project) instead of polling *(still in force: events live in `shared.state.milestone_events`; PipelineAgent falls back to 0.5s polling of `milestone_decisions` when no Event is registered)*

**Rationale**: The polling pattern (sleep 0.5s, check state) wastes CPU. The `asyncio.Event` pattern from the reference project is cleaner: runner creates an Event, milestone node awaits it, `respond_milestone()` sets decision + signals the Event.

## R4: UI Approach

**Decision**: Copy and adapt NiceGUI UI from cr_automation-agentic

**Rationale**: The reference UI is well-structured with clear separation of concerns. Key changes: replace `PipelineOrchestrator` with `PipelineRunner`, replace `event_bus` subscription patterns. Same layout, CSS, navigation pattern.

## R5: Database Strategy

**Decision**: Copy SQLite schema and db.py from reference project

**Rationale**: The schema (pipeline_runs, step_results, settings, etc.) is framework-agnostic. Same tables, same queries, same persistence logic. No need to redesign.

## R6: ADK Session State Is Not a Reliable Data Bus

**Decision**: Do not use `ctx.session.state` for control flow, status checks, or inter-agent data. Use module-level dicts in `shared/state.py` (`pipeline_actions`, `reviewer_feedback`, `milestone_events`, `milestone_decisions`, `milestone_waiting`), `requirements_store` in `agents/requirements_agent.py`, and on-disk `step_{N}_error.json` files for step status. Run completion is detected from the final report on disk.

**Rationale**: Writes to `ctx.session.state` in sub-agents were not visible to the parent PipelineAgent (bc567e9). Reviewers skipped even when steps failed (bf6b061). The runner reported COMPLETED for failed runs (dea588a, 0f52f58). All control-flow reads were replaced in 5455064.

**Supersedes**: original FR-018 ("ADK session state MUST carry all pipeline data") and the SessionState table in data-model.md.

**Residual risk**: Some agents still store skill instances in session state for `regenerate()` (`_ciq_gen_skill`, `_network_config_gen_skill`, `_simulator_setup_skill`) and `_simulator_process`. See 004-simulator-setup S8-08.

## R7: 23 → 26 → 7 → 9 Skill-Based Agents

**Decision**: One thin BaseAgent per skill package; milestones built into PipelineAgent; reviewers folded into QA skills; routers replaced by `_retry_target` set by QA agents.

**History**:
- 23 agents in the original plan: 10 steps + 7 milestones + 3 reviewers + 3 routers. Steps 5, 7 and 9 got milestones so pass/fail results could be shown with "Skip & Continue".
- 27 agents in bc567e9 (4 milestone reviewers added).
- 26 agents in 2427a61 (RequirementsGatherer replaced steps 1 and 2).
- 7 agents in bd35015.
- 8 agents in 558626a (MOP QA split from Exec QA so steps run in UI order).
- 9 agents in 1cbef81 (`helper_script_gen` added, sharing step 6).

**Rationale**: Most of the 26 agents were glue whose only job was to pass state, and that state passing was unreliable (R6). Skill packages are reusable outside ADK and testable via `test_step.py --skill`. Each generation skill keeps its context so `regenerate(feedback)` can drive correction loops.

## R8: json-output.yaml Built Deterministically

**Decision**: `skills/ciq_validation_json_gen/autofix.py::fix_json_output()` builds json-output from CIQ sheets instead of asking the LLM.

**Rationale**: The structure is the same for every UC. Only node names and data columns vary. Six consecutive commits (af70573, 7b72115, 91e5085, d2d499f, 10bb0f1, 5872ec4) chased LLM structure errors that the MOP JAR rejected with "No nodes found". The deterministic build (d448e21) removed the whole class of error.

**Constraints the build must honor**:
- `data.schemaVersion: "2"`, or the JAR uses its v1 parser.
- `data.meta{nodeType, activity}` from the UC name.
- `nodeGroups[]` at the data root, not under `crGroups`.
- `segregate_by: Index.CRGroup`.
- One node entry per zone, with node types as subkeys.

**Deviation**: Constitution Principle II says configs are LLM-generated. This is a justified exception: there is no per-UC variance, so the LLM added only risk.

## R9: Sanitize LLM Output to What the JARs Accept (Whitelist)

**Decision**: Before invoking a JAR, write a sanitized copy of the input and keep the original for review:
- **validation-rules**: top-level `sheets`/`settings` only; whitelisted column properties; `crossRef` limited to sheet/column; no sheet row rules; `allowedValues` only on enum columns.
- **workflow**: a step-field whitelist, `_VALID_COMMAND_FIELDS`.
- **Simulator config**: the first YAML document only, with duplicate keys allowed.

**Rationale**: The JARs deserialize strictly with SnakeYAML JavaBean mapping, and every unknown property is fatal. Blacklisting fields one at a time (5826e71, 00b5d00) kept missing new LLM inventions; the whitelist (f889146) closed that gap. Combined with prompt tightening (664c3b4) and correct CLI flags (6771413, d9d34d0, 1c7e53a), this stopped step 5 from silently passing.

**Caveat**: Whitelisting can silently drop meaning. The first-document strip for simulator YAML dropped every node except the first (004 S8-02).
