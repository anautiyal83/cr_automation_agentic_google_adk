# Bug Register: Google ADK Orchestrator Pipeline (NetConfig)

**Feature**: [spec.md](spec.md) | **Tasks**: [tasks.md](tasks.md) Phase 9 | **Created**: 2026-11-02

**Scope**: Every bug fixed on `feature/skill-packages` after the spec baseline (a51b094, 2026-10-18), reconstructed from commit messages, plus currently open bugs. Commits that fixed several distinct bugs are split into one entry per bug.

**Severity**: Critical = wrong result, silent pass, or infinite loop · High = step fails or crashes · Medium = wrong display or artifact · Low = cosmetic or tooling.

**Status**: Fixed · Superseded (the fix was later replaced) · Open · Unverified · Not reproducing.

## Summary

| ID | Area | Title | Severity | Status | Commit | Spec ref |
|----|------|-------|----------|--------|--------|----------|
| BUG-001 | Orchestration | Sub-agent session-state writes invisible to parent | Critical | Fixed | bc567e9 | FR-018 |
| BUG-002 | Orchestration | Reviewers skipped even when step failed | Critical | Fixed | bf6b061 | FR-004, US3 |
| BUG-003 | Orchestration | Remaining session-state control-flow reads unreliable | Critical | Fixed | 5455064 | FR-018 |
| BUG-004 | Orchestration | `_Path` used before import in method | High | Fixed | 2c467ed | — |
| BUG-005 | Orchestration | RUN COMPLETED reported on pipeline failure | Critical | Fixed | dea588a | FR-023 |
| BUG-006 | Orchestration | Infinite CIQ gen ↔ QA retry loop | Critical | Fixed | 0f52f58 | FR-007 |
| BUG-007 | Orchestration | Run completion read from unreliable `step_10_status` | Critical | Fixed | 0f52f58 | FR-023 |
| BUG-008 | Orchestration | Step 8 executed before step 7 (QA agent ordering) | High | Fixed | 558626a | FR-001 |
| BUG-009 | Orchestration | Milestone status ignored agent's other display steps | Critical | Fixed | 96359f3 | FR-020 |
| BUG-010 | Requirements | Questionnaire milestone showed no questions | High | Fixed | 6995d26 | FR-024, US2 |
| BUG-011 | Requirements | Questionnaire input text invisible on dark theme | Medium | Fixed | 1188fdb, 3e5cdda | FR-012 |
| BUG-012 | Requirements | Questionnaire dialog theme mismatch | Low | Fixed | 21e5c43 | FR-012 |
| BUG-013 | Requirements | Dialog not opening; Continue missing at step 2; milestone lost on refresh | High | Fixed | 04deb45 | FR-006, US2 |
| BUG-014 | Requirements | LLM output contradicted explicit developer answers | Medium | Fixed | 4031d45 | FR-024 |
| BUG-015 | Requirements | 36+ questions, asked for data already in documents | Medium | Fixed | 48951b0 | FR-024 |
| BUG-016 | Requirements | Blank XML tags in activity phase | Medium | Fixed | 07c9691 | FR-024 |
| BUG-017 | Requirements | Empty CIQ structure in requirements summary HTML | Medium | Fixed | 44e7543 | FR-024 |
| BUG-018 | Requirements | Re-run from history failed / bound to old document set | High | Fixed | 1d71d9b | US4 |
| BUG-019 | Requirements | Summary HTML showed "?" for known fields | Medium | Fixed | 29de59f | FR-024 |
| BUG-020 | Requirements | Questionnaire skipped on PARTIAL readiness | Medium | Fixed | ccea245 | FR-024 |
| BUG-021 | Requirements | Questionnaire re-asked on re-run despite sufficient answers | Low | Fixed | f046a72 | FR-024 |
| BUG-022 | CIQ (3-5) | NoneType crash when `xml_templates` is None | High | Fixed | 0f52f58 | FR-025 |
| BUG-023 | CIQ (3-5) | LLM YAML with unquoted colons / phases as dict | High | Fixed | f046a72 | FR-026, FR-028 |
| BUG-024 | CIQ (3-5) | Negative-test failures counted as blocking | Medium | Fixed | f046a72 | US3 |
| BUG-025 | CIQ (3-5) | Step 5 artifact path did not match filename | Medium | Fixed | f046a72 | FR-014 |
| BUG-026 | CIQ (3-5) | ciq-processor called with wrong flags → step 5 silent pass | Critical | Fixed | 6771413 | FR-027, SC-008 |
| BUG-027 | CIQ (3-5) | JAR SnakeYAML rejects LLM-only fields | High | Fixed | 5826e71 | FR-026 |
| BUG-028 | CIQ (3-5) | Sheet-level row rules break JAR deserialization | High | Fixed | 00b5d00 | FR-026 |
| BUG-029 | CIQ (3-5) | Field-by-field stripping missed nested props | High | Fixed | f889146 | FR-026 |
| BUG-030 | CIQ (3-5) | Validation report picked up as MOP JSON data | High | Fixed | c7be2c5 | FR-027 |
| BUG-031 | CIQ (3-5) | Hallucinated `allowedValues` → false CIQ failures | High | Fixed | b906d36 | FR-026 |
| BUG-032 | CIQ (3-5) | Broken `workbook_rules` → false "Invalid YAML" | High | Fixed | f6094a6 | FR-026 |
| BUG-033 | CIQ (3-5) | Prompts requested sections the LLM can't produce | High | Fixed | 664c3b4 | FR-026 |
| BUG-034 | CIQ (3-5) | json-output flat structure → "No nodes found" | High | Fixed | af70573 | FR-025 |
| BUG-035 | CIQ (3-5) | nodeGroups not nested under crGroups | High | Superseded | 7b72115 → 91e5085 | FR-025 |
| BUG-036 | CIQ (3-5) | nodeGroups must be at root; schemaVersion/meta missing | High | Fixed | 91e5085 | FR-025 |
| BUG-037 | CIQ (3-5) | schemaVersion/meta in template; ZONE segregation | High | Superseded | d2d499f → 10bb0f1, 5872ec4 | FR-025 |
| BUG-038 | CIQ (3-5) | LLM cannot reliably produce json-output | High | Fixed | d448e21 | FR-025 |
| BUG-039 | CIQ (3-5) | Missing `schemaVersion: "2"` → JAR v1 parser | High | Fixed | 10bb0f1 | FR-025 |
| BUG-040 | CIQ (3-5) | Null MOP header values; duplicate nodes per zone | Medium | Fixed | 5872ec4 | FR-025 |
| BUG-041 | CIQ (3-5) | Step 5 QA report showed only 1 test case | Medium | Fixed | 44bf869 | US3 AC1 |
| BUG-042 | Workflow/MOP (6-7) | Workflow prompt: wrong phase names, su, on_failure | Medium | Fixed | 19437b9 | FR-028 |
| BUG-043 | Workflow/MOP (6-7) | MOP JAR invoked with invalid flags | High | Fixed | 1c7e53a | FR-027 |
| BUG-044 | Workflow/MOP (6-7) | `--yaml` given stem name, not file path | High | Fixed | d9d34d0 | FR-027 |
| BUG-045 | Workflow/MOP (6-7) | Step 7 artifact returned even if missing | Medium | Fixed | 3e34af6 | FR-014 |
| BUG-046 | Workflow/MOP (6-7) | Step 7 wrong filename in runner restore path | Medium | Fixed | e05a6c8 | FR-014 |
| BUG-047 | Workflow/MOP (6-7) | `LOCAL` node referenced but undefined | High | Fixed | 60f60e2 | FR-028 |
| BUG-048 | Workflow/MOP (6-7) | Step `when` field breaks JAR deserialization | High | Fixed | a497018 | FR-028 |
| BUG-049 | Simulator (8) | Step 8 milestone artifact pointed to overview.html | Medium | Fixed | 9fd3550 | FR-014 |
| BUG-050 | Simulator (8) | MockWire launched with wrong CLI (`--config-dir`) | High | Fixed | 0f52f58 | FR-031 |
| BUG-051 | Simulator (8) | Truncated simulator YAML; MockWire logs lost | High | Fixed | 1c7e53a | FR-031 |
| BUG-052 | Simulator (8) | Multi-document YAML and duplicate keys fail parse | High | Fixed (side effect: S8-02) | a497018 | FR-031 |
| BUG-053 | Exec QA (9) | Step 9 artifact path missing `exec_report/` | Medium | Fixed | f8391da | FR-014 |
| BUG-054 | Exec QA (9) | Step 9 re-run always targeted step 6 | Medium | Fixed | ba4c30a | FR-005 |
| BUG-055 | LLM/Config | LLM usage stats empty (duplicate module) | Medium | Fixed | cf14c7b | FR-010 |
| BUG-056 | LLM/Config | No retry on LLM 503/429/timeout | High | Fixed | f046a72 | FR-008 |
| BUG-057 | LLM/Config | Running pipeline used stale model/API key | Medium | Fixed | 9af3510 | FR-032, SC-006 |
| BUG-058 | LLM/Config | CLI runs had no API key | High | Fixed | cff5d3c | FR-032 |
| BUG-059 | LLM/Config | Model list offered unavailable free tiers | Low | Fixed | 2b962fe | FR-015 |
| BUG-060 | Storage/DB | `uc_document_sets` rows silently not saved | High | Fixed | 5f236ee | FR-009 |
| BUG-061 | UI | Continue allowed after generation failure | High | Fixed | 82ccddd | FR-021 |
| BUG-062 | UI | Step statuses not reset on re-run | Medium | Fixed | 52961a6 | FR-022 |
| BUG-063 | UI | History pagination conflicted with Quasar built-in | Low | Fixed | b50eb1a | US4 |
| BUG-064 | UI | Usage page cards ignored dark theme | Low | Fixed | 9c73b4c | FR-012 |
| BUG-065 | UI | Step tracker text truncated; pending steps not marked skipped | Low | Fixed | 558626a | FR-013 |
| BUG-066 | Logging/Tooling | flow.log and trace.log empty after `src/` dissolve | High | Fixed | 1a15994 | — |
| BUG-067 | Logging/Tooling | test_step.py could not load API key | Medium | Fixed | 1541161 | — |
| BUG-068 | Logging/Tooling | test_step.py async state build silently failed | Medium | Fixed | e8b057d | — |
| BUG-069 | Logging/Tooling | Stale cr_import module cache in test_step.py | Low | Fixed (obsolete) | f1b4d7d | — |
| BUG-070 | Logging/Tooling | Cache check failures invisible (`logger.debug`) | Medium | Fixed (partially, see OB-1) | 36eb46a | FR-030 |

