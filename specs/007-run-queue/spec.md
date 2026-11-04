# Feature Specification: Pipeline Run Queue (phase 1) and Parallel Runs (phase 2)

**Feature Branch**: `007-run-queue` (work continues on `feature/skill-packages`)

**Created**: 2026-11-04

**Status**: Draft — spec and plan for review; implementation step by step after approval

**Input**: "The pipeline can only run one job at a time … our pipeline should support parallel runs" →
after the parallel-run proposal: "ok lets not run pipeline parallely but make a queue based system for
now. If one pipeline is running then other waits in a queue, but user should be able to see the queue so
that he can estimate the time." Then: "lets make it phase wise for now. phase 1 queue based phase 2
supports parallel runs, picked from queue only" and "queue should show job status also, like waiting,
paused, running, completed etc".

## Problem

The pipeline runs one job at a time (milestone state is process-wide). Today a start while another
user's run is active is **refused** (spec 006); before that, it silently **cancelled** the active run. Users
have to keep retrying and cannot tell when the pipeline will be free.

## Decisions (2026-11-04, user)

| # | Question | Decision |
|---|----------|----------|
| Q-D1 | Parallel runs or a queue | **Queue**: one pipeline at a time, the other starts wait in a queue (parallel runs later) |
| Q-D2 | Queue visibility | Users **see the queue** and can **estimate when their run starts** |
| Q-D4 | Job status in the queue | The queue shows each job's status — waiting, running, paused, completed, failed, cancelled, removed — including recently finished jobs |
| Q-D3 | Phases | **Phase 1**: the queue, one run at a time. **Phase 2**: several runs in parallel, **always taken from the queue** (the queue stays the only way in) |

## Phases

| Phase | What runs | How a run starts |
|-------|-----------|------------------|
| **1 — Queue** | One pipeline run at a time | Every start goes into the queue; the dispatcher starts the oldest queued run when the pipeline is free |
| **2 — Parallel runs** | Up to `execution.max_parallel_runs` runs at once (a setting) | Same queue; the dispatcher starts queued runs while a slot is free |

Phase 1 is built so that phase 2 only raises the number of slots: one **dispatcher** owns starting runs
(phase 1: 1 slot), every start path goes through the queue, and the queue view and estimates already
handle "N running + queued". Phase 2 adds what concurrent runs need (below).

## Phase 1 — Behaviour

- **Start** (New Run, Run History → Re-run): when the pipeline is free the run starts at once (as today);
  otherwise it is stored with status **queued** and the user is told its position and estimated start.
- **First in, first out**: when the active run ends (completed, failed, cancelled) the oldest queued run
  starts automatically — no one has to be logged in.
- **The active run holds the pipeline while it waits at a milestone** (the review is part of the run).
- **Leaving the queue**: the owner removes their own queued run; the administrator removes any.
- **Server restart**: queued runs stay queued and the queue resumes when the server starts (the run that
  was executing is marked failed, as today).
- Runs from the command line (`main.py`, `test_run.py`) use the same queue.

## The queue view

A **Pipeline queue** panel (on Live Run, and summarised on the Dashboard and New Run) lists every job
with its **status**:

| Status | Meaning | Shown with |
|--------|---------|------------|
| **Waiting** | In the queue (status `queued`) | position, waiting since, estimated start and duration |
| **Running** | The pipeline is executing it | current step (e.g. "Step 6 / 10 — Generate Execution Config"), started at, estimated time left |
| **Paused** | Waiting at a milestone for its owner's review or for step 1 answers | the step and milestone, paused since; time left "after the review + ~X min" |
| **Completed** | Finished successfully | ended at, duration |
| **Failed** | Ended with an error | ended at, failed step |
| **Cancelled** | Stopped / cancelled by a user | ended at, by whom (admin) |
| **Removed** | Taken out of the queue before it started | removed at |

Order: running/paused first, then waiting (by position), then the **recently finished** jobs (completed,
failed, cancelled, removed — last 24 h, at most 10) so users see how the queue moved and how long jobs took.

| # | Use case | Started by | Status | Since | Estimated start | Duration |
|---|----------|------------|--------|-------|-----------------|----------|
| ▶ | MRF_ANNOUNCEMENT_LOADING | You | Running — step 6 / 10 | 10:02 | — | ~4 min left |
| 1 | PGW_RDS_1058_… | Another user | Waiting | 10:05 | ~10:08 | ~6 min |
| 2 | MRF_ANNOUNCEMENT_LOADING | You | Waiting | 10:07 | ~10:14 | ~4 min |
| — | PGW_RDS_1058_… | Another user | Completed | 09:51 | — | 6 min 12 s |
| — | MRF_ANNOUNCEMENT_LOADING | You | Failed — step 9 | 09:40 | — | 4 min 40 s |

- Statuses use the same coloured pills as Run History; status changes appear without reloading.
- Every logged-in user sees the whole queue (positions, use cases, status, times) so they can estimate
  their wait; **who started another user's run is shown only to the administrator** ("Another user"
  otherwise, consistent with spec 006 D3). Only one's own queued runs can be removed (admin: any).
- The panel refreshes live (every few seconds and on queue changes).

### Estimate
- **Duration of a run** = median *working time* of the last 5 completed runs of the same use case
  (fallback: median over all use cases; fallback: 10 min). Working time excludes time spent waiting at
  milestones for a reviewer (steps record start and end; milestone waits are measured).
