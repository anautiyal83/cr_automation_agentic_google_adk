# Tasks: Pipeline Run Queue (phase 1) → Parallel Runs (phase 2)

**Input**: [spec.md](spec.md), [plan.md](plan.md)

Implement step by step; each step is reviewed (and committed) before the next.

## Step 0: Decisions
- [X] T000 Queue instead of parallel runs; users see the queue to estimate their wait (2026-11-04)
- [X] T000c Queue shows each job's status incl. recently finished (2026-11-04)
- [X] T000a Two phases: phase 1 queue, phase 2 parallel runs picked from the queue only (2026-11-04)
- [X] T000b Q1 no auto-cancel, Q2 no reordering, Q3 max 3 waiting jobs per user (configurable), Q4 one job per use case, Q5 phase 2 deferred

# Phase 1 — Queue

## Step 1: Queue in the runner — done 2026-11-04
- [X] T001 Schema: `pipeline_runs.queued_at`, `review_milestones`, `work_seconds` (+ index on status, queued_at); `execution.max_queued_per_user` (default 3, config + `core.config.max_queued_per_user()`)
- [X] T002 `PipelineRunner.submit()` (always through the queue; refuses — `QueueRefused` — when the use case already has a waiting/running/paused job or the user has `max_queued_per_user` waiting), `dispatch()` (one lock; starts the oldest queued run when nothing runs; a run that cannot start is marked failed and the next one goes), `_launch()` (the former start path), `queue_position()`, `remove_queued()` (status `removed`), `resume_queue()` (interrupted runs failed — to become "resumed", see restart recovery — then dispatch); every run end dispatches the next; `start()` = `submit()`; no DB (CLI) → runs start directly (CLI runs are outside the queue: separate process)
- [X] T003 `tests/test_run_queue.py` (7): FIFO + automatic next start, failed run frees the pipeline, review choice kept, one job per use case, per-user limit, removal, restart, single start under concurrent dispatch, unstartable run skipped, no-DB path

## Step 1b: Restart recovery
- [X] T020 `run_checkpoints` table + `orchestrator/checkpoints.py`; PipelineAgent saves a checkpoint before every agent runs (`agent`) and when a review pause begins (`milestone`); deleted at run end
- [X] T021 `_launch(resume=…)` / `_resume_state`: session from the checkpoint, requirements restored, start index (step 9 → step 8's agent; a step 8 review re-runs step 8 — its simulator is gone), other reviews come back as the same pause (agent not re-run)
- [X] T022 `resume_queue()` at server start (`app.on_startup`): interrupted runs → `resuming`, dispatched before waiting runs; no checkpoint / run folder missing / more than 2 resumes at the same point → failed with the reason; Live Run's old "Server restarted — pipeline lost" marking removed
- [X] T023 Tests (`tests/test_run_queue.py`: resume ahead of the queue, attempts, checkpoint round trip, resume positions). E2E (DB copy, PGW): run killed 5 s into step 6 → a new process resumed it at step 6 (steps 1–5 not re-run) and it went on through steps 7, 8, 9 to the step 9 review. Run 761db0de (PGW, reviews on): killed at the step 5 review → new process: the same review back after 1 s with 0 LLM calls (agent not re-run), continued through 6–8; killed 5 s into step 9 → new process: step 8 ran again first (simulators restarted, sanity 2/2), then step 9 and on to RUN COMPLETED. Step 9 itself failed 0/2 on PGW content (ndsLoadStats "Rejected are not 0", `exit` prompt) — checked against a fresh run without restart (Q-008)
- [X] T024 Live Run keeps showing the user's latest run unless it was cancelled/removed (`_show_latest_run`), also when it is not executing in this process (Q-D6)
- [X] T025 No system entries in the trail (Q-D7, spec 006 D11)

## Step 2: Timing and estimates — done 2026-11-04
- [X] T004 Review pause time per run (`paused_since` / `paused_total` in `_active_runs`), `runner.work_seconds()` (run time without review pauses), stored in `pipeline_runs.work_seconds` at run end
- [X] T005 `orchestrator/run_queue.py`: `estimate_duration` (median working time of the UC's last 5 completed runs → all UCs → 10 min) + `snapshot(runner, user)`; test `test_queue_snapshot_statuses_positions_estimates` (estimates, order, positions, owners, after-review, Remove rights, row texts)

## Step 3: UI — done 2026-11-04
- [X] T006 Start paths use `submit()` (New Run, Re-run via `ui/pages/queue_panel.submit_run`; CLI: `start()` = `submit()`) with "waiting in the queue — position n, estimated start" / refusal messages; `may_start_run` removed
- [X] T007 Queue panel (Live Run; compact on Dashboard and New Run): status per job (Waiting, Running — step n/10, Paused — milestone, Completed, Failed — step, Cancelled, Removed), positions, use case, owner (You / Another user / admin: email), since, estimates / durations, recently finished jobs (24 h, max 10), Remove; live refresh
- [X] T008 Status `queued` (shown "Waiting") in Run History (pill, filter Waiting/Removed, sorted by queue time), Dashboard (donut "Waiting", removed counted with cancelled), Live Run ("Waiting in the queue — position n" / "Resuming after a server restart", switches to live on `run_started`, cleared on remove); Delete / Cleanup Old Runs skip waiting and resuming jobs
- [X] T009 Trail (user actions only, Q-D7): `run.start` / `run.rerun` with `queued` + `position` (or `refused` + reason), `queue.remove`; no system run.queued / run.dequeued entries

## Step 4: Check + docs
- [~] T010 E2E on the preview (DB copy, dispatch disabled, one fake running job + 2 waiting): ana / bob / admin see the queue on Dashboard, New Run, Live Run; owners "You" / "Another user" (admin: emails); Remove only on own waiting jobs (admin: all); ana removed hers → notified, positions and estimates moved up, Live Run back to idle. Open: a real queued run starting after another ends
- [X] T011 README (Run Queue & Restart Recovery), CLAUDE.md (spec 007 section), spec 006 "one pipeline at a time" marked superseded

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
