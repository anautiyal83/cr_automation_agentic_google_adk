# Feature Specification: Google ADK Orchestrator Pipeline

**Feature Branch**: `001-adk-orchestrator-pipeline`

**Created**: 2026-10-16

**Status**: Draft

**Input**: Rewrite of cr_automation-agentic pipeline using Google ADK for orchestration with native LlmAgents, peer reviewer agents for failure coordination, smart retry routing, and optimized NiceGUI UI.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Run Full Pipeline End-to-End (Priority: P1)

An automation engineer uploads a CIQ Excel workbook and starts the pipeline. The system parses documents, generates YAML configs via LLM agents, validates them via Java JARs, generates workflow and simulator configs, executes against MockWire, and produces final reports — all orchestrated by Google ADK with automatic retry on failures.

**Why this priority**: Core value proposition — automated end-to-end UC config generation with zero manual YAML authoring.

**Independent Test**: Upload a known CIQ (e.g., DSR_1058_IFC_CAPABILITY_MODIFICATION), start pipeline, observe all 10 steps completing with generated artifacts.

**Acceptance Scenarios**:

1. **Given** a valid CIQ is uploaded, **When** pipeline starts, **Then** all 10 steps execute in sequence with status updates visible in UI
2. **Given** Step 3 generates validation rules, **When** Step 5 QA finds failures, **Then** the QA Validation Reviewer analyzes failures and retries Step 3 with fix instructions automatically
3. **Given** Step 9 execution fails, **When** failure is classified as workflow issue, **Then** pipeline retries from Step 6; when classified as simulator issue, **Then** retries from Step 8

---

### User Story 2 - Milestone Review and Manual Control (Priority: P1)

At each milestone (Steps 3,4,5,6,7,8,9), the pipeline pauses and shows generated artifacts for review. The engineer can view/download artifacts, continue, re-run, or cancel.

**Why this priority**: Human-in-the-loop review is required per constitution Principle III for trustworthy automation.

**Independent Test**: Run pipeline, wait for Step 3 milestone, view the validation-rules.yaml artifact, click Continue, observe pipeline proceeding to Step 4.

**Acceptance Scenarios**:

1. **Given** Step 3 completes, **When** milestone is reached, **Then** UI shows artifact name, View/Download buttons, and Continue/Re-run/Cancel options
2. **Given** a milestone with failed test results, **When** user clicks Re-run, **Then** pipeline routes to the correct upstream step with feedback
3. **Given** user clicks Cancel at any milestone, **Then** pipeline stops and status shows "Cancelled"

---

### User Story 3 - Smart Failure Recovery with Peer Reviewers (Priority: P1)

When a test step fails, a specialized reviewer agent analyzes the failure, classifies the root cause, and generates structured fix instructions for the upstream LLM agent. The pipeline automatically retries with these instructions.

**Why this priority**: Eliminates manual debugging of LLM generation failures — self-healing loop per constitution Principle III.

**Independent Test**: Trigger a Step 9 execution failure (e.g., bad validation expression), observe the QA Execution Reviewer classify it as "workflow issue", generate fix instructions, and route retry to Step 6.

**Acceptance Scenarios**:

1. **Given** Step 5 CIQ QA fails, **When** QA Validation Reviewer runs, **Then** it produces JSON fix instructions identifying the specific rule, current value, problem, and recommended fix
2. **Given** Step 9 fails with "PatternSyntaxException", **When** QA Execution Reviewer classifies, **Then** it identifies "workflow" as failure type and routes to Step 6
3. **Given** Step 9 fails with "Connection refused", **When** QA Execution Reviewer classifies, **Then** it identifies "simulator" as failure type and routes to Step 8
4. **Given** retry limit is exhausted, **When** pipeline cannot self-correct, **Then** it fails gracefully with a clear error and all reviewer analysis reports

---

### User Story 4 - Dashboard and Run History (Priority: P2)

The engineer sees a dashboard with pipeline statistics (total runs, success rate, average duration) and can browse past runs, view their artifacts, and re-run failed pipelines.

**Why this priority**: Operational visibility and ability to recover from past failures.

**Independent Test**: After 3 pipeline runs, open Dashboard and verify stats are correct. Open Run History, click on a completed run, view its artifacts.

**Acceptance Scenarios**:

1. **Given** 5 runs have completed (3 success, 2 failed), **When** dashboard loads, **Then** shows "5 Runs", "60% Success", and average duration
2. **Given** a failed run in history, **When** user clicks Re-run, **Then** a new pipeline starts with the same CIQ and settings

---

### User Story 5 - Settings Configuration (Priority: P2)

The engineer configures API keys, LLM models (per-step overrides), retry limits, JAR paths, HTML templates, and simulator settings through the Settings UI.

**Why this priority**: Operational flexibility — different environments need different configs.

**Independent Test**: Change default model in Settings, save, start new pipeline, verify the new model is used in logs.

**Acceptance Scenarios**:

