"""The run queue as users see it (spec 007): jobs, statuses, positions and estimates.

``snapshot`` lists the job in the pipeline (running / paused / resuming), the waiting jobs in order
and the jobs that finished in the last 24 h (at most 10). Estimates use *working time* — run
time without review pauses — of past completed runs of the same use case (median of the last 5;
fallbacks: all use cases, 10 min). A paused run's time left is unknown: the waiting jobs then
start "after the review + …".
"""
from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone

DEFAULT_DURATION = 600.0
RECENT_HOURS = 24
RECENT_MAX = 10
MIN_LEFT = 60.0

STEP_NAMES = {1: "DOC Parser", 2: "DOC Analyzer", 3: "CIQ Config Generation", 4: "Generate CIQ JSON Config",
              5: "QA-Validation", 6: "Generate Execution Config", 7: "QA-MOP", 8: "Simulator Setup",
              9: "QA-Execution", 10: "Final Report"}
_ACTIVE = ("running", "paused_milestone", "paused_hil", "resuming",
           "PipelineStatus.RUNNING", "PipelineStatus.PAUSED_MILESTONE", "PipelineStatus.PAUSED_HIL")
_FINISHED = ("completed", "failed", "cancelled", "removed")


async def estimate_duration(db, uc_name: str) -> float:
    """Expected working seconds of a run of ``uc_name``."""
    def median(rows):
        values = [float(r["s"]) for r in rows if r["s"]]
        return statistics.median(values) if values else None
    rows = await db.execute(
        "SELECT COALESCE(r.work_seconds, r.duration_seconds) AS s FROM pipeline_runs r "
        "JOIN uc_document_sets d ON d.id = r.document_set_id WHERE d.uc_name = ? AND r.status = 'completed' "
        "AND COALESCE(r.work_seconds, r.duration_seconds) > 0 ORDER BY r.completed_at DESC LIMIT 5", (uc_name,))
    value = median(rows)
    if value is None:
        rows = await db.execute(
            "SELECT COALESCE(work_seconds, duration_seconds) AS s FROM pipeline_runs WHERE status = 'completed' "
            "AND COALESCE(work_seconds, duration_seconds) > 0 ORDER BY completed_at DESC LIMIT 20")
        value = median(rows)
    return value or DEFAULT_DURATION


def _status(db_status: str, live: dict | None) -> str:
    s = str(db_status or "").replace("PipelineStatus.", "").lower()
    if live and live.get("status") in ("paused_milestone", "paused_hil"):
        return "paused"
    return {"queued": "waiting", "paused_milestone": "paused", "paused_hil": "paused"}.get(s, s)


def _owner(row: dict, user) -> str:
    if user is not None and user.is_admin:
        return "command line" if row["started_by"] == "cli" else (row.get("email") or "—")
    return "You" if user is not None and row["started_by"] == user.id else "Another user"


def _ts(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


async def snapshot(runner, user) -> list[dict]:
    """Jobs for the queue view, in display order. Each: ``run_id, uc_name, owner, mine, status
    (waiting | running | paused | resuming | completed | failed | cancelled | removed), position,
    step, step_name, since, est_start, after_review, est_seconds, duration, error, can_remove``."""
    db = runner._db
    if db is None:
        return []
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(hours=RECENT_HOURS)).isoformat()
    active_sql = ", ".join(f"'{s}'" for s in _ACTIVE)
    rows = await db.execute(
        "SELECT r.id, r.status, r.started_by, r.started_at, r.queued_at, r.completed_at, r.duration_seconds, "
        "r.current_step, r.error_summary, d.uc_name, u.email FROM pipeline_runs r "
        "LEFT JOIN uc_document_sets d ON d.id = r.document_set_id LEFT JOIN users u ON u.id = r.started_by "
        f"WHERE r.status IN ({active_sql}, 'queued') "
        "OR (r.status IN ('completed', 'failed', 'cancelled', 'removed') AND COALESCE(r.completed_at, r.started_at) >= ?)",
        (recent,))
    active = [r for r in rows if r["status"] in _ACTIVE]
    waiting = sorted((r for r in rows if r["status"] == "queued"), key=lambda r: str(r["queued_at"] or ""))
    finished = sorted((r for r in rows if r["status"] in _FINISHED),
                      key=lambda r: str(r["completed_at"] or r["started_at"] or ""), reverse=True)[:RECENT_MAX]

    jobs: list[dict] = []
    cursor = now          # when the pipeline is expected to be free
    after_review = False  # a paused run holds the pipeline for an unknown time
    for r in sorted(active, key=lambda r: 0 if r["status"] != "resuming" else 1):
        live = runner._active_runs.get(r["id"])
        status = _status(r["status"], live)
        estimate = await estimate_duration(db, r["uc_name"] or "")
        step = (live or {}).get("milestone_step") if status == "paused" else None
        step = step or (live or {}).get("current_step") or r["current_step"] or 0
        left = None
        if status == "paused":
            after_review = True
        else:
            worked = runner.work_seconds(r["id"]) if live else 0.0
            left = max(MIN_LEFT, estimate - worked)
            cursor = cursor + timedelta(seconds=left)
        since = (live or {}).get("paused_at") if status == "paused" else r["started_at"]
        jobs.append(_job(r, user, status, position=0, step=step, since=since, est_seconds=left))
    for position, r in enumerate(waiting, 1):
        estimate = await estimate_duration(db, r["uc_name"] or "")
        job = _job(r, user, "waiting", position=position, since=r["queued_at"], est_seconds=estimate)
        job["est_start"] = cursor
        job["after_review"] = after_review
        jobs.append(job)
        cursor = cursor + timedelta(seconds=estimate)
    for r in finished:
        job = _job(r, user, _status(r["status"], None), position=None, since=r["completed_at"] or r["started_at"],
                   est_seconds=None, step=r["current_step"] or 0)
        job["duration"] = r["duration_seconds"] if r["status"] != "removed" else None
        job["error"] = (r["error_summary"] or "")[:160]
        jobs.append(job)
    return jobs


def _job(r: dict, user, status: str, position, since, est_seconds, step: int = 0) -> dict:
    mine = user is not None and r["started_by"] == user.id
    return {
        "run_id": r["id"], "uc_name": r["uc_name"] or "—", "owner": _owner(r, user), "mine": mine,
        "status": status, "position": position, "step": int(step or 0),
        "step_name": STEP_NAMES.get(int(step or 0), ""), "since": _ts(since), "est_start": None,
        "after_review": False, "est_seconds": est_seconds, "duration": None, "error": "",
        "can_remove": status == "waiting" and user is not None and (mine or user.is_admin),
    }