**Totals**: 70 fixed/superseded entries from 61 commits · Critical 8 · High 32 · Medium 23 · Low 7 · Fixed 68 · Superseded 2. Open: 7 pipeline-level (OB-1–OB-7; OB-8 fixed 2026-11-02) + step 8/9 rows in the table below (S8-*, S9-*, DEP-*, G-*).

---

## Orchestration / State

### BUG-001 — Sub-agent session-state writes invisible to parent
- **Symptom**: Pipeline state machine misrouted; UI did not track progress.
- **Root cause**: `ctx.session.state` writes in sub-agents are not visible to the parent PipelineAgent.
- **Fix**: Module-level shared dicts (`pipeline_actions`, `reviewer_feedback`); unified EventBus singleton; step_started/step_completed events published from the PipelineAgent loop.
- **Commit**: bc567e9 · **Related**: FR-018, research R6 · **Verification**: not recorded

### BUG-002 — Reviewers skipped even when step failed
- **Symptom**: QA reviewers never ran after failures.
- **Root cause**: Redundant skip check read step status from unreliable session state.
- **Fix**: Check `step_{N}_error.json` on disk instead.
- **Commit**: bf6b061 · **Related**: FR-004, US3 · **Verification**: not recorded

### BUG-003 — Remaining session-state control-flow reads unreliable
- **Symptom**: Status checks in PipelineAgent, milestones, and routers were inconsistent.
- **Root cause**: Same as BUG-001.
- **Fix**: All control-flow reads replaced with disk error-file checks; old error files cleared before re-run; ~200 lines of dead duplicate code removed.
- **Commit**: 5455064 · **Related**: FR-018 · **Verification**: not recorded

### BUG-004 — `_Path` used before import in method
- **Symptom**: Runtime NameError in a pipeline method.
- **Root cause**: Local import placed after first use.
- **Fix**: Import moved to the top of the method.
- **Commit**: 2c467ed · **Related**: — · **Verification**: not recorded

### BUG-005 — RUN COMPLETED reported on pipeline failure
- **Symptom**: Failed runs shown as completed.
- **Root cause**: Runner could not read `pipeline_failed` from session state.
- **Fix**: Runner also checks whether step 10 was reached.
- **Commit**: dea588a · **Related**: FR-023 · **Verification**: not recorded

### BUG-006 — Infinite CIQ gen ↔ QA retry loop
- **Symptom**: E2E run ef4fec2f looped between CIQ generation and QA forever.
- **Root cause**: No retry limit in the auto-retry block.
- **Fix**: `MAX_RETRIES = 3` per retry target. Exhaustion now *continues* the pipeline; see OB-4.
- **Commit**: 0f52f58 · **Related**: FR-007 · **Verification**: E2E completed in 205s; retry loops respect MAX_RETRIES

### BUG-007 — Run completion read from unreliable `step_10_status`
- **Symptom**: Wrong final run status after the 7-agent refactor.
- **Root cause**: Session state is unreliable (R6).
- **Fix**: Completion is detected from the report HTML on disk.
- **Commit**: 0f52f58 · **Related**: FR-023 · **Verification**: E2E run (0f52f58)

### BUG-008 — Step 8 executed before step 7
- **Symptom**: Step 7 showed "pending" while step 8 showed "failed".
- **Root cause**: One `network_config_qa` agent covered both MOP QA (7) and Exec QA (9), and ran after simulator setup.
- **Fix**: Split into `network_config_mop_qa` (before simulator) and `network_config_exec_qa` (after simulator); pipeline became 8 agents.
- **Commit**: 558626a · **Related**: FR-001 · **Verification**: not recorded

### BUG-009 — Milestone status ignored agent's other display steps
- **Symptom**: json-output.yaml had invalid YAML, but the step 4 milestone showed passed and allowed Continue.
- **Root cause**: The step 4 milestone only checked `step_4_error.json`; the gen agent writes `step_3_error.json`.
- **Fix**: Check error files and state for all display steps of the agent.
- **Commit**: 96359f3 · **Related**: FR-020 · **Verification**: not recorded

## Step 1–2: Requirements

### BUG-010 — Questionnaire milestone showed no questions
- **Symptom**: Empty questionnaire; step 0 looked like it was still running.
- **Root cause**: Questions were only in `milestone_waiting`, not in the event payload.
- **Fix**: Questions included in the `milestone_reached` payload; proper paused banner; dialog auto-opens.
- **Commit**: 6995d26 · **Related**: FR-024, US2 · **Verification**: not recorded

### BUG-011 — Questionnaire input text invisible
- **Symptom**: Typed text and defaults were not visible on the dark theme.
- **Root cause**: Quasar input colours on the dark background.
- **Fix**: Explicit colours and a styled wrapper; defaults pre-filled.
- **Commit**: 1188fdb, 3e5cdda · **Related**: FR-012 · **Verification**: not recorded

### BUG-012 — Questionnaire dialog theme mismatch
- **Symptom**: Dialog did not match the app theme; buttons misaligned.
- **Root cause**: Hard-coded styles.
- **Fix**: Theme CSS vars; smart input types; button alignment.
- **Commit**: 21e5c43 · **Related**: FR-012 · **Verification**: not recorded

### BUG-013 — Dialog not opening; Continue missing; milestone lost on refresh
- **Symptom**: Questionnaire dialog never opened; no Continue at step 2; page refresh lost the milestone.
- **Root cause**: `ui.dialog()` created from an async handler; milestone UI state not reset; restore used `current_step`.
- **Fix**: Pre-built dialog with clear+repopulate; reset button state; restore steps 0/2 via `milestone_step`.
- **Commit**: 04deb45 · **Related**: FR-006, US2 · **Verification**: not recorded

### BUG-014 — LLM output contradicted explicit developer answers
- **Symptom**: Compiled requirements kept items the user had rejected (e.g. backup phase).
- **Root cause**: No post-compile enforcement.
- **Fix**: Answer enforcement overrides LLM output; raw artifacts passed downstream.
- **Commit**: 4031d45 · **Related**: FR-024 · **Verification**: not recorded

### BUG-015 — Excess questions
- **Symptom**: 36+ questions, many about data already in the uploaded documents.
- **Root cause**: LLM was not told what data was already available.
- **Fix**: `{available_data}` section in the prompt; cap of 10–12 questions per round; fuzzy matching of previous answers; stable question IDs.
- **Commit**: 48951b0 · **Related**: FR-024 · **Verification**: not recorded

### BUG-016 — Blank XML tags in activity phase
- **Symptom**: Generated SPML XML had empty tags.
- **Root cause**: No mapping from CIQ columns to XML tags in the compile prompt.
- **Fix**: `xml_templates.ciq_to_xml_mapping`; raw XML samples in the prompt.
- **Commit**: 07c9691 · **Related**: FR-024 · **Verification**: not recorded

### BUG-017 — Empty CIQ structure in requirements summary HTML
- **Symptom**: Summary showed no CIQ sheets.
- **Root cause**: `ciq_data` arrived as a JSON string; per-sheet data was missing.
- **Fix**: Parse the string; fallbacks to top-level fields, then to openpyxl.
- **Commit**: 44e7543 · **Related**: FR-024 · **Verification**: not recorded

### BUG-018 — Re-run from history failed
- **Symptom**: Re-run could not find the documents, or broke after cleanup.
- **Root cause**: Re-run reused the old `document_set_id`; JSON fields were not decoded.
- **Fix**: New document set copy (uuid4); multiple lookup fallbacks; JSON decode.
- **Commit**: 1d71d9b · **Related**: US4 AC2 · **Verification**: not recorded

### BUG-019 — Summary HTML showed "?" for known fields
- **Symptom**: Fields present in requirements showed "?".
- **Root cause**: Read from analysis instead of compiled requirements.
- **Fix**: Compiled requirements read first; `_v()` safe access with "—"; always-on gaps section.
- **Commit**: 29de59f · **Related**: FR-024 · **Verification**: not recorded

### BUG-020 — Questionnaire skipped on PARTIAL readiness
- **Symptom**: Gaps remained but no questions were asked.
- **Root cause**: Skip condition accepted PARTIAL.
- **Fix**: Skip only when readiness is COMPLETE.
- **Commit**: ccea245 · **Related**: FR-024 · **Verification**: not recorded

