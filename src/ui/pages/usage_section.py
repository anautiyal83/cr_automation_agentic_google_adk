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
                ui.label("").classes("text-subtitle1 font-bold cr-stat-value")
                ui.button("Refresh", icon="refresh", on_click=self._refresh) \
                    .props("flat color=deep-purple-6 size=sm no-caps")

            # Summary cards row
            self.summary_row = ui.row().classes("w-full gap-3 flex-wrap")

            # Per-model breakdown
            self.model_container = ui.column().classes("w-full")

            # Call log
            self.log_container = ui.column().classes("w-full")

        ui.timer(0.5, self._refresh, once=True)

    async def _refresh(self):
        from src.shared.llm import get_usage_stats
        raw = get_usage_stats()

        # Aggregate into the format the UI expects
        by_model: dict[str, dict] = {}
        total_tokens = 0
        total_prompt = 0
        total_completion = 0
        total_time = 0.0

        for entry in raw:
            m = entry.get("model", "unknown")
            t = entry.get("total_tokens", 0)
            p = entry.get("prompt_tokens", 0)
            c = entry.get("completion_tokens", 0)
            e = entry.get("elapsed_sec", 0.0)

            total_tokens += t
            total_prompt += p
            total_completion += c
            total_time += e

            md = by_model.setdefault(m, {"calls": 0, "tokens": 0, "prompt": 0, "completion": 0, "time": 0.0})
            md["calls"] += 1
            md["tokens"] += t
            md["prompt"] += p
            md["completion"] += c
            md["time"] += e

        stats = {
            "total_calls": len(raw),
            "total_tokens": total_tokens,
            "total_prompt": total_prompt,
            "total_completion": total_completion,
            "total_time": total_time,
            "by_model": by_model,
            "calls": raw,
        }

        self._render_summary(stats)
        self._render_model_breakdown(stats)
        self._render_call_log(stats)

    def _render_summary(self, stats: dict):
        self.summary_row.clear()
        with self.summary_row:
            cards = [
                ("LLM Calls", str(stats["total_calls"]), "call_made", "deep-purple-4"),
                ("Total Tokens", f"{stats['total_tokens']:,}", "token", "light-blue-4"),
                ("Prompt Tokens", f"{stats['total_prompt']:,}", "upload", "purple-3"),
                ("Completion Tokens", f"{stats['total_completion']:,}", "download", "green-4"),
                ("Total Time", f"{stats['total_time']:.1f}s", "timer", "amber-4"),
            ]
            for title, value, icon, color in cards:
                with ui.card().classes("cr-card-inner flex-1 min-w-36").style(
                    "border: 1px solid var(--cr-border)"
                ):
                    with ui.column().classes("items-center gap-1 py-3"):
                        ui.icon(icon, size="28px").classes(f"text-{color}")
                        ui.label(value).classes(f"text-h5 font-bold text-{color}")
                        ui.label(title).classes("text-caption cr-text-dim")

    def _render_model_breakdown(self, stats: dict):
        self.model_container.clear()
        by_model = stats.get("by_model", {})
        if not by_model:
            with self.model_container:
                ui.label("No LLM calls recorded yet").classes("text-body2 cr-text-dim")
            return

        with self.model_container:
            ui.label("Per-Model Breakdown").classes("text-subtitle2 font-bold cr-stat-value")
            columns = [
                {"name": "model", "label": "Model", "field": "model", "align": "left", "sortable": True},
                {"name": "calls", "label": "Calls", "field": "calls", "sortable": True},
                {"name": "tokens", "label": "Total Tokens", "field": "tokens", "sortable": True},
                {"name": "prompt", "label": "Prompt", "field": "prompt", "sortable": True},
                {"name": "completion", "label": "Completion", "field": "completion", "sortable": True},
                {"name": "time", "label": "Time (s)", "field": "time", "sortable": True},
                {"name": "avg_tokens", "label": "Avg/Call", "field": "avg_tokens", "sortable": True},
            ]
            rows = []
            for model, data in by_model.items():
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
                .props("dense flat bordered dark").classes("w-full")

    def _render_call_log(self, stats: dict):
        self.log_container.clear()
        calls = stats.get("calls", [])
        if not calls:
            return

        with self.log_container:
            ui.label(f"Call Log ({len(calls)} calls)").classes("text-subtitle2 font-bold cr-stat-value mt-2")
            columns = [
                {"name": "time", "label": "Time", "field": "time", "align": "left", "sortable": True},
                {"name": "model", "label": "Model", "field": "model", "align": "left", "sortable": True},
                {"name": "prompt_tok", "label": "Prompt", "field": "prompt_tok", "sortable": True},
                {"name": "comp_tok", "label": "Completion", "field": "comp_tok", "sortable": True},
                {"name": "total_tok", "label": "Total", "field": "total_tok", "sortable": True},
                {"name": "elapsed", "label": "Time (s)", "field": "elapsed", "sortable": True},
                {"name": "chars", "label": "Prompt Chars", "field": "chars", "sortable": True},
            ]
            rows = []
            for c in reversed(calls):
                ts = datetime.fromtimestamp(c["timestamp"], tz=timezone.utc).strftime("%H:%M:%S")
                short = c["model"].split("/")[-1] if "/" in c["model"] else c["model"]
                rows.append({
                    "time": ts,
                    "model": short,
                    "prompt_tok": f"{c['prompt_tokens']:,}",
                    "comp_tok": f"{c['completion_tokens']:,}",
                    "total_tok": f"{c['total_tokens']:,}",
                    "elapsed": f"{c['elapsed_sec']:.1f}",
                    "chars": f"{c.get('prompt_chars', c.get('prompt_tokens', 0)):,}",
                })
            ui.table(columns=columns, rows=rows, row_key="time") \
                .props("dense flat bordered dark").classes("w-full")
