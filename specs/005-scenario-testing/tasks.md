# Tasks: Step 9 Scenario Test Cases

**Input**: [spec.md](spec.md)

## Phase 1: Sunny day (done 2026-11-02)

- [X] T001 Probe JAR failure semantics (`stop` / `continue`, skipped steps absent from the report) and `when` + conditional register
- [X] T002 `jar_executor.prepare_execution` / `execute_target` (one `execute` per case); `run_execution_test` built on them
- [X] T003 `scenarios.py`: workflow model (phase kind, effective `on_failure`), sunny-day catalogue, alignment of executed commands to steps, evaluation, failure routing
- [X] T004 Simulator restart per case (agent-provided control keeps `shared.state` registration); step 8 configs restored
- [X] T005 `scenario_report.py`: HTML test-case report; `<uc>_test-cases.json`
- [X] T006 Skill + agent wiring: scenario mode when the simulator overlay exists; step 9 status from case results; `step_9_error.json` lists failing cases
- [X] T007 Unit tests `tests/test_scenarios.py`
- [X] T008 E2E: `python test_step.py --agents 6,8,9 --run-id b61b977c… --transcripts … --developer-notes "…"` (2026-11-02): suite ran TC-P01 CR1/EAST and TC-P02 CR1/NORTH on a restarted simulator, report + JSON written; both FAIL for step 6 reasons — `ndsLoadStats` and `provgw status` regexes (already flagged by the step 6 validator, routed to step 6) and the MSISDN lookup never running because its `when` quoted the column (DEP-18, fixed)
- [ ] T009 Re-run E2E after DEP-18 and the step 9 → 6 retry; target: both sunny-day cases PASS. Runs on c43f64ef with notes, 2026-11-02: step 6 18 errors (self-correction stuck) -> 0/2, 5 failing steps per case; after DEP-26/G-5: step 6 15->1 and 14->1 errors (3rd round + repairs), step 8 passed; last run: all health checks pass (sar, provgw, ndsLoadStats), 9 steps per case, 1 failing step each (expression placeholder imsi=, -> DEP-28, fixed after the run; DEP-27/S8-21 fixed before it); verification run after DEP-28: LLM produced a weaker workflow (11 errors, correction stalled), 3 failing steps per case (ndsLoadStats / provgw regexes flagged by step 6, create_xml.py ExpatError → smoke-run rule refined). Results vary per generation; the pipeline's step 9 → 6 retry feeds these exact errors back (c43f64ef with notes: 1 failing step per case — step 6 `${ifcTriggerName}` and S8-16)
- [X] T013 Sunny-day robustness from run c43f64ef: S9-04, DEP-19/20/21, S8-17, S8-18, UI-3
- [X] T014 S8-16 via MockWire VFS + triggers (stateful file checks around uploads) — with 004 T055. E2E c43f64ef with notes (3 runs, 2026-11-02): step 6 0-4 errors, step 8 passed with inbox/outbox VFS + trigger, EAST resolves IMSI via MSISDN, upload flow reached; last run 13 steps per case, 2 failing (provgw regex with \s+ join, create_xml.py NameError) — both fixed after it (DEP-22 greedy relaxation, S9-06 smoke run), re-verification pending

## Phase 2: Negative cases (later — built, switched off)

- [ ] T010 Enable `include_negatives`; E2E pre-check / activity / post-check / login faults
- [ ] T011 Show negative fault output + checks in the step 9 milestone view
- [ ] T012 Decide policy when a workflow has no gating pre-check (report finding vs fail)
