"""Pipeline queue view (spec 007): every job with its status, position and estimated start.

Full panel on Live Run; compact one (in the pipeline + waiting jobs) on Dashboard and New Run.
Refreshes every few seconds. Owners remove their own waiting jobs (administrator: any); other
users' jobs show "Another user" (the administrator sees the email).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from nicegui import ui

from orchestrator.run_queue import snapshot
from ui.access import record, requires, scope
from ui.components import card_title

logger = logging.getLogger(__name__)

REFRESH_SECONDS = 4
_STATUS_LABELS = {"waiting": "Waiting", "running": "Running", "paused": "Paused", "resuming": "Resuming",
                  "completed": "Completed", "failed": "Failed", "cancelled": "Cancelled", "removed": "Removed"}


def _minutes(seconds: float | None, approx: bool = True) -> str:
    if seconds is None:
        return "—"
    if not approx:
        m, s = divmod(int(round(seconds)), 60)
        return f"{m} min {s:02d} s" if m else f"{s} s"
    m = max(1, int(round(seconds / 60)))
    return f"~{m} min"


def _clock(dt) -> str:
    return dt.astimezone().strftime("%H:%M") if dt else "—"


def job_rows(jobs: list[dict]) -> list[dict]:
    """Table rows (text) for the queue view."""
    rows = []
    for j in jobs:
        status = j["status"]
        detail = _STATUS_LABELS.get(status, status.title())
        if status in ("running", "resuming") and j["step"]:
            detail += f" — step {j['step']} / 10 {j['step_name']}"
        elif status == "paused":
            detail += f" — review of step {j['step']}" if j["step"] else " — waiting for answers"
        elif status == "failed" and j["step"]:
            detail += f" at step {j['step']}"
        if status in ("running", "resuming"):
            estimate, duration = "now", (f"{_minutes(j['est_seconds'])} left" if j["est_seconds"] else "—")
        elif status == "paused":
            estimate, duration = "now", "after the review"
        elif status == "waiting":
            start = j["est_start"]
            ahead = (start - datetime.now(timezone.utc)).total_seconds() if start else 0
            estimate = (f"after the review + {_minutes(ahead)}" if j["after_review"] and ahead >= 30
                        else "after the review" if j["after_review"] else f"~{_clock(start)}")
            duration = _minutes(j["est_seconds"])
        else:
            estimate, duration = "—", _minutes(j["duration"], approx=False) if j["duration"] else "—"
        rows.append({
            "run_id": j["run_id"], "pos": "▶" if j["position"] == 0 else (str(j["position"]) if j["position"] else "—"),
            "uc": j["uc_name"], "owner": j["owner"], "status": status, "detail": detail,
            "since": _clock(j["since"]), "estimate": estimate, "duration": duration,
            "can_remove": j["can_remove"], "error": j["error"],
        })
    return rows


class QueuePanel:
    def __init__(self, runner, compact: bool = False):
        self.runner = runner
        self.compact = compact
        self._scope = scope()
        self._build_ui()
        ui.timer(0.3, self.refresh, once=True)
        ui.timer(REFRESH_SECONDS, self.refresh)

    def _build_ui(self):
        with ui.column().classes("w-full gap-2"):
            with ui.row().classes("w-full items-center justify-between"):
                card_title("Pipeline queue", "queue")
                self.summary = ui.label("").classes("text-caption cr-text-dim")
            ui.label("One pipeline runs at a time; the other jobs wait here and start by themselves, "
                     "oldest first. Times are estimates.").classes("text-caption cr-text-dim")
            columns = [
                {"name": "pos", "label": "#", "field": "pos", "align": "center", "style": "width: 36px"},
                {"name": "uc", "label": "Use case", "field": "uc", "align": "left"},
                {"name": "owner", "label": "Started by", "field": "owner", "align": "left"},
                {"name": "status", "label": "Status", "field": "status", "align": "left"},
                {"name": "since", "label": "Since", "field": "since", "align": "left"},
                {"name": "estimate", "label": "Estimated start", "field": "estimate", "align": "left"},
                {"name": "duration", "label": "Duration", "field": "duration", "align": "left"},
            ]
            if self.compact:
                columns = [c for c in columns if c["name"] in ("pos", "uc", "owner", "status", "estimate")]
            else:
                columns.append({"name": "actions", "label": "", "field": "actions", "align": "right"})
            self.table = ui.table(columns=columns, rows=[], row_key="run_id") \
                .props("dense flat bordered").classes("w-full")
            self.table.add_slot("body-cell-status", r'''
                <q-td :props="props" style="white-space: nowrap">
                    <span :class="'cr-status cr-status-' + props.row.status">{{ props.row.detail }}</span>
                    <q-tooltip v-if="props.row.error">{{ props.row.error }}</q-tooltip>
                </q-td>''')
            if not self.compact:
                self.table.add_slot("body-cell-actions", r'''
                    <q-td :props="props">
                        <q-btn v-if="props.row.can_remove" class="cr-action" flat dense no-caps size="sm"
                               color="negative" icon="remove_circle_outline" label="Remove"
                               @click="$parent.$emit('remove', props.row)" />
                    </q-td>''')
                self.table.on("remove", self._remove)
            self.empty = ui.label("The pipeline is free — no jobs running or waiting.") \
                .classes("text-caption cr-text-dim")

    async def refresh(self):
        try:
            jobs = await snapshot(self.runner, self._scope.user)
        except RuntimeError:
            return
        except Exception as e:  # noqa: BLE001 — the view must not break the page
            logger.warning("Queue view refresh failed: %s", e)
            return
        live = [j for j in jobs if j["status"] in ("running", "paused", "resuming", "waiting")]
        shown = live if self.compact else jobs
        try:
            self.table.rows = job_rows(shown)
            self.table.update()
            in_pipeline = sum(1 for j in live if j["status"] != "waiting")
            waiting = sum(1 for j in live if j["status"] == "waiting")
            self.summary.text = (f"{in_pipeline} in the pipeline · {waiting} waiting"
                                 + ("" if self.compact else f" · {len(jobs) - len(live)} finished in 24 h"))
            self.empty.set_visibility(not shown)
        except RuntimeError:
            pass  # the page was closed

    @requires("progress", "queue.remove")
    async def _remove(self, e):
        row = e.args if isinstance(e.args, dict) else {}
        run_id = row.get("run_id", "")
        if not run_id or not await self._scope.check(self.runner._db, run_id, "queue.remove", "progress"):
            return
        if await self.runner.remove_queued(run_id):
            await record("queue.remove", target_type="run", target_id=run_id, run_id=run_id, uc_name=row.get("uc", ""))
            ui.notify(f"{row.get('uc', 'Job')} removed from the queue", type="info")
        else:
            ui.notify("This job is no longer waiting (it has started or ended)", type="warning")
        await self.refresh()


async def submit_run(runner, document_set: dict, review_milestones: bool = True, action_name: str = "run.start",
                     details: dict | None = None) -> dict | None:
    """Put a run in the queue for the page's user (New Run, Re-run): it starts now or waits.
    Notifies the user (started / position + estimated start / why it was refused) and records the
    user's action. Returns ``runner.submit``'s result, or None when refused."""
    from orchestrator.runner import QueueRefused
    access_scope = scope()
    user = access_scope.user
    uc_name = document_set.get("uc_name", "")
    try:
        result = await runner.submit(document_set, review_milestones=review_milestones,
                                     started_by=user.id if user else "cli")
    except QueueRefused as e:
        ui.notify(str(e), type="warning", timeout=8000)
        await record(action_name, outcome="refused", target_type="use_case", target_id=uc_name, uc_name=uc_name,
                     details={"reason": str(e)})
        return None
    run_id = result["run_id"]
    queued = result["status"] == "queued"
    await record(action_name, target_type="run", target_id=run_id, run_id=run_id, uc_name=uc_name,
                 details={**(details or {}), "queued": queued, "position": result["position"]})
    if queued:
        jobs = await snapshot(runner, user)
        mine = next((j for j in jobs if j["run_id"] == run_id), None)
        when = ""
        if mine and mine["est_start"]:
            when = ", estimated start after the current review" if mine["after_review"] \
                else f", estimated start ~{_clock(mine['est_start'])}"
        ui.notify(f"{uc_name} is waiting in the queue — position {result['position']}{when}",
                  type="info", timeout=8000)
    else:
        ui.notify(f"Pipeline started for {uc_name}", type="positive")
    return result
