"""LLM Usage stats page — shows token consumption and cost per UC, step, and model."""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone

from nicegui import ui

from ui.access import action, record, requires, scope

from ui.components import card_title, confirm, stat_card

logger = logging.getLogger(__name__)

# Friendly step names for display
_STEP_LABELS = {
    "requirements_gatherer": "Requirements Gathering",
    "ciq_validation_json_gen": "CIQ Validation & JSON Gen",
    "ciq_validation_json_qa": "CIQ QA",
    "network_config_gen": "Network Config Gen",
    "helper_script_gen": "Helper Script Gen",
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

            # Use case → pipeline step → calls (spec 008 U-D6)
            self.uc_container = ui.column().classes("w-full gap-1")

            # Per-model breakdown
            self.model_container = ui.column().classes("w-full")

        ui.timer(0.5, self._refresh, once=True)

    @requires("usage", "usage.clear")
    async def _clear(self):
        async def clear():
            from shared.llm import clear_usage_stats
            await clear_usage_stats(owner=self._scope.owner)
            ui.notify("Usage stats cleared", type="info")
            await record("usage.clear", details={"scope": "own runs" if self._scope.owner else "all runs"})
            await self._refresh()

        confirm("Clear the LLM usage statistics?",
                ("The usage records of your runs are deleted" if self._scope.owner
                 else "The usage records of every run are deleted") + " (calls, tokens, cost). This cannot be undone.",
                clear, ok_label="Clear", ok_icon="delete_outline")

    async def _refresh(self):
        from shared.llm import load_usage_stats
        raw = await load_usage_stats(owner=self._scope.owner)

        # Aggregate totals
        by_model: dict[str, dict] = {}
        by_uc: dict[str, dict] = {}
        by_uc_step: dict[str, dict[str, dict]] = {}  # use case → step → bucket + its calls
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

            # By use case and step, with the step's calls
            sd = by_uc_step.setdefault(uc, {}).setdefault(step, {**_new_bucket(), "entries": []})
            _add_to_bucket(sd, t, p, c, e, cost)
            sd["entries"].append(entry)

        stats = {
            "total_calls": len(raw),
            "total_tokens": total_tokens,
            "total_prompt": total_prompt,
            "total_completion": total_completion,
            "total_time": total_time,
            "total_cost": total_cost,
            "by_model": by_model,
            "by_uc": by_uc,
            "by_uc_step": by_uc_step,
        }

        self._render_summary(stats)
        self._render_uc_breakdown(stats)
        self._render_model_breakdown(stats)

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
        """Use case (collapsible) → its pipeline steps (collapsible) → each step's calls."""
        self.uc_container.clear()
        by_uc = stats.get("by_uc", {})
        if not by_uc:
            return
        with self.uc_container:
            card_title("Per Use Case", "inventory_2")
            ui.label("Open a use case for its pipeline steps, and a step for its LLM calls.") \
                .classes("text-caption cr-text-dim")
            # most expensive first; calls without a use case last
            for uc, data in sorted(by_uc.items(), key=lambda x: (x[0] == "Unknown", -x[1]["cost"])):
                label = "Without a use case" if uc == "Unknown" else uc
                with ui.expansion().classes("w-full cr-usage-uc").props("dense") as uc_exp:
                    with uc_exp.add_slot("header"):
                        _bucket_header(label, data, "inventory_2", bold=True)
                    steps = stats["by_uc_step"].get(uc, {})
                    for step in sorted(steps, key=_step_order):
                        sdata = steps[step]
                        with ui.expansion().classes("w-full cr-usage-step").props("dense") as step_exp:
                            with step_exp.add_slot("header"):
                                _bucket_header(_STEP_LABELS.get(step, step.replace("_", " ").title()), sdata,
                                               "account_tree")
                            body = ui.column().classes("w-full")
                        # the call table is built when the step is first opened (large logs)
                        step_exp.on_value_change(lambda e, b=body, s=sdata: _fill_calls(b, s) if e.value else None)

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

def _step_order(step: str) -> tuple:
    keys = list(_STEP_LABELS)
    return (keys.index(step), "") if step in keys else (len(keys), step)


def _bucket_header(label: str, data: dict, icon: str, bold: bool = False) -> None:
    """Expansion header: name on the left, calls / tokens / cost / time on the right."""
    with ui.row().classes("w-full items-center no-wrap gap-3"):
        ui.icon(icon, size="18px").classes("cr-text-dim")
        ui.label(label).classes("flex-1 text-body2" + (" font-semibold" if bold else ""))
        for text in (f"{data['calls']} calls", f"{data['tokens']:,} tokens", f"${data['cost']:.4f}",
                     f"{data['time']:.1f}s"):
            ui.label(text).classes("text-caption cr-text-dim").style("min-width: 84px; text-align: right")


def _fill_calls(container, data: dict) -> None:
    """The step's calls, newest first (built once)."""
    if getattr(container, "_filled", False):
        return
    container._filled = True
    rows = []
    for i, c in enumerate(sorted(data["entries"], key=lambda c: c.get("timestamp") or 0, reverse=True)):
        ts = datetime.fromtimestamp(c.get("timestamp") or 0, tz=timezone.utc).astimezone()
        model = c.get("model", "") or ""
        rows.append({
            "i": i,
            "time": ts.strftime("%Y-%m-%d %H:%M:%S"),
            "model": model.split("/")[-1],
            "prompt_tok": f"{c.get('prompt_tokens', 0) or 0:,}",
            "comp_tok": f"{c.get('completion_tokens', 0) or 0:,}",
            "total_tok": f"{c.get('total_tokens', 0) or 0:,}",
            "cost": f"${c.get('cost_usd', 0) or 0:.4f}",
            "elapsed": f"{c.get('elapsed_sec', 0) or 0:.1f}s",
        })
    with container:
        ui.table(
            columns=[
                {"name": "time", "label": "Time", "field": "time", "align": "left", "sortable": True},
                {"name": "model", "label": "Model", "field": "model", "align": "left", "sortable": True},
                {"name": "prompt_tok", "label": "Prompt", "field": "prompt_tok"},
                {"name": "comp_tok", "label": "Completion", "field": "comp_tok"},
                {"name": "total_tok", "label": "Total", "field": "total_tok"},
                {"name": "cost", "label": "Cost", "field": "cost"},
                {"name": "elapsed", "label": "Duration", "field": "elapsed"},
            ],
            rows=rows, row_key="i", pagination=25,
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
