"""Artifact Cache page (spec 008): steps a use case already passed, reused by its next runs.

Admin only — the cache is shared by every user's runs (spec 006, 008 U-D3). Clearing asks first.
"""
from __future__ import annotations

import shutil

from nicegui import ui

from ui.access import record, requires_admin
from ui.components import card_title, confirm


class CacheSection:
    def __init__(self):
        self._build_ui()
        ui.timer(0.5, self.refresh, once=True)

    def _build_ui(self):
        with ui.column().classes("w-full gap-2"):
            with ui.row().classes("w-full items-center justify-between"):
                card_title("Cached use cases", "cached")
                with ui.row().classes("gap-2"):
                    ui.button("Refresh", icon="refresh", on_click=self.refresh) \
                        .props("flat size=sm no-caps color=primary")
                    ui.button("Clear All Cache", icon="delete_sweep", on_click=self._clear_all) \
                        .props("flat size=sm no-caps color=red-6")
            ui.label("Cached artifacts skip LLM generation for previously completed steps. "
                     "Clear a use case's cache to force regeneration.").classes("text-caption cr-text-dim")
            self.table = ui.table(
                columns=[
                    {"name": "uc", "label": "UC Name", "field": "uc_name", "align": "left", "sortable": True},
                    {"name": "steps", "label": "Cached Steps", "field": "cached_steps", "align": "center"},
                    {"name": "detail", "label": "Cached Artifacts", "field": "cached_detail", "align": "left"},
                    {"name": "size", "label": "Size (KB)", "field": "size_kb", "align": "right"},
                    {"name": "actions", "label": "", "field": "actions", "align": "center"},
                ],
                rows=[], row_key="uc_name",
            ).classes("w-full").props(
                'flat bordered dense separator=cell '
                'no-data-label="No cached artifacts" '
                'hide-pagination :rows-per-page-options="[0]" :pagination="{rowsPerPage: 0}"'
            )
            self.table.add_slot('body-cell-actions', '''
                <q-td :props="props">
                    <q-btn flat dense icon="delete_outline" color="red-4" size="sm"
                           @click="$parent.$emit('clear_cache', props.row)"
                           label="Clear" no-caps />
                </q-td>
            ''')
            self.table.on('clear_cache', self._clear_one)

    async def refresh(self):
        try:
            from storage.artifact_cache import ArtifactCache
            ucs = ArtifactCache().list_cached_ucs()
            for uc in ucs:
                agents = uc.get("agents", {})
                detail = ", ".join(a.replace('_', ' ').title() for a, s in agents.items() if s == "passed")
                uc["cached_detail"] = detail or "None"
            self.table.rows = ucs
            self.table.update()
        except RuntimeError:
            pass  # the page was closed
        except Exception as e:  # noqa: BLE001 — shown to the admin
            ui.notify(f"Cache refresh failed: {e}", type="negative")

    @requires_admin("cache.clear")
    async def _clear_one(self, e):
        uc = (e.args or {}).get("uc_name", "") if isinstance(e.args, dict) else ""
        if not uc:
            return

        async def clear():
            try:
                from storage.artifact_cache import ArtifactCache
                ArtifactCache().clear_cache(uc)
                ui.notify(f"Cache cleared for {uc}", type="positive")
                await record("cache.clear", target_type="use_case", target_id=uc, uc_name=uc)
                await self.refresh()
            except Exception as ex:  # noqa: BLE001
                ui.notify(f"Failed: {ex}", type="negative")

        confirm(f"Clear the cache of {uc}?",
                "Its next run generates every step again (LLM calls). This cannot be undone.",
                clear, ok_label="Clear", ok_icon="delete_outline")

    @requires_admin("cache.clear_all")
    async def _clear_all(self):
        async def clear():
            try:
                from storage.artifact_cache import ArtifactCache
                cache = ArtifactCache()
                if cache._cache_dir.exists():
                    shutil.rmtree(cache._cache_dir, ignore_errors=True)
                    cache._cache_dir.mkdir(parents=True, exist_ok=True)
                ui.notify("All cache cleared", type="positive")
                await record("cache.clear_all")
                await self.refresh()
            except Exception as e:  # noqa: BLE001
                ui.notify(f"Failed: {e}", type="negative")

        confirm("Clear the whole artifact cache?",
                "Every use case's next run generates every step again (LLM calls). This cannot be undone.",
                clear, ok_label="Clear all", ok_icon="delete_sweep")
