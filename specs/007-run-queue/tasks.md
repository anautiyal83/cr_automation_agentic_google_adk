# Tasks: Pipeline Run Queue (phase 1) → Parallel Runs (phase 2)

**Input**: [spec.md](spec.md), [plan.md](plan.md)

Implement step by step; each step is reviewed (and committed) before the next.

## Step 0: Decisions
- [X] T000 Queue instead of parallel runs; users see the queue to estimate their wait (2026-11-04)
- [X] T000c Queue shows each job's status incl. recently finished (2026-11-04)
- [X] T000a Two phases: phase 1 queue, phase 2 parallel runs picked from the queue only (2026-11-04)
- [X] T000b Q1 no auto-cancel, Q2 no reordering, Q3 max 3 waiting jobs per user (configurable), Q4 one job per use case, Q5 phase 2 deferred

# Phase 1 — Queue

## Step 1: Queue in the runner
- [ ] T001 Schema: `queued_at`, `review_milestones`, `work_seconds`; `step_results.started_at`
- [ ] T002 `runner.submit()` (always queues; refuses past `max_queued_per_user` or when the use case already has a job) / `dispatch()` (lock, `slots = 1`) / `_launch()` / `remove_queued()` / `resume_queue()`; no cancel-on-start
- [ ] T003 `tests/test_run_queue.py` (FIFO, auto-start, removal, restart, single start)

## Step 2: Timing and estimates
- [ ] T004 Step start times, milestone wait times, `work_seconds` at run end
- [ ] T005 `orchestrator/run_queue.py`: estimates + `snapshot(viewer)`; tests

## Step 3: UI
- [ ] T006 Start paths use `submit()` (New Run, Re-run, CLI) with "queued — position, estimate" messages; remove `may_start_run`
- [ ] T007 Queue panel (Live Run; compact on Dashboard and New Run): status per job (Waiting, Running — step n/10, Paused — milestone, Completed, Failed — step, Cancelled, Removed), positions, use case, owner (You / Another user / admin: email), since, estimates / durations, recently finished jobs (24 h, max 10), Remove; live refresh
- [ ] T008 Status `queued` in Run History, Dashboard, Output Documents, Live Run
- [ ] T009 Trail: run.queued, run.dequeued, queue.remove

## Step 4: Check + docs
- [ ] T010 E2E on the preview (spec plan "Testing")
- [ ] T011 README / CLAUDE.md; spec 006 note (one pipeline at a time → queue)

# Phase 2 — Parallel runs (picked from the queue)

## Step 5: Isolation
- [ ] T012 Per-run milestone state (`shared.state.runs[run_id]`), `respond_milestone(run_id, …)`, per-run monitor
- [ ] T013 `contextvars` run context for LLM usage and the flow log
- [ ] T014 Port reservation for simulators; atomic sample-library writes; stop/cancel touch one run only
- [ ] T015 `tests/test_parallel_runs.py` (no crossed milestones, ports, usage)

## Step 6: Slots
- [ ] T016 `execution.max_parallel_runs` (Settings), dispatcher with slots, one run per use case, per-user limit
- [ ] T017 Live Run: several running runs (choose one); queue view and estimates with N slots

## Step 7: Check + docs
- [ ] T018 E2E: PGW + MRF together end to end, third run queued (SC-201/202)
- [ ] T019 README / CLAUDE.md