### BUG-021 — Questionnaire re-asked despite sufficient answers
- **Symptom**: Re-runs prompted again with ≥10 answers already on record.
- **Root cause**: Readiness heuristic did not count stored answers.
- **Fix**: Skip when ≥10 answers plus compiled requirements exist; cap of 50 answers.
- **Commit**: f046a72 · **Related**: FR-024 · **Verification**: not recorded

## Steps 3–5: CIQ Validation & JSON Output

### BUG-022 — NoneType crash when `xml_templates` is None
- **Symptom**: CIQ generation crashed.
- **Root cause**: `json_output_gen` assumed `xml_templates` was a dict.
- **Fix**: Guard against None.
- **Commit**: 0f52f58 · **Related**: FR-025 · **Verification**: E2E run (0f52f58)

### BUG-023 — LLM YAML with unquoted colons / phases as dict
- **Symptom**: YAML parse failures in generated configs.
- **Root cause**: LLM emitted values containing `:` and dict-shaped `phases`.
- **Fix**: Sanitizer quotes values; handles list items; parse→dump round trip; dict→list conversion for phases.
- **Commit**: f046a72 · **Related**: FR-026, FR-028 · **Verification**: not recorded

### BUG-024 — Negative-test failures counted as blocking
- **Symptom**: Step 5 failed on expected-negative test cases.
- **Root cause**: Verdict counted all failures.
- **Fix**: Negative failures shown as WARN; only positive failures block.
- **Commit**: f046a72 · **Related**: US3 · **Verification**: not recorded

### BUG-025 — Step 5 artifact path did not match filename
- **Symptom**: No artifact at the step 5 milestone.
- **Root cause**: Filename mismatch.
- **Fix**: Path aligned with the actual filename.
- **Commit**: f046a72 · **Related**: FR-014 · **Verification**: not recorded

### BUG-026 — ciq-processor called with wrong flags (silent pass)
- **Symptom**: Step 5 passed although validation never ran.
- **Root cause**: Used `--validation-rules`/`--output-dir`/`--report-format`; missing `--node-type`/`--activity`; JAR failure was not detected.
- **Fix**: Correct flags; sanitize rules first; real failures block progression.
- **Commit**: 6771413 · **Related**: FR-027, SC-008 · **Verification**: not recorded

### BUG-027 — JAR SnakeYAML rejects LLM-only fields
- **Symptom**: ciq-processor deserialization errors.
- **Root cause**: `crossRef.ignoreCase`, rule `message`, malformed `workbook_rules`, list-shaped `outputs`.
- **Fix**: Sanitized copy for the JAR (top-level `sheets`/`settings`; crossRef sheet/column only).
- **Commit**: 5826e71 · **Related**: FR-026 · **Verification**: not recorded

### BUG-028 — Sheet-level row rules break deserialization
- **Symptom**: Fatal JAR errors on require/forbid/when rules.
- **Root cause**: Structure incompatible with JavaBean mapping.
- **Fix**: Sheet rules stripped from the sanitized copy.
- **Commit**: 00b5d00 · **Related**: FR-026 · **Verification**: not recorded

### BUG-029 — Field-by-field stripping missed nested props
- **Symptom**: New LLM props (`allowedValuesWhen`, `conditionalPattern`…) kept breaking the JAR.
- **Root cause**: Blacklist approach.
- **Fix**: Whitelist of column, sheet, and crossRef keys.
- **Commit**: f889146 · **Related**: FR-026, research R9 · **Verification**: not recorded

### BUG-030 — Validation report picked up as MOP JSON data
- **Symptom**: Step 7 failed with "No nodes found".
- **Root cause**: `--output` and `--json-output-dir` pointed to the same directory.
- **Fix**: Separate `ciq_validation/` and `json_data/` dirs; VALIDATION_REPORT files filtered out; actionable error message.
- **Commit**: c7be2c5 · **Related**: FR-027 · **Verification**: Step 5 fails correctly; step 7 shows a clear message

### BUG-031 — Hallucinated `allowedValues` → false CIQ failures
- **Symptom**: Valid CIQ rejected (e.g. 202 APN values; IFC restricted to 0–15).
- **Root cause**: LLM put `allowedValues` on string columns.
- **Fix**: Strip `allowedValues` from non-enum columns.
- **Commit**: b906d36 · **Related**: FR-026 · **Verification**: E2E: step 5 0 errors; step 7 produced 46 KB MOP HTML

### BUG-032 — Broken `workbook_rules` → false "Invalid YAML"
- **Symptom**: validation-rules.yaml failed to parse.
- **Root cause**: Nested lists under mapping keys in `workbook_rules`.
- **Fix**: Autofix keeps only `sheets`/`settings`; raw-text fallback strip.
- **Commit**: f6094a6 · **Related**: FR-026 · **Verification**: previously broken file now parses

### BUG-033 — Prompts requested sections the LLM can't produce
- **Symptom**: Recurrent broken YAML (root cause of BUG-027–032).
- **Root cause**: Prompt asked for `workbook_rules`/`outputs`/`report` with no examples.
- **Fix**: Ask for `sheets:` only; enum vs string guidance; concrete examples; `_each` syntax examples.
- **Commit**: 664c3b4 · **Related**: FR-026 · **Verification**: not recorded

### BUG-034 — json-output flat structure → "No nodes found"
- **Symptom**: MOP JAR found no nodes.
- **Root cause**: Flat `nodes[]` / `subscribers[]` instead of the production `nodeGroups[]` structure.
- **Fix**: Prompt with the exact production structure.
- **Commit**: af70573 · **Related**: FR-025 · **Verification**: not recorded

### BUG-035 — nodeGroups not nested under crGroups *(Superseded)*
- **Symptom**: Thought to mismatch production.
- **Root cause**: Hierarchy assumption.
- **Fix**: `crGroups[] → nodeGroups[]`. Reverted by 91e5085: the JAR expects nodeGroups at root.
- **Commit**: 7b72115 · **Related**: FR-025 · **Verification**: not recorded

### BUG-036 — nodeGroups must be at root; schemaVersion/meta missing
- **Symptom**: MOP JAR lookup failed.
- **Root cause**: Wrong nesting; required fields missing.
- **Fix**: nodeGroups at root; `schemaVersion`/`meta` derived from the UC name.
- **Commit**: 91e5085 · **Related**: FR-025 · **Verification**: not recorded

### BUG-037 — schemaVersion/meta in template; ZONE segregation *(Superseded)*
- **Symptom**: Believed the JAR injects schemaVersion/meta at runtime.
- **Root cause**: Assumption later disproved.
- **Fix**: Stripped schemaVersion/meta; unwrapped crGroups; forced `segregate_by: Index.CRGroup`. The CRGroup and unwrap parts stand; schemaVersion/meta were re-added by 10bb0f1 and 5872ec4.
- **Commit**: d2d499f · **Related**: FR-025 · **Verification**: not recorded

### BUG-038 — LLM cannot reliably produce json-output
- **Symptom**: Repeated structure errors (BUG-034–037).
- **Root cause**: Structure is standard across UCs; LLM generation adds variance only.
- **Fix**: Deterministic build from CIQ sheets (`autofix.fix_json_output`).
- **Commit**: d448e21 · **Related**: FR-025, research R8 · **Verification**: not recorded

### BUG-039 — Missing `schemaVersion: "2"` → JAR v1 parser
- **Symptom**: "No nodes found in data section".
- **Root cause**: Without schemaVersion the JAR falls back to the v1 schema.
- **Fix**: `schemaVersion: "2"` added in data.
- **Commit**: 10bb0f1 · **Related**: FR-025 · **Verification**: E2E ciq-processor → JSON (2 nodeGroups) → MOP JAR → 58 KB HTML

### BUG-040 — Null MOP header values; duplicate nodes per zone
- **Symptom**: MOP header showed null Node Type / Activity; nodes duplicated.
- **Root cause**: No `meta`; `_each` iteration over `IP.Node_Name`.
- **Fix**: `meta{nodeType, activity}`; one node entry per zone with PGW/RDS subkeys.
- **Commit**: 5872ec4 · **Related**: FR-025 · **Verification**: E2E MOP HTML correct header, 1 node per zone

### BUG-041 — Step 5 QA report showed only 1 test case
- **Symptom**: Only TC_BASE shown when no LLM test cases were available.
- **Root cause**: JAR report JSON not parsed into test cases.
- **Fix**: TC_JAR, TC_SHEET_*, and per-cell TC_* derived from the JAR report.
- **Commit**: 44bf869 · **Related**: US3 AC1 · **Verification**: not recorded

## Steps 6–7: Workflow & MOP

### BUG-042 — Workflow prompt: wrong phase names, su handling, on_failure
- **Symptom**: Workflow did not match the production convention.
- **Root cause**: Prompt example was wrong.
- **Fix**: Phase names `preNodeHealthCheck`/`activity_configuration`/`postNodeHealthCheck`; `su - sdfrun` as its own step; `on_failure: continue` for health checks; MOP header vars.
- **Commit**: 19437b9 · **Related**: FR-028 · **Verification**: not recorded

### BUG-043 — MOP JAR invoked with invalid flags
- **Symptom**: Step 7 always failed.
- **Root cause**: Used `--mode`/`--workflow` instead of the `generate`/`execute` subcommands; no ciq-processor JSON step.
- **Fix**: Correct subcommands and flags; ciq-processor run before MOP generation; JAR stdout/stderr logged.
- **Commit**: 1c7e53a · **Related**: FR-027 · **Verification**: not recorded