- **Running run, time left** = estimated duration − its working time so far (at least 1 min).
- **Estimated start of queued run n** = now + time left of the running run + the durations of the runs
  ahead of it.
- When the running run is **waiting for review**, its time left is unknown: the panel says "waiting for
  review by its owner since 10:12" and the estimates are shown as "after the review + ~X min".
- Estimates are labelled as estimates (~) and recomputed on every refresh.

## User Scenarios

### US1 — Start while busy (P1)
1. Given a run is active, when I start a run, then it is queued and I see "Queued — position 2,
   estimated start ~10:14".
2. When the active run ends, the next queued run starts automatically and its owner's Live Run (if
   open) follows it.

### US2 — See the queue (P1)
1. Any user sees the running run and the queued runs with positions, use cases, status and estimates;
   other users' runs show "Another user" (admin sees the email).
2. The estimates change as the running run advances.

### US3 — Leave the queue (P1)
1. I remove my queued run; the runs behind it move up.
2. I cannot remove another user's queued run; the administrator can.

### US4 — Restart (P2)
1. After a server restart, queued runs are still queued and the oldest one starts.

## Requirements

- **FR-001** At most one pipeline run executes at a time; further starts are queued (no refusal, no
  cancelling of the active run).
- **FR-002** FIFO by queue time; the next run starts automatically when the active run ends.
- **FR-003** Queued runs persist in the DB (status `queued`, everything needed to start them) and survive
  a server restart.
- **FR-004** The queue is visible to every logged-in user: running, paused and waiting jobs plus the recently
  finished ones (last 24 h, max 10), each with its **status** (Waiting, Running, Paused, Completed, Failed,
  Cancelled, Removed), position, use case, step, times, estimated start and duration; the owner identity of
  other users' runs only to the administrator.
- **FR-005** Estimates from working time (milestone waits excluded) of past completed runs (median of the
  last 5 of the use case, fallbacks), labelled as estimates.
- **FR-006** Owners remove their own queued runs; the administrator any; removal is recorded.
- **FR-007** Queue events go to the activity trail: run.queued, run.dequeued (system: started from the
  queue), queue.remove (user), with positions.
- **FR-008** Run History, Dashboard and Output Documents show `queued` as a status; a queued run has no
  outputs yet.

## Phase 2 — Parallel runs (picked from the queue)

- **Slots**: `execution.max_parallel_runs` (Settings, default 1 = phase 1 behaviour); the dispatcher starts
  queued runs, oldest first, while fewer than that many are running.
- **One run per use case at a time**: a queued run whose use case is already running is skipped until that
  run ends (the next queued run of another use case may start) — the per-use-case files (artifact cache,
  saved requirements and answers) are not shared by two live runs.
- **Isolation needed before more than one slot** (found in the code, 2026-11-04):
  - milestone signalling per run, not per step (`shared.state` milestone dicts → one state object per run);
    a milestone decision reaches exactly one run;
  - no process-wide "current run": LLM usage and flow-log tagging per run (`contextvars`);
  - simulator ports reserved under a process-wide lock (step 8 and the per-case restarts of step 9);
  - the shared command-sample library written atomically under a lock;
  - stopping or cancelling a run touches only that run's state.
- **Live Run** lists the user's running runs (admin: all) and follows the chosen one; the queue view shows
  every running run with its step and time left; estimates use the number of slots (a queued run starts
  when the earliest of the running runs ends).
- Optional per-user limit of runs running at the same time (Q5).

### Phase 2 requirements
- **FR-201** At most `max_parallel_runs` runs execute; every run starts from the queue (FIFO, use-case rule).
- **FR-202** Two runs never share milestone state, LLM-usage attribution, simulator ports or per-use-case files.
- **FR-203** Estimates and the queue view account for several running runs.

### Phase 2 success criteria
- **SC-201** With 2 slots, PGW and MRF runs submitted together run at the same time end to end (steps 1–10),
  each with its own milestones, usage and simulator; a third run waits in the queue and starts when a slot
  frees.
- **SC-202** A milestone decision on one run never moves the other.

## Out of Scope
- Priorities or reordering of the queue (beyond removal).
- E-mail / push notification when a queued run starts.

## Open Questions

| # | Question | Proposed default |
|---|----------|------------------|
| Q1 | A run waiting for a milestone review holds the queue indefinitely. Auto-cancel after a time? | No auto-cancel; the queue shows how long it has been waiting; the admin can cancel it |
| Q2 | May the administrator move a queued run to the front? | No (FIFO only) in this iteration |
| Q3 | Limit queued runs per user? | No limit |
| Q4 | Same use case queued more than once? | Allowed (they run one after another) |
| Q5 | Phase 2: default number of parallel runs; per-user limit? | Default 2 slots once phase 2 ships (Settings); per-user limit 1 running run (admin unlimited) |

## Success Criteria (phase 1)
- **SC-001** With a run active, a second start is queued (not refused, nothing cancelled); when the first
  ends the second starts by itself.
- **SC-002** Every user sees the queue with positions, statuses (waiting, running, paused at a milestone,
  completed, failed, cancelled, removed) and estimates, updated live; other users' runs are anonymous to
  non-admins.
- **SC-003** Removing a queued run moves the ones behind it up; non-owners cannot remove it (refused,
  recorded).
- **SC-004** After a restart the queue continues.
- **SC-005** Estimates for a use case with history are within the range of its past working times.
