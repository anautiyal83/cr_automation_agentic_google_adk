# Implementation Plan: Pipeline Run Queue

**Spec**: [spec.md](spec.md) | **Date**: 2026-11-04

## Target Design

```
UI start / Re-run / CLI ──► PipelineRunner.submit(document_set, review_milestones, started_by)
                               │ pipeline busy?
                               ├─ no  → start now (existing start path)             status running
                               └─ yes → INSERT pipeline_runs status 'queued',        status queued
                                        queued_at, review_milestones (+ doc set row)
                                        trail run.queued (position)
run ends (completed / failed / cancelled) ──► runner._start_next()
                               │ oldest queued run (queued_at) → existing start path with its run id
                               └─ trail run.dequeued (system)
server start ──► runner.resume_queue()  (after orphaned 'running' runs are marked failed)

queue view ◄── RunQueue.snapshot(viewer)  →  [{position, run_id, uc, owner?, status, since,
                                               est_start, est_duration, waiting_review}]
               estimates from run_timing (working time = step time, milestone waits excluded)
```

## Components

| # | Component | File | Notes |
|---|-----------|------|-------|
| C1 | Schema | `storage/db.py` | `pipeline_runs`: `queued_at`, `review_milestones`, `work_seconds` (working time at the end); `step_results.started_at` filled; index on (status, queued_at) |
| C2 | Runner queue | `orchestrator/runner.py` | `submit()` (start or queue); existing `start()` becomes `_launch(run_id, …)` reused for queued runs; `_start_next()` after every run end (one asyncio lock so two ends cannot start two runs); `remove_queued(run_id)`; `resume_queue()` at server start; `_cancel_active_runs()` no longer called on start (only the same run's state is cleared on its end) |
| C3 | Timing | `orchestrator/runner.py`, `pipeline_agent.py` | step start time from `step_started` events; milestone wait = `milestone_reached` → decision; `work_seconds` = duration − waits, stored at run end |
| C4 | Estimates | `orchestrator/run_queue.py` (new) | `estimate_duration(uc)` median `work_seconds` of the last 5 completed runs of the UC → all UCs → 10 min; `snapshot(viewer)` with positions, time left of the running run (or "waiting for review"), estimated starts |
| C5 | Queue panel | `ui/pages/queue_panel.py` (new) | Table on Live Run (and compact versions on Dashboard + New Run): position, use case, started by (You / Another user / email for admin), status, waiting since, est. start, est. duration; Remove for own queued runs (admin: all) — `action()` + `@requires("progress", …)` + ownership check; refresh timer (5 s) |
| C6 | Start paths | `ui/app.py`, `history_section.py`, `main.py`, `test_run.py` | call `submit()`; message "Started" or "Queued — position n, est. start ~hh:mm"; `may_start_run` refusal removed; Live Run follows the run when it starts |
| C7 | Status `queued` | Run History, Dashboard, Output Documents, Live Run restore | pill colour, counts, no outputs yet; Live Run shows "Queued — position n" for a queued run |
| C8 | Trail | `core/audit.py` users | run.queued (user), run.dequeued (system), queue.remove (user, refused when not owner) |
| C9 | Tests | `tests/test_run_queue.py` | FIFO; second submit queued; end of run starts next; removal re-numbers; non-owner removal refused; resume after restart; estimates (median, fallbacks, waiting-for-review); one start even when two runs end together |

## Key Decisions

| ID | Decision | Why |
|----|----------|-----|
| K1 | Queue in the DB (`pipeline_runs.status = 'queued'`), not in memory | Survives restarts (FR-003); one source for UI and runner |
| K2 | Starting the next run is done by the runner when a run ends | Works with nobody logged in; no polling loop |
| K3 | Estimates from working time, not wall time | Review waits vary per person; working time is what the pipeline needs |
| K4 | Other users' runs anonymous in the queue | Keeps spec 006 D3 (users see only their runs) while showing what they wait for |

## Testing
- Unit: C9.
- E2E (preview, DB copy): admin starts run A (review on, pauses at a milestone); Ana starts B (queued, #1,
  estimate), Bob starts C (#2); Bob's queue shows A and B anonymous; Ana removes nothing, Bob removes C →
  gone; admin approves A to the end → B starts by itself; restart with a queued run → it starts.

## Risks
- A run paused for review blocks everyone (Q1) — mitigated by visibility and admin cancel.
- Long-running steps make the "time left" of the running run coarse until enough history exists.
