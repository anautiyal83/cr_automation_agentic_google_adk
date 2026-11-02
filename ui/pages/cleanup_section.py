"""Artifact cleanup section — paginated selection UI with bulk delete."""
from __future__ import annotations

import shutil
from pathlib import Path
from uuid import UUID

from nicegui import ui

from ui.components import status_cells


STATUS_COLORS = {
    "completed": "#16A34A", "failed": "#DC2626",
    "cancelled": "#D97706", "running": "#0B5FFF", "pending": "#94A3B8",
}

PAGE_SIZE = 10


class CleanupSection:
    def __init__(self, runner=None, artifact_store=None):
        self.runner = runner
        self.artifact_store = artifact_store
        self._current_page = 1
        self._total_pages = 1
        self._all_filtered_runs: list[dict] = []
        self._build_ui()
        if runner:
            async def _safe_refresh():
                try:
                    await self._refresh()
                except RuntimeError:
                    pass
            ui.timer(0.5, _safe_refresh, once=True)

    def _build_ui(self):
        with ui.column().classes("w-full gap-4"):
            # Filter bar
            with ui.row().classes("cr-toolbar"):
                self.node_filter = ui.select(
                    {"": "All Nodes"}, value="", label="Node Type",
                    on_change=lambda _: self._go_page(1),
                ).props("outlined dense").classes("min-w-32")
                self.activity_filter = ui.select(
                    {"": "All Activities"}, value="", label="Activity",
                    on_change=lambda _: self._go_page(1),
                ).props("outlined dense").classes("min-w-32")
                ui.space()
                ui.button("Bulk Cleanup", icon="delete_sweep",
                          on_click=self._confirm_bulk_cleanup) \
                    .props("flat dense no-caps color=red-6 size=sm")
                ui.button("Refresh", icon="refresh", on_click=self._refresh) \
                    .props("flat color=primary size=sm no-caps")

            self.table = ui.table(
                columns=[
                    {"name": "uc_name", "label": "UC Name", "field": "uc_name",
                     "sortable": True, "align": "left"},
                    {"name": "status", "label": "Status", "field": "status",
                     "sortable": True, "align": "center"},
                    {"name": "started_at", "label": "Started", "field": "started_at",
                     "sortable": True, "align": "left"},
                    {"name": "disk", "label": "Disk", "field": "disk_size",
                     "align": "right"},
                    {"name": "actions", "label": "", "field": "actions", "align": "center"},
                ],
                rows=[],
                row_key="id",
            ).classes("w-full").props(
                'flat bordered separator=cell dense '
                'table-header-class="cr-bg-surface-light cr-text-dim" '
                'no-data-label="No runs to clean up" '
                'hide-pagination :rows-per-page-options="[0]" :pagination="{rowsPerPage: 0}"'
            )
            status_cells(self.table)
            self.table.add_slot('body-cell-actions', '''
                <q-td :props="props">
                    <q-btn flat dense icon="delete_outline" color="red-4" size="sm"
                           @click="$parent.$emit('delete', props.row)"
                           label="Delete" no-caps />
                </q-td>
            ''')
            self.table.on('delete', self._on_delete_click)

            # Pagination row
            with ui.row().classes("w-full items-center justify-center gap-2 py-1"):
                self.btn_prev = ui.button(icon="chevron_left",
                                          on_click=lambda: self._go_page(self._current_page - 1)) \
                    .props("flat dense round size=sm disable")
                self.page_label = ui.label("Page 1 / 1").classes("text-caption cr-text-dim")
                self.btn_next = ui.button(icon="chevron_right",
                                          on_click=lambda: self._go_page(self._current_page + 1)) \
                    .props("flat dense round size=sm disable")
                self.total_label = ui.label("").classes("text-caption cr-text-dim ml-4")

    async def _go_page(self, page: int):
        self._current_page = max(1, page)
        await self._refresh()

    async def _refresh(self):
        try:
            return await self._do_refresh()
        except RuntimeError:
            pass

    async def _do_refresh(self):
        if not self.runner or not self.runner._db:
            return
        db = self.runner._db
        try:
            runs = await db.execute(
                "SELECT r.id, r.status, r.started_at, d.uc_name "
                "FROM pipeline_runs r "
                "LEFT JOIN uc_document_sets d ON r.document_set_id = d.id "
                "ORDER BY r.started_at DESC",
                (),
            )
        except Exception:
            runs = []

        # Extract nodes and activities
        nodes = set()
        all_activities: dict[str, set] = {}
        for r in runs:
            uc = r.get("uc_name") or ""
            parts = uc.split("_", 1)
            if len(parts) == 2:
                nodes.add(parts[0])
                all_activities.setdefault(parts[0], set()).add(parts[1])
                all_activities.setdefault("", set()).add(parts[1])
            elif uc:
                nodes.add(uc)

        node_opts = {"": "All Nodes"}
        node_opts.update({n: n for n in sorted(nodes)})
        self.node_filter.options = node_opts
        self.node_filter.update()

        node_val = self.node_filter.value or ""
        relevant_activities = all_activities.get(node_val, set())
        act_opts = {"": "All Activities"}
        act_opts.update({a: a for a in sorted(relevant_activities)})
        self.activity_filter.options = act_opts
        self.activity_filter.update()

        # Apply filters
        act_val = self.activity_filter.value or ""
        if node_val:
            runs = [r for r in runs if (r.get("uc_name") or "").startswith(node_val + "_") or (r.get("uc_name") or "") == node_val]
        if act_val:
            runs = [r for r in runs if (r.get("uc_name") or "").endswith("_" + act_val)]

        # Format rows + compute disk size
        for r in runs:
            r["started_at"] = str(r.get("started_at") or "")[:19]
            r["status"] = str(r.get("status") or "").replace("PipelineStatus.", "")
            # Disk size
            run_dir = Path("data/runs") / r.get("id", "")
            if run_dir.exists():
                try:
                    size = sum(f.stat().st_size for f in run_dir.rglob("*") if f.is_file())
                    if size > 1_048_576:
                        r["disk_size"] = f"{size / 1_048_576:.1f} MB"
                    else:
                        r["disk_size"] = f"{size / 1024:.0f} KB"
                except Exception:
                    r["disk_size"] = "—"
            else:
                r["disk_size"] = "—"

        # Paginate
        self._all_filtered_runs = runs
        total = len(runs)
        self._total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        self._current_page = min(self._current_page, self._total_pages)
        start = (self._current_page - 1) * PAGE_SIZE
        page_runs = runs[start:start + PAGE_SIZE]

        self.table.rows = page_runs

        # Update pagination controls
        self.page_label.text = f"Page {self._current_page} / {self._total_pages}"
        self.total_label.text = f"({total} runs)"
        if self._current_page <= 1:
            self.btn_prev.props("disable")
        else:
            self.btn_prev.props(remove="disable")
        if self._current_page >= self._total_pages:
            self.btn_next.props("disable")
        else:
            self.btn_next.props(remove="disable")

    async def _on_delete_click(self, e):
        row = e.args
        rid = row.get("id", "")
        uc = row.get("uc_name", rid[:8])
        if not rid:
            return

        async def do_delete():
            try:
                if self.artifact_store:
                    self.artifact_store.delete_run(UUID(rid))
                if self.runner and self.runner._db:
                    await self._delete_run_from_db(rid)
                # Also remove disk artifacts
                run_dir = Path("data/runs") / rid
                if run_dir.exists():
                    shutil.rmtree(run_dir, ignore_errors=True)
                self.table.rows = [r for r in self.table.rows if r.get("id") != rid]
                ui.notify(f"Deleted {uc}", type="positive")
            except Exception as exc:
                ui.notify(f"Delete failed: {exc}", type="negative")

        with ui.dialog() as dialog:
            with ui.card().classes("p-4 cr-card-inner"):
                ui.label(f"Delete all artifacts for {uc}?").classes("text-body2 font-semibold")
                ui.label("This action cannot be undone.").classes("text-caption cr-text-error")
                with ui.row().classes("w-full justify-end gap-2 mt-2"):
                    ui.button("Cancel", on_click=dialog.close).props("flat color=grey-5 size=sm no-caps")

                    async def confirm():
                        dialog.close()
                        await do_delete()

                    ui.button("Delete", icon="delete", on_click=confirm) \
                        .props("unelevated color=red-6 size=sm no-caps")
        dialog.open()

    async def _confirm_bulk_cleanup(self):
        """Bulk delete old runs — keep last N."""
        if not self.runner or not self.runner._db:
            ui.notify("No database available", type="warning")
            return

        db = self.runner._db
        try:
            rows = await db.execute(
                "SELECT COUNT(*) as cnt FROM pipeline_runs WHERE status NOT IN "
                "('running', 'paused_milestone', 'paused_hil', "
                "'PipelineStatus.RUNNING', 'PipelineStatus.PAUSED_HIL', 'PipelineStatus.PAUSED_MILESTONE')", ()
            )
            total = rows[0]["cnt"] if rows else 0
        except Exception:
            total = 0

        dialog = ui.dialog()
        with dialog, ui.card().classes("p-4").style("min-width: 400px"):
            ui.label("Bulk Cleanup").classes("text-subtitle1 font-bold")
            ui.separator()
            ui.label(f"Found {total} completed/failed/cancelled runs.").classes("text-body2 my-2")
            keep_input = ui.number("Keep last N runs:", value=5, min=0, max=100, step=1) \
                .props("outlined dense").classes("w-full")
            with ui.row().classes("w-full justify-end gap-2 mt-3"):
                ui.button("Cancel", on_click=dialog.close).props("flat no-caps")

                async def do_cleanup():
                    keep = int(keep_input.value or 5)
                    dialog.close()
                    await self._bulk_cleanup(keep)

                ui.button("Delete Old Runs", icon="delete_sweep", on_click=do_cleanup) \
                    .props("unelevated no-caps color=negative")
        dialog.open()

    async def _bulk_cleanup(self, keep: int = 5):
        """Delete old runs, keeping the last N."""
        if not self.runner or not self.runner._db:
            return
        db = self.runner._db
        try:
            runs = await db.execute(
                "SELECT r.id FROM pipeline_runs r "
                "WHERE r.status NOT IN ('running', 'paused_milestone', 'paused_hil', "
                "'PipelineStatus.RUNNING', 'PipelineStatus.PAUSED_HIL', 'PipelineStatus.PAUSED_MILESTONE') "
                "ORDER BY r.started_at DESC", ()
            )
        except Exception:
            ui.notify("Failed to query runs", type="negative")
            return

        if len(runs) <= keep:
            ui.notify(f"Only {len(runs)} runs — nothing to clean up", type="info")
            return

        to_delete = runs[keep:]
        deleted = 0
        for run in to_delete:
            try:
                rid = run["id"]
                await self._delete_run_from_db(rid)
                run_dir = Path("data/runs") / rid
                if run_dir.exists():
                    shutil.rmtree(run_dir, ignore_errors=True)
                deleted += 1
            except Exception:
                pass

        ui.notify(f"Cleaned up {deleted} runs", type="positive")
        self._current_page = 1
        await self._refresh()

    async def _delete_run_from_db(self, run_id: str):
        """Delete a single run and its related records from the DB."""
        db = self.runner._db
        for table in ("retry_attempts", "step_results", "generated_configs", "hil_requests"):
            try:
                if table == "retry_attempts":
                    await db.execute_insert(
                        "DELETE FROM retry_attempts WHERE step_result_id IN "
                        "(SELECT id FROM step_results WHERE run_id = ?)", (run_id,))
                else:
                    await db.execute_insert(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))
            except Exception:
                pass
        await db.execute_insert("DELETE FROM pipeline_runs WHERE id = ?", (run_id,))
