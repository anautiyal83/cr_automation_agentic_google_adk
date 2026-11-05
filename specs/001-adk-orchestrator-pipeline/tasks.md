# Tasks: Google ADK Orchestrator Pipeline

**Input**: Design documents from `specs/001-adk-orchestrator-pipeline/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/pipeline-runner-api.md

**Organization**: Tasks grouped by user story for independent implementation and testing.

**Revision 2026-11-02**: Status reconciled against code on `feature/skill-packages`. Tasks made obsolete by the move from 23/26 agents to 9 skill-based agents (bd35015) and by dissolving `src/` (9ca88db) are struck through and marked SUPERSEDED; they are kept, not deleted. Paths in the original task text refer to the old `src/` layout. Bug fixes are tracked in Phase 9 and [bugs.md](bugs.md).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: User story label (US1-US5)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization, dependencies, and copied infrastructure modules

- [X] T001 Create requirements.txt with google-adk>=2.11.0, litellm, nicegui>=2.0, ruamel.yaml, pydantic>=2.0, openpyxl, xlsxwriter, aiosqlite in requirements.txt
- [X] T002 Create .gitignore with data/, __pycache__/, *.pyc, .env in .gitignore
- [X] T003 [P] Copy Pydantic models (PipelineRun with fields: id UUID, document_set_id UUID, status Enum[running|paused_milestone|completed|failed|cancelled], current_step Integer 1-10, started_at Datetime, completed_at Datetime nullable, duration_seconds Float nullable, config_snapshot JSON, error_summary String nullable; StepResult, UCDocumentSet, PipelineConfiguration, RetryContext, StepOutput) from D:/my_workspace/cr_automation-agentic/src/core/models.py to src/core/models.py — *now `models/domain.py` (5255e61, 9ca88db)*
- [X] T004 [P] Copy EventBus (publish/subscribe pattern with PipelineEvent dataclass: run_id UUID, event_type str, step_number int, timestamp datetime, payload dict) from D:/my_workspace/cr_automation-agentic/src/core/events.py to src/core/events.py — *now `core/events.py`*
- [X] T005 [P] Copy retry module (InfrastructureError exception, RetryContext model with attempt_number int and previous_error dict) from D:/my_workspace/cr_automation-agentic/src/core/retry.py to src/core/retry.py — *now `core/retry.py`*
- [X] T006 [P] Copy and adapt ConfigManager (update PROJECT_ROOT to this project's root) from D:/my_workspace/cr_automation-agentic/src/core/config.py to src/core/config.py — *now `core/config.py`*
- [X] T007 [P] Copy Database module (SQLite with tables: pipeline_runs, uc_document_sets, step_results, settings, generated_configs, knowledge_base_entries) from D:/my_workspace/cr_automation-agentic/src/storage/db.py to src/storage/db.py — *now `storage/db.py`*
- [X] T008 [P] Copy ArtifactStore (run_dir management, content_hash, file operations) from D:/my_workspace/cr_automation-agentic/src/storage/artifact_store.py to src/storage/artifact_store.py — *now `storage/artifact_store.py`*
- [X] T009 [P] Copy and adapt pipeline_config.yaml (update external_modules paths for this project) from D:/my_workspace/cr_automation-agentic/config/pipeline_config.yaml to config/pipeline_config.yaml
- [X] T010 Create all __init__.py files for src/core/, src/storage/, src/ui/, src/ui/pages/ — *now root-level packages*

**Checkpoint**: Core infrastructure copied and importable

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Custom PipelineAgent and PipelineRunner — the orchestration core that ALL user stories depend on

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T011 ~~Implement PipelineAgent(BaseAgent) in src/orchestrator/pipeline_agent.py — custom orchestrator with state-machine loop: 23 agents indexed 0-22, _step_to_index dict mapping step numbers to agent indices, _run_async_impl that loops through agents checking _retry_target in session state for backtracking, pipeline_cancelled/pipeline_failed for termination, conditional execution for reviewers~~ — DONE in revised form: `orchestrator/pipeline_agent.py` runs 9 agents with `_AGENT_DISPLAY_STEPS`, `_AGENT_MILESTONES`, `_RETRY_STEP_TO_INDEX`, `MAX_RETRIES` (bd35015, 558626a, 1cbef81)
- [ ] T012 ~~Refactor step_nodes.py — keep only 10 BaseAgent step wrappers … in src/nodes/step_nodes.py~~ SUPERSEDED by bd35015 (replaced by one thin agent per skill in `agents/`)
- [ ] T013 [P] ~~Extract milestone nodes to src/nodes/milestone_nodes.py — 7 MilestoneNode(BaseAgent) instances~~ SUPERSEDED by bd35015 (milestones built into `PipelineAgent._milestone_pause`, still `asyncio.Event`-based)
- [ ] T014 [P] ~~Extract router nodes to src/nodes/router_nodes.py — 3 FailureRouter(BaseAgent)~~ SUPERSEDED by bd35015 (QA agents set `_retry_target` directly)
- [ ] T015 [P] ~~Extract shared tools to src/nodes/tools.py~~ SUPERSEDED by 5255e61 + bd35015 (`tools/yaml_tools.py`, `tools/report_tools.py`, `tools/ciq_tools.py`; skill-local autofix modules)
- [ ] T016 [P] ~~Refactor reviewer_agents.py — 3 LlmAgent reviewers~~ SUPERSEDED by 21d8864 → bd35015 (reviewers became BaseAgents, then were folded into QA skills)
- [X] T017 Remove src/nodes/llm_agents.py — LlmAgent factory functions deprecated per research decision R2 (whole `src/nodes/` removed in 5255e61)
- [X] T018 Rewrite PipelineRunner in src/orchestrator/runner.py — init with Database + ArtifactStore + ConfigManager + EventBus; start(), get_status, get_state, respond_milestone, cancel_run; milestone handling via asyncio.Event; step completion persists StepResult to DB and publishes PipelineEvent — *now `orchestrator/runner.py`. **`resume_run` NOT implemented** (see bugs.md OB-6)*
- [X] T019 ~~Wire PipelineAgent with all 23 agents~~ — DONE in revised form: `build_pipeline()` wires 9 agents; retry map {3→1, 4→1, 6→3, 8→6}

**Checkpoint**: Pipeline can execute end-to-end via CLI (test_run.py) with backtracking on failures

---

## Phase 3: User Story 1 — Run Full Pipeline End-to-End (Priority: P1) 🎯 MVP

**Goal**: Upload CIQ, run 10-step pipeline with auto-retry, get final reports

**Independent Test**: python test_run.py with DSR_1058_IFC_CAPABILITY_MODIFICATION CIQ → all 10 steps complete

### Implementation for User Story 1

- [X] T020 [US1] Update test_run.py to use new PipelineRunner API (auto-continue milestones, auto-discover CIQ from uploads/) in test_run.py (bd35015, 0f52f58)
- [ ] T021 [US1] Update main.py CLI to use new PipelineRunner with DB persistence, add --resume flag for resuming failed runs in main.py — *runner wired; `--resume` not present*
- [X] T022 [US1] Verify end-to-end pipeline execution with test CIQ — E2E COMPLETED in 205s, reports generated (0f52f58)
- [X] T023 [US1] Verify retry loop — CIQ gen ↔ QA retry observed in run ef4fec2f; MAX_RETRIES enforced (0f52f58). ~~Router5 sets _retry_target=3~~ → QA agent sets it

**Checkpoint**: Full pipeline runs end-to-end from CLI with automatic retry loops

---

## Phase 4: User Story 2 — Milestone Review and Manual Control (Priority: P1)

**Goal**: Pipeline pauses at milestones, user can view artifacts and choose Continue/Re-run/Cancel

**Independent Test**: Run pipeline, wait for Step 4 milestone, respond with "continue", observe pipeline proceeding

### Implementation for User Story 2

- [X] T024 [US2] Verify milestone pause works — pauses with `milestone_waiting[step]` (shared dict) and resumes on respond_milestone("continue") (logs 2026-11-02)
- [X] T025 [US2] Verify re-run routing — re-run at milestone jumps to `_RETRY_STEP_TO_INDEX` target (logs 2026-11-02 13:00:37)
- [ ] T026 [US2] Verify cancel — at any milestone, respond "cancel", confirm pipeline stops with status "cancelled" — *no recorded verification*
- [X] T027 [US2] Verify artifact file paths are set correctly at each milestone (fixed via BF-025, BF-045, BF-046, BF-049, BF-053)

**Checkpoint**: Milestone pause/resume works correctly for all milestone steps

---

## Phase 5: User Story 3 — Smart Failure Recovery (Priority: P1)

**Goal**: QA skills analyze failures and route to correct upstream step

**Independent Test**: Trigger Step 9 failure, verify QA skill classifies and routes correctly

### Implementation for User Story 3

- [X] T028 [US3] Test CIQ QA failure analysis — per-sheet / per-cell test cases from JAR report (44bf869). ~~QA Validation Reviewer~~ → `ciq_validation_json_test` skill
- [ ] T029 [US3] Test MOP QA — trigger Step 7 failure (invalid workflow YAML), verify failure_type and `_retry_target=6` — *no recorded verification*
- [ ] T030 [US3] Test Exec QA classification — "PatternSyntaxException" → workflow/6; "Connection refused" → simulator/8 — *blocked: Step 9 has never executed a command (004 DEP-2)*
- [ ] T031 [US3] Verify QA feedback flows to upstream step — feedback reaches `regenerate(feedback)` on retry — *partially verified for CIQ gen; Step 8 regenerate broken (004 S8-08)*
- [X] T051 [US3] Fail the run gracefully when MAX_RETRIES is exhausted instead of continuing (bugs.md OB-4)
- [ ] T052 [US3] Confirm/fix: Continue on a failed QA milestone should not auto-retry (bugs.md OB-5)

**Checkpoint**: All QA skills correctly analyze failures and route retries

---

## Phase 6: User Story 4 — Dashboard and Run History (Priority: P2)

**Goal**: NiceGUI web UI with dashboard stats, upload, progress tracking, history

**Independent Test**: Open http://localhost:8000, see dashboard with stats, upload CIQ, watch pipeline progress

### Implementation for User Story 4

- [X] T032 [P] [US4] Create NiceGUI app entry point with sidebar (140px), content area, dark theme CSS, navigation — `ui/app.py`
- [X] T033 [P] [US4] Create upload section (CIQ required; MOP, constraints, sample I/O, developer notes optional; vendor → node type → activity selectors) — `ui/pages/upload_section.py`
- [X] T034 [US4] Create progress section with 10-step single-row tracker, status banner, milestone review panel, EventBus subscription — `ui/pages/progress_section.py`
- [X] T035 [P] [US4] Create history section with paginated runs table and re-run — `ui/pages/history_section.py` (37f3bb7, b50eb1a)
- [X] T036 [P] [US4] Create output viewer section with artifact browser and cache management — `ui/pages/output_viewer_section.py` (d5cd4a0)
- [X] T037 [P] [US4] Create cleanup section with paginated listing, disk sizes, bulk cleanup — `ui/pages/cleanup_section.py` (7d9f4bf)
- [X] T038 [US4] Create dashboard panel with stat cards, status pie, UC bar chart, last 5 runs (8a97fb0, 37f3bb7)
- [X] T039 [US4] Wire UI to PipelineRunner — upload → runner.start(); progress subscribes to EventBus; milestone buttons → respond_milestone(); history re-run → new run with copied document set (1d71d9b)
- [X] T040 [US4] Create start.ps1 and start.sh launcher scripts — run `python ui/app.py` (f572c61)

**Checkpoint**: Full web UI operational — dashboard, upload, progress tracking, history, cleanup

---

## Phase 7: User Story 5 — Settings Configuration (Priority: P2)

**Goal**: Settings UI for API key, models, retry limits, JAR paths, templates

**Independent Test**: Change default model in Settings, save, verify new model used in next pipeline run

### Implementation for User Story 5

- [X] T041 [US5] Create settings section with tabs (API & Models / Per-Step Models / Retry Policy / External Modules) plus Vendor Profiles tab — `ui/pages/settings_section.py`, `ui/pages/vendor_section.py`
- [X] T042 [US5] Wire settings to Database — persisted in `settings` table; LLM reads live config (9af3510) and DB fallback for CLI (cff5d3c)
- [ ] T043 [US5] Verify settings persistence — save API key, restart server, verify key is loaded; change retry limit, verify new limit is used — *retry limit is hard-coded `MAX_RETRIES=3` in pipeline_agent.py; setting not wired*

**Checkpoint**: Settings save/load/apply correctly across server restarts

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Final integration, testing, documentation

- [ ] T044 Verify content area uses full viewport width/height with proper scrolling on 1920x1080 — *no recorded verification*
- [X] T045 Verify completed step visuals are preserved when continuing from mid-pipeline milestone (52961a6, 558626a)
- [ ] T046 [P] Create tests/test_pipeline_agent.py — sequential, Step5→3, Step9→6, Step9→8, cancellation, retry exhaustion — *`tests/` contains only `__init__.py`*
- [ ] T047 [P] Create tests/test_runner.py — lifecycle, DB persistence, resume — *not created*
- [X] T048 Update README.md with final architecture (f572c61) — *README describes 7 agents; now 9 — refresh needed*
- [X] T049 Update CLAUDE.md with final file paths and architecture decisions (f572c61) — *CLAUDE.md says 7 agents; now 9 — refresh needed*
- [ ] T050 Run quickstart.md validation scenarios V1-V5 end-to-end

---

## Phase 9: Post-MVP Bug Fixes

**Purpose**: Track every bug fixed after the spec baseline (a51b094). Details (symptom, root cause, fix, verification) are in [bugs.md](bugs.md). BF-NNN ↔ BUG-NNN.

### Orchestration / State
- [X] BF-001 Shared dicts replace session state for control flow — BUG-001 (bc567e9)
- [X] BF-002 Reviewer skip check from disk error file — BUG-002 (bf6b061)
- [X] BF-003 Eliminate all session-state control-flow reads — BUG-003 (5455064)
- [X] BF-004 Fix `_Path` import scope — BUG-004 (2c467ed)
- [X] BF-005 Don't report COMPLETED on failure — BUG-005 (dea588a)
- [X] BF-006 Add MAX_RETRIES to stop infinite retry loop — BUG-006 (0f52f58)
- [X] BF-007 Detect run completion from report on disk — BUG-007 (0f52f58)
- [X] BF-008 Split QA agent into MOP QA (7) and Exec QA (9) — BUG-008 (558626a)
- [X] BF-009 Milestone status checks all of the agent's display steps — BUG-009 (96359f3)

### Steps 1–2: Requirements
- [X] BF-010 Questionnaire questions in event payload — BUG-010 (6995d26)
- [X] BF-011 Visible questionnaire inputs + defaults — BUG-011 (1188fdb, 3e5cdda)
- [X] BF-012 Questionnaire dialog theme — BUG-012 (21e5c43)
- [X] BF-013 Dialog open / Continue at step 2 / milestone restore — BUG-013 (04deb45)
- [X] BF-014 Enforce explicit answers over LLM output — BUG-014 (4031d45)
- [X] BF-015 Reduce unnecessary questions — BUG-015 (48951b0)
- [X] BF-016 CIQ→XML tag mapping — BUG-016 (07c9691)
- [X] BF-017 CIQ structure in summary HTML — BUG-017 (44e7543)
- [X] BF-018 Re-run from history with new document set — BUG-018 (1d71d9b)
- [X] BF-019 Summary HTML missing data — BUG-019 (29de59f)
- [X] BF-020 Skip questionnaire only when COMPLETE — BUG-020 (ccea245)
- [X] BF-021 Skip questionnaire when sufficient previous answers — BUG-021 (f046a72)

### Steps 3–5: CIQ Validation & JSON Output
- [X] BF-022 Guard `xml_templates` None — BUG-022 (0f52f58)
- [X] BF-023 YAML sanitizer (colons, phases shape) — BUG-023 (f046a72)
- [X] BF-024 Negative tests as WARN — BUG-024 (f046a72)
- [X] BF-025 Step 5 artifact path — BUG-025 (f046a72)
- [X] BF-026 ciq-processor correct CLI flags — BUG-026 (6771413)
- [X] BF-027 Sanitize validation-rules for JAR — BUG-027 (5826e71)
- [X] BF-028 Strip sheet row rules — BUG-028 (00b5d00)
- [X] BF-029 Whitelist sanitization — BUG-029 (f889146)
- [X] BF-030 Separate validation report and JSON data dirs — BUG-030 (c7be2c5)
- [X] BF-031 Strip `allowedValues` from non-enum columns — BUG-031 (b906d36)
- [X] BF-032 Strip broken `workbook_rules` — BUG-032 (f6094a6)
- [X] BF-033 Rewrite validation-rules / json-output prompts — BUG-033 (664c3b4)
- [X] BF-034 Production nodeGroups structure — BUG-034 (af70573)
- [X] BF-035 crGroups hierarchy — BUG-035 (7b72115) *(superseded by 91e5085)*
- [X] BF-036 nodeGroups at root + schemaVersion/meta — BUG-036 (91e5085)
- [X] BF-037 Force CRGroup segregation, unwrap crGroups — BUG-037 (d2d499f) *(schemaVersion/meta part superseded)*
- [X] BF-038 Deterministic json-output build — BUG-038 (d448e21)
- [X] BF-039 `schemaVersion: "2"` in data — BUG-039 (10bb0f1)
- [X] BF-040 `meta` + one node per zone — BUG-040 (5872ec4)
- [X] BF-041 Per-sheet / per-cell step 5 test cases — BUG-041 (44bf869)

### Steps 6–7: Workflow & MOP
- [X] BF-042 Workflow prompt conventions — BUG-042 (19437b9)
- [X] BF-043 MOP JAR subcommands/flags — BUG-043 (1c7e53a)
- [X] BF-044 `--yaml` full path — BUG-044 (d9d34d0)
- [X] BF-045 Step 7 artifact existence check — BUG-045 (3e34af6)
- [X] BF-046 Step 7 restore filename — BUG-046 (e05a6c8)
- [X] BF-047 Auto-add missing LOCAL node — BUG-047 (60f60e2)
- [X] BF-048 Step-field whitelist (`when`) — BUG-048 (a497018)

### Step 8: Simulator
- [X] BF-049 Step 8 milestone artifact — BUG-049 (9fd3550)
- [X] BF-050 MockWire CLI `--name/--port` — BUG-050 (0f52f58)
- [X] BF-051 Truncated YAML repair; capture stdout — BUG-051 (1c7e53a)
- [X] BF-052 Multi-doc YAML / duplicate keys — BUG-052 (a497018) *(side effect → 004 S8-02)*

### Step 9: Execution QA
- [X] BF-053 Step 9 artifact path — BUG-053 (f8391da)
- [X] BF-054 Dynamic step 9 re-run target — BUG-054 (ba4c30a)

### LLM / Config
- [X] BF-055 Single module instance for usage stats — BUG-055 (cf14c7b)
- [X] BF-056 LLM retry on 503/429/timeout — BUG-056 (f046a72)
- [X] BF-057 Live LLM config — BUG-057 (9af3510)
- [X] BF-058 DB fallback for API key in CLI — BUG-058 (cff5d3c)
- [X] BF-059 Model list update — BUG-059 (2b962fe)

### Storage / DB
- [X] BF-060 `uc_document_sets` NOT NULL coercion — BUG-060 (5f236ee)

### UI
- [X] BF-061 Block Continue after generation failure — BUG-061 (82ccddd)
- [X] BF-062 Reset steps on re-run — BUG-062 (52961a6)
- [X] BF-063 History pagination — BUG-063 (b50eb1a)
- [X] BF-064 Usage page theme — BUG-064 (9c73b4c)
- [X] BF-065 Step tracker wrapping + skipped status — BUG-065 (558626a)

### Logging / Tooling
- [X] BF-066 Log filters after `src/` dissolve — BUG-066 (1a15994)
- [X] BF-067 test_step.py API key from DB — BUG-067 (1541161)
- [X] BF-068 test_step.py async state build — BUG-068 (e8b057d)
- [X] BF-069 cr_import cache clear — BUG-069 (f1b4d7d) *(obsolete)*
- [X] BF-070 Cache check logging — BUG-070 (36eb46a)

### Open
- [ ] BF-071 Fix `clear_from_step()` wiping the whole UC cache on step 5/7/8/9 re-run — OB-1 (tracked in [002-artifact-cache](../002-artifact-cache/tasks.md))
- [X] BF-072 Fail run on MAX_RETRIES exhaustion — OB-4 (= T051)
- [ ] BF-073 Continue on failed QA milestone must not auto-retry — OB-5 (= T052)
- [X] BF-074 Implement resume after restart — OB-6 (= T021) — done by spec 007 (`resume_queue`)
- [X] BF-076 Cache helper scripts by content — OB-7 (002 T007 / 003 T009) — done with OB-14
- [ ] BF-077 helper_script_gen must report failure — OB-8 (003 T007)
- [ ] BF-075 Step 8 issues S8-01..S8-12, DEP-1..DEP-5 — tracked in [004-simulator-setup](../004-simulator-setup/spec.md)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately
- **Phase 2 (Foundational)**: Depends on Phase 1 — BLOCKS all user stories
- **Phase 3 (US1 - E2E Pipeline)**: Depends on Phase 2
- **Phase 4 (US2 - Milestones)**: Depends on Phase 2 (can run parallel with US1)
- **Phase 5 (US3 - Recovery)**: Depends on Phase 2 (can run parallel with US1/US2)
- **Phase 6 (US4 - UI)**: Depends on Phase 2 + Phase 3 (needs working pipeline to connect to)
- **Phase 7 (US5 - Settings)**: Depends on Phase 6 (needs UI app.py)
- **Phase 8 (Polish)**: Depends on all previous phases
- **Phase 9 (Bug Fixes)**: Ongoing; open items depend on 002 / 004

### User Story Dependencies

- **US1 (E2E Pipeline)**: Phase 2 only — core pipeline execution
- **US2 (Milestones)**: Phase 2 only — milestones built into PipelineAgent
- **US3 (Recovery)**: Phase 2 only — QA skills set `_retry_target`
- **US4 (UI)**: Phase 2 + US1 working pipeline
- **US5 (Settings)**: Phase 6 (US4) — needs UI framework

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story
- All agents are thin BaseAgent wrappers over skill packages (research R2, R7)
- No LlmAgent remains; reviewers are part of QA skills (research R7)
- Milestones use asyncio.Event via `shared.state.milestone_events` (research R3)
- Never use ADK session state for control flow (research R6)
- UI adapted from cr_automation-agentic (research R4)
- DB schema copied from cr_automation-agentic (research R5)