### BUG-044 — `--yaml` given stem name, not file path
- **Symptom**: JAR could not find the workflow.
- **Root cause**: Flag semantics misunderstood.
- **Fix**: Pass the full path; sanitized rules passed as `--schema`.
- **Commit**: d9d34d0 · **Related**: FR-027 · **Verification**: not recorded

### BUG-045 — Step 7 artifact returned even if missing
- **Symptom**: "no artifact" shown at the milestone.
- **Root cause**: No existence check.
- **Fix**: Check that the file exists; include the MOP output HTML.
- **Commit**: 3e34af6 · **Related**: FR-014 · **Verification**: not recorded

### BUG-046 — Step 7 wrong filename in runner restore path
- **Symptom**: "no artifact" on refresh.
- **Root cause**: `_mop-generation-report.html` used instead of `_mop-qa-report.html`.
- **Fix**: Filename corrected; MOP output HTML added as an extra artifact.
- **Commit**: e05a6c8 · **Related**: FR-014 · **Verification**: not recorded

### BUG-047 — `LOCAL` node referenced but undefined
- **Symptom**: Workflow validation failed.
- **Root cause**: LLM used `node: LOCAL` without defining it.
- **Fix**: Autofix adds missing referenced nodes (`LOCAL` → `{type: local, plugin: local}`).
- **Commit**: 60f60e2 · **Related**: FR-028 · **Verification**: workflow with 3 LOCAL refs passes validation

### BUG-048 — Step `when` field breaks JAR deserialization
- **Symptom**: JAR rejected the workflow.
- **Root cause**: Unknown step fields.
- **Fix**: `_VALID_COMMAND_FIELDS` whitelist (adds `command_description`, `timeout_sec`).
- **Commit**: a497018 · **Related**: FR-028 · **Verification**: not recorded

## Step 8: Simulator Setup

### BUG-049 — Step 8 milestone artifact pointed to overview.html
- **Symptom**: Wrong artifact at the step 8 milestone.
- **Root cause**: Artifact map entry.
- **Fix**: Point to simulator-config.yaml; manifest and per-node configs as extras.
- **Commit**: 9fd3550 · **Related**: FR-014 · **Verification**: not recorded

### BUG-050 — MockWire launched with wrong CLI
- **Symptom**: Simulator failed to start.
- **Root cause**: `--config-dir` is not supported.
- **Fix**: `--name/--port`; CWD set to the simulator dir; multiple processes per node.
- **Commit**: 0f52f58 · **Related**: FR-031 · **Verification**: E2E run (0f52f58)

### BUG-051 — Truncated simulator YAML; MockWire logs lost
- **Symptom**: Invalid YAML; no diagnostics.
- **Root cause**: LLM output truncated; MockWire logs to stdout, which was not captured.
- **Fix**: Repair unclosed quotes; skip MockWire start on invalid YAML; capture stdout and stderr.
- **Commit**: 1c7e53a · **Related**: FR-031 · **Verification**: not recorded

### BUG-052 — Multi-document YAML and duplicate keys fail parse
- **Symptom**: Simulator config parse failure.
- **Root cause**: LLM emits one document per node separated by `---`; MockWire configs repeat command keys.
- **Fix**: Keep the first document only; allow duplicate keys. **Side effect**: dropping the other documents drops every node except the first, now tracked as open issue S8-02.
- **Commit**: a497018 · **Related**: FR-031 · **Verification**: not recorded

### Open (see [004-simulator-setup](../004-simulator-setup/spec.md))

