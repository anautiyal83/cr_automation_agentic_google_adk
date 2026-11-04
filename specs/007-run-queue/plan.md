# Implementation Plan: Pipeline Run Queue (phase 1) → Parallel Runs (phase 2)

**Spec**: [spec.md](spec.md) | **Date**: 2026-11-04

## Target Design (phase 1; phase 2 changes the number of slots)

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

queue view ◄── RunQueue.snapshot(viewer)  →  [{position, run_id, uc, owner?, status (waiting | running |
                                               paused | completed | failed | cancelled | removed), step, since,
                                               est_start, est_duration, waiting_review}]
               estimates from run_timing (working time = step time, milestone waits excluded)
```

## Components

| # | Component | File | Notes |
|---|-----------|------|-------|
| C1 | Schema | `storage/db.py` | `pipeline_runs`: `queued_at`, `review_milestones`, `work_seconds` (working time at the end); `step_results.started_at` filled; index on (status, queued_at) |
| C2 | Runner queue + dispatcher | `orchestrator/runner.py` | `submit()` always queues (status `queued`) and calls `dispatch()`; `dispatch()` (under one asyncio lock) starts the oldest queued runs while `running < slots` (phase 1: `slots = 1`) via `_launch(run_id, …)` (today's start path); called after every run end and at server start (`resume_queue`); `remove_queued(run_id)`; `_cancel_active_runs()` no longer called on start (only the same run's state is cleared on its end) |
| C3 | Timing | `orchestrator/runner.py`, `pipeline_agent.py` | step start time from `step_started` events; milestone wait = `milestone_reached` → decision; `work_seconds` = duration − waits, stored at run end |
| C4 | Estimates | `orchestrator/run_queue.py` (new) | `estimate_duration(uc)` median `work_seconds` of the last 5 completed runs of the UC → all UCs → 10 min; `snapshot(viewer)` with positions, time left of the running run (or "waiting for review"), estimated starts |
| C5 | Queue panel | `ui/pages/queue_panel.py` (new) | Table on Live Run (and compact versions on Dashboard + New Run): status pill per job (Waiting / Running — step / Paused — milestone / Completed / Failed — step / Cancelled / Removed), position, use case, started by (You / Another user / email for admin), since, est. start, est. duration or actual duration; recently finished jobs (24 h, max 10; removed runs keep status `removed`); Remove for own queued runs (admin: all) — `action()` + `@requires("progress", …)` + ownership check; refresh timer (5 s) |
| C6 | Start paths | `ui/app.py`, `history_section.py`, `main.py`, `test_run.py` | call `submit()`; message "Started" or "Queued — position n, est. start ~hh:mm"; `may_start_run` refusal removed; Live Run follows the run when it starts |
| C7 | Status `queued` | Run History, Dashboard, Output Documents, Live Run restore | pill colour, counts, no outputs yet; Live Run shows "Queued — position n" for a queued run |
| C8 | Trail | `core/audit.py` users | run.queued (user), run.dequeued (system), queue.remove (user, refused when not owner) |
| C10 | Checkpoints | `storage/db.py`, `orchestrator/pipeline_agent.py`, `orchestrator/runner.py` | table `run_checkpoints(run_id PK, agent_index, step, state_json, requirements_json, saved_at, resume_attempts)`; PipelineAgent saves one after every agent (and on a milestone pause: `paused_at`); `_launch(resume=True)` builds the session from the checkpoint, restores `requirements_store`, sets `resume_index` (live-chain rule: index of step 8's agent when the interrupted step is 9) and, for a pause, `resume_milestone`; the agent loop starts at `resume_index` |
| C11 | Resume on start | `runner.resume_queue()` | interrupted runs → status `resuming` at the front of the queue (dispatcher prefers `resuming`), attempts counted; no checkpoint / too many attempts → failed with reason; trail run.interrupted / run.resumed |
| C9 | Tests | `tests/test_run_queue.py` | FIFO; second submit queued; end of run starts next; removal re-numbers; non-owner removal refused; resume after restart; estimates (median, fallbacks, waiting-for-review); one start even when two runs end together |

## Phase 2 — Parallel runs (picked from the queue)

| # | Component | File | Notes |
|---|-----------|------|-------|
| P1 | Slots | `config/pipeline_config.yaml`, Settings | `execution.max_parallel_runs` (1 = phase 1); dispatcher uses it; per-user running limit (Q5) |
| P2 | Use-case rule | dispatcher | skip a queued run whose use case is running; next eligible run starts |
| P3 | Per-run state | `shared/state.py`, `requirements_agent.py`, `pipeline_agent.py`, `runner.py`, `progress_section.py` | milestone events / decisions / waiting per run (`runs[run_id]`), `respond_milestone(run_id, …)` reaches one run; `_monitor_milestones` per run |
| P4 | Run context | `shared/llm.py`, `core/logging_config.py` | `contextvars` for run id / UC / step (LLM usage, flow log prefix) set in each run's task |
| P5 | Ports | `skills/network_simulator_setup/simulator_runner.py` | reservation set + lock in `allocate_ports`, released on stop |
| P6 | Shared files | `tools/command_samples.py` | atomic write under a lock |
| P7 | Live Run | `progress_section.py`, queue panel | list of running runs, choose which to follow; estimates with N slots |
| P8 | Tests + E2E | `tests/test_parallel_runs.py`; PGW + MRF together (SC-201/202) |

## Key Decisions

| ID | Decision | Why |
|----|----------|-----|
| K1 | Queue in the DB (`pipeline_runs.status = 'queued'`), not in memory | Survives restarts (FR-003); one source for UI and runner |
| K2 | Starting the next run is done by the runner when a run ends | Works with nobody logged in; no polling loop |
| K3 | Estimates from working time, not wall time | Review waits vary per person; working time is what the pipeline needs |
| K4 | Other users' runs anonymous in the queue | Keeps spec 006 D3 (users see only their runs) while showing what they wait for |
| K5 | One dispatcher owns all starts, every start goes through the queue — from phase 1 on | Phase 2 then only raises the slot count and adds isolation; no second start path to retire |

## Testing
- Unit: C9.
- E2E (preview, DB copy): admin starts run A (review on, pauses at a milestone); Ana starts B (queued, #1,
  estimate), Bob starts C (#2); Bob's queue shows A and B anonymous; Ana removes nothing, Bob removes C →
  gone; admin approves A to the end → B starts by itself; restart with a queued run → it starts.

## Risks
- A run paused for review blocks everyone (Q1) — mitigated by visibility and admin cancel.
- Long-running steps make the "time left" of the running run coarse until enough history exists.
