# Feature Specification: Google ADK Orchestrator Pipeline (NetConfig)

**Feature Branch**: `001-adk-orchestrator-pipeline` (implemented on `feature/skill-packages`)

**Created**: 2026-10-16

**Last Revised**: 2026-11-02 (see [Revision History](#revision-history))

**Status**: Implemented — revised to match as-built architecture; open items tracked in [bugs.md](bugs.md#open-bugs)

**Input**: Rewrite of cr_automation-agentic pipeline using Google ADK for orchestration. As built: a custom `PipelineAgent(BaseAgent)` runs 9 skill-backed agents with built-in milestone pauses and `_retry_target`-based retry routing; QA skills analyze their own failures (no separate reviewer/router agents); NiceGUI UI. Product renamed **NetConfig**.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Run Full Pipeline End-to-End (Priority: P1)

An automation engineer uploads a CIQ Excel workbook (plus optional MOP, SSH logs, sample XMLs, constraints) and starts the pipeline. The system gathers requirements, generates CIQ validation rules and JSON output config, tests them via the ciq-processor JAR, generates the execution workflow YAML and helper scripts, tests MOP generation, sets up the MockWire simulator, executes the workflow against it, and produces final reports — all orchestrated by Google ADK with automatic retry on failures.

**Why this priority**: Core value proposition — automated end-to-end UC config generation with zero manual YAML authoring.

**Independent Test**: Upload a known CIQ (e.g., DSR_1058_IFC_CAPABILITY_MODIFICATION), start pipeline, observe all 10 display steps completing with generated artifacts.

**Acceptance Scenarios**:

1. **Given** a valid CIQ is uploaded, **When** pipeline starts, **Then** all 10 display steps execute in UI order (1→10) with status updates visible in UI
2. **Given** Step 3/4 generates validation rules and JSON output, **When** Step 5 QA finds failures, **Then** the CIQ QA skill sets `_retry_target=3` and the pipeline re-runs CIQ generation via `regenerate(feedback)`
3. **Given** Step 9 execution fails, **When** the Exec QA skill classifies the failure as a workflow issue, **Then** pipeline retries from Step 6; when classified as a simulator issue, **Then** retries from Step 8

---

### User Story 2 - Milestone Review and Manual Control (Priority: P1)

After each reviewable agent (display steps 4, 5, 6, 7, 8, 9), plus the requirements questionnaire (step 0) and requirements summary (step 2), the pipeline pauses and shows generated artifacts for review. The engineer can view/download artifacts, continue, re-run, or cancel.

**Why this priority**: Human-in-the-loop review is required per constitution Principle III for trustworthy automation.

**Independent Test**: Run pipeline, wait for Step 4 milestone, view the validation-rules.yaml artifact, click Continue, observe pipeline proceeding to Step 5.

**Acceptance Scenarios**:

1. **Given** CIQ generation completes, **When** the Step 4 milestone is reached, **Then** UI shows artifact name, View/Download buttons, and Continue/Re-run/Cancel options
2. **Given** a milestone with failed test results, **When** user clicks Re-run, **Then** pipeline routes to the correct upstream step with feedback
3. **Given** user clicks Cancel at any milestone, **Then** pipeline stops and status shows "Cancelled"
4. **Given** a *generation* step (3/4, 6, 8) failed, **When** its milestone is shown, **Then** Continue and Skip are hidden — only Re-run and Cancel are offered

---

### User Story 3 - Smart Failure Recovery (Priority: P1)

When a test step fails, the QA skill for that step analyzes the failure, classifies the root cause, and produces structured feedback for the upstream generation skill. The QA agent sets `_retry_target`; the pipeline jumps back and the generation skill's `regenerate(feedback)` applies the fixes.

**Why this priority**: Eliminates manual debugging of LLM generation failures — self-healing loop per constitution Principle III.

**Independent Test**: Trigger a Step 9 execution failure (e.g., bad validation expression), observe the Exec QA skill classify it as "workflow", and the pipeline route retry to Step 6.

**Acceptance Scenarios**:

1. **Given** Step 5 CIQ QA fails, **When** the CIQ QA skill analyzes, **Then** it produces per-sheet / per-cell failure detail and feedback identifying the rule, current value, problem, and recommended fix
2. **Given** Step 9 fails with "PatternSyntaxException", **When** Exec QA classifies, **Then** classification is "workflow" and `_retry_target=6`
3. **Given** Step 9 fails with "Connection refused", **When** Exec QA classifies, **Then** classification is "simulator" and `_retry_target=8`
4. **Given** retry limit (`MAX_RETRIES=3`) is exhausted for a target, **When** pipeline cannot self-correct, **Then** it fails gracefully with a clear error and all QA reports. *(Currently NOT met — pipeline continues; see bugs.md OB-4.)*

---

### User Story 4 - Dashboard and Run History (Priority: P2)

The engineer sees a dashboard with pipeline statistics (total runs, success rate, average duration, status chart, recent 5 runs) and can browse past runs (paginated), view their artifacts, and re-run failed pipelines.

**Why this priority**: Operational visibility and ability to recover from past failures.

**Independent Test**: After 3 pipeline runs, open Dashboard and verify stats are correct. Open Run History, click on a completed run, view its artifacts.

**Acceptance Scenarios**:

1. **Given** 5 runs have completed (3 success, 2 failed), **When** dashboard loads, **Then** shows "5 Runs", "60% Success", and average duration
2. **Given** a failed run in history, **When** user clicks Re-run, **Then** a new pipeline starts with a new copy of the same document set and settings

---

### User Story 5 - Settings Configuration (Priority: P2)

The engineer configures API keys, LLM models (per-step overrides), retry limits, JAR paths, HTML templates, simulator settings, and vendor profiles through the Settings UI.

**Why this priority**: Operational flexibility — different environments need different configs.

**Independent Test**: Change default model in Settings, save, start new pipeline, verify the new model is used in logs.

**Acceptance Scenarios**:

1. **Given** API key is entered, **When** Save is clicked, **Then** key is persisted to DB and used for subsequent LLM calls (UI and CLI)
2. **Given** per-step model override is set for Step 6, **When** pipeline runs, **Then** Step 6 uses the override model while others use the default
3. **Given** model or API key is changed while a pipeline is running, **Then** the next LLM call uses the new value (no restart)

---

### Edge Cases

- What happens when the LLM API is unreachable? `call_llm` retries 503/429/timeout/connection errors 3 times (5s/15s/30s), then the step fails with a clear error.
- What happens when a JAR file is missing? Step fails immediately with "JAR not found" error — no retry.
- What happens when a JAR is invoked with bad arguments or exits non-zero? The step MUST fail (never silently pass) and the JAR stdout/stderr MUST be logged.
- What happens when MockWire simulator fails to start or its generated config is invalid YAML? Step 8 fails with a clear error; MockWire is not started on invalid YAML.
- What happens when CIQ has no data? CIQ generation fails with "No CIQ structure in requirements.doc_extracted.ciq_sheets" written to `step_3_error.json`.
- What happens when the LLM returns malformed YAML (unquoted colons, multi-document, duplicate keys, wrong top-level shape)? Skill autofix/sanitizers repair or strip before validation.
- What happens during server restart mid-pipeline? Run/step rows are persisted to SQLite; resuming an in-progress run is **not yet implemented** (no `resume_run`).

## Requirements *(mandatory)*

### Functional Requirements

**Orchestration**

- **FR-001**: System MUST orchestrate the pipeline with a custom ADK `PipelineAgent(BaseAgent)` running 9 agents in order — `requirements_gatherer`, `ciq_validation_json_gen`, `ciq_validation_json_qa`, `network_config_gen`, `helper_script_gen`, `network_config_mop_qa`, `network_simulator_setup`, `network_config_exec_qa`, `report_generator` — mapped to 10 UI display steps (`_AGENT_DISPLAY_STEPS`: 1-2, 3-4, 5, 6, 6, 7, 8, 9, 10)
- **FR-002**: Each agent MUST be a thin `BaseAgent` wrapper over a reusable skill package in `skills/`; LLM-using skills MUST call the LLM via `shared/llm.py` (`get_llm_fn(config)`)
- **FR-003**: Test steps 5, 7, 9 MUST invoke Java JARs deterministically via skill JAR executors and produce HTML QA reports
- **FR-004**: QA skills MUST analyze their own failures and produce structured feedback; QA agents MUST set `_retry_target` (5→3, 7→6, 9→6|8). Generation skills MUST expose `regenerate(feedback)`. No separate reviewer, router, or milestone agents
- **FR-005**: System MUST classify Step 9 failures as "workflow" (route to Step 6) or "simulator" (route to Step 8) using error pattern matching; UI re-run label MUST reflect the target
- **FR-006**: PipelineAgent MUST pause at built-in milestones after `ciq_validation_json_gen` (4), `ciq_validation_json_qa` (5), `helper_script_gen` (6), `network_config_mop_qa` (7), `network_simulator_setup` (8), `network_config_exec_qa` (9); the requirements agent MUST pause at step 0 (questionnaire) and step 2 (summary review). Decisions: Continue / Re-run / Cancel
- **FR-007**: System MUST retry a generation target at most `MAX_RETRIES=3` times via `_RETRY_STEP_TO_INDEX` {3→1, 4→1, 6→3, 8→6}; on exhaustion the run MUST fail with a clear error *(open: OB-4)*
- **FR-008**: System MUST retry infrastructure errors (LLM 503/429/timeout/connection) with backoff (3 attempts, 5s/15s/30s)
- **FR-009**: System MUST persist pipeline run and step results to SQLite in real time; runs SHOULD be resumable after server restart *(resume not implemented)*
- **FR-010**: System MUST use LiteLlm/OpenRouter for LLM integration (default Gemini 2.5 Flash) and track per-call token usage and cost by run, UC, step, and model
- **FR-011**: Agents MUST NOT import code from the cr_automation-agentic project; `shared/cr_bridge.py` is legacy and unused
- **FR-018**: Inter-agent control flow and shared data MUST NOT rely on ADK `ctx.session.state` reads across agent boundaries. Use module-level dicts in `shared/state.py` (`pipeline_actions`, `reviewer_feedback`, `milestone_events`, `milestone_decisions`, `milestone_waiting`), `requirements_store`, and on-disk `step_{N}_error.json` files for status
- **FR-020**: Milestone status MUST be "failed" if any display step owned by the agent has an error file or failed status (e.g. step 4 milestone checks steps 3 and 4)
- **FR-021**: When a *generation* step fails, its milestone MUST NOT offer Continue or Skip — only Re-run and Cancel
- **FR-022**: On re-run from step N, all display steps ≥ N MUST be reset to pending before the target is marked retrying
- **FR-023**: Run status MUST be reported "completed" only when the final report exists on disk; a run that ends early MUST be "failed"

**Generation & Test**

- **FR-024**: Requirements gathering MUST parse CIQ, MOP, SSH logs, and XML samples deterministically, ask the developer plain-language questions in rounds (max ~12 per round, decision dialog every 3 rounds), enforce explicit answers over LLM output, persist answers per UC, and skip the questionnaire only when readiness is COMPLETE
- **FR-025**: `json-output.yaml` MUST be built deterministically from CIQ structure (not by LLM): `data.schemaVersion: "2"`, `data.meta{nodeType, activity}`, `data.nodeGroups[]` at root, `segregate_by: Index.CRGroup`, one node entry per zone
- **FR-026**: `validation-rules.yaml` MUST be generated with `sheets:` only; before passing to ciq-processor it MUST be sanitized to a JAR-supported whitelist (top-level `sheets`/`settings`; whitelisted column props; `crossRef` sheet/column only; no sheet row rules; `allowedValues` only on enum columns). The original MUST be kept for review
- **FR-027**: JAR invocations MUST use the documented CLI: ciq-processor `--rules --output --format --node-type --activity --json-output-dir` (report dir separate from JSON data dir); cli-automation `generate|execute --yaml <full path> --schema --json`. A failed/no-output JAR call MUST fail the step and log stdout/stderr
- **FR-028**: Workflow YAML autofix MUST add nodes referenced by steps but undefined (e.g. `LOCAL` → `{type: local, plugin: local}`) and strip step fields not in the JAR whitelist
- **FR-029**: Helper Python scripts referenced by `LOCAL` workflow steps MUST be generated (see [003-helper-script-gen](../003-helper-script-gen/spec.md))
- **FR-030**: Successful generation artifacts SHOULD be cached per UC and reused on later runs (see [002-artifact-cache](../002-artifact-cache/spec.md))
- **FR-031**: Simulator setup MUST tolerate common LLM YAML defects (multi-document, duplicate keys, truncation) and MUST NOT start MockWire with invalid config (see [004-simulator-setup](../004-simulator-setup/spec.md))

**UI & Settings**

- **FR-012**: System MUST provide a NiceGUI web UI with: sidebar navigation, dashboard, upload section (with vendor → node type → activity selectors), pipeline progress tracker, output viewer (with cache management), run history, cleanup, LLM usage, settings, and vendor profiles
- **FR-013**: UI pipeline progress MUST show all 10 steps in a single row with real-time status updates; remaining steps MUST show "skipped" when a run fails or is cancelled
- **FR-014**: UI MUST display artifacts with View (in-app iframe) and Download buttons at milestones; primary and extra artifacts MUST resolve to files that exist
- **FR-015**: Settings MUST support: API key, default model, per-step model overrides, retry limits, JAR paths, HTML template paths, simulator config, vendor profiles
- **FR-016**: System MUST generate final HTML and Excel reports per constitution Principle V
- **FR-017**: QA skills MUST read failure detail from JAR output files on disk, not from session state
- **FR-019**: UI content area MUST use full viewport width/height with proper scrolling — no content truncation
- **FR-032**: LLM calls MUST use the live model/API key (`ConfigManager._live_instance`, falling back to the DB `settings` table for CLI use) so settings changes apply without restart

### Key Entities

- **PipelineRun**: A single execution of the pipeline (run_id, status, current_step, timestamps, config_snapshot, vendor_profile, developer_notes)
- **StepResult**: Outcome of one display step (step_number, status, error_message, artifacts, retry_count)
- **QA Feedback**: Structured output of a QA skill (failure_type, classification, fix_instructions, retry_target) — replaces the former ReviewerAnalysis agent output
- **Artifact**: Generated file (requirements.json, validation-rules.yaml, json-output.yaml, workflow.yaml, scripts/*.py, simulator-config.yaml, QA reports, final reports)
- **Requirements Package**: Compiled requirements per run in `requirements_store`, persisted per UC at `data/knowledge/{uc_name}/requirements.json`
- **Vendor Profile**: Vendor → node types (prompt patterns, elevate_to_root) → activities (CIQ field mappings), plus PAM gateway config

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Pipeline completes all 10 steps for a standard UC within 10 minutes (excluding user review time) — *observed 205s in E2E (0f52f58)*
- **SC-002**: Self-correction loop resolves at least 70% of generation failures without human intervention
- **SC-003**: Smart failure routing correctly classifies Step 9 failures (workflow vs simulator) in 90%+ of cases
- **SC-004**: All milestones display artifacts with View/Download within 2 seconds of step completion
- **SC-005**: UI loads and renders all sections without content truncation on standard 1920x1080 displays
- **SC-006**: Settings changes take effect without server restart (including for a running pipeline)
- **SC-007**: Pipeline state survives server restart — resumed runs complete successfully *(not implemented)*
- **SC-008**: No step reports "passed" when its underlying JAR did not run or produced no output

## Assumptions

- Google ADK 2.11.0+ is installed and available
- ADK `ctx.session.state` writes in sub-agents are NOT reliably visible to the parent agent (finding from bc567e9 / 5455064; drives FR-018)
- OpenRouter API access is available with a valid API key
- Java JARs (ciq-processor 1.0.3, cli-automation-standalone, execution-report-generator 1.0.2) are pre-built and available in lib/
- MockWire simulator JAR is available at lib/mockwire/mockwire-simulator/mockwire.jar
- NiceGUI is the UI framework
- SQLite is sufficient for persistence (no need for external DB)

## Revision History

| Date | Change |
|------|--------|
| 2026-10-16 | Initial draft: 23 agents (10 step wrappers + 7 milestones + 3 LlmAgent reviewers + 3 routers), ADK session state as data bus, cross-project imports via cr_import |
| 2026-11-02 | Revised to as-built architecture. 9 skill-backed agents with built-in milestones (bd35015, 558626a, 1cbef81); reviewers folded into QA skills; routers replaced by `_retry_target`; session state replaced by shared dicts + disk error files (5455064); `src/` dissolved (9ca88db); cr_bridge removed (5d3032c); product renamed NetConfig. FR-001–FR-011, FR-017, FR-018 rewritten; FR-020–FR-032 added from bug fixes. MOP QA / Exec QA split (558626a) and vendor profiles (8a97fb0) folded into this spec rather than separate features. Bug register added: [bugs.md](bugs.md). New feature specs: 002-artifact-cache, 003-helper-script-gen, 004-simulator-setup |
