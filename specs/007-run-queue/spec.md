# Feature Specification: Pipeline Run Queue

**Feature Branch**: `007-run-queue` (work continues on `feature/skill-packages`)

**Created**: 2026-11-04

**Status**: Draft — spec and plan for review; implementation step by step after approval

**Input**: "The pipeline can only run one job at a time … our pipeline should support parallel runs" →
after the parallel-run proposal: "ok lets not run pipeline parallely but make a queue based system for
now. If one pipeline is running then other waits in a queue, but user should be able to see the queue so
that he can estimate the time."

## Problem

The pipeline runs one job at a time (milestone state is process-wide). Today a start while another
user's run is active is **refused** (spec 006); before that, it silently **cancelled** the active run. Users
have to keep retrying and cannot tell when the pipeline will be free.

## Decisions (2026-11-04, user)

| # | Question | Decision |
|---|----------|----------|
| Q-D1 | Parallel runs or a queue | **Queue**: one pipeline at a time, the other starts wait in a queue (parallel runs later) |
| Q-D2 | Queue visibility | Users **see the queue** and can **estimate when their run starts** |

## Behaviour

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

A **Pipeline queue** panel (on Live Run, and summarised on the Dashboard and New Run) lists:

| Position | Use case | Started by | Status | Waiting since | Estimated start | Estimated duration |
|----------|----------|------------|--------|---------------|-----------------|--------------------|
| ▶ running | MRF_ANNOUNCEMENT_LOADING | You | Step 6 / 10 (or "Waiting for review — step 5") | 10:02 | — | ~4 min left |
| 1 | PGW_RDS_1058_… | Another user | queued | 10:05 | ~10:08 | ~6 min |
| 2 | MRF_ANNOUNCEMENT_LOADING | You | queued | 10:07 | ~10:14 | ~4 min |

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
- **FR-004** The queue (running + queued runs) is visible to every logged-in user with position, use case,
  status, waiting since, estimated start and duration; the owner identity of other users' runs only to the
  administrator.
- **FR-005** Estimates from working time (milestone waits excluded) of past completed runs (median of the
  last 5 of the use case, fallbacks), labelled as estimates.
- **FR-006** Owners remove their own queued runs; the administrator any; removal is recorded.
- **FR-007** Queue events go to the activity trail: run.queued, run.dequeued (system: started from the
  queue), queue.remove (user), with positions.
- **FR-008** Run History, Dashboard and Output Documents show `queued` as a status; a queued run has no
  outputs yet.

## Out of Scope (this iteration)
- Parallel runs (see the parallel-run proposal; the per-run state work can come later).
- Priorities or reordering of the queue (beyond removal).
- E-mail / push notification when a queued run starts.

## Open Questions

| # | Question | Proposed default |
|---|----------|------------------|
| Q1 | A run waiting for a milestone review holds the queue indefinitely. Auto-cancel after a time? | No auto-cancel; the queue shows how long it has been waiting; the admin can cancel it |
| Q2 | May the administrator move a queued run to the front? | No (FIFO only) in this iteration |
| Q3 | Limit queued runs per user? | No limit |
| Q4 | Same use case queued more than once? | Allowed (they run one after another) |

## Success Criteria
- **SC-001** With a run active, a second start is queued (not refused, nothing cancelled); when the first
  ends the second starts by itself.
- **SC-002** Every user sees the queue with positions and estimates; other users' runs are anonymous to
  non-admins.
- **SC-003** Removing a queued run moves the ones behind it up; non-owners cannot remove it (refused,
  recorded).
- **SC-004** After a restart the queue continues.
- **SC-005** Estimates for a use case with history are within the range of its past working times.
