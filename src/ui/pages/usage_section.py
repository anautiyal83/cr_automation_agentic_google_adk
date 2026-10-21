"""LLM Usage stats page — shows token consumption per model and per call."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from nicegui import ui

logger = logging.getLogger(__name__)


class UsageSection:
    def __init__(self):
        self._build_ui()

    def _build_ui(self):
        with ui.column().classes("w-full gap-4"):
            # Header row with refresh
            with ui.row().classes("w-full items-center justify-between"):
                ui.label("LLM Usage Statistics").classes("text-subtitle1 font-bold")
                ui.button("Refresh", icon="refresh", on_click=self._refresh) \
                    .props("flat color=deep-purple-6 size=sm no-caps")

            # Summary cards row
            self.summary_row = ui.row().classes("w-full gap-3")

            # Per-model breakdown table
            self.model_container = ui.column().classes("w-full")

            # Call log table
            self.log_container = ui.column().classes("w-full")

        # Initial load
        ui.timer(0.5, self._refresh, once=True)

    async def _refresh(self):
        """Refresh usage stats from the LLM tracking store."""
        try:
            from src.nodes.cr_import import import_cr_module
            agents_mod = import_cr_module("src.agents")
            stats = agents_mod.get_usage_stats()
        except Exception:
            stats = {"total_calls": 0, "total_tokens": 0, "total_prompt": 0,
                     "total_completion": 0, "total_time": 0, "by_model": {}, "calls": []}

        self._render_summary(stats)
        self._render_model_breakdown(stats)
        self._render_call_log(stats)

    def _render_summary(self, stats: dict):
        self.summary_row.clear()
        with self.summary_row:
            cards = [
                ("LLM Calls", str(stats["total_calls"]), "call_made", "#6c8aff"),
                ("Total Tokens", f"{stats['total_tokens']:,}", "token", "#38bdf8"),
                ("Prompt Tokens", f"{stats['total_prompt']:,}", "upload", "#a78bfa"),
                ("Completion Tokens", f"{stats['total_completion']:,}", "download", "#22c55e"),
                ("Total Time", f"{stats['total_time']:.1f}s", "timer", "#f59e0b"),
            ]
            for title, value, icon, color in cards:
                with ui.card().classes("flex-1 min-w-32").style(
                    f"background: rgba(30,35,50,0.6); border: 1px solid #2e3348"
                ):
                    with ui.column().classes("items-center gap-0 py-2"):
                        ui.icon(icon, size="24px").style(f"color: {color}")
                        ui.label(value).classes("text-h6 font-bold").style(f"color: {color}")
                        ui.label(title).classes("text-caption cr-text-dim")

    def _render_model_breakdown(self, stats: dict):
        self.model_container.clear()
        by_model = stats.get("by_model", {})
        if not by_model:
            return

        with self.model_container:
            ui.label("Per-Model Breakdown").classes("text-subtitle2 font-bold mt-2")
            columns = [
                {"name": "model", "label": "Model", "field": "model", "align": "left", "sortable": True},
                {"name": "calls", "label": "Calls", "field": "calls", "sortable": True},
                {"name": "tokens", "label": "Total Tokens", "field": "tokens", "sortable": True},
                {"name": "prompt", "label": "Prompt", "field": "prompt", "sortable": True},
                {"name": "completion", "label": "Completion", "field": "completion", "sortable": True},
                {"name": "time", "label": "Time (s)", "field": "time", "sortable": True},
                {"name": "avg_tokens", "label": "Avg Tokens/Call", "field": "avg_tokens", "sortable": True},
            ]
            rows = []
            for model, data in by_model.items():
                # Shorten model name for display
                short = model.split("/")[-1] if "/" in model else model
                avg = data["tokens"] // data["calls"] if data["calls"] > 0 else 0
                rows.append({
                    "model": short,
                    "calls": data["calls"],
                    "tokens": f"{data['tokens']:,}",
                    "prompt": f"{data['prompt']:,}",
                    "completion": f"{data['completion']:,}",
                    "time": f"{data['time']:.1f}",
                    "avg_tokens": f"{avg:,}",
                })
            ui.table(columns=columns, rows=rows, row_key="model") \
                .props("dense flat bordered").classes("w-full")

    def _render_call_log(self, stats: dict):
        self.log_container.clear()
        calls = stats.get("calls", [])
        if not calls:
            return

        with self.log_container:
            ui.label(f"Call Log ({len(calls)} calls)").classes("text-subtitle2 font-bold mt-2")
            columns = [
                {"name": "time", "label": "Time", "field": "time", "align": "left", "sortable": True},
                {"name": "model", "label": "Model", "field": "model", "align": "left", "sortable": True},
                {"name": "prompt_tok", "label": "Prompt", "field": "prompt_tok", "sortable": True},
                {"name": "comp_tok", "label": "Completion", "field": "comp_tok", "sortable": True},
                {"name": "total_tok", "label": "Total", "field": "total_tok", "sortable": True},
                {"name": "elapsed", "label": "Time (s)", "field": "elapsed", "sortable": True},
                {"name": "prompt_chars", "label": "Prompt Chars", "field": "prompt_chars", "sortable": True},
            ]
            rows = []
            for c in reversed(calls):  # newest first
                ts = datetime.fromtimestamp(c["timestamp"], tz=timezone.utc).strftime("%H:%M:%S")
                short_model = c["model"].split("/")[-1] if "/" in c["model"] else c["model"]
                rows.append({
                    "time": ts,
                    "model": short_model,
                    "prompt_tok": f"{c['prompt_tokens']:,}",
                    "comp_tok": f"{c['completion_tokens']:,}",
                    "total_tok": f"{c['total_tokens']:,}",
                    "elapsed": f"{c['elapsed_sec']:.1f}",
                    "prompt_chars": f"{c['prompt_chars']:,}",
                })
            ui.table(columns=columns, rows=rows, row_key="time") \
                .props("dense flat bordered").classes("w-full") \
                .style("max-height: 400px; overflow-y: auto")
