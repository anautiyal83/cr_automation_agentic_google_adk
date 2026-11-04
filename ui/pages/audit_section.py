"""Audit Trail (spec 006 D8) — the administrator's read-only view of every recorded action.

Filters: date range, user, action, outcome, run id or use case, free text. Newest first, paged.
A row opens its details. CSV export of the filtered entries (the export itself is recorded).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from nicegui import ui

from core.audit import OUTCOMES
from ui.access import requires_admin
from ui.components import card_title

PAGE_SIZE = 50
_ALL = "All"


class AuditSection:
    def __init__(self, audit, user):
        self.audit = audit
        self.user = user
        self._page = 0
        self._total = 0
        self._build_ui()
        ui.timer(0.3, self.refresh, once=True)

    def _build_ui(self):
        with ui.column().classes("w-full gap-2"):
            card_title("Activity trail", "fact_check")
            with ui.row().classes("w-full items-end gap-2"):
                week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
                self.since = ui.input("From", value=week_ago).props("outlined dense type=date").classes("w-36")
                self.until = ui.input("To").props("outlined dense type=date").classes("w-36")
                self.email = ui.select([_ALL], value=_ALL, label="User").props("outlined dense").classes("w-56")
                self.action = ui.select([_ALL], value=_ALL, label="Action").props("outlined dense").classes("w-56")
                self.outcome = ui.select([_ALL, *OUTCOMES], value=_ALL, label="Outcome") \
                    .props("outlined dense").classes("w-32")
                self.run_or_uc = ui.input("Run id or use case").props("outlined dense clearable").classes("w-64")
                self.text = ui.input("Search").props("outlined dense clearable").classes("w-48")
                ui.button("Search", icon="search", on_click=self._search) \
                    .props("unelevated color=primary size=sm no-caps")
                ui.button("Reset", on_click=self._reset).props("flat size=sm no-caps color=primary")
                ui.element("div").classes("flex-1")
                ui.button("Export CSV", icon="download", on_click=self._export) \
                    .props("flat size=sm no-caps color=primary")
            for field in (self.run_or_uc, self.text):
                field.on("keydown.enter", self._search)

            self.table = ui.table(
                columns=[
                    {"name": "at", "label": "When (UTC)", "field": "at", "align": "left", "style": "white-space: nowrap"},
                    {"name": "email", "label": "User", "field": "email", "align": "left", "style": "white-space: nowrap"},
                    {"name": "action", "label": "Action", "field": "action", "align": "left", "style": "white-space: nowrap"},
                    {"name": "outcome", "label": "Outcome", "field": "outcome", "align": "left", "style": "white-space: nowrap"},
                    {"name": "target", "label": "Target", "field": "target", "align": "left"},
                    {"name": "run", "label": "Run / use case", "field": "run", "align": "left"},
                    {"name": "summary", "label": "Details", "field": "summary", "align": "left",
                     "style": "max-width: 360px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis"},
                ],
                rows=[], row_key="id",
            ).props("dense flat bordered").classes("w-full")
            self.table.add_slot("body-cell-outcome", r'''
                <q-td :props="props" style="white-space: nowrap">
                    <span :class="'cr-status cr-status-' + ({done: 'completed', refused: 'cancelled',
                                  failed: 'failed'}[props.value] || 'pending')">{{ props.value }}</span>
                </q-td>''')
            self.table.on("rowClick", lambda e: self._details(e.args[1] if isinstance(e.args, list) else e.args))
            with ui.row().classes("w-full items-center justify-center gap-2"):
                self.prev_btn = ui.button(icon="chevron_left", on_click=lambda: self._go(-1)) \
                    .props("flat dense round size=sm")
                self.page_label = ui.label("").classes("text-caption cr-text-dim")
                self.next_btn = ui.button(icon="chevron_right", on_click=lambda: self._go(1)) \
                    .props("flat dense round size=sm")

    # ------------------------------------------------------------------ data
    def _filters(self) -> dict:
        def day(value: str, end: bool) -> str:
            if not value:
                return ""
            try:
                d = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                return ""
            return (d + timedelta(days=1) if end else d).isoformat(timespec="milliseconds")

        run_or_uc = (self.run_or_uc.value or "").strip()
        is_run = len(run_or_uc) >= 8 and all(c in "0123456789abcdef-" for c in run_or_uc.lower())
        return {
            "since": day(self.since.value, False), "until": day(self.until.value, True),
            "email": "" if self.email.value in (None, _ALL) else self.email.value,
            "action": "" if self.action.value in (None, _ALL) else self.action.value,
            "outcome": "" if self.outcome.value in (None, _ALL) else self.outcome.value,
            "run_id": run_or_uc if is_run else "", "uc_name": "" if is_run else run_or_uc,
            "text": (self.text.value or "").strip(),
        }

    async def refresh(self):
        try:
            emails = await self.audit.distinct("email")
            actions = await self.audit.distinct("action")
        except RuntimeError:
            return
        self.email.options = [_ALL, *emails]
        self.action.options = [_ALL, *actions]
        self.email.update()
        self.action.update()
        await self._load()

    async def _load(self):
        filters = self._filters()
        if filters["run_id"] and len(filters["run_id"]) < 36:  # a short run id: prefix search
            filters["text"], filters["run_id"] = filters["run_id"], ""
        self._total = await self.audit.count(**filters)
        rows = await self.audit.query(**filters, limit=PAGE_SIZE, offset=self._page * PAGE_SIZE)
        self.table.rows = [{
            "id": r["id"], "at": str(r["at"])[:19].replace("T", " "), "email": r["email"],
            "action": r["action"], "outcome": r["outcome"],
            "target": f"{r['target_type']}: {r['target_id']}" if r["target_id"] else (r["target_type"] or "—"),
            "run": " · ".join(x for x in (str(r["run_id"])[:8], r["uc_name"]) if x) or "—",
            "summary": json.dumps(r["details"], default=str) if r["details"] else "",
            "_full": r,
        } for r in rows]
        self.table.update()
        pages = max(1, (self._total + PAGE_SIZE - 1) // PAGE_SIZE)
        self.page_label.text = f"Page {self._page + 1} / {pages} ({self._total} entries)"
        self.prev_btn.set_enabled(self._page > 0)
        self.next_btn.set_enabled(self._page + 1 < pages)

    async def _search(self):
        self._page = 0
        await self._load()

    async def _reset(self):
        self.since.value = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        self.until.value = ""
        for select in (self.email, self.action, self.outcome):
            select.value = _ALL
        self.run_or_uc.value = self.text.value = ""
        await self._search()

    async def _go(self, step: int):
        self._page = max(0, self._page + step)
        await self._load()

    def _details(self, row: dict):
        entry = (row or {}).get("_full") or {}
        with ui.dialog() as dialog, ui.card().classes("p-4 gap-2").style("min-width: 520px; max-width: 760px"):
            ui.label(f"{entry.get('action', '')} — {entry.get('outcome', '')}").classes("text-subtitle1 font-semibold")
            for label, key in (("When (UTC)", "at"), ("User", "email"), ("Role", "role"),
                               ("Target", "target_type"), ("Target id", "target_id"), ("Run", "run_id"),
                               ("Use case", "uc_name")):
                if entry.get(key):
                    with ui.row().classes("w-full gap-2 no-wrap"):
                        ui.label(label).classes("text-caption cr-text-dim").style("width: 90px")
                        ui.label(str(entry[key])).classes("text-caption font-mono")
            ui.code(json.dumps(entry.get("details") or {}, indent=2, default=str), language="json") \
                .classes("w-full").style("max-height: 360px; overflow: auto")
            ui.button("Close", on_click=dialog.close).props("flat no-caps").classes("self-end")
        dialog.open()

    @requires_admin("audit.export")
    async def _export(self):
        filters = self._filters()
        text = await self.audit.export_csv(**filters)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        ui.download(text.encode("utf-8"), f"netconfig-audit-{stamp}.csv")
        await self.audit.record("audit.export", user=self.user, details={
            "filters": {k: v for k, v in filters.items() if v}, "rows": max(0, text.count("\n") - 1)})
