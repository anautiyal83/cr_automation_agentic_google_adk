# Tasks: Pipeline Run Queue

**Input**: [spec.md](spec.md), [plan.md](plan.md)

Implement step by step; each step is reviewed (and committed) before the next.

## Step 0: Decisions
- [X] T000 Queue instead of parallel runs; users see the queue to estimate their wait (2026-11-04)
- [ ] T000b Open questions Q1–Q4 (spec)

## Step 1: Queue in the runner
- [ ] T001 Schema: `queued_at`, `review_milestones`, `work_seconds`; `step_results.started_at`
- [ ] T002 `runner.submit()` / `_launch()` / `_start_next()` (lock) / `remove_queued()` / `resume_queue()`; no cancel-on-start
- [ ] T003 `tests/test_run_queue.py` (FIFO, auto-start, removal, restart, single start)

## Step 2: Timing and estimates
- [ ] T004 Step start times, milestone wait times, `work_seconds` at run end
- [ ] T005 `orchestrator/run_queue.py`: estimates + `snapshot(viewer)`; tests

## Step 3: UI
- [ ] T006 Start paths use `submit()` (New Run, Re-run, CLI) with "queued — position, estimate" messages; remove `may_start_run`
- [ ] T007 Queue panel (Live Run; compact on Dashboard and New Run): positions, use case, owner (You / Another user / admin: email), status, since, estimates, Remove; live refresh
- [ ] T008 Status `queued` in Run History, Dashboard, Output Documents, Live Run
- [ ] T009 Trail: run.queued, run.dequeued, queue.remove

## Step 4: Check + docs
- [ ] T010 E2E on the preview (spec plan "Testing")
- [ ] T011 README / CLAUDE.md; spec 006 note (one pipeline at a time → queue)
