"""LLM Usage stats page — shows token consumption and cost per UC, step, and model."""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone

from nicegui import ui

from ui.access import action, requires, scope

from ui.components import card_title, stat_card

logger = logging.getLogger(__name__)

# Friendly step names for display
_STEP_LABELS = {
    "requirements_gatherer": "Requirements Gathering",
    "ciq_validation_json_gen": "CIQ Validation & JSON Gen",
    "ciq_validation_json_qa": "CIQ QA",
    "network_config_gen": "Network Config Gen",
    "network_simulator_setup": "Simulator Setup",
    "network_config_mop_qa": "MOP QA",
    "network_config_exec_qa": "Execution QA",
    "report_generator": "Report Generation",
}


class UsageSection:
    def __init__(self):
        self._scope = scope()  # the admin sees every run's usage, others their own runs'
        self._build_ui()

    def _build_ui(self):
        with ui.column().classes("w-full gap-4"):
            with ui.row().classes("w-full items-center justify-end"):
                with ui.row().classes("gap-2"):
                    ui.button("Refresh", icon="refresh", on_click=self._refresh) \
                        .props("flat color=primary size=sm no-caps")
                    action(ui.button("Clear", icon="delete_outline", on_click=self._clear) \
                        .props("flat color=red-4 size=sm no-caps"), "usage")

            # Summary cards
            self.summary_row = ui.element("div").classes("cr-stat-grid")

            # Per-UC breakdown
            self.uc_container = ui.column().classes("w-full")

            # Per-step breakdown
            self.step_container = ui.column().classes("w-full")

            # Per-model breakdown
            self.model_container = ui.column().classes("w-full")

            # Call log
            self.log_container = ui.column().classes("w-full")

        ui.timer(0.5, self._refresh, once=True)

    @requires("usage", "usage.clear")
    async def _clear(self):
        from shared.llm import clear_usage_stats
        await clear_usage_stats(owner=self._scope.owner)
        ui.notify("Usage stats cleared", type="info")
        await self._refresh()

    async def _refresh(self):
        from shared.llm import load_usage_stats
        raw = await load_usage_stats(owner=self._scope.owner)

        # Aggregate totals
        by_model: dict[str, dict] = {}
        by_uc: dict[str, dict] = {}
        by_step: dict[str, dict] = {}
        total_tokens = 0
        total_prompt = 0
        total_completion = 0
        total_time = 0.0
        total_cost = 0.0

        for entry in raw:
            m = entry.get("model", "unknown")
            t = entry.get("total_tokens", 0)
            p = entry.get("prompt_tokens", 0)
            c = entry.get("completion_tokens", 0)
            e = entry.get("elapsed_sec", 0.0)
            cost = entry.get("cost_usd", 0.0)
            uc = entry.get("uc_name", "") or "Unknown"
            step = entry.get("step", "") or "unknown"

            total_tokens += t
            total_prompt += p
            total_completion += c
            total_time += e
            total_cost += cost

            # By model
            md = by_model.setdefault(m, _new_bucket())
            _add_to_bucket(md, t, p, c, e, cost)

            # By UC
            ud = by_uc.setdefault(uc, _new_bucket())
            _add_to_bucket(ud, t, p, c, e, cost)

            # By step
            sd = by_step.setdefault(step, _new_bucket())
            _add_to_bucket(sd, t, p, c, e, cost)

        stats = {
            "total_calls": len(raw),
            "total_tokens": total_tokens,
            "total_prompt": total_prompt,
            "total_completion": total_completion,
            "total_time": total_time,
            "total_cost": total_cost,
            "by_model": by_model,
            "by_uc": by_uc,
            "by_step": by_step,
            "calls": raw,
        }

        self._render_summary(stats)
        self._render_uc_breakdown(stats)
        self._render_step_breakdown(stats)
        self._render_model_breakdown(stats)
        self._render_call_log(stats)

    def _render_summary(self, stats: dict):
        self.summary_row.clear()
        with self.summary_row:
            cards = [
                ("LLM Calls", str(stats["total_calls"]), "call_made", "#0B5FFF"),
                ("Total Tokens", f"{stats['total_tokens']:,}", "token", "#0891B2"),
                ("Est. Cost", f"${stats['total_cost']:.4f}", "attach_money", "#16A34A"),
                ("Prompt Tokens", f"{stats['total_prompt']:,}", "upload", "#8B5CF6"),
                ("Completion Tokens", f"{stats['total_completion']:,}", "download", "#0D9488"),
                ("Total Time", f"{stats['total_time']:.1f}s", "timer", "#D97706"),
            ]
            for title, value, icon, color in cards:
                stat_card(title, value, icon, color)

    def _render_uc_breakdown(self, stats: dict):
        self.uc_container.clear()
        by_uc = stats.get("by_uc", {})
        if not by_uc or (len(by_uc) == 1 and "Unknown" in by_uc):
            return

        with self.uc_container:
            card_title("Per Use Case", "inventory_2")
            rows = []
            for uc, data in sorted(by_uc.items(), key=lambda x: x[1]["cost"], reverse=True):
                if uc == "Unknown":
                    continue
                avg = data["tokens"] // data["calls"] if data["calls"] > 0 else 0
                rows.append({
                    "uc_name": uc,
                    "calls": data["calls"],
                    "tokens": f"{data['tokens']:,}",
                    "prompt": f"{data['prompt']:,}",
                    "completion": f"{data['completion']:,}",
                    "cost": f"${data['cost']:.4f}",
                    "time": f"{data['time']:.1f}s",
                    "avg_tokens": f"{avg:,}",
                })
            if rows:
                ui.table(
                    columns=[
                        {"name": "uc_name", "label": "Use Case", "field": "uc_name", "align": "left", "sortable": True},
                        {"name": "calls", "label": "Calls", "field": "calls", "sortable": True},
                        {"name": "tokens", "label": "Total Tokens", "field": "tokens", "sortable": True},
                        {"name": "cost", "label": "Est. Cost", "field": "cost", "sortable": True},
                        {"name": "prompt", "label": "Prompt", "field": "prompt", "sortable": True},
                        {"name": "completion", "label": "Completion", "field": "completion", "sortable": True},
                        {"name": "time", "label": "Time", "field": "time", "sortable": True},
                        {"name": "avg_tokens", "label": "Avg/Call", "field": "avg_tokens", "sortable": True},
                    ],
                    rows=rows, row_key="uc_name",
                ).props("dense flat bordered").classes("w-full")

    def _render_step_breakdown(self, stats: dict):
        self.step_container.clear()
        by_step = stats.get("by_step", {})
        if not by_step or (len(by_step) == 1 and "unknown" in by_step):
            return

        with self.step_container:
            card_title("Per Pipeline Step", "account_tree")
            rows = []
            for step, data in sorted(by_step.items(), key=lambda x: x[1]["cost"], reverse=True):
                label = _STEP_LABELS.get(step, step.replace("_", " ").title())
                rows.append({
                    "step": label,
                    "calls": data["calls"],
                    "tokens": f"{data['tokens']:,}",
                    "cost": f"${data['cost']:.4f}",
                    "prompt": f"{data['prompt']:,}",
                    "completion": f"{data['completion']:,}",
                    "time": f"{data['time']:.1f}s",
                })
            if rows:
                ui.table(
                    columns=[
                        {"name": "step", "label": "Step", "field": "step", "align": "left", "sortable": True},
                        {"name": "calls", "label": "Calls", "field": "calls", "sortable": True},
                        {"name": "tokens", "label": "Total Tokens", "field": "tokens", "sortable": True},
                        {"name": "cost", "label": "Est. Cost", "field": "cost", "sortable": True},
                        {"name": "prompt", "label": "Prompt", "field": "prompt", "sortable": True},
                        {"name": "completion", "label": "Completion", "field": "completion", "sortable": True},
                        {"name": "time", "label": "Time", "field": "time", "sortable": True},
                    ],
                    rows=rows, row_key="step",
                ).props("dense flat bordered").classes("w-full")

    def _render_model_breakdown(self, stats: dict):
        self.model_container.clear()
        by_model = stats.get("by_model", {})
        if not by_model:
            with self.model_container:
                ui.label("No LLM calls recorded yet").classes("text-body2 cr-text-dim")
            return

        with self.model_container:
            card_title("Per Model", "smart_toy")
            rows = []
            for model, data in sorted(by_model.items(), key=lambda x: x[1]["cost"], reverse=True):
                short = model.split("/")[-1] if "/" in model else model
                avg = data["tokens"] // data["calls"] if data["calls"] > 0 else 0
                rows.append({
                    "model": short,
                    "calls": data["calls"],
                    "tokens": f"{data['tokens']:,}",
                    "cost": f"${data['cost']:.4f}",
                    "prompt": f"{data['prompt']:,}",
                    "completion": f"{data['completion']:,}",
                    "time": f"{data['time']:.1f}s",
                    "avg_tokens": f"{avg:,}",
                })
            ui.table(
                columns=[
                    {"name": "model", "label": "Model", "field": "model", "align": "left", "sortable": True},
                    {"name": "calls", "label": "Calls", "field": "calls", "sortable": True},
                    {"name": "tokens", "label": "Total Tokens", "field": "tokens", "sortable": True},
                    {"name": "cost", "label": "Est. Cost", "field": "cost", "sortable": True},
                    {"name": "prompt", "label": "Prompt", "field": "prompt", "sortable": True},
                    {"name": "completion", "label": "Completion", "field": "completion", "sortable": True},
                    {"name": "time", "label": "Time", "field": "time", "sortable": True},
                    {"name": "avg_tokens", "label": "Avg/Call", "field": "avg_tokens", "sortable": True},
                ],
                rows=rows, row_key="model",
            ).props("dense flat bordered").classes("w-full")

    def _render_call_log(self, stats: dict):
        self.log_container.clear()
        calls = stats.get("calls", [])
        if not calls:
            return

        with self.log_container:
            with ui.expansion(f"Call Log ({len(calls)} calls)").classes("w-full") \
                    .props("dense header-class='text-caption font-semibold'"):
                rows = []
                for c in reversed(calls[-100:]):  # Show last 100
                    ts = datetime.fromtimestamp(c["timestamp"], tz=timezone.utc).strftime("%H:%M:%S")
                    short = c["model"].split("/")[-1] if "/" in c["model"] else c["model"]
                    step = _STEP_LABELS.get(c.get("step", ""), c.get("step", ""))
                    rows.append({
                        "time": ts,
                        "step": step,
                        "model": short,
                        "prompt_tok": f"{c['prompt_tokens']:,}",
                        "comp_tok": f"{c['completion_tokens']:,}",
                        "total_tok": f"{c['total_tokens']:,}",
                        "cost": f"${c.get('cost_usd', 0):.4f}",
                        "elapsed": f"{c['elapsed_sec']:.1f}s",
                    })
                ui.table(
                    columns=[
                        {"name": "time", "label": "Time", "field": "time", "align": "left", "sortable": True},
                        {"name": "step", "label": "Step", "field": "step", "align": "left", "sortable": True},
                        {"name": "model", "label": "Model", "field": "model", "align": "left", "sortable": True},
                        {"name": "prompt_tok", "label": "Prompt", "field": "prompt_tok", "sortable": True},
                        {"name": "comp_tok", "label": "Completion", "field": "comp_tok", "sortable": True},
                        {"name": "total_tok", "label": "Total", "field": "total_tok", "sortable": True},
                        {"name": "cost", "label": "Cost", "field": "cost", "sortable": True},
                        {"name": "elapsed", "label": "Time", "field": "elapsed", "sortable": True},
                    ],
                    rows=rows, row_key="time",
                ).props("dense flat bordered").classes("w-full")


def _new_bucket() -> dict:
    return {"calls": 0, "tokens": 0, "prompt": 0, "completion": 0, "time": 0.0, "cost": 0.0}


def _add_to_bucket(bucket: dict, tokens: int, prompt: int, completion: int, time_s: float, cost: float):
    bucket["calls"] += 1
    bucket["tokens"] += tokens
    bucket["prompt"] += prompt
    bucket["completion"] += completion
    bucket["time"] += time_s
    bucket["cost"] += cost
