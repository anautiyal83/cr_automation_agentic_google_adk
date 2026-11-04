# Bug Register: Run Queue + Restart Recovery (spec 007)

**Feature**: [spec.md](spec.md) | **Tasks**: [tasks.md](tasks.md) | **Created**: 2026-11-04

**Severity**: Critical = wrong result, silent pass, or infinite loop · High = step fails or crashes · Medium = wrong display or artifact · Low = cosmetic or tooling.

**Status**: Fixed · Open.

## Summary

| ID | Area | Title | Severity | Status | Found by | Commit |
|----|------|-------|----------|--------|----------|--------|
| Q-001 | Recovery | Loop stopped after the first agent (`break` outside the `except` body) | Critical | Fixed | E2E restart test | 442e998 |
| Q-002 | Recovery | `resume_queue` failed waiting jobs at server start | High | Fixed | Unit test | 41dbee8 |
| Q-003 | Queue | Dispatch `SELECT r.*, d.*` overwrote the run `id` with the document set's | Critical | Fixed | Unit test | 41dbee8 |
| Q-004 | Queue | A run task cancelled outright stayed `running` in the DB and blocked the queue | High | Fixed | Review | 41dbee8 |
| Q-005 | UI | Dashboard counted waiting jobs as "Running" | Medium | Fixed | Preview E2E | (this commit) |
| Q-006 | UI | Run History / Dashboard sorted waiting jobs last (no `started_at` yet) | Low | Fixed | Preview E2E | (this commit) |
| Q-007 | UI | Delete / Cleanup Old Runs could delete a waiting or resuming job | High | Fixed | Review | (this commit) |

## Details

**Q-001** — `pipeline_agent.py`: the `break` meant for the failure branch sat one level out, so every run ended after
step 1. Moved into the `except` body.

**Q-002** — `resume_queue()` treated every live status as interrupted, including `queued`. Now `AND status != 'queued'`:
waiting jobs simply stay in the queue.

**Q-003** — both tables have an `id` column; the join's later `id` won. Explicit columns, `r.id AS run_id`.

**Q-004** — a task cancelled without reaching `_persist_run_status` left the row live, so `dispatch()` found the
pipeline "busy" forever. The run's `finally` block now marks a still-live row cancelled, deletes the checkpoint and
dispatches the next job.

**Q-005** — `running = total - completed - failed - cancelled` swept `queued` and `removed` into Running. Waiting is its
own slice; removed counts with cancelled.

**Q-006** — `ORDER BY started_at DESC` puts NULLs last. Both lists order by `COALESCE(started_at, queued_at)` and show
the queue time as the start until the job starts.

**Q-007** — the bulk delete excluded only running/paused statuses and the per-row Delete checked nothing; deleting a
waiting job's row left the queue consistent but discarded the user's job silently. Both skip `queued`/`resuming`; the
per-row Delete says "remove it from the queue / cancel it first".