1. **Given** API key is entered, **When** Save is clicked, **Then** key is persisted to DB and used for subsequent LLM calls
2. **Given** per-step model override is set for Step 6, **When** pipeline runs, **Then** Step 6 uses the override model while others use the default

---

### Edge Cases

- What happens when the LLM API is unreachable? Pipeline retries with exponential backoff up to the configured infra retry limit, then fails with clear error.
- What happens when a JAR file is missing? Step fails immediately at pre-flight check with "JAR not found" error — no retry.
- What happens when MockWire simulator fails to start? QA Execution Reviewer classifies as simulator issue, retries Step 8.
- What happens when CIQ has no data? Step 1 parser returns empty structure, Step 3 generates minimal rules, Step 5 QA may flag issues.
- What happens during server restart mid-pipeline? Pipeline state is persisted to SQLite; on restart, in-progress runs can be resumed from the last completed step.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST orchestrate 10 pipeline steps using Google ADK SequentialAgent with conditional routing
- **FR-002**: Steps 2, 3, 4, 6, 8 MUST use native ADK LlmAgent with FunctionTools for YAML generation and post-processing
- **FR-003**: Steps 1, 5, 7, 9, 10 MUST use deterministic BaseAgent wrappers that call existing Java JARs
- **FR-004**: System MUST provide 3 peer reviewer LlmAgents that analyze test failures and generate structured JSON fix instructions
- **FR-005**: System MUST classify Step 9 failures as "workflow" (route to Step 6) or "simulator" (route to Step 8) using error pattern matching
- **FR-006**: System MUST pause at 7 milestones (Steps 3,4,5,6,7,8,9) for human review with Continue/Re-run/Cancel options
- **FR-007**: System MUST retry LLM generation steps up to a configurable limit (default 3) with previous error context
- **FR-008**: System MUST retry infrastructure errors (LLM API timeouts, rate limits) with exponential backoff
- **FR-009**: System MUST persist pipeline state to SQLite so runs can be resumed after server restart
- **FR-010**: System MUST use LiteLlm for LLM integration supporting OpenRouter and Gemini 2.5 Flash
- **FR-011**: System MUST import existing agents from cr_automation-agentic via importlib without src package conflicts
- **FR-012**: System MUST provide a NiceGUI web UI with: sidebar navigation, dashboard, upload section, pipeline progress tracker, output viewer, run history, and settings
- **FR-013**: UI pipeline progress MUST show all 10 steps in a single compact row with real-time status updates
- **FR-014**: UI MUST display artifacts with View (in-app iframe) and Download buttons at milestones
- **FR-015**: Settings MUST support: API key, default model, per-step model overrides, retry limits, JAR paths, HTML template paths, simulator config
- **FR-016**: System MUST generate final HTML and Excel reports per constitution Principle V
- **FR-017**: Each reviewer agent MUST use tools to read failure reports, artifacts, and configs — not receive raw data in prompts
- **FR-018**: ADK session state MUST carry all pipeline data between steps (artifacts, feedback, intermediate representations)
- **FR-019**: UI content area MUST use full viewport width/height with proper scrolling — no content truncation

### Key Entities

- **PipelineRun**: A single execution of the 10-step pipeline (run_id, status, current_step, timestamps, config_snapshot)
- **StepResult**: Outcome of one pipeline step (step_number, status, error_message, artifacts, retry_count)
- **ReviewerAnalysis**: Structured output from a peer reviewer agent (failure_type, classification, fix_instructions, retry_target)
- **Artifact**: Generated file (validation-rules.yaml, json-output.yaml, workflow.yaml, simulator configs, reports)
- **Session State**: ADK session carrying pipeline context (intermediate_repr, ciq_data, artifacts, feedback)

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Pipeline completes all 10 steps for a standard UC within 10 minutes (excluding user review time)
- **SC-002**: Self-correction loop resolves at least 70% of generation failures without human intervention
- **SC-003**: Smart failure routing correctly classifies Step 9 failures (workflow vs simulator) in 90%+ of cases
- **SC-004**: All 7 milestones display artifacts with View/Download within 2 seconds of step completion
- **SC-005**: UI loads and renders all sections without content truncation on standard 1920x1080 displays
- **SC-006**: Settings changes take effect on the next pipeline run without server restart
- **SC-007**: Pipeline state survives server restart — resumed runs complete successfully

## Assumptions

- Google ADK 2.11.0+ is installed and available (already confirmed)
- Existing cr_automation-agentic agents at D:/my_workspace/cr_automation-agentic/ are stable and tested
- OpenRouter API access is available with a valid API key
- Java JARs (ciq-processor, cli-automation-standalone, execution-report-generator) are pre-built and available in lib/
- MockWire simulator JAR is available at lib/mockwire/mockwire-simulator/mockwire.jar
- NiceGUI is the UI framework (consistent with existing project)
- SQLite is sufficient for persistence (no need for external DB)
- ADK's SequentialAgent with BaseAgent subclasses supports the conditional routing pattern via session state flags