| ID | Severity | Title | Status |
|----|----------|-------|--------|
| S8-01 | Critical | Step 9 never targets the simulator; execution resolves to real NIAM hosts from CIQ | Fixed (004 Phase 5: step 9 executes step 8's overlay workflow) |
| S8-02 | Critical | All nodes collapse into one config (first-document strip from a497018) | Fixed (004 Phase 2): one MockWire instance per remote node |
| S8-03 | Critical | Step 8 always fails validation; `_extract_ports()` always `[]` | Fixed (uncommitted, 004 Phase 1): MockWire-format validator |
| S8-04 | High | Sanity check is raw TCP, not SSH; results meaningless | Fixed (004 Phase 4): real SSH sanity via paramiko + HTML report |
| S8-05 | High | Cross-run pollution in shared `lib/mockwire` config dir; `file:` responses never found | Fixed (uncommitted, 004 Phase 1): per-run JAR copy; nothing written to lib/ |
| S8-06 | High | Simulator process leaks on failure/cancel/re-run; undrained PIPEs; no TTL | Fixed (uncommitted, 004 Phase 1): registry + stop on every exit path, log files, --ttl-minutes |
| S8-07 | High | Phase 2 (command → response) is a stub returning `{}` | Partly fixed: commands with recorded real output are served verbatim (`recorded_responses`); the LLM answers only the rest |
| S8-08 | Medium | `regenerate()` doesn't write, restart, or re-sanity; skill kept in session state | Partly fixed: retry re-runs setup with step 9 feedback incl. MockWire unmatched commands; targeted regeneration open |
| S8-09 | Medium | MockWire error truncated to 500 chars; root cause hidden | Fixed (uncommitted, 004 Phase 1): error carries log path + last 50 lines |
| S8-10 | Medium | Prompts, `su` escalation, and NIAM not modelled | Fixed (004 Phase 2): prompts/`su`/NIAM built deterministically; Ctrl+P,E elevation is a MockWire gap (MW-01) |
| S8-11 | Medium | Placeholder commands left to LLM instead of resolved from CIQ JSON | Open |
| S8-12 | Low | Unused `simulator.endpoint/port`; CWD-dependent JAR path; `LOCAL` gets stub server | Fixed (uncommitted, 004 Phase 1): config wired, JAR path from project root, LOCAL skipped |
| S8-13 | High | `extract_workflow_commands()` read `cmd`/list phases; workflows use `send:` + phase map → LLM prompt never saw any command | Fixed (uncommitted, 004 Phase 1) |
| S8-14 | High | Relative run_dir + MockWire cwd=simulator/ → --config resolved twice, no instance starts | Fixed (absolute simulator dir; found by test_step.py --agents 8,9) |
| S8-15 | High | `su` step's own `prompt_regex` ignored when the node regex didn't cover it → simulator pushed the wrong prompt, JAR timed out | Fixed (step prompt_regex wins in `_escalations`) |
| S9-01 | High | LOCAL helper scripts not found: execute ran in pipeline cwd instead of run_dir/scripts | Fixed (cwd=run_dir/scripts, absolute JAR args) |
| S9-02 | Medium | Non-simulator failures (LOCAL scripts) classified as simulator despite event log | Fixed (no unmatched event ⇒ workflow) |
| DEP-1 | — | Workflow uses `${node_row.RDS.NIAM_NAME1}`; CIQ JSON exposes `RDS.niamID-1` (step 6) | Partly fixed: NIAM IDs → `${X.niamID-n}`; `NIAM_USER` flagged by step 6 validator |
| DEP-2 | — | `execute --node` given `node_type` (`PGW_RDS`), not a node name (step 9) — confirmed | Fixed (uncommitted): one execute per zone × CR group (`--node EAST --cr CR1`) |
| DEP-3 | — | Malformed `${{…}}` placeholders in generated workflow (step 6 autofix) | Fixed (step 6 autofix) |
| DEP-4 | — | `su`/`exit` steps validate a prompt the JAR strips from output (step 6) | Fixed (step 6 autofix: step `prompt_regex`, no output criterion) |
| DEP-5 | — | `sar 1 10` regex expects 5 numeric columns; real output has 6 (step 6) | Fixed (generic): real command outputs from uploaded transcripts → `command_samples`; step 6 validator checks every regex against them (JAR semantics: find, MULTILINE, no DOTALL) and self-corrects ≤2 rounds |
| DEP-6 | — | `${configData…'Test IMSI'}` resolves empty in `execute` and stays unresolved in the MOP (step 6) | Fixed (step 6 autofix: `${configSequences.0.records.0.data.<Col>}`); EAST has no IMSI in CIQ |
| DEP-7 | — | Python `(?P<…>)` / underscore group names → PatternSyntaxException; `${register.x}` never resolves (step 6) | Fixed (step 6 autofix) |
| DEP-8 | — | `${env.x}` never resolves (OS env is `${ENV.X}`) (step 6) | Fixed (rewritten to `${x}`) |
| DEP-9 | — | Only the first CIQ record per node group executes; `for_each` over records NPEs in the JAR | Open |
| DEP-11 | — | Workflow ↔ helper script CLI mismatch (--refinmocName vs --refinmoc_name, --pdn_type, empty ${ORDERNO}) (step 6 / helper_script_gen) | Fixed (generic): flags and success messages come from the workflow (CLI + output contract, checked, 1 retry); `${ORDERNO}` see DEP-14 |
| DEP-12 | — | Quoted CIQ column `${configSequences.0.records.0.data.'Test IMSI'}` resolves empty; inside a single-quoted YAML scalar it breaks parsing | Fixed (autofix unquotes, also on raw text; validator rule) |
| DEP-13 | — | `${…}` inside validation/register regexes is not interpolated → PatternSyntaxException | Fixed (autofix → `.+?`; validator; prompt) |
| DEP-14 | — | `${ORDERNO}` is a framework runtime parameter, flagged as never registered and empty in step 9 | Fixed: `execution.runtime_params` in config; step 6 treats them as defined, step 9 passes `--var NAME=<test value>` |
| DEP-15 | — | `su` step `prompt_regex` (`sdfrun@rdsggn01:~>`) differs from the real prompt after su (`rdsggn01$`) | Fixed: validator checks it against the next prompt in the transcript |
| DEP-16 | — | EAST CIQ rows have no IMSI → `--imsi ''`, `imsi=,` | Not a data error: the subscriber is given by IMSI *or* MSISDN → DEP-17 |
| DEP-17 | — | IMSI must be resolved via MSISDN when only the MSISDN is given (`ldapsearch … msisdn=… \| grep -i provisionedIMSI`); production syntax `${rec_row.data.<Col>}` unknown to the JAR | Fixed (generic): step 6 prompt pattern "identifier from one of several columns" (`when`-guarded steps registering one variable, JAR-verified), autofix `rec_row.data` → `configSequences.0.records.0.data` |
| DEP-18 | — | Quoted CIQ column in a bare `when` path (`data.'Test MSISDN' != ''`) is always false → the guarded lookup never runs | Fixed (autofix unquotes; prompt rule) |
| DEP-19 | — | A CIQ column only some rows fill (EAST has no `Test IMSI`) was used unguarded → `imsi=,` | Fixed: step 6 validator reports it with the columns those rows do fill (`Test MSISDN` ranked first) → self-correction |
| DEP-20 | — | `sar` regex unverifiable when a run uploads no health-check transcript (c43f64ef) | Fixed: real outputs are kept in `data/knowledge/command_samples.json` (seeded from uploads: 32) and reused by steps 6/8 for commands a run did not record; c43f64ef re-run: `sar` checked and correct |
| DEP-21 | — | Without the lookup guidance step 6 faked the MSISDN branch (`echo "No IMSI provided"`) so `${resolvedImsi}` stayed empty for EAST | Fixed (detection): validator evaluates simple `when` guards per CIQ sample row and reports variables a row never registers; the lookup itself must come from developer notes |
| DEP-22 | — | LLM multi-line regexes assume adjacent lines (`apache … running\n\s+instance…`) while real output has log lines between; 2 self-correction rounds did not fix it | Fixed: step 6 widens the fewest `\n` / `\s+` joins to `[\s\S]*?` (all widened, then each re-tightened if still matching) when the regex fails recorded real output and the widened one matches |
| DEP-23 | — | JAR fails any non-zero exit before its regex (`use_exit_code: false` and failure-branch regexes do not help) → `ls` absence checks always failed | Fixed: autofix appends `2>&1 \|\| true` when the success regex expects absence; prompt rule |
| DEP-24 | — | `expr` semantics (JAR probes): only registered variables (validation-regex groups invisible), no `Float()`/`Int()`, literal `True` always fails; LLM wrote `Float(rdsCpuIdle)` with group `idleCpu`, and 5-number sar regexes capture the wrong column | Fixed: autofix unwraps casts, drops literal/unsupported exprs, moves a used validation group into `register`; validator flags unregistered expr vars and evaluates `var OP number` on real output (wrong column → error) |
| DEP-25 | — | LLM gave sftp steps an output regex (`File transfer successful`) — sftp prints nothing, the step always failed | Fixed: autofix drops regex criteria on sftp steps |
| S9-06 | — | Generated `create_xml.py` crashed (`NameError: name 'arg' is not defined`) — the helper contract checked flags and messages but never ran the script | Fixed: helper_script_gen smoke-runs each script with the workflow invocation (test values) in a temp dir; programming-error tracebacks go back to the LLM; missing-input failures are ignored. Refined: any traceback counts except environment errors (FileNotFoundError, PermissionError, connection errors) — a run's `create_xml.py` crashed with `ExpatError: duplicate attribute` that the first allow-list missed |
| S9-05 | — | Scenario cases shared the simulator VFS: case 2's upload hit `remote_path exists and overwrite=false` | Fixed: step 9 resets the VFS before each case |
| DEP-26 | — | LLM miscounts table columns: sar `%idle` regex skips 4 numbers instead of 5 and captures `%steal` (0.00) → `cpuIdle >= 90` fails on a healthy node; recurred in almost every run, self-correction did not fix it | Fixed: step 6 shifts the capture 1-4 fields right until the expr holds on recorded real output (must capture a value; unhealthy samples are never "repaired") |
| G-5 | — | Self-correction stalled (18 → 18 errors): feedback was escaped JSON capped at 15 000 chars, 2 rounds fixed | Fixed: deterministic repairs before/after each round, errors as a numbered plain-text list grouped by kind (24 000 chars), a 3rd round while the error count still drops |
| S9-07 | — | UI run 3649ea61: every executed step passed but both sunny-day cases were FAIL — step 9 compared the JAR's `overall_status` with "PASS", the JAR reports a clean run as `SUCCESS` | Fixed: `scenarios.run_passed()` accepts SUCCESS/PASS/PASSED/COMPLETED/OK |
| G-6 | — | Step 6 accepted a cut-short workflow (76 lines: RDS pre-checks only, no activity/post phases, no PGW, 5 of 106 MOP commands) — every check passed because nothing in it was wrong | Fixed: `check_completeness` — a step per phase kind the requirements list, every node with ≥2 commands used, ≥12% of the commands (25% flagged a complete 27-of-116 workflow); ranked below any complete workflow and fed back as "Incomplete workflow" |
| OB-11 | — | Artifact cache served stale artifacts: UI run 1bb18847 reused a step 6 workflow generated before the fixes; the step 9 → 6 auto-retry reloaded the cached workflow it was meant to replace; new documents/developer notes reused cached requirements; re-running steps 5/7/8/9 wiped the whole UC cache | Fixed: cache keyed on a hash of the generating code and of the document set (file contents + notes); auto-retry clears the cache from its target; a regenerated agent invalidates later caches; `clear_from_step` ignores uncached agents |
| S8-22 | — | `su - sdfrun` answered with the regex text `(?m)(?:^|n)rdshyd201 $ ` as the prompt: the regex-to-example heuristic kept inline flags and line-start groups, and the fallback used a non-matching candidate → every `su` step failed (the "su prompt_regex mismatch") | Fixed: prompts are generated from the parsed regex (`topology._shortest_match`, Java named groups converted) and re-checked against it |
| S8-23 | — | Response-file poll (`ls -l …/outbox/${ORDERNO}.xml.res`, 30 retries) never passed: MockWire lists every file `-rw-r--r--`, the workflow regex (from the real node) expects `-rwx------` → case timed out after 300 s | Fixed: MockWire `vfs.listing` (per-path mode/owner/group, 575344d); step 8 fills it from recorded `ls -l` output, else from the mode the workflow regex spells out |
| DEP-29 | — | LOCAL `parse_response.py --response_file '${ORDERNO}.xml.res'` read the PGW outbox file by its bare name without downloading it ("not found" in both cases); the file-flow check only knew absolute paths | Fixed: `autofix.remote_file_reads` also matches bare names of node files (uploaded files count as local); autofix inserts the `sftp` download before the LOCAL step (node/path from the listing step), the validator reports any left |
| DEP-30 | — | Second node group's response-file download failed: `local_path exists and overwrite=false` (the first group downloaded the same `${ORDERNO}.xml.res`) — also true for a real run over several node groups | Fixed: autofix sets `overwrite: true` on every sftp download without it |
| DEP-31 | — | Without developer notes the LLM handled CIQ rows without a Test IMSI by registering `(?<resolvedImsi>)` (always empty), so later ldapsearch/create_xml ran with `imsi=` | Fixed: validator rejects an always-empty register used in a command ("obtain it from real data or skip with when"); developer notes with the MSISDN → IMSI lookup still give the best workflow |
| DEP-32 | — | LLM wrote the response-file presence check as `total\s+\d+…\.xml\.res` for `ls -l <file>` — `ls` of one file prints no `total` line, so it can never match (node or simulator) | Fixed: validator rejects `total` in regexes of `ls -l <single file>` steps |
| S9-08 | — | A never-matching poll (30 retries × 10 s) outlasted the 300 s case timeout: the JAR was killed and the case had no report at all | Fixed: the step 9 overlay (simulator only) caps retries at 3 and delays at 2 s — the simulator creates files at once |
| G-7 | — | UI run 8eb82d00: `create_xml.py` crashed in every attempt, each time differently (`find(...).text` on None, `tostring()` arguments): a retry regenerated the script from scratch with only the problem list | Fixed: retries repair the previous script (code + traceback in the prompt), 3 attempts |
| OB-12 | — | Rerun at the step 6 milestone re-ran only `helper_script_gen` (the agent before the milestone): a workflow error (`${resolvedImsi}` unregistered for EAST) was never regenerated, 3 reruns in a row | Fixed: step 6 agents record `failed_agents` in `step_6_error.json`; a milestone rerun starts at the earliest failed agent of the step |
| G-8 | — | `create_xml.py` still failed after repair: ElementTree/minidom pitfalls recur (duplicate xmlns attributes → ExpatError, `tostring(…, 'utf-8', encoding=…)`, `find()` None on unqualified tags) — the prompt itself said "use ElementTree" | Fixed: create_xml prompt builds the XML as text templates from the sample requests with escaped values; no ElementTree/minidom round-trip |
| DEP-29b | — | LOCAL step read the node's response file from a made-up local dir (`/tmp/${ORDERNO}.xml.res`) with no download | Fixed: a path whose file name is a node file counts as a read of it: download inserted, command pointed at the local copy |
| DEP-33 | — | RDS `exit` leaving `su - sdfrun` waited for `Connection to .* closed.` (ssh-client text, never sent by the node) → step failed although the root prompt came back | Fixed: autofix gives such exits the node's prompt_regex; an `exit` with no `su` to leave (it would close the JAR's session) is dropped |
| OB-13 | — | Step 6 kept failing in the UI when the documents lack a fact only the developer knows (MSISDN → IMSI lookup for CIQ rows without a Test IMSI) and no developer notes were entered: reruns could not fix it | Fixed in step 1: CIQ columns where every sample row gives exactly one of two alike values (Test IMSI / Test MSISDN) become a deterministic questionnaire question (`questionnaire.ciq_alternative_questions`), asked even when the LLM finds no gaps and stored with the UC's answers; requirements carry `developer_questions` so step 6 reads each answer with its question. (A first version asked at the step 6 milestone; replaced at the user's request.) Answer for this UC saved 2026-11-03 |
| DEP-34 | — | `su - sdfrun` prompt_regex `rdshyd201$` (unescaped end anchor) never matches the real prompt `rdshyd201$`; step 6 flagged it but the LLM did not fix it in 3 rounds | Fixed: deterministic repair sets a su/exit prompt_regex that misses the recorded prompt to that prompt, escaped |
| S8-24 | — | Simulator prompts always got a trailing space: with an anchored regex (`rdshyd201$`) the prompt `rdshyd201 ` never matched → every su step failed | Fixed: the space is added only when the regex still matches with it |
| DEP-35 | — | LLM joined `when` conditions with `and` (`Test IMSI == '' and Test MSISDN != ''`): the JAR skipped every such step, so EAST never looked up its IMSI and never created the XML (upload: file not found) | Fixed: autofix rewrites `and`/`or` to `&&`/`||` outside quoted values; prompt rule + CLAUDE.md note |
| S8-25 | — | UI run 4fab06dd: the IMSI dump was answered in an invented format (`dn: imsi=…,dc=IMSI`) although a template from the recorded real output existed: the LLM's `regex:` key came first and MockWire answers with the first match → workflow regex (from real output) failed | Fixed: `merge_responses` puts recorded/templated responses first and drops invented keys that only answer commands real output covers |
| DEP-36 | — | LOCAL `python create_html_report.py --pre_data "…" --post_data "…"` failed: "arguments are required". Probed: the JAR's LOCAL runner splits double-quoted arguments at spaces and drops the rest (`--a "x y" --c "z"` → `['--a','x']`); single quotes arrive intact, multi-line values too | Fixed: autofix single-quotes LOCAL arguments; prompt rule + CLAUDE.md note |
| DEP-37 | — | `--ciq_data "${configSequences.0.records.0.data}"` (whole CIQ record) arrives as `[object Object]` | Fixed: validator rejects it (pass the columns one by one); prompt rule |
| OB-14 | — | UI run 3000d2cb: step 6 failed (10 errors) but the run went on without its review, using helper scripts cached from an older workflow whose "code" was the script's path (`create_xml.py` = `data
uns\…\create_xml.py`) | Fixed: the cache stores script contents, not run-dir paths; an agent that runs again (cache miss) invalidates later caches even when it then fails; a cache hit never turns a failed display step into "passed" |
| DEP-38 | — | Regexes written from the wording of developer answers ("Updates Rejected: 0", "Apache status: running") instead of the real output; the first prompt never showed the recorded outputs, only correction rounds did | Fixed: the first step 6 prompt has a REAL COMMAND OUTPUTS section (one per command, trimmed): first draft 10-15 → 1 error |
| DEP-39 | — | `ls -l <file>` presence checks kept `total\s+\d+…` even after validator feedback | Fixed: autofix rewrites them to the file's long-listing line (non-zero size when the step talks about an empty file) |
| DEP-40 | — | `python parse_response.py` (a generated helper) placed on node PGW: no node has it, the simulator invented an answer | Fixed: autofix moves helper calls by bare name to LOCAL (scripts with a path stay); the download fix then fetches the file |
| G-9 | — | A step reading the script's output through `register` regexes + `expr` got a script printing something else | Fixed: the helper output contract includes register regexes |
| G-10 | — | Script required `--imsi` the workflow never passes; the smoke run only treated tracebacks as failures | Fixed: argparse usage errors on the workflow's own command line are contract failures |
| DEP-41 | — | UI run 77d59487: IMSI looked up via MSISDN into `resolvedImsi`, but every IMSI step still used `${…Test IMSI}` guarded `Test IMSI != ''` → rows like EAST skipped them (4 step 6 errors) | Fixed: autofix `unify_alternative_columns` — when a lookup registers V only for rows without A (from an alternative column B), steps guarded `A != ''` using A use `${V}` unguarded, V is registered from A for rows that give it, the lookup runs first; left alone when the workflow has its own branch |
| G-11 | — | parse_response.py searched `spml:response` (namespaced); the sample's `<response>` is unqualified → failed on every node; the smoke run never gave it a file | Fixed: the smoke run writes the UC's sample response for response-file arguments; the script must exit 0 and print what the step's regexes read |
| DEP-42 | — | `--ciq_data '{'Mandatory Cap': '…'}'` nests quotes: the shell splits it, the script got `{Mandatory` | Fixed: validator rejects LOCAL script calls whose arguments are not flag/value pairs after shell splitting |
| DEP-43 | — | `apache\s+\.\.\.\s+running.*instance1…` joins two lines with `.*`, which never crosses a line break; the line-join repair only widened `\s*`/`\s+` | Fixed: unescaped `.*`/`.+` joins are repair candidates too (widened to `[\s\S]*?` only where needed to match the real output) |
| DEP-44 | — | UI run c4ab4c13: `--ciq_data '{'Mandatory Cap': '…', …}'` again (validator feedback ignored). Probed: the JAR's LOCAL runner breaks on any double quote, even inside single quotes, so JSON/dict values can never be passed | Fixed: autofix expands a dict-valued argument into one flag per field (`--ciq_data_mandatory_cap '${…}'`); validator rejects any double quote in a LOCAL command; prompt rule |
| G-11b | — | parse_response.py kept searching `spml:response` through 3 repair rounds | Fixed: the smoke-run feedback lists the tags exactly as ElementTree sees the sample (namespaced root, unqualified children) |
| DEP-45 | — | "Response file must not exist yet" check placed after the upload that produces the file → raced the node, failed on every node | Fixed: autofix moves a `No such file` check on a file named after an upload before that upload |
| DEP-46 | — | A fresh workflow never used Test MSISDN: rows like EAST skipped every IMSI step and step 9 PASSED anyway (false green) | Fixed: validator rejects a workflow that uses one column of a step 1 either-or pair but never the other; ranked like an incomplete workflow |
| DEP-47 | — | create_xml.py was never given the IMSI and defaulted to the sample's (NORTH's) IMSI — EAST's request targeted the wrong subscriber, invisible to the simulator; also hidden in the 4fab06dd "pass" | Fixed: CIQ values found in the UC's sample request must reach the script that builds the uploaded request (directly, via the alternative column or a variable derived from either); helper contract message forbids defaults copied from samples |
| DEP-48 | — | UI run 03b82f21: the LLM wrote columns in subscript form `data['Test IMSI']` (placeholders and `when`); DEP-47 did not see the IMSI reach create_xml.py via `${resolvedImsi}` and step 6 failed — the JAR only knows the dot form | Fixed: autofix rewrites `data['X']` / `data["X"]` (also doubled quotes inside single-quoted scalars) to `data.X` |
| DEP-49 | — | Fresh step 6 on 03b82f21: both correction rounds returned a 0-error-after-fix workflow that was thrown away as "Invalid YAML syntax" — a `send:` wrapped over lines held ` : ` (`${resolvedImsi != null ? resolvedImsi : …}`) on a continuation line, which the per-line colon quoting never saw; the 8-error first draft was kept and step 9 failed | Fixed: `sanitize_yaml` first folds plain-scalar continuation lines into their key line, then quotes the whole value |
| DEP-50 | — | Fresh step 6 passed with `ldapsearch … objectclass=*"` (stray trailing quote): the shell waited for the closing quote, the step timed out for EAST and NORTH in step 9 | Fixed: autofix drops a trailing quote when that alone balances the command; validator rejects any other command whose quotes don't close |
| OB-15 | — | UI run 5a15f076: every step passed but step 10 showed no artifacts — the run page lists a step's files by name fragments and step 10's were `pipeline-report` / `final-report`, while the report skill writes `<uc>_report.html` / `<uc>_report.xlsx` | Fixed: step 10 rule is `<uc>_report.` (`ui/pages/progress_section._STEP_FILE_RULES`) |
| OB-16 | — | Runs had no requirements document: steps 1-2 restored from the artifact cache wrote only `requirements.json`, never `<uc>_requirements-summary.html` (step 2 artifacts, final report) | Fixed: `summary_html_from_requirements` rebuilds it from the requirements package on cache restore; step 10 rebuilds it when missing; it is the first step 10 deliverable |
| OB-17 | — | "View full details" of a completed run (run history details) showed no output files: its Output Artifacts section reads the `generated_configs` table, which nothing writes | Fixed: the details page lists the run's Final Documents (final report + step 10 deliverables, requirements document first) from the run folder, with View/Download |
| OB-18 | — | New Run with "Pause for review at milestones" unchecked still paused at every milestone: the checkbox value stopped at `on_start_pipeline` and never reached the runner | Fixed: `runner.start(review_milestones=…)` → run state; with review off a milestone of a passed step continues on its own (step 2 summary and steps 4-9), a failed step still pauses for rerun/cancel; the step 1 questionnaire is still asked. Also: run page step list larger font, compact rows |
| OB-19 | — | Live Run page: the step Logs tab showed flow.log lines of every earlier run (filter by step only) and opened at the oldest line; no live clock / step timing; page looked static | Fixed: logs limited to the run (since its start time), newest-first console; run page redesign — state badge (LIVE/REVIEW/DONE/FAILED), run id + UC, elapsed clock, KPI strip, per-step segment bar, step timeline with live durations, step header + spec cells, live activity console in Overview |
| OB-20 | — | LLM Usage page and the dashboard usage card were always empty: usage was kept only in an in-memory list, wiped by every server restart | Fixed: each LLM call is also appended to `data/llm_usage.jsonl` and loaded at startup; Clear removes the file; dashboard card renamed "LLM Usage by Step" |
| OB-20b | — | LLM usage kept outside the run data (JSONL file) | Changed: table `llm_usage` in the pipeline DB, one row per call keyed by `run_id` (= `pipeline_runs.id`) with UC, step, model, tokens, cost, time; PipelineRunner registers its DB with `shared.llm`; LLM Usage page and dashboard read it (`load_usage_stats`); run details show the run's usage per step; deleting a run deletes its usage rows; JSONL file dropped |
| OB-21 | — | UI run 9deb8c2c (MRF_ANNOUNCEMENT_LOADING): after 10 answered questions the summary still listed 8 "remaining gaps", 6 of them answered (report commands, sftp transfer, chmod 777): the re-analysis got the answers as bare ids (`{"orch_q2": "ls -ltrh …"}`) without their questions; step 1's LLM calls also passed system/user prompts swapped | Fixed: answers go to the analysis as Q/A pairs; a gap whose words are mostly in an answered question is dropped (none left → COMPLETE); analysis prompt: answered items are KNOWN; prompt order fixed in analyzer + questionnaire |
| DEP-27 | — | The JAR stores the FIRST group's value under a register name: `( +[0-9.]+){5} +(?<cpuIdle>…)` registered 0.00 (probe) — the step 6 prompt example itself had the unnamed group | Fixed: autofix makes every other group non-capturing in register regexes with a named group; validator reads the value like the JAR; prompt example corrected |
| S8-21 | — | Placeholder commands got invented responses (NORTH queried imsi=404530000000132, the simulator answered another subscriber) although real output was recorded | Fixed: step 8 turns recorded output into a `regex:` template (named capture per placeholder, recorded values → `${name}`), so any queried value is answered with real formatting |
| DEP-28 | — | LLM wrote expression placeholders `${resolvedImsi != null ? resolvedImsi : configSequences…Test IMSI}`; the JAR interpolates names only → `imsi=,` | Fixed: autofix rewrites to the first registered variable and, when the else-branch is a CIQ column, inserts a when-guarded `echo` step registering the variable from it |
| G-4 | — | Autofix stripped valid step fields (`sftp`, `for_each`, `steps`, `rest`, `email`, …): correct transfer steps were deleted | Fixed: allow-list = schema StepDefinition |
| G-3 | — | Developer notes never reached step 6 (only requirement gathering saw them) | Fixed: kept verbatim in `requirements.developer_notes`; authoritative section in the step 6 generate/regenerate prompts |
| S9-03 | — | Step 9 never executed a failure scenario: LLM test-case descriptions graded against one all-success run | Fixed by [005](../005-scenario-testing/spec.md): test cases from workflow + CIQ JSON, run per case on the simulator, test-case report (sunny day now, negatives later) |
| G-1 | — | Step 6 `regenerate()` sent only the first 10 000 chars of the workflow → truncated output | Fixed (60 000) |
| G-2 | — | Self-correction ranked unparseable YAML as 1 error and kept it over a valid workflow | Fixed (`_rank`: invalid YAML is worst; error now names line + text) |
| S8-16 | — | Simulator has no response file after the XML upload (`outbox/<ORDERNO>.xml.res`), so `ls`/`parse_response.py` fail. Run c43f64ef: the same `ls …xml.res` is checked absent (pre-upload) and present (post-upload) — no static response can pass both | Fixed 2026-11-02: MockWire SFTP fix + shell operators (fork 0b37ef0, pushed); step 6 writes sftp upload/download steps and exit-0 absence checks; step 8 derives `vfs.dirs` + triggers from the workflow (`file_flows.py`), produced file = UC sample response; file commands answered by VFS builtins |
| S8-17 | — | Step 6 invented an unused `NIAM_SERVER` node (NIAM is `ssh.ne_id`); step 8 found no commands for it and refused to start the simulator | Fixed: autofix drops nodes no step uses (kept when a step picks its node via `${…}`); step 8 builds instances only for nodes steps use |
| S8-18 | — | Invented simulator responses contradicted the step's own success check (NORTH `ls …xml.res` listed a file the pre-check expects absent) and were routed to step 6 | Fixed: step 8 checks invented responses against the step's success regex (placeholders resolved to test values), regenerates once with the regex as feedback; recorded real output is never changed; stateful commands are skipped (S8-16) |
| S8-19 | — | Step 8's LLM wrote no response for some commands (EAST MSISDN lookup → `command not found`) | Fixed: step 8 lists node commands no entry (exact/regex/prefix) answers and asks for them in the conformance round |
| S8-20 | — | Step 8's LLM mapped `echo <IMSI>` to a fixed number, overriding the builtin | Fixed: `echo`/`true`/`false`/`:` keys are never mapped |
| MW-15 | — | MockWire: SFTP uploads on Windows failed with `23: Invalid parameter` and fired no trigger (ProviderMismatchException in the close listener) | Fixed in MockWire 0b37ef0 |
| MW-16 | — | MockWire: `&&`, `\|\|`, `2>&1`, `2>/dev/null` hung the session 60 s (exception on Windows paths) | Fixed in MockWire 0b37ef0: shell semantics; any command error answers `-bash: …` (exit 1) |
| S9-04 | — | LOCAL steps failed on the server: `Cannot run program "bash"` (server started from PowerShell has no bash on PATH; System32 bash is WSL) | Fixed: JAR environment gets Git Bash and this Python first on PATH on Windows |

## Step 9: Execution QA

### BUG-053 — Step 9 artifact path missing `exec_report/`
- **Symptom**: No execution report at the milestone.
- **Root cause**: Report lives in the `exec_report/` subdir.
- **Fix**: Path corrected.
- **Commit**: f8391da · **Related**: FR-014 · **Verification**: not recorded

### BUG-054 — Step 9 re-run always targeted step 6
- **Symptom**: Simulator issues re-generated the workflow needlessly.
- **Root cause**: Static re-run target.
- **Fix**: Target taken from the classification (simulator → 8, workflow → 6, fallback 6); dynamic UI label.
- **Commit**: ba4c30a · **Related**: FR-005 · **Verification**: not recorded

## LLM / Config

### BUG-055 — LLM usage stats empty
- **Symptom**: Usage page showed nothing.
- **Root cause**: `import_cr_module()` created a second module instance; writer and reader used different `llm_usage_log`s.
- **Fix**: Reuse the `sys.modules` instance. Code later replaced by `shared/llm.py` (bd35015).
- **Commit**: cf14c7b · **Related**: FR-010 · **Verification**: not recorded

### BUG-056 — No retry on LLM 503/429/timeout
- **Symptom**: Transient provider errors failed the step.
- **Root cause**: No infrastructure retry.
- **Fix**: 3 retries with 5s/15s/30s delays.
- **Commit**: f046a72 · **Related**: FR-008 · **Verification**: not recorded

### BUG-057 — Running pipeline used stale model/API key
- **Symptom**: Settings changes ignored until the next run or restart.
- **Root cause**: Runner passed a frozen config snapshot.
- **Fix**: `call_llm` reads `ConfigManager._live_instance`.
- **Commit**: 9af3510 · **Related**: FR-032, SC-006 · **Verification**: not recorded

### BUG-058 — CLI runs had no API key
- **Symptom**: test_step.py / test_run.py LLM auth failures.
- **Root cause**: `_live_instance` only exists when the UI server runs.
- **Fix**: Fall back to `api_key`/`default_model` in the `settings` table of pipeline.db.
- **Commit**: cff5d3c · **Related**: FR-032 · **Verification**: helper_script_gen generated 3 scripts from CLI

### BUG-059 — Model list offered unavailable free tiers
- **Symptom**: Selecting removed models failed.
- **Root cause**: Provider removed free tiers.
- **Fix**: Model list updated.
- **Commit**: 2b962fe · **Related**: FR-015 · **Verification**: not recorded

## Storage / DB

### BUG-060 — `uc_document_sets` rows silently not saved
- **Symptom**: Document sets missing, so re-run failed.
- **Root cause**: `INSERT OR IGNORE` with None in NOT NULL columns.
- **Fix**: `OR REPLACE` with None→"" coercion.
- **Commit**: 5f236ee · **Related**: FR-009 · **Verification**: not recorded

## UI

### BUG-061 — Continue allowed after generation failure
- **Symptom**: User could proceed with broken output.
- **Root cause**: Milestone offered the same buttons for every step.
- **Fix**: Hide Continue/Skip for failed generation steps; error banner.
- **Commit**: 82ccddd · **Related**: FR-021, US2 AC4 · **Verification**: not recorded

### BUG-062 — Step statuses not reset on re-run
- **Symptom**: Later steps kept stale passed/failed status.
- **Root cause**: Only the target step was reset.
- **Fix**: Reset steps ≥ target to pending; restore the running banner.
- **Commit**: 52961a6 · **Related**: FR-022 · **Verification**: not recorded

### BUG-063 — History pagination conflict
- **Symptom**: Double or broken pagination.
- **Root cause**: Quasar built-in pagination enabled.
- **Fix**: `hide-pagination`, `rowsPerPage=0`; buttons disabled until load.
- **Commit**: b50eb1a · **Related**: US4 · **Verification**: not recorded

### BUG-064 — Usage page cards ignored dark theme
- **Symptom**: Unreadable cards.
- **Root cause**: Hard-coded colours.
- **Fix**: App theme classes.
- **Commit**: 9c73b4c · **Related**: FR-012 · **Verification**: not recorded

### BUG-065 — Step tracker text truncated; pending steps not marked skipped
- **Symptom**: Truncated descriptions; steps after a failure stayed "pending".
- **Root cause**: Fixed box height; no terminal-state handling.
- **Fix**: Taller wrapping boxes; remaining steps marked skipped on failure/cancel.
- **Commit**: 558626a · **Related**: FR-013 · **Verification**: not recorded

## Logging / Tooling

### BUG-066 — flow.log and trace.log empty
- **Symptom**: Only error.log was written.
- **Root cause**: Filters matched the `src.` prefix after `src/` was dissolved.
- **Fix**: Filters use project package prefixes.
- **Commit**: 1a15994 · **Related**: — · **Verification**: not recorded

### BUG-067 — test_step.py could not load API key
- **Symptom**: CLI step tests failed auth.
- **Root cause**: Key stored in the DB `settings` table as `api_key`.
- **Fix**: Read the DB before config/env.
- **Commit**: 1541161 · **Related**: — · **Verification**: not recorded

### BUG-068 — test_step.py async state build silently failed
- **Symptom**: `ciq_file_path` empty.
- **Root cause**: `loop.run_until_complete` inside an async context.
- **Fix**: Fully async `_build_state_from_run`.
- **Commit**: e8b057d · **Related**: — · **Verification**: not recorded

### BUG-069 — Stale cr_import module cache *(obsolete)*
- **Symptom**: Code changes not picked up in test_step.py.
- **Root cause**: Cached cross-project modules.
- **Fix**: `clear_cache()`. cr_import was removed in bd35015.
- **Commit**: f1b4d7d · **Related**: — · **Verification**: not recorded

### BUG-070 — Cache check failures invisible
- **Symptom**: Cache never hit, with no log.
- **Root cause**: Exceptions logged at DEBUG.
- **Fix**: CACHE CHECK at FLOW level; errors at ERROR with traceback. The save path still logs at DEBUG; see OB-1.
- **Commit**: 36eb46a · **Related**: FR-030 · **Verification**: logs 2026-11-02 show CACHE CHECK lines

---

## Open Bugs

### OB-1 — Artifact cache does not hit on later runs (High, Open, root cause found)
- **Symptom**: After a full run, the next run of the same UC regenerates everything; `data/cache/<uc>/_cache_meta.json` is empty.
- **Root cause (confirmed from logs)**: `ArtifactCache.clear_from_step()` (`storage/artifact_cache.py:167-181`) looks up `from_agent` in a 4-item list of cacheable agents. For agents that are not cacheable (`network_simulator_setup`, `network_config_exec_qa`, `network_config_mop_qa`, `ciq_validation_json_qa`), `index()` raises ValueError and the fallback is `idx = 0`, so **a re-run at step 5, 7, 8, or 9 wipes the whole UC cache**. Evidence: `logs/trace.log` 2026-11-02 13:00:37 — "RERUN — cleared cache from network_simulator_setup onward", immediately preceded by "Cleared cache" for all four cacheable agents.
- **Contributing**:
  - (a) Cache save errors are still swallowed at `logger.debug` (`orchestrator/pipeline_agent.py:248-249`).
  - (b) Save gates on `step_{N}_status`, which defaults to `"passed"` when unset, so a failed agent that doesn't set status could be cached.
  - (c) `save_cache()` logs `len(entries)` rather than files written, and marks status `passed` even if an artifact key was missing. `has_cache()` then returns False. This is a likely explanation for the unexplained `ciq_validation_json_gen` miss at 12:36:53, 7 minutes after it was saved at 12:29:20 with no clear logged.
- **Fix direction**: When `from_agent` is not cacheable, clear only cacheable agents *downstream of it* (none for steps 8/9). Log save failures at ERROR. Count files actually written and mark `passed` only if all were written.
- **Related**: [002-artifact-cache](../002-artifact-cache/spec.md), FR-030, BUG-070

### OB-2 — "0 CIQ sheets" parsed (E2E run ef4fec2f) — Not reproducing
- Logs from 2026-10-30 to 2026-11-02 consistently show "Document extraction: … 3 CIQ sheets". No explicit fix commit was identified; likely resolved by the vendor-profile / requirement-gathering changes (8a97fb0) or by correct `ciq_file_path` passing. Close after one more clean run.

### OB-3 — CIQ gen fails with status=failed but 0 errors — Likely fixed
- `skills/ciq_validation_json_gen/skill.py:92` sets `error="No CIQ structure in requirements.doc_extracted.ciq_sheets"`. The agent writes `output.error` to `step_3_error.json` and logs `error[:200]` (agent error logging improved in 0f52f58). Unverified by a reproduction.

### OB-4 — MAX_RETRIES exhaustion continues the pipeline (High, Open)
- `orchestrator/pipeline_agent.py:304-306` logs "MAX RETRIES exceeded … continuing pipeline" and proceeds to the next agent with failed artifacts. This contradicts US3 AC4 / FR-007 ("fails gracefully with a clear error").

### OB-5 — Continue on a failed QA milestone still auto-retries (Medium, Suspected)
- After `_milestone_pause` returns `continue`, the loop falls through to the auto-retry block, which pops `_retry_target` set by the QA agent and jumps back. "Skip & Continue" on a failed step 5/7/9 therefore re-runs generation instead of continuing. Needs confirmation in the UI.

### OB-7 — Cached helper scripts contain file paths, not code (High, Open)
- `artifacts["helper_scripts"]` is `{name: path}`; `save_cache()` writes `str(value)`, so a cache hit writes path strings into `run_dir/scripts/*.py`. Tracked as [002](../002-artifact-cache/spec.md) C-2 / [003](../003-helper-script-gen/spec.md) H-3.

### OB-8 — helper_script_gen failure shows as passed (High, Fixed 2026-11-02)
- The agent never sets `step_6_status` or writes `step_6_error.json`, so a failed or partial script generation passes the step 6 milestone and can be cached. Tracked as [003](../003-helper-script-gen/spec.md) H-1.
- **Fix**: the agent sets `step_6_status=failed` and merges its error into `step_6_error.json`. A second bug turned up while fixing it (**OB-9**): `PipelineAgent` deleted the step's error file before *every* agent, so `helper_script_gen` wiped `network_config_gen`'s step 6 error. Now only the first agent of a shared display step clears it (`_first_agent_of_step`). Test: `tests/test_generation_contracts.py`.

### OB-10 — A second UI instance marks live runs as failed (High, Open)
- `ProgressSection._restore_active_run` marks any `running` DB run that its own runner does not hold as "Server restarted — pipeline lost". A second server (or a preview) on the same `data/pipeline.db` therefore fails the first server's live run. Happened 2026-11-02 with a UI preview on :8001 (row restored). Mitigation: previews use a DB copy; fix: record the owning process (pid/port) on the run and only recover runs whose owner is gone.

### UI-1 — Inconsistent layout across pages (Medium, Fixed 2026-11-02)
- Step boxes sized by their labels (2px border on the active one), connectors painted as blocks, dashboard banner/KPIs at half width, nested cards, mixed button cases, Settings without a title, sidebar labels wrapping.
- **Fix**: `ui/components.py` (page header, KPI tile, card title, status pills) + one CSS layer in `ui/app.py`; step tracker = 10 equal fixed-height boxes; responsive grids; uploader headers readable.

### UI-2 — Product theme, run page, New Run wizard (Enhancement, Done 2026-11-02)
- Theme A "Enterprise light": one blue primary (#0B5FFF) via design tokens + Quasar brand colours, neutral greys, no gradients, 13px base type, 32px controls, light header with logo + breadcrumb, grouped sidebar (Workspace / Results / Admin), token-driven dark mode.
- Live Run page: run header (status, actions, progress), vertical step list with status + duration, tabbed step detail (Overview with inline milestone review · Artifacts with view/download · Test cases from `<uc>_test-cases.json` · Logs from `logs/flow.log`); Run History "Open" loads any past run read-only; live events of another run are ignored while one is open.
- New Run: 3-step wizard (Activity → Documents → Notes & review with live summary), same widgets and handlers.
- Found while building: `progress_section.py` used `Path` without a module import (NameError swallowed by NiceGUI) — fixed; flow log lives in `logs/`, not `data/logs/`.
- Known limit: the Logs tab filters the global flow log by step, not by run.

### UI-3 — Developer notes retyped every run (Medium, Fixed 2026-11-02)
- Notes are per use case; run c43f64ef was started without them, so the IMSI/MSISDN lookup was unknown. The New Run wizard now pre-fills empty notes from the last run of the same UC.

### OB-6 — Resume after server restart not implemented (Medium, Open)
- There is no `resume_run` in `orchestrator/runner.py` and no `--resume` in `main.py`. FR-009 / SC-007 / contract `resume_run` are not met.
