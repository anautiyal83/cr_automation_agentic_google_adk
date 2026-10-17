# Tasks: Google ADK Orchestrator Pipeline

**Input**: Design documents from `specs/001-adk-orchestrator-pipeline/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/pipeline-runner-api.md

**Organization**: Tasks grouped by user story for independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: User story label (US1-US5)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization, dependencies, and copied infrastructure modules

- [ ] T001 Create requirements.txt with google-adk>=2.11.0, litellm, nicegui>=2.0, ruamel.yaml, pydantic>=2.0, openpyxl, xlsxwriter, aiosqlite in requirements.txt
- [ ] T002 Create .gitignore with data/, __pycache__/, *.pyc, .env in .gitignore
- [ ] T003 [P] Copy Pydantic models (PipelineRun with fields: id UUID, document_set_id UUID, status Enum[running|paused_milestone|completed|failed|cancelled], current_step Integer 1-10, started_at Datetime, completed_at Datetime nullable, duration_seconds Float nullable, config_snapshot JSON, error_summary String nullable; StepResult, UCDocumentSet, PipelineConfiguration, RetryContext, StepOutput) from D:/my_workspace/cr_automation-agentic/src/core/models.py to src/core/models.py
- [ ] T004 [P] Copy EventBus (publish/subscribe pattern with PipelineEvent dataclass: run_id UUID, event_type str, step_number int, timestamp datetime, payload dict) from D:/my_workspace/cr_automation-agentic/src/core/events.py to src/core/events.py
- [ ] T005 [P] Copy retry module (InfrastructureError exception, RetryContext model with attempt_number int and previous_error dict) from D:/my_workspace/cr_automation-agentic/src/core/retry.py to src/core/retry.py
- [ ] T006 [P] Copy and adapt ConfigManager (update PROJECT_ROOT to this project's root) from D:/my_workspace/cr_automation-agentic/src/core/config.py to src/core/config.py
- [ ] T007 [P] Copy Database module (SQLite with tables: pipeline_runs, uc_document_sets, step_results, settings, generated_configs, knowledge_base_entries) from D:/my_workspace/cr_automation-agentic/src/storage/db.py to src/storage/db.py
- [ ] T008 [P] Copy ArtifactStore (run_dir management, content_hash, file operations) from D:/my_workspace/cr_automation-agentic/src/storage/artifact_store.py to src/storage/artifact_store.py
- [ ] T009 [P] Copy and adapt pipeline_config.yaml (update external_modules paths for this project) from D:/my_workspace/cr_automation-agentic/config/pipeline_config.yaml to config/pipeline_config.yaml
- [ ] T010 Create all __init__.py files for src/core/, src/storage/, src/ui/, src/ui/pages/

**Checkpoint**: Core infrastructure copied and importable

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Custom PipelineAgent and PipelineRunner — the orchestration core that ALL user stories depend on

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [ ] T011 Implement PipelineAgent(BaseAgent) in src/orchestrator/pipeline_agent.py — custom orchestrator with state-machine loop: 23 agents indexed 0-22, _step_to_index dict mapping step numbers to agent indices, _run_async_impl that loops through agents checking _retry_target in session state for backtracking, pipeline_cancelled/pipeline_failed for termination, conditional execution for reviewers (skip if preceding test step passed)
- [ ] T012 Refactor step_nodes.py — keep only 10 BaseAgent step wrappers (DocParserNode, UCAnalyzerNode, ValidationRulesNode, JsonTemplateNode, CiqQANode, WorkflowGenNode, MopTestNode, SimulatorSetupNode, ExecutionTestNode, FinalReportNode) in src/nodes/step_nodes.py; each reads from ctx.session.state via _get_step_data(), calls existing agent via cr_import.py, writes results via _save_step_result()
- [ ] T013 [P] Extract milestone nodes to src/nodes/milestone_nodes.py — 7 MilestoneNode(BaseAgent) instances for steps 3,4,5,6,7,8,9; each sets milestone_N_waiting=True in state, waits on asyncio.Event (not polling), reads milestone_N_decision on resume
- [ ] T014 [P] Extract router nodes to src/nodes/router_nodes.py — 3 FailureRouter(BaseAgent) for steps 5,7,9; Router5 checks step_5_status and sets _retry_target=3; Router7 checks step_7_status and sets _retry_target=6; Router9 calls classify_failure_type() and sets _retry_target=6 or 8
- [ ] T015 [P] Extract shared tools to src/nodes/tools.py — move validate_yaml_syntax, extract_yaml_from_response, read_ciq_structure, read_file_content, write_artifact, fix_validation_rules_yaml, fix_json_template_yaml, fix_workflow_yaml, validate_mock_response from llm_agents.py; add read_qa_report, read_exec_report, read_mop_report, classify_failure_type from reviewer_agents.py
- [ ] T016 [P] Refactor reviewer_agents.py — 3 LlmAgent reviewers (qa_validation_reviewer, qa_mop_reviewer, qa_execution_reviewer) in src/nodes/reviewer_agents.py; each uses FunctionTools from tools.py; add skip guard: if preceding step passed, yield immediately without LLM call
- [ ] T017 Remove src/nodes/llm_agents.py — LlmAgent factory functions deprecated per research decision R2; tools already moved to tools.py
- [ ] T018 Rewrite PipelineRunner in src/orchestrator/runner.py — init with Database + ArtifactStore + ConfigManager + EventBus; start() creates PipelineRun row in DB, builds initial session state, launches PipelineAgent in asyncio.Task; get_status/get_state/respond_milestone/resume_run/cancel_run per contracts/pipeline-runner-api.md; milestone handling via asyncio.Event (runner creates Event, milestone node awaits, respond_milestone signals); step completion persists StepResult to DB and publishes PipelineEvent
- [ ] T019 Wire PipelineAgent with all 23 agents in src/orchestrator/pipeline_agent.py — build agent list: [DocParser, UCAnalyzer, ValRules, Milestone3, JsonTemplate, Milestone4, CiqQA, Milestone5, QAValReviewer, Router5, WorkflowGen, Milestone6, MopTest, Milestone7, QAMopReviewer, Router7, SimSetup, Milestone8, ExecTest, Milestone9, QAExecReviewer, Router9, FinalReport]; build _step_to_index: {3:2, 6:10, 8:16}

**Checkpoint**: Pipeline can execute end-to-end via CLI (test_run.py) with backtracking on failures

---

## Phase 3: User Story 1 — Run Full Pipeline End-to-End (Priority: P1) 🎯 MVP

**Goal**: Upload CIQ, run 10-step pipeline with auto-retry, get final reports

**Independent Test**: python test_run.py with DSR_1058_IFC_CAPABILITY_MODIFICATION CIQ → all 10 steps complete

### Implementation for User Story 1

- [ ] T020 [US1] Update test_run.py to use new PipelineRunner API (import from src.orchestrator.runner, pass config with DB path, auto-continue milestones, print step-by-step results) in test_run.py
- [ ] T021 [US1] Update main.py CLI to use new PipelineRunner with DB persistence, add --resume flag for resuming failed runs in main.py
- [ ] T022 [US1] Verify end-to-end pipeline execution with test CIQ — run test_run.py, confirm all 10 steps execute, artifacts generated in data/runs/<run_id>/, final report produced
- [ ] T023 [US1] Verify retry loop — deliberately fail Step 3 (corrupt CIQ columns), confirm QA Validation Reviewer runs, Router5 sets _retry_target=3, PipelineAgent jumps back, Step 3 retries with qa_feedback

**Checkpoint**: Full pipeline runs end-to-end from CLI with automatic retry loops

---

## Phase 4: User Story 2 — Milestone Review and Manual Control (Priority: P1)

**Goal**: Pipeline pauses at milestones, user can view artifacts and choose Continue/Re-run/Cancel

**Independent Test**: Run pipeline, wait for Step 3 milestone, respond with "continue", observe pipeline proceeding

### Implementation for User Story 2

- [ ] T024 [US2] Verify milestone pause works — run pipeline, confirm it pauses at Step 3 with milestone_3_waiting=True in state, respond_milestone("continue") resumes execution
- [ ] T025 [US2] Verify re-run routing — at Step 5 milestone with failures, respond "rerun", confirm pipeline jumps back to Step 3 with qa_feedback in state
- [ ] T026 [US2] Verify cancel — at any milestone, respond "cancel", confirm pipeline stops with status "cancelled"
- [ ] T027 [US2] Verify artifact file paths are set correctly in state — at each milestone, step_N_output should contain artifact file paths (e.g., validation-rules.yaml, workflow.yaml)

**Checkpoint**: Milestone pause/resume works correctly for all 7 milestone steps

---

## Phase 5: User Story 3 — Smart Failure Recovery with Peer Reviewers (Priority: P1)

**Goal**: Reviewer agents analyze failures and route to correct upstream step

**Independent Test**: Trigger Step 9 failure, verify reviewer classifies and routes correctly

### Implementation for User Story 3

- [ ] T028 [US3] Test QA Validation Reviewer — trigger Step 5 failure, verify reviewer produces JSON with analysis, fix_instructions array (each with location, problem, fix, priority), retry_recommended flag
- [ ] T029 [US3] Test QA MOP Reviewer — trigger Step 7 failure (invalid workflow YAML), verify reviewer identifies failure_type as "yaml_parse" or "invalid_field", generates fix_instructions for Step 6
- [ ] T030 [US3] Test QA Execution Reviewer classification — trigger Step 9 failure with "PatternSyntaxException", verify classification="workflow" and retry_target=6; trigger with "Connection refused", verify classification="simulator" and retry_target=8
- [ ] T031 [US3] Verify reviewer instructions flow to upstream step — after reviewer generates fix_instructions, verify reviewer_instructions key in session state is populated, and upstream step receives it on retry via retry_context

**Checkpoint**: All 3 reviewers correctly analyze failures and route retries

---

## Phase 6: User Story 4 — Dashboard and Run History (Priority: P2)

**Goal**: NiceGUI web UI with dashboard stats, upload, progress tracking, history

**Independent Test**: Open http://localhost:8000, see dashboard with stats, upload CIQ, watch pipeline progress

### Implementation for User Story 4

- [ ] T032 [P] [US4] Create NiceGUI app entry point with sidebar (140px), content area (calc(100vw-140px)), dark theme CSS, navigation system (dashboard/new_run/progress/outputs/history/cleanup/settings) adapted from D:/my_workspace/cr_automation-agentic/src/ui/app.py to src/ui/app.py
- [ ] T033 [P] [US4] Create upload section with CIQ file upload (required), MOP upload (optional), constraints upload (optional), UC name input, Start Pipeline button adapted from D:/my_workspace/cr_automation-agentic/src/ui/pages/upload_section.py to src/ui/pages/upload_section.py
- [ ] T034 [US4] Create progress section with 10-step single-row tracker (28px circles, 10px connectors, step names: DOC Parser/DOC Analyzer/CIQ Config Generation/Generate CIQ JSON Config/QA-Validation/Generate Execution Config/QA-MOP/Simulator Setup/QA-Execution/Final Report), status banner, progress bar, milestone review panel (View Artifact/Download/Continue/Re-run/Cancel), EventBus subscription adapted from D:/my_workspace/cr_automation-agentic/src/ui/pages/progress_section.py to src/ui/pages/progress_section.py
- [ ] T035 [P] [US4] Create history section with pipeline_runs table (run_id, UC name, status, duration, actions: re-run/view details/view outputs) reading from Database adapted from D:/my_workspace/cr_automation-agentic/src/ui/pages/history_section.py to src/ui/pages/history_section.py
- [ ] T036 [P] [US4] Create output viewer section with artifact browser per run (YAML preview, HTML iframe, download buttons) adapted from D:/my_workspace/cr_automation-agentic/src/ui/pages/output_viewer_section.py to src/ui/pages/output_viewer_section.py
- [ ] T037 [P] [US4] Create cleanup section with run listing, artifact sizes, delete actions adapted from D:/my_workspace/cr_automation-agentic/src/ui/pages/cleanup_section.py to src/ui/pages/cleanup_section.py
- [ ] T038 [US4] Create dashboard panel with stat cards (Pipeline Runs count, Success Rate %, Avg Duration) querying pipeline_runs table in src/ui/app.py
- [ ] T039 [US4] Wire UI to PipelineRunner — upload_section calls runner.start(), progress_section subscribes to EventBus, milestone buttons call runner.respond_milestone(), history re-run calls runner.resume_run()
- [ ] T040 [US4] Create start.ps1 and start.sh launcher scripts that set PYTHONPATH, activate venv, run python -u src/ui/app.py in start.ps1 and start.sh

**Checkpoint**: Full web UI operational — dashboard, upload, progress tracking, history, cleanup

---

## Phase 7: User Story 5 — Settings Configuration (Priority: P2)

**Goal**: Settings UI for API key, models, retry limits, JAR paths, templates

**Independent Test**: Change default model in Settings, save, verify new model used in next pipeline run

### Implementation for User Story 5

- [ ] T041 [US5] Create settings section with tabs (API & Models / Per-Step Models / Retry Policy / External Modules), compact layout (p-3 padding, gap-2), all inputs dense+outlined; API key input with Save Key button; model select from AVAILABLE_MODELS list; per-step model overrides for steps 2,3,4,6,8; retry sliders (test 1-10, infra 1-10, backoff 1.0-10.0); JAR paths in rows (CIQ Processor, CLI Automation, Report Generator); HTML templates in rows; simulator endpoint+folder adapted from D:/my_workspace/cr_automation-agentic/src/ui/pages/settings_section.py to src/ui/pages/settings_section.py
- [ ] T042 [US5] Wire settings to Database — load_saved_settings() reads from settings table on startup; _save_all() persists all values; _save_key() persists API key; update ConfigManager on save
- [ ] T043 [US5] Verify settings persistence — save API key, restart server, verify key is loaded; change retry limit, start pipeline, verify new limit is used

**Checkpoint**: Settings save/load/apply correctly across server restarts

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Final integration, testing, documentation

- [ ] T044 Verify content area uses full viewport width/height with proper scrolling (no truncation) on 1920x1080 display — check all sections: dashboard, upload, progress, settings, history, outputs
- [ ] T045 Verify completed step visuals are preserved when continuing from mid-pipeline milestone (steps 1-N show green checkmarks, not greyed out pending)
- [ ] T046 [P] Create tests/test_pipeline_agent.py — test: sequential execution all pass, Step5→3 retry, Step9→6 retry, Step9→8 retry, cancellation, retry limit exhaustion (6 test cases)
- [ ] T047 [P] Create tests/test_runner.py — test: start/get_status/respond_milestone lifecycle, DB persistence of PipelineRun and StepResult rows, resume_run from step N
- [ ] T048 Update README.md with final architecture diagram, usage instructions, all 23 agents listed
- [ ] T049 Update CLAUDE.md with final file paths and architecture decisions
- [ ] T050 Run quickstart.md validation scenarios V1-V5 end-to-end

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately
- **Phase 2 (Foundational)**: Depends on Phase 1 — BLOCKS all user stories
- **Phase 3 (US1 - E2E Pipeline)**: Depends on Phase 2
- **Phase 4 (US2 - Milestones)**: Depends on Phase 2 (can run parallel with US1)
- **Phase 5 (US3 - Reviewers)**: Depends on Phase 2 (can run parallel with US1/US2)
- **Phase 6 (US4 - UI)**: Depends on Phase 2 + Phase 3 (needs working pipeline to connect to)
- **Phase 7 (US5 - Settings)**: Depends on Phase 6 (needs UI app.py)
- **Phase 8 (Polish)**: Depends on all previous phases

### User Story Dependencies

- **US1 (E2E Pipeline)**: Phase 2 only — core pipeline execution
- **US2 (Milestones)**: Phase 2 only — milestone nodes already in Phase 2
- **US3 (Reviewers)**: Phase 2 only — reviewer agents already in Phase 2
- **US4 (UI)**: Phase 2 + US1 working pipeline
- **US5 (Settings)**: Phase 6 (US4) — needs UI framework

### Parallel Opportunities

**Phase 1** — T003-T010 all parallelizable (different files, copy operations)

**Phase 2** — T013, T014, T015, T016 parallelizable (different files)

**Phase 6** — T032, T033, T035, T036, T037 parallelizable (different UI pages)

---

## Parallel Example: Phase 1

```bash
# All copy tasks run in parallel:
Task: T003 Copy models.py to src/core/models.py
Task: T004 Copy events.py to src/core/events.py
Task: T005 Copy retry.py to src/core/retry.py
Task: T006 Copy config.py to src/core/config.py
Task: T007 Copy db.py to src/storage/db.py
Task: T008 Copy artifact_store.py to src/storage/artifact_store.py
```

## Parallel Example: Phase 6 (UI)

```bash
# All UI pages run in parallel:
Task: T032 Create app.py with layout
Task: T033 Create upload_section.py
Task: T035 Create history_section.py
Task: T036 Create output_viewer_section.py
Task: T037 Create cleanup_section.py
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (copy infrastructure)
2. Complete Phase 2: Foundational (PipelineAgent + Runner)
3. Complete Phase 3: US1 — E2E Pipeline via CLI
4. **STOP and VALIDATE**: Run test_run.py, confirm 10 steps complete
5. This gives a working CLI pipeline without UI

### Incremental Delivery

1. Setup + Foundational → Core orchestration working
2. US1 (E2E Pipeline) → CLI pipeline functional (MVP!)
3. US2+US3 (Milestones + Reviewers) → Smart retry loops working
4. US4 (UI) → Full web interface
5. US5 (Settings) → Configuration management
6. Polish → Tests, docs, validation

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story
- All step agents are BaseAgent wrappers (research decision R2)
- Only 3 reviewer agents use LlmAgent (research decision R2)
- Milestone nodes use asyncio.Event (research decision R3)
- UI adapted from cr_automation-agentic (research decision R4)
- DB schema copied from cr_automation-agentic (research decision R5)
