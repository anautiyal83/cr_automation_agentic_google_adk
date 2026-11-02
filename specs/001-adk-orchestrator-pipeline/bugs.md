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
| G-3 | — | Developer notes never reached step 6 (only requirement gathering saw them) | Fixed: kept verbatim in `requirements.developer_notes`; authoritative section in the step 6 generate/regenerate prompts |
| S9-03 | — | Step 9 never executed a failure scenario: LLM test-case descriptions graded against one all-success run | Fixed by [005](../005-scenario-testing/spec.md): test cases from workflow + CIQ JSON, run per case on the simulator, test-case report (sunny day now, negatives later) |
| G-1 | — | Step 6 `regenerate()` sent only the first 10 000 chars of the workflow → truncated output | Fixed (60 000) |
| G-2 | — | Self-correction ranked unparseable YAML as 1 error and kept it over a valid workflow | Fixed (`_rank`: invalid YAML is worst; error now names line + text) |
| S8-16 | — | Simulator has no response file after the XML upload (`outbox/<ORDERNO>.xml.res`), so `ls`/`parse_response.py` fail | Open (Phase 3: VFS triggers) |

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

### OB-6 — Resume after server restart not implemented (Medium, Open)
- There is no `resume_run` in `orchestrator/runner.py` and no `--resume` in `main.py`. FR-009 / SC-007 / contract `resume_run` are not met.
