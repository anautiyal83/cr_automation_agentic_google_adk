"""Pipeline progress tracking — polished 10-step visual tracker."""
from __future__ import annotations

import asyncio
import functools
import inspect
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from pathlib import Path
from uuid import UUID

logger = logging.getLogger(__name__)

from nicegui import ui, context

from ui.access import action, download, record, record_document, record_later, requires, scope

from core.events import PipelineEvent
from core.events import event_bus

STATUS_CONFIG = {
    "pending":     {"color": "#94A3B8", "icon": "radio_button_unchecked", "text": "#94A3B8"},
    "in_progress": {"color": "#0B5FFF", "icon": "sync",                  "text": "#0B5FFF"},
    "passed":      {"color": "#16A34A", "icon": "check_circle",          "text": "#16A34A"},
    "failed":      {"color": "#DC2626", "icon": "cancel",                "text": "#DC2626"},
    "retrying":    {"color": "#D97706", "icon": "replay",                "text": "#D97706"},
    "skipped":     {"color": "#94A3B8", "icon": "skip_next",             "text": "#94A3B8"},
}

STEP_ICONS = {
    1: "file_open", 2: "analytics", 3: "fact_check",
    4: "data_object", 5: "science", 6: "account_tree",
    7: "description", 8: "code", 9: "play_circle", 10: "summarize",
}

TOTAL_STEPS = 10

# Short names for compact single-row display
_SHORT_NAMES = {
    1: "DOC Parser", 2: "DOC Analyzer", 3: "CIQ Config Generation",
    4: "Generate CIQ JSON Config", 5: "QA-Validation", 6: "Generate Execution Config",
    7: "QA-MOP", 8: "Simulator Setup", 9: "QA-Execution",
    10: "Final Report",
}

# Milestone names mapping
_MILESTONE_NAMES = {
    0: "Requirements Gathering",
    2: "Requirements Summary",
    3: "Validation Rules",
    4: "JSON Output",
    5: "QA Validation",
    6: "Workflow",
    7: "MOP Generation Test",
    8: "Simulator Config",
    9: "QA Execution",
}


def _own_run(action_name: str):
    """Run only on the user's own tracked run (the admin: any run) — spec 006."""
    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(self, *args, **kwargs):
            if self.active_run_id and self.runner and not await self._scope.check(
                    self.runner._db, str(self.active_run_id), action_name, "progress"):
                return None
            result = fn(self, *args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            return result
        return wrapper
    return deco


class ProgressSection:
    def __init__(self, runner=None, on_view_details=None, on_view_outputs=None):
        self.runner = runner
        self.on_view_details = on_view_details  # callback(run_id)
        self.on_view_outputs = on_view_outputs  # callback(run_id)
        self.active_run_id: UUID | None = None
        self.step_widgets: dict[int, dict] = {}
        # Capture NiceGUI client for background event updates
        try:
            self._client = context.client
        except Exception:
            self._client = None
        self._scope = scope()  # the admin follows every run, others only their own (spec 006)
        self._build_ui()
        # Check for active runs on load
        if runner:
            ui.timer(0.5, self._restore_active_run, once=True)

    def _build_ui(self):
        self._selected_step = 1
        self._follow_live = True          # select the running step until the user picks one
        self._step_started: dict[int, float] = {}
        self._run_dir = ""
        self._uc_name = ""
        self._run_state = "idle"
        self._run_t0: float | None = None     # monotonic start of the tracked run (live runs)
        self._run_elapsed = ""                # fixed elapsed text (finished runs)
        self._run_since = ""                  # local start time of the run: its log lines only
        self._tick_n = 0
        with ui.column().classes("w-full gap-3"):
            # Run header: state badge + run id, status, actions, KPI strip, step segments
            self.banner = ui.element("div").classes("w-full cr-run-banner is-idle")
            with self.banner:
                with ui.row().classes("items-center justify-between w-full no-wrap"):
                    with ui.row().classes("items-center gap-2 no-wrap min-w-0"):
                        self.state_badge = ui.html("").classes("cr-run-state")
                        self.run_chip = ui.label("").classes("cr-run-chip")
                        self.uc_chip = ui.label("").classes("cr-run-uc")
                    with ui.row().classes("gap-2 no-wrap") as self.action_btns:
                        self.restart_btn = action(ui.button("Restart", icon="restart_alt",
                            on_click=self._restart) \
                            .props("outline color=primary size=sm no-caps"), "progress")
                        self.stop_btn = action(ui.button("Stop", icon="pause",
                            on_click=self._stop) \
                            .props("outline color=warning size=sm no-caps"), "progress")
                        self.cancel_btn = action(ui.button("Cancel", icon="close",
                            on_click=self._cancel) \
                            .props("outline color=negative size=sm no-caps"), "progress")
                    self.action_btns.set_visibility(False)
                with ui.row().classes("items-center gap-3 no-wrap mt-3"):
                    self.status_icon = ui.icon("hourglass_empty", size="20px") \
                        .classes("cr-text-dim")
                    with ui.column().classes("gap-0 min-w-0"):
                        self.status_label = ui.label("No active pipeline run").classes("cr-run-title")
                        self.status_detail = ui.label("Start a new run from New Run") \
                            .classes("cr-run-sub")
                # One segment per step, coloured by its status
                with ui.element("div").classes("cr-seg-bar"):
                    self.segments = {i: ui.element("div").classes("cr-seg").tooltip(
                        f"{i:02d} {_SHORT_NAMES.get(i, '')}") for i in range(1, TOTAL_STEPS + 1)}
                with ui.element("div").classes("cr-kpis"):
                    self.kpi = {}
                    for key, label in (("elapsed", "Elapsed"), ("progress", "Steps passed"),
                                       ("current", "Current step"), ("tests", "Test cases")):
                        with ui.element("div").classes("cr-kpi"):
                            ui.label(label).classes("cr-kpi-label")
                            self.kpi[key] = ui.label("—").classes("cr-kpi-value")
                # Overall progress (kept for the event handlers; the segments above show it)
                self.progress_bar = ui.linear_progress(value=0, show_value=False) \
                    .props("color=primary rounded size=6px").classes("mt-3")
                self.progress_bar.set_visibility(False)

            # Run page: step list (left) + selected step detail (right)
            with ui.element("div").classes("cr-run-layout"):
                with ui.element("div").classes("cr-run-steps"):
                    with ui.row().classes("items-center justify-between w-full no-wrap cr-run-steps-head"):
                        ui.label("Pipeline").classes("cr-run-steps-title")
                        self.steps_count = ui.label("0/10").classes("cr-run-steps-count")
                    for i in range(1, 11):
                        self._step_widget(i)

                with ui.element("div").classes("cr-run-detail"):
                    with ui.row().classes("items-start justify-between w-full no-wrap cr-run-detail-head"):
                        with ui.column().classes("gap-0 min-w-0"):
                            self.sel_eyebrow = ui.label("").classes("cr-eyebrow")
                            self.sel_title = ui.label("").classes("cr-detail-title")
                            self.sel_meta = ui.label("").classes("cr-detail-meta")
                        self.sel_status = ui.html("")
                    with ui.tabs().props("dense no-caps align=left active-color=primary "
                                         "indicator-color=primary").classes("cr-tabs") as self.tabs:
                        ui.tab("overview", "Overview", icon="info_outline")
                        ui.tab("artifacts", "Artifacts", icon="description")
                        ui.tab("tests", "Test cases", icon="fact_check")
                        ui.tab("logs", "Logs", icon="subject")
                    with ui.tab_panels(self.tabs, value="overview").classes("w-full cr-tab-panels"):
                        with ui.tab_panel("overview"):
                            self.overview_info = ui.column().classes("w-full gap-2")
                            self._build_overview_actions()
                        with ui.tab_panel("artifacts"):
                            self.artifacts_box = ui.column().classes("w-full gap-0")
                        with ui.tab_panel("tests"):
                            self.tests_box = ui.column().classes("w-full gap-2")
                        with ui.tab_panel("logs"):
                            self.logs_box = ui.column().classes("w-full gap-2")
                    self.tabs.on_value_change(lambda _: self._refresh_tab())

            # Pre-built questionnaire dialog (populated on demand)
            self._q_dialog = ui.dialog().props("maximized")
            self._q_content = None  # will hold the card's content container
            self._q_answer_inputs: dict[str, Any] = {}
            with self._q_dialog:
                self._q_card = ui.card().classes("w-full h-full p-0").style(
                    "display:flex; flex-direction:column; max-height:100vh; "
                    "background: #f8f9fc; color: #1a1a2e"
                )
            self._q_dialog.on("hide", lambda: None)  # ensure dialog is interactive
            # Pre-built "Edit requirements" dialog (step 2 review), filled on demand: one created in
            # the click handler did not take any typing
            self._edit_dialog = ui.dialog()
            with self._edit_dialog:
                self._edit_card = ui.card().classes("w-full p-0").style(
                    "max-width: 880px; max-height: 90vh; display: flex; flex-direction: column")
        self._select_step(1, user=False)
        self._set_run_state("idle")
        ui.timer(1.0, self._tick)

    def _build_overview_actions(self):
        """Milestone review, error details and completion actions (Overview tab)."""
        # Error / retry detail area
        self.detail_container = ui.column().classes("w-full")

        # Milestone review panel (hidden until milestone reached)
        self.milestone_row = ui.column().classes("w-full gap-3 cr-review-panel")
        self.milestone_row.set_visibility(False)
        with self.milestone_row:
            with ui.row().classes("items-center gap-2"):
                ui.icon("rate_review", size="18px").classes("cr-text-warning")
                ui.label("Review required").classes("font-semibold")
            # Artifact info row
            with ui.row().classes("w-full items-center gap-3 cr-card-inner p-3"):
                ui.icon("description", size="20px").classes("cr-text-accent")
                self.ms_artifact_label = ui.label("").classes("text-body2 font-semibold flex-1")
                self.ms_view_btn = ui.button("View artifact", icon="visibility",
                    on_click=self._show_artifact_dialog) \
                    .props("flat color=primary size=sm no-caps")
            # Extra artifact files (view/download) — populated dynamically
            self.ms_extra_files_row = ui.row().classes("w-full items-center gap-3 cr-card-inner p-3")
            self.ms_extra_files_row.set_visibility(False)
            # Action buttons
            with ui.row().classes("w-full gap-2"):
                self.ms_continue_btn = action(ui.button("Approve & continue", icon="check",
                    on_click=lambda: self._milestone_respond("continue")) \
                    .props("unelevated color=primary size=sm no-caps"), "progress")
                self.ms_skip_btn = action(ui.button("Skip & continue", icon="skip_next",
                    on_click=lambda: self._milestone_respond("continue")) \
                    .props("unelevated color=warning size=sm no-caps"), "progress")
                self.ms_skip_btn.set_visibility(False)
                self.ms_rerun_btn = action(ui.button("Re-run step", icon="replay",
                    on_click=lambda: self._milestone_respond("rerun")) \
                    .props("outline color=primary size=sm no-caps")
                    .tooltip("Regenerates the step; after 3 re-runs of the same step the run fails"), "progress")
                # Step 2 review: correct answers or say what was captured wrongly
                self.ms_edit_btn = action(ui.button("Edit requirements", icon="edit_note",
                    on_click=self._open_requirements_editor) \
                    .props("outline color=primary size=sm no-caps")
                    .tooltip("Correct an answer or describe what is wrong; the requirements are compiled again"),
                    "progress")
                self.ms_edit_btn.set_visibility(False)
                self.ms_cancel_btn = action(ui.button("Cancel pipeline", icon="stop",
                    on_click=lambda: self._milestone_respond("cancel")) \
                    .props("outline color=negative size=sm no-caps"), "progress")
            with ui.row().classes("w-full gap-2"):
                self.ms_clarify_btn = action(ui.button("Clarify in requirements", icon="quiz",
                    on_click=lambda: self._milestone_respond("clarify")) \
                    .props("unelevated color=primary size=sm no-caps") \
                    .tooltip("Answer the open questions in step 1; the later steps run again with the answers"), "progress")
            self.ms_clarify_btn.set_visibility(False)
            # Open questions this step could not settle (shared.clarifications)
            self.ms_clarify_info = ui.column().classes("w-full gap-1")
            self.ms_clarify_info.set_visibility(False)
            # Container for questionnaire-specific button (cleared on each call)
            self.ms_questionnaire_row = ui.row().classes("w-full gap-3")
            self.ms_questionnaire_row.set_visibility(False)

        # Completion actions (hidden until pipeline finishes)
        self.completion_row = ui.row().classes("w-full items-center gap-2 mt-2")
        self.completion_row.set_visibility(False)
        with self.completion_row:
            self.btn_details = ui.button("View full details", icon="info_outline",
                                         on_click=self._go_details) \
                .props("unelevated color=primary size=sm no-caps")
            self.btn_outputs = ui.button("Browse outputs", icon="folder_open",
                                         on_click=self._go_outputs) \
                .props("outline color=primary size=sm no-caps")
            # A completed run stays here until the user is done with it (spec 008 U-D7)
            self.btn_finish = action(ui.button("Mark as finished", icon="task_alt", on_click=self._finish)
                                     .props("unelevated color=positive size=sm no-caps"), "progress")
            self.btn_finish.tooltip("Done with this run: it leaves Live Run (Run History keeps it)")
            self.btn_finish.set_visibility(False)

    def _step_widget(self, step_num: int):
        with ui.element("div").classes("cr-step-row") as box:
            box.on("click", lambda _, n=step_num: self._select_step(n, user=True))
            icon_container = ui.element("div").classes("cr-step-node")
            with icon_container:
                num = ui.label(f"{step_num:02d}").classes("cr-step-node-num")
                step_icon = ui.icon("check", size="12px")
                step_icon.set_visibility(False)
            with ui.column().classes("gap-0 min-w-0 flex-1"):
                name = ui.label(_SHORT_NAMES.get(step_num, "")).classes("cr-step-row-name")
                label = ui.label(_STEP_AGENTS.get(step_num, "")).classes("cr-step-agent")
            with ui.column().classes("items-end gap-0 cr-step-right"):
                status_label = ui.label("Pending").classes("cr-step-chip")
                duration = ui.label("").classes("cr-step-duration")

        self.step_widgets[step_num] = {
            "col": box, "box": box, "icon_container": icon_container, "step_icon": step_icon,
            "num": num, "label": label, "name": name, "status": status_label, "duration": duration,
            "raw_status": "pending",
        }

    # ------------------------------------------------------------------
    # Run page: selection, tabs
    # ------------------------------------------------------------------

    def _select_step(self, step_num: int, user: bool = False):
        if step_num not in self.step_widgets:
            return
        if user:
            self._follow_live = self.step_widgets[step_num]["raw_status"] == "in_progress"
        self._selected_step = step_num
        for n, w in self.step_widgets.items():
            w["box"].classes(add="is-selected") if n == step_num else w["box"].classes(remove="is-selected")
        self._refresh_selected_header()
        self._refresh_tab()

    def _refresh_selected_header(self):
        n = self._selected_step
        w = self.step_widgets.get(n, {})
        raw = w.get("raw_status", "pending")
        self.sel_eyebrow.text = f"STEP {n:02d} / {TOTAL_STEPS:02d}"
        self.sel_title.text = _SHORT_NAMES.get(n, "")
        meta = [_STEP_AGENTS.get(n, ""),
                f"⏱ {w['duration'].text}" if w.get("duration") and w["duration"].text else ""]
        self.sel_meta.text = "  ·  ".join(m for m in meta if m)
        base = raw.split("(")[0].strip()
        self.sel_status.content = (f"<span class='cr-status cr-status-{base.replace('_', '-')}'>"
                                   f"{_humanize(raw)}</span>")

    # ------------------------------------------------------------------
    # Run header: state badge, KPIs, live ticker
    # ------------------------------------------------------------------

    _STATE_BADGES = {
        "running": ("LIVE", "is-running"), "review": ("REVIEW", "is-review"),
        "completed": ("DONE", "is-completed"), "failed": ("FAILED", "is-failed"),
        "idle": ("IDLE", "is-idle"), "history": ("RUN", "is-idle"), "waiting": ("WAITING", "is-idle"),
    }

    def _set_run_state(self, state: str) -> None:
        """running | review | completed | failed | idle | history — badge, header accent, ticker."""
        self._run_state = state
        text, cls = self._STATE_BADGES.get(state, ("RUN", "is-idle"))
        self.state_badge.content = f"<span class='cr-state {cls}'><i></i>{text}</span>"
        self.banner.classes(replace=f"w-full cr-run-banner {cls}")
        rid = str(self.active_run_id or "")
        self.run_chip.text = f"RUN {rid[:8]}" if rid else ""
        self.uc_chip.text = self._uc_name or ""
        self._refresh_kpis()

    def _stop_clock(self) -> None:
        if self._run_t0 is not None:
            self._run_elapsed = _fmt_clock(time.monotonic() - self._run_t0)
            self._run_t0 = None

    def _refresh_kpis(self) -> None:
        if not hasattr(self, "kpi"):
            return
        statuses = {n: str(w.get("raw_status", "pending")).split("(")[0].strip()
                    for n, w in self.step_widgets.items()}
        passed = sum(1 for s in statuses.values() if s in ("passed", "completed"))
        self.kpi["progress"].text = f"{passed} / {TOTAL_STEPS}"
        if hasattr(self, "steps_count"):
            self.steps_count.text = f"{passed}/{TOTAL_STEPS}"
        running = next((n for n, s in statuses.items() if s in ("in_progress", "retrying")), None)
        failed = next((n for n, s in statuses.items() if s == "failed"), None)
        cur = running or failed
        self.kpi["current"].text = f"{cur:02d} · {_SHORT_NAMES.get(cur, '')}" if cur else (
            "—" if self._run_state != "completed" else "All steps done")
        if self._run_t0 is not None:
            self.kpi["elapsed"].text = _fmt_clock(time.monotonic() - self._run_t0)
        else:
            self.kpi["elapsed"].text = self._run_elapsed or "—"
        self.kpi["tests"].text = _test_summary(self._run_dir, self._uc_name) or "—"
        self.uc_chip.text = self._uc_name or ""

    async def _tick(self) -> None:
        """Once a second while a run is live: elapsed clock, running step's duration, live logs."""
        if self._run_t0 is not None:
            self.kpi["elapsed"].text = _fmt_clock(time.monotonic() - self._run_t0)
        if self._run_state != "running":
            return
        self._tick_n += 1
        for n, t0 in list(self._step_started.items()):
            w = self.step_widgets.get(n)
            if w and str(w.get("raw_status", "")).startswith(("in_progress", "retrying")):
                w["duration"].text = _fmt_duration(time.monotonic() - t0)
        if self._tick_n % 3 == 0:
            sel = self.step_widgets.get(self._selected_step, {})
            if str(sel.get("raw_status", "")).startswith(("in_progress", "retrying")):
                tab = self.tabs.value
                if tab == "logs":
                    self._render_logs()
                elif tab == "overview":
                    self._render_activity()
            self._refresh_kpis()

    def _refresh_tab(self):
        tab = self.tabs.value if hasattr(self, "tabs") else "overview"
        if tab == "artifacts":
            self._render_artifacts()
        elif tab == "tests":
            self._render_tests()
        elif tab == "logs":
            self._render_logs()
        else:
            self._render_overview()

    def _render_overview(self):
        self.overview_info.clear()
        n = self._selected_step
        w = self.step_widgets.get(n, {})
        raw = w.get("raw_status", "pending")
        with self.overview_info:
            if not self.active_run_id:
                _empty_state("play_circle_outline", "No run selected",
                             "Start a run from New Run, or re-run one from Run History.")
                return
            with ui.element("div").classes("cr-spec"):
                for k, v in (("Status", _humanize(raw)),
                             ("Duration", w["duration"].text if w.get("duration") and w["duration"].text
                              else "running…" if str(raw).startswith(("in_progress", "retrying")) else "—"),
                             ("Agent", _STEP_AGENTS.get(n, "—")),
                             ("Produces", _STEP_OUTPUTS.get(n, "—"))):
                    with ui.element("div").classes("cr-spec-cell"):
                        ui.label(k).classes("cr-spec-key")
                        ui.label(v).classes("cr-spec-value")
            self.activity_box = ui.column().classes("w-full gap-0")
        self._render_activity()

    def _render_activity(self):
        """Last log lines of the selected step, as a small live console (Overview tab)."""
        box = getattr(self, "activity_box", None)
        if box is None or not self.active_run_id:
            return
        box.clear()
        lines = _step_log_lines(self._selected_step, self._run_since)[-14:]
        if not lines:
            return
        raw = str(self.step_widgets.get(self._selected_step, {}).get("raw_status", ""))
        live = raw.startswith(("in_progress", "retrying"))
        with box:
            ui.html(_console(lines, f"activity · step {self._selected_step:02d}", live, compact=True)) \
                .classes("w-full mt-3")

    def _render_artifacts(self):
        self.artifacts_box.clear()
        with self.artifacts_box:
            files = _step_files(self._run_dir, self._uc_name, self._selected_step)
            labels: dict = {}
            headers: dict = {}  # file → category heading shown above it (step 10)
            if self._selected_step == 10:
                # The run's final documents by category, in the order the pipeline produced them
                from skills.report_generation.deliverables import collect_deliverables, group_deliverables
                groups = group_deliverables(collect_deliverables(self._run_dir, self._uc_name))
                listed = [d for _, docs in groups for d in docs]
                labels = {d.path: d.label for d in listed}
                headers = {docs[0].path: (cat, len(docs)) for cat, docs in groups}
                files = [d.path for d in listed] + [f for f in files if f not in labels]
            if not files:
                _empty_state("description", "No artifacts yet",
                             "Files this step writes to the run folder appear here.")
                return
            from ui.pages.output_viewer_section import _category_box
            container = None
            for f in files:
                if f in headers:  # step 10: a bordered box per category
                    container = _category_box(*headers[f])
                elif headers and f not in labels:
                    container = None
                if container is not None:
                    with container:
                        self._file_row_for(f, labels)
                else:
                    self._file_row_for(f, labels)

    def _file_row_for(self, f, labels: dict):
        with ui.row().classes("cr-file-row items-center no-wrap"):
            ui.icon(_file_icon(f), size="18px").classes("cr-text-accent")
            with ui.column().classes("gap-0 flex-1 min-w-0"):
                if f in labels:
                    ui.label(labels[f]).classes("cr-file-label")
                    ui.label(f"{Path(f).relative_to(self._run_dir).as_posix()} · {_size(f.stat().st_size)}") \
                        .classes("cr-file-path")
                else:
                    ui.label(str(f.relative_to(self._run_dir))).classes("cr-file-name")
                    ui.label(_size(f.stat().st_size)).classes("text-caption cr-text-dim")
            ui.button("View", icon="visibility", on_click=lambda _, p=f: _open_view(p)) \
                .props("flat dense color=primary size=sm no-caps")
            ui.button("Download", icon="download", on_click=lambda _, p=f: download(p)) \
                .props("flat dense color=primary size=sm no-caps")

    def _render_tests(self):
        import json as _json
        self.tests_box.clear()
        with self.tests_box:
            path = Path(self._run_dir) / f"{self._uc_name}_test-cases.json" if self._run_dir else None
            if not path or not path.exists():
                _empty_state("fact_check", "No test cases yet",
                             "Step 9 runs the scenario test cases on the simulator; results appear here.")
                return
            data = _json.loads(path.read_text(encoding="utf-8"))
            ui.html(f"<div class='cr-test-summary'><b>{data.get('passed', 0)}</b> of "
                    f"<b>{data.get('total', 0)}</b> test cases passed</div>")
            for tc in data.get("test_cases", []):
                failed = [c for c in tc.get("checks", []) if not c.get("ok")]
                with ui.expansion().classes("w-full cr-test-row").props("dense") as exp:
                    with exp.add_slot("header"):
                        with ui.row().classes("items-center gap-3 w-full no-wrap"):
                            ui.html(f"<span class='cr-status cr-status-"
                                    f"{'completed' if tc['status'] == 'PASS' else 'failed'}'>{tc['status']}</span>")
                            ui.label(tc["id"]).classes("cr-file-name")
                            ui.label(tc["title"]).classes("flex-1")
                            ui.label(tc.get("actual", "")).classes("text-caption cr-text-dim")
                    ui.label(f"Expected: {tc.get('expected', '')}").classes("text-body2")
                    for c in tc.get("checks", []):
                        with ui.row().classes("items-start gap-2 no-wrap"):
                            ui.icon("check_circle" if c["ok"] else "cancel", size="16px") \
                                .classes("cr-text-success" if c["ok"] else "cr-text-error")
                            ui.label(c["check"] + (f" — {c['detail']}" if c.get("detail") and not c["ok"] else "")) \
                                .classes("text-body2")
                    if failed:
                        ui.label(f"{len([x for x in tc.get('commands', []) if x.get('validation') == 'failed'])} "
                                 "failed step(s); see the full report in Artifacts (step 9).").classes("text-caption cr-text-dim")

    def _render_logs(self):
        self.logs_box.clear()
        with self.logs_box:
            lines = _step_log_lines(self._selected_step, self._run_since)
            if not lines:
                _empty_state("subject", "No log lines", "Agent log lines for this step appear here.")
                return
            raw = str(self.step_widgets.get(self._selected_step, {}).get("raw_status", ""))
            ui.html(_console(lines[-300:], f"flow.log · step {self._selected_step:02d}",
                             raw.startswith(("in_progress", "retrying")))).classes("w-full")

    async def _load_run_info(self):
        if not (self.runner and self.active_run_id):
            return
        try:
            state = await self.runner.get_state(str(self.active_run_id))
            self._run_dir = state.get("run_dir", "") or self._run_dir
            self._uc_name = state.get("uc_name", "") or self._uc_name
        except Exception:
            pass
        if not self._uc_name and self.runner._db:
            try:
                rows = await self.runner._db.execute(
                    "SELECT d.uc_name FROM pipeline_runs r LEFT JOIN uc_document_sets d "
                    "ON r.document_set_id = d.id WHERE r.id = ?", (str(self.active_run_id),))
                if rows:
                    self._uc_name = rows[0].get("uc_name") or self._uc_name
            except Exception:
                pass
        # First run folder that exists: session state, the artifact store, the default layout
        store = getattr(self.runner, "_artifact_store", None)
        candidates = [self._run_dir,
                      str(Path(getattr(store, "base_dir", getattr(store, "_base_dir", "")) or "data/runs")
                          / str(self.active_run_id)),
                      str(Path(__file__).resolve().parents[2] / "data" / "runs" / str(self.active_run_id))]
        self._run_dir = next((c for c in candidates if c and Path(c).is_dir()), self._run_dir)
        self._refresh_kpis()
        self._refresh_selected_header()
        self._refresh_tab()

    async def _show_latest_run(self):
        """Nothing executes: Live Run keeps showing the user's latest run (completed, failed, waiting…) —
        a run leaves Live Run when it is cancelled (spec 007) or marked finished (spec 008 U-D7)."""
        if not self.runner or not self.runner._db:
            return
        where, params = self._scope.where("r")
        rows = await self.runner._db.execute(
            "SELECT r.id, r.status, r.finished_at FROM pipeline_runs r "
            f"WHERE {where} ORDER BY COALESCE(r.queued_at, r.started_at) DESC LIMIT 1", params)
        if rows and not rows[0]["finished_at"] and \
                str(rows[0]["status"]).replace("PipelineStatus.", "").lower() not in ("cancelled", "removed"):
            await self.view_run(rows[0]["id"])

    async def _show_waiting(self, run_id) -> None:
        """Banner of a run that waits in the queue: its position; it starts by itself."""
        position = await self.runner.queue_position(str(run_id)) if self.runner else 0
        resuming = False
        if self.runner and self.runner._db and not position:
            rows = await self.runner._db.execute("SELECT status FROM pipeline_runs WHERE id = ?", (str(run_id),))
            resuming = bool(rows) and rows[0]["status"] == "resuming"
        self._set_run_state("waiting")
        self.action_btns.set_visibility(False)
        self.progress_bar.set_visibility(False)
        self.status_icon.name = "schedule"
        self.status_icon.classes(replace="cr-text-dim")
        self.status_label.text = ("Resuming after a server restart" if resuming
                                  else f"Waiting in the queue — position {position}" if position
                                  else "Waiting in the queue")
        self.status_detail.text = ("It starts by itself when the pipeline is free — see Pipeline Queue "
                                   "for the estimated start")
        self.banner.style(replace="background: var(--cr-surface-light); border: 1px solid var(--cr-border)")

    async def _restore_active_run(self):
        """Check runner's live state (or DB) for any running/paused pipeline and restore the progress view."""
        try:
            if not self.runner:
                logger.debug("_restore: no runner")
                return

            # First check runner's in-memory active runs (survives page refresh)
            run_id = None
            status = None
            step = 0
            uc_name = ""

            logger.info("_restore: checking %d active runs", len(self.runner._active_runs))
            for rid, info in self.runner._active_runs.items():
                live_status = info.get("status", "")
                logger.info("_restore: run %s status=%s", rid[:8], live_status)
                if live_status in ("running", "paused_milestone", "paused_hil"):
                    if not (self._scope.user and self._scope.user.owns_run(info.get("started_by"))):
                        continue  # another user's run
                    run_id = UUID(rid)
                    status = live_status
                    step = info.get("current_step", 0)
                    try:
                        state = await self.runner.get_state(rid)
                        uc_name = state.get("uc_name", "")
                    except Exception:
                        pass
                    break

            # Fallback: check DB for runs marked as running
            if not run_id and self.runner._db:
                rows = await self.runner._db.execute(
                    "SELECT r.id, r.status, r.current_step, d.uc_name "
                    "FROM pipeline_runs r "
                    "LEFT JOIN uc_document_sets d ON r.document_set_id = d.id "
                    "WHERE r.status IN ('running', 'paused_hil', 'paused_milestone', "
                    "'PipelineStatus.RUNNING', 'PipelineStatus.PAUSED_HIL', 'PipelineStatus.PAUSED_MILESTONE') "
                    f"AND {self._scope.where('r')[0]} "
                    "ORDER BY r.started_at DESC LIMIT 1", self._scope.where('r')[1]
                )
                if not rows:
                    await self._show_latest_run()
                    return
                run = rows[0]
                db_run_id = run["id"]

                # Not alive in this process: the runner resumes or fails interrupted runs at server
                # start (spec 007 restart recovery) — show it as it stands
                if db_run_id not in self.runner._active_runs:
                    logger.info("_restore: run %s is not executing in this process", db_run_id[:8])
                    await self.view_run(db_run_id)
                    return

                run_id = UUID(db_run_id)
                status = str(run.get("status") or "").replace("PipelineStatus.", "").lower()
                step = run.get("current_step", 0)
                uc_name = (run.get("uc_name") or "")

            if not run_id:
                logger.info("_restore: no active run found in memory or DB")
                await self._show_latest_run()
                return

            logger.info("_restore: found run %s, status=%s, step=%d, uc=%s",
                        str(run_id)[:8], status, step, uc_name)
            self.active_run_id = run_id
            self.action_btns.set_visibility(True)
            self.progress_bar.set_visibility(True)
            self._run_t0, self._run_elapsed = await self._clock_start(run_id), ""
            self._set_run_state("running" if status == "running" else "review")
            await self._load_run_info()

            # Restore step visuals from live session state first, then DB
            restored_steps = set()
            try:
                live_state = await self.runner.get_state(str(run_id))
                for i in range(1, TOTAL_STEPS + 1):
                    ss = live_state.get(f"step_{i}_status")
                    if ss and i in self.step_widgets:
                        self._update_step_visual(i, ss)
                        restored_steps.add(i)
                        step = max(step, i)
            except Exception:
                pass

            # Fill gaps from DB
            if self.runner._db and len(restored_steps) < TOTAL_STEPS:
                try:
                    step_rows = await self.runner._db.execute(
                        "SELECT step_number, status FROM step_results WHERE run_id = ? ORDER BY step_number",
                        (str(run_id),)
                    )
                    for sr in step_rows:
                        sn = sr.get("step_number", 0)
                        if sn and sn not in restored_steps and sn in self.step_widgets:
                            ss = str(sr.get("status") or "pending").replace("StepStatus.", "").lower()
                            self._update_step_visual(sn, ss)
                except Exception:
                    pass

            # Set banner based on status and subscribe to events
            if status == "running":
                self.status_icon.name = "sync"
                self.status_icon.classes(replace="cr-text-accent cr-spin")
                self.status_label.text = f"Pipeline Running — {uc_name}"
                self.status_detail.text = f"Step {step}/{TOTAL_STEPS}: {_SHORT_NAMES.get(step, '')}"
                self.progress_bar.value = (step - 1) / TOTAL_STEPS if step > 0 else 0
                self.progress_bar.props("color=primary")
                self.banner.style(replace=(
                    "background: rgba(11,95,255,0.08); border: 1px solid rgba(11,95,255,0.3)"
                ))
                event_bus.subscribe(self._on_event, run_id)
            elif status == "paused_milestone":
                # Use milestone_step (which step the milestone is for) not current_step
                run_info = self.runner._active_runs.get(str(run_id), {})
                ms_step = run_info.get("milestone_step", step)

                self.status_icon.name = "rate_review"
                self.status_icon.classes(replace="cr-text-warning")
                self.progress_bar.props("color=primary")
                self.progress_bar.props(remove="animate")
                self.banner.style(replace=(
                    "background: rgba(251,191,36,0.08); border: 1px solid rgba(251,191,36,0.3)"
                ))

                milestone_name = _MILESTONE_NAMES.get(ms_step, f"Step {ms_step}")

                if ms_step == 0:
                    # Questionnaire — restore interactive dialog
                    from shared.state import milestone_waiting as _mw
                    questions = _mw.get(0, {}).get("questions", [])
                    self.status_icon.name = "quiz"
                    self.status_label.text = f"Requirements Gathering — {uc_name}"
                    self.status_detail.text = f"{len(questions)} questions need your input"
                    self.progress_bar.value = 0
                    self._show_questionnaire_dialog(questions, milestone_name)
                elif ms_step == 2:
                    # Requirements Summary review
                    self.status_icon.name = "checklist"
                    self.status_label.text = f"Review: Requirements Summary — {uc_name}"
                    self.status_detail.text = "Review the gathered requirements before proceeding"
                    self.progress_bar.value = 2 / TOTAL_STEPS
                    artifact_file = ""
                    extra_files = []
                    try:
                        ms_state = await self.runner.get_state(str(run_id))
                        from shared.state import milestone_waiting as _mw
                        artifact_file = _mw.get(2, {}).get("artifact_file", "")
                        if not artifact_file:
                            artifact_file, extra_files = self.runner._resolve_milestone_artifacts(
                                ms_state, ms_step
                            )
                    except Exception:
                        pass
                    self._show_milestone_review(ms_step, milestone_name, artifact_file,
                                                extra_artifact_files=extra_files)
                else:
                    # Regular milestone (steps 3-9)
                    self.status_label.text = f"Milestone Review — {uc_name}"
                    self.status_detail.text = f"Step {ms_step}/{TOTAL_STEPS} completed — awaiting review"
                    self.progress_bar.value = ms_step / TOTAL_STEPS
                    artifact_file = ""
                    extra_files = []
                    try:
                        ms_state = await self.runner.get_state(str(run_id))
                        artifact_file, extra_files = self.runner._resolve_milestone_artifacts(
                            ms_state, ms_step
                        )
                    except Exception:
                        pass
                    has_failures = False
                    if ms_step in (5, 7, 9):
                        try:
                            has_failures = ms_state.get(f"step_{ms_step}_status") == "failed"
                        except Exception:
                            pass
                    self._show_milestone_review(ms_step, milestone_name, artifact_file,
                                                has_failures=has_failures,
                                                extra_artifact_files=extra_files)
                event_bus.subscribe(self._on_event, run_id)
            elif status == "paused_hil":
                self.status_icon.name = "help_outline"
                self.status_icon.classes(replace="cr-text-warning")
                self.status_label.text = f"Waiting for Input — {uc_name}"
                self.status_detail.text = f"Step {step}/{TOTAL_STEPS} needs human clarification"
                self.progress_bar.value = (step - 1) / TOTAL_STEPS
                self.progress_bar.props("color=warning")
                self.banner.style(replace=(
                    "background: rgba(251,191,36,0.08); border: 1px solid rgba(251,191,36,0.3)"
                ))
                event_bus.subscribe(self._on_event, run_id)
        except Exception as exc:
            logger.error("_restore_active_run failed: %s", exc, exc_info=True)

    async def _clock_start(self, run_id) -> float:
        """Monotonic start of a run that was already going when the page loaded (DB started_at)."""
        try:
            rows = await self.runner._db.execute("SELECT started_at FROM pipeline_runs WHERE id = ?", (str(run_id),))
            started = datetime.fromisoformat(str(rows[0]["started_at"]).replace("Z", "+00:00"))
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            self._run_since = _local_since(rows[0]["started_at"])
            return time.monotonic() - max(0.0, (datetime.now(timezone.utc) - started).total_seconds())
        except Exception:
            return time.monotonic()

    async def start_tracking(self, run_id: UUID, queued: bool | None = None):
        """Follow a run live. ``queued``: it waits in the run queue (spec 007) — shown as waiting
        until the pipeline starts it (event ``run_started``); None = find out."""
        if queued is None:
            queued = bool(self.runner) and str(run_id) not in self.runner._active_runs
        self.active_run_id = run_id
        self._run_t0, self._run_elapsed = time.monotonic(), ""
        self._run_since = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._set_run_state("running")
        self.action_btns.set_visibility(True)
        self.completion_row.set_visibility(False)
        self.milestone_row.set_visibility(False)
        self.progress_bar.set_visibility(True)
        self.progress_bar.value = 0
        self.progress_bar.props("color=primary indeterminate")
        self.status_icon.name = "hourglass_top"
        self.status_icon.classes(replace="cr-text-accent cr-spin")
        self.status_label.text = "Pipeline Starting..."
        self.status_detail.text = "Initializing agents and loading models — this may take a few seconds"
        self.banner.style(replace=(
            "background: rgba(11,95,255,0.08); border: 1px solid rgba(11,95,255,0.3)"
        ))
        # Reset all steps to pending first
        for i in range(1, TOTAL_STEPS + 1):
            self._update_step_visual(i, "pending")

        if queued:
            await self._show_waiting(run_id)

        # Subscribe to events FIRST (before any steps can complete)
        event_bus.subscribe(self._on_event, run_id)
        self._run_dir, self._uc_name, self._follow_live = "", "", True
        # Steps that finished before the subscription (a re-run's cached steps 1-2 take
        # milliseconds): replayed, so step 1 is not left "pending" until a page refresh
        await event_bus.replay(self._on_event, run_id)
        ui.timer(0.5, self._load_run_info, once=True)

        # Run sync after a brief delay to catch fast steps
        ui.timer(1.0, lambda: self._sync_steps(run_id), once=True)
        ui.timer(3.0, lambda: self._sync_steps(run_id), once=True)

    async def _sync_steps(self, run_id) -> None:
        """Catch up with steps that completed before the subscription — and with the earlier steps
        of a run that was still waiting or resuming (no session yet when the page opened, OB-16)."""
        if not self.runner or not self.active_run_id or str(self.active_run_id) != str(run_id):
            return
        try:
            state = await self.runner.get_state(str(run_id))
            for i in range(1, TOTAL_STEPS + 1):
                ss = state.get(f"step_{i}_status")
                if ss and i in self.step_widgets:
                    self._update_step_visual(i, ss)
        except Exception:
            pass

    async def view_run(self, run_id: str):
        """Open a finished (or any) run on the run page: steps, artifacts, test cases, logs."""
        if self.runner and not await self._scope.check(self.runner._db, str(run_id), "run.open", "progress"):
            return
        if self.runner and self.runner._db and str(run_id) not in self.runner._active_runs:
            rows = await self.runner._db.execute("SELECT status FROM pipeline_runs WHERE id = ?", (str(run_id),))
            if rows and rows[0]["status"] in ("queued", "resuming"):  # follow it until it starts
                await self.start_tracking(UUID(str(run_id)), queued=True)
                return
        live = self.runner._active_runs.get(str(run_id), {}) if self.runner else {}
        if live.get("status") in ("running", "paused_milestone", "paused_hil"):
            await self.start_tracking(UUID(str(run_id)))
            return
        self.active_run_id = UUID(str(run_id))
        self._run_dir, self._uc_name, self._follow_live = "", "", False
        self._step_started.clear()
        self._run_t0, self._run_elapsed = None, ""
        self.action_btns.set_visibility(False)
        self.milestone_row.set_visibility(False)
        self.completion_row.set_visibility(True)
        status, error, statuses, finished = "unknown", "", {}, False
        if self.runner and self.runner._db:
            try:
                rows = await self.runner._db.execute(
                    "SELECT status, error_summary, current_step, started_at, completed_at, finished_at "
                    "FROM pipeline_runs WHERE id = ?", (str(run_id),))
                if rows:
                    finished = bool(rows[0].get("finished_at"))
                    status = str(rows[0].get("status") or "").replace("PipelineStatus.", "").lower()
                    error = rows[0].get("error_summary") or ""
                    self._run_elapsed = _elapsed_between(rows[0].get("started_at"), rows[0].get("completed_at"))
                    self._run_since = _local_since(rows[0].get("started_at"))
                for sr in await self.runner._db.execute(
                        "SELECT step_number, status FROM step_results WHERE run_id = ? ORDER BY rowid",
                        (str(run_id),)):
                    statuses[sr["step_number"]] = str(sr.get("status") or "").replace("StepStatus.", "").lower()
            except Exception as exc:
                logger.warning("view_run: could not read run %s: %s", str(run_id)[:8], exc)
        for i in range(1, TOTAL_STEPS + 1):
            self._update_step_visual(i, statuses.get(i, "pending"))
        failed = [n for n, st in statuses.items() if st.startswith("failed")]
        done = sum(1 for st in statuses.values() if st in ("passed", "completed"))
        ok = status == "completed"
        self.btn_finish.set_visibility(status in ("completed", "failed") and not finished)
        self.status_icon.name = "check_circle" if ok else "error" if status == "failed" else "history"
        self.status_icon.classes(replace="cr-text-success" if ok else "cr-text-error" if status == "failed"
                                 else "cr-text-dim")
        self.status_label.text = f"Run {str(run_id)[:8]} — {_humanize(status)}"
        self.status_detail.text = error or f"{done}/{TOTAL_STEPS} steps passed"
        self.banner.style(replace="")
        self._set_run_state("completed" if ok else "failed" if status == "failed" else "history")
        self.progress_bar.set_visibility(True)
        self.progress_bar.props(remove="indeterminate")
        self.progress_bar.value = done / TOTAL_STEPS
        self.progress_bar.props(f"color={'positive' if ok else 'negative' if status == 'failed' else 'primary'}")
        await self._load_run_info()
        self._select_step(failed[0] if failed else max(statuses or {1: 0}), user=False)

    async def _on_event(self, event: PipelineEvent):
        # Ignore events if run was already cleared (cancelled/stopped)
        if not self.active_run_id:
            return
        # Another run is open on the run page: its view must not take live events
        if getattr(event, "run_id", None) and str(event.run_id) != str(self.active_run_id):
            return
        # Run UI updates within NiceGUI client context (needed for background task events)
        if self._client:
            try:
                with self._client:
                    await self._handle_event(event)
            except Exception as e:
                logger.error("Event handler failed (with client): %s — %s", event.event_type, e,
                             exc_info=True)
                try:
                    await self._handle_event(event)
                except Exception as e2:
                    logger.error("Event handler failed (no client): %s — %s", event.event_type, e2,
                                 exc_info=True)
        else:
            try:
                await self._handle_event(event)
            except Exception as e:
                logger.error("Event handler failed: %s — %s", event.event_type, e, exc_info=True)

    async def _handle_event(self, event: PipelineEvent):
        if event.event_type == "step_started":
            self._set_run_state("running")
            self._update_step_visual(event.step_number, "in_progress")
            self.status_icon.name = "sync"
            self.status_icon.classes(replace="cr-text-accent cr-spin")
            self.status_label.text = "Pipeline Running"
            self.status_detail.text = f"Step {event.step_number}/{TOTAL_STEPS}: {_SHORT_NAMES.get(event.step_number, '')}"
            self.progress_bar.props(remove="indeterminate")
            self.progress_bar.value = (event.step_number - 1) / TOTAL_STEPS
            self.milestone_row.set_visibility(False)
        elif event.event_type == "step_completed":
            self._update_step_visual(event.step_number, event.payload.get("status", "passed"))
            self.progress_bar.value = event.step_number / TOTAL_STEPS
        elif event.event_type == "step_failed":
            self._update_step_visual(event.step_number, "failed")
        elif event.event_type == "retry_started":
            n = event.payload.get("attempt", 1)
            # Reset all steps from retry target onward to pending
            for sn in range(event.step_number, TOTAL_STEPS + 1):
                if sn in self.step_widgets:
                    self._update_step_visual(sn, "pending")
            self._update_step_visual(event.step_number, "retrying")
            self._set_run_state("running")
            self.status_detail.text = f"Retrying from step {event.step_number} (attempt {n})..."
            self.milestone_row.set_visibility(False)
            self.action_btns.set_visibility(True)
            self.progress_bar.props("color=primary")
            self.status_icon.name = "sync"
            self.status_icon.classes(replace="cr-text-accent cr-spin")
            self.status_label.text = "Pipeline Running"
            self.banner.style(replace=(
                "background: rgba(11,95,255,0.08); border: 1px solid rgba(11,95,255,0.3)"
            ))
        elif event.event_type == "milestone_reached":
            milestone = event.payload.get("milestone", "")
            artifact_file = event.payload.get("artifact_file", "")
            test_status = event.payload.get("test_status")

            # Determine failure type
            has_failures = test_status == "failed"
            is_test_step = event.step_number in (5, 7, 9)
            self._set_run_state("failed" if has_failures else "review")

            if has_failures and is_test_step:
                # Test step failure — tests ran and failed
                self._update_step_visual(event.step_number, "failed")
                self.status_icon.name = "error_outline"
                self.status_icon.classes(replace="cr-text-error")
                self.status_detail.text = "Tests failed — re-run to fix, or skip to continue."
                self.banner.style(replace=(
                    "background: rgba(248,113,113,0.08); border: 1px solid rgba(248,113,113,0.3)"
                ))
            elif has_failures:
                # Generation step failed — block progression
                self._update_step_visual(event.step_number, "failed")
                self.status_icon.name = "error_outline"
                self.status_icon.classes(replace="cr-text-error")
                self.status_detail.text = "Generation failed — re-run to fix, or cancel the pipeline."
                self.banner.style(replace=(
                    "background: rgba(248,113,113,0.08); border: 1px solid rgba(248,113,113,0.3)"
                ))
            else:
                self._update_step_visual(event.step_number, "passed")
                self.status_icon.name = "rate_review"
                self.status_icon.classes(replace="cr-text-warning")
                self.status_detail.text = "Review the generated artifact below, then continue, re-run, or cancel."
                self.banner.style(replace=(
                    "background: rgba(251,191,36,0.08); border: 1px solid rgba(251,191,36,0.3)"
                ))

            self.progress_bar.value = max(event.step_number, 1) / TOTAL_STEPS
            self.progress_bar.props(remove="animate")
            self.status_label.text = f"Review: {milestone}"
            if event.step_number in self.step_widgets:
                self._select_step(event.step_number)
            self.tabs.set_value("overview")

            # Requirements Gathering (step 0) — show interactive questionnaire dialog
            if event.step_number == 0:
                # Get questions from event payload or milestone_waiting
                questions = event.payload.get("questions", [])
                if not questions:
                    from shared.state import milestone_waiting as _mw
                    questions = _mw.get(0, {}).get("questions", [])

                # Update banner to show paused state
                self.status_icon.name = "quiz"
                self.status_icon.classes(replace="cr-text-warning")
                self.status_label.text = f"Requirements Gathering"
                self.status_detail.text = f"{len(questions)} questions need your input"
                self.banner.style(replace=(
                    "background: rgba(251,191,36,0.08); border: 1px solid rgba(251,191,36,0.3)"
                ))
                self.progress_bar.props(remove="stripe animate indeterminate")

                self._show_questionnaire_dialog(questions, milestone)
            elif event.step_number == 2:
                # Requirements Summary review
                self.status_icon.name = "checklist"
                self.status_icon.classes(replace="cr-text-warning")
                self.status_label.text = "Review: Requirements Summary"
                self.status_detail.text = "Review the gathered requirements before proceeding to YAML generation"
                self.banner.style(replace=(
                    "background: rgba(251,191,36,0.08); border: 1px solid rgba(251,191,36,0.3)"
                ))
                self.progress_bar.props(remove="stripe animate indeterminate")

                self._show_milestone_review(event.step_number, milestone, artifact_file,
                                            has_failures=False,
                                            extra_artifact_files=event.payload.get("extra_artifact_files", []))
            else:
                extra_files = event.payload.get("extra_artifact_files", [])
                self._show_milestone_review(event.step_number, milestone, artifact_file,
                                            has_failures=has_failures,
                                            extra_artifact_files=extra_files)
        elif event.event_type == "run_started":
            # a run that waited in the queue starts now (spec 007)
            self._set_run_state("running")
            self.action_btns.set_visibility(True)
            self.progress_bar.set_visibility(True)
            self.progress_bar.props("color=primary indeterminate")
            self._run_t0 = time.monotonic()
            self.status_icon.name = "hourglass_top"
            self.status_icon.classes(replace="cr-text-accent cr-spin")
            self.status_label.text = "Pipeline Starting..."
            self.status_detail.text = "Your run left the queue — initializing agents"
            self.banner.style(replace="background: rgba(11,95,255,0.08); border: 1px solid rgba(11,95,255,0.3)")
            # a resumed run: its earlier steps' results exist only now (the session is new)
            await self._sync_steps(event.run_id)

        elif event.event_type == "run_completed":
            self.status_icon.name = "check_circle"
            self.status_icon.classes(replace="cr-text-success")
            self.status_label.text = "Pipeline Completed"
            self._stop_clock()
            self._set_run_state("completed")
            dur = event.payload.get("duration", 0)
            self.status_detail.text = f"All 10 steps completed in {dur:.0f}s"
            self.banner.style(replace=(
                "background: rgba(74,222,128,0.08); border: 1px solid rgba(74,222,128,0.3)"
            ))
            self.action_btns.set_visibility(False)
            self.completion_row.set_visibility(True)
            self.btn_finish.set_visibility(True)
            self.progress_bar.value = 1.0
            self.progress_bar.props("color=positive")
        elif event.event_type == "run_failed":
            self.status_icon.name = "error"
            self.status_icon.classes(replace="cr-text-error")
            self.status_label.text = "Pipeline Failed"
            self._stop_clock()
            self._set_run_state("failed")
            self.status_detail.text = event.payload.get("error", "Unknown error")
            self.banner.style(replace=(
                "background: rgba(248,113,113,0.08); border: 1px solid rgba(248,113,113,0.3)"
            ))
            self.action_btns.set_visibility(False)
            self.completion_row.set_visibility(True)
            self.progress_bar.props("color=negative")
            self.progress_bar.props(remove="stripe animate")
            # Mark all remaining pending steps as skipped
            self._mark_remaining_skipped()
        elif event.event_type == "run_cancelled":
            self._reset_to_default()
            self.progress_bar.props("color=warning")
            self.progress_bar.props(remove="stripe animate")
            # Mark all remaining pending steps as skipped
            self._mark_remaining_skipped()

    def _update_step_visual(self, step_num: int, status: str):
        if not step_num or step_num not in self.step_widgets:
            return
        base = status.split("(")[0].strip() if "(" in str(status) else str(status)
        cfg = STATUS_CONFIG.get(base, STATUS_CONFIG["pending"])

        w = self.step_widgets[step_num]
        # Node: the step number while pending, the state icon after (spinning while it runs)
        shows_icon = base in ("in_progress", "passed", "failed", "retrying")
        w["num"].set_visibility(not shows_icon)
        w["step_icon"].set_visibility(shows_icon)
        w["step_icon"].name = {"passed": "check", "failed": "close", "in_progress": "sync",
                               "retrying": "replay"}.get(base, cfg["icon"])
        w["step_icon"].classes(replace="cr-spin" if base in ("in_progress", "retrying") else "")
        w["raw_status"] = status
        w["status"].text = {"in_progress": "Running", "passed": "Passed", "failed": "Failed",
                            "retrying": "Retrying", "skipped": "Skipped"}.get(base, _humanize(status))
        if step_num in getattr(self, "segments", {}):
            self.segments[step_num].classes(replace=f"cr-seg is-{base.replace('_', '-')}")

        # Step timing from the live events (restored runs have no start time)
        if base == "in_progress":
            self._step_started[step_num] = time.monotonic()
            w["duration"].text = ""
        elif base in ("passed", "failed") and step_num in self._step_started:
            w["duration"].text = _fmt_duration(time.monotonic() - self._step_started.pop(step_num))
        elif base == "pending":
            w["duration"].text = ""

        # Row state: same size in every state; only colours change (CSS .cr-step-row.is-*)
        state = base if base in ("in_progress", "passed", "failed", "retrying", "skipped") else ""
        selected = " is-selected" if step_num == getattr(self, "_selected_step", 0) else ""
        w["box"].classes(replace=f"cr-step-row {('is-' + state.replace('_', '-')) if state else ''}{selected}")
        self._refresh_kpis()

        # The page follows the running step until the user picks another one
        if base == "in_progress" and getattr(self, "_follow_live", False) and step_num != self._selected_step:
            self._select_step(step_num)
        elif step_num == getattr(self, "_selected_step", 0) and hasattr(self, "sel_title"):
            self._refresh_selected_header()

    def _mark_remaining_skipped(self):
        """Mark all steps still pending as skipped."""
        for sn, w in self.step_widgets.items():
            if w.get("raw_status", "pending") == "pending":
                self._update_step_visual(sn, "skipped")

    def _show_clarifications(self):
        """Offer step 1 for what this step could not settle from the documents."""
        from shared import clarifications
        run_dir = self._run_dir or (str(Path("data/runs") / str(self.active_run_id)) if self.active_run_id else "")
        questions = clarifications.load(run_dir).get("questions") or []
        self.ms_clarify_btn.set_visibility(bool(questions))
        self.ms_clarify_btn.text = f"Clarify in requirements ({len(questions)})"
        self.ms_clarify_info.clear()
        self.ms_clarify_info.set_visibility(bool(questions))
        with self.ms_clarify_info:
            if questions:
                ui.label("Open questions — answer them in step 1, or re-run / cancel:") \
                    .classes("text-caption cr-text-dim")
                for q in questions[:5]:
                    ui.label("• " + str(q.get("question", ""))[:220]).classes("text-caption")

    def _show_milestone_review(self, step_num: int, milestone: str, artifact_file: str,
                               has_failures: bool = False,
                               extra_artifact_files: list[str] | None = None):
        self.milestone_row.set_visibility(True)
        self.action_btns.set_visibility(False)
        self.ms_questionnaire_row.set_visibility(False)
        self._current_artifact_file = artifact_file
        self._current_milestone = milestone
        self._current_milestone_step = step_num
        # Update rerun button label for test steps
        if step_num == 5:
            self.ms_rerun_btn.text = "Re-run Steps 3-5"
        elif step_num == 7:
            self.ms_rerun_btn.text = "Re-run Steps 6-7"
        elif step_num == 9:
            # Dynamic label based on reviewer classification
            rerun_label = "Re-run Steps 6-9"
            try:
                import json as _json
                from shared.state import reviewer_feedback as _fb
                run_id_str = str(self.active_run_id) if self.active_run_id else ""
                fb = _fb.get(f"{run_id_str}:9", "")
                if fb:
                    fb_data = _json.loads(fb) if isinstance(fb, str) else fb
                    classification = fb_data.get("classification", "")
                    if classification == "simulator":
                        rerun_label = "Re-run Step 8-9 (Simulator)"
                    elif classification == "workflow":
                        rerun_label = "Re-run Steps 6-9 (Workflow)"
            except Exception:
                pass
            self.ms_rerun_btn.text = rerun_label
        else:
            self.ms_rerun_btn.text = "Re-run Step"

        # Reset Continue button (may have been changed by questionnaire)
        self.ms_continue_btn.text = "Continue"
        self.ms_continue_btn._props["icon"] = "play_arrow"
        self.ms_continue_btn.props("color=positive")
        # Step 2 (requirements document): edited, not re-run — the requirements step never re-ran
        self.ms_rerun_btn.set_visibility(step_num != 2)
        self.ms_edit_btn.set_visibility(step_num == 2)
        self._show_clarifications()

        # On failure: block Continue for all step types
        if has_failures:
            if step_num in (5, 7, 9):
                # Test step failure — allow skip as workaround
                self.ms_continue_btn.set_visibility(False)
                self.ms_skip_btn.set_visibility(True)
            else:
                # Generation step failure — must re-run or cancel, no skip
                self.ms_continue_btn.set_visibility(False)
                self.ms_skip_btn.set_visibility(False)
        else:
            self.ms_continue_btn.set_visibility(True)
            self.ms_skip_btn.set_visibility(False)

        from pathlib import Path
        fname = Path(artifact_file).name if artifact_file else "no artifact"
        self.ms_artifact_label.text = f"{milestone}  —  {fname}"
        self.ms_view_btn.set_visibility(bool(artifact_file))

        # Render extra artifact files (view + download)
        self.ms_extra_files_row.clear()
        if extra_artifact_files:
            from nicegui import app as nicegui_app
            with self.ms_extra_files_row:
                for fpath in extra_artifact_files:
                    if fpath and Path(fpath).exists():
                        ef_name = Path(fpath).name
                        try:
                            serve_url = nicegui_app.add_media_file(local_file=fpath)
                        except Exception:
                            serve_url = ""
                        ui.icon("article", size="18px").classes("cr-text-accent")
                        ui.label(ef_name).classes("text-body2 font-mono")
                        if serve_url:
                            ui.button("View", icon="visibility",
                                      on_click=lambda u=serve_url, f=fpath: (record_document("document.view", f),
                                                                       ui.navigate.to(u, new_tab=True))) \
                                .props("flat color=primary size=sm no-caps")
                            ui.button("Download", icon="download",
                                      on_click=lambda u=serve_url, f=fpath: (record_document("document.download", f),
                                                                       ui.download(u))) \
                                .props("flat color=positive size=sm no-caps")
            self.ms_extra_files_row.set_visibility(True)
        else:
            self.ms_extra_files_row.set_visibility(False)

    def _show_questionnaire_dialog(self, questions: list, milestone_name: str):
        """Show milestone row with questionnaire controls and auto-open dialog."""
        self.milestone_row.set_visibility(True)
        self.action_btns.set_visibility(False)
        self._current_milestone_step = 0
        self._current_milestone = milestone_name
        self._pending_questions = questions

        # Update milestone row labels
        self.ms_artifact_label.text = f"{milestone_name} — {len(questions)} questions need your input"
        self.ms_view_btn.set_visibility(False)
        self.ms_rerun_btn.set_visibility(False)
        self.ms_skip_btn.set_visibility(False)
        self.ms_extra_files_row.set_visibility(False)

        # Show Answer Questions + Skip buttons
        self.ms_continue_btn.text = "Skip & Use Defaults"
        self.ms_continue_btn.props("color=grey-6")

        # Populate questionnaire button row (cleared first to avoid duplicates)
        self.ms_questionnaire_row.clear()
        with self.ms_questionnaire_row:
            ui.button("Answer Questions", icon="quiz",
                      on_click=lambda q=questions, m=milestone_name: self._open_questionnaire_dialog(q, m)) \
                .props("unelevated color=primary size=sm no-caps")
        self.ms_questionnaire_row.set_visibility(True)

        # Auto-open the dialog on first show
        self._open_questionnaire_dialog(questions, milestone_name)

    def _open_questionnaire_dialog(self, questions: list, milestone_name: str):
        """Populate and open the pre-built questionnaire dialog — light+dark hybrid theme."""
        cat_labels = {
            "node_activity": ("Node & Activity", "dns"),
            "commands": ("Commands & Parameters", "terminal"),
            "orchestration": ("Orchestration Details", "account_tree"),
        }
        imp_colors = {
            "critical": "negative",
            "recommended": "warning",
            "optional": "grey-6",
        }

        by_cat: dict[str, list] = {}
        for q in questions:
            if isinstance(q, dict):
                by_cat.setdefault(q.get("category", "other"), []).append(q)

        self._q_answer_inputs.clear()
        dialog = self._q_dialog
        card = self._q_card

        # Clear previous content and rebuild inside the pre-built card
        card.clear()
        with card:
            # ── Header — dark accent bar ──
            with ui.element("div").classes("w-full px-6 py-4").style(
                "flex-shrink:0; "
                "background: linear-gradient(135deg, #1e1e32 0%, #2d2b55 100%); "
                "color: #ffffff"
            ):
                with ui.row().classes("items-center justify-between w-full"):
                    with ui.row().classes("items-center gap-3"):
                        ui.icon("quiz", size="28px").style("color: #a78bfa")
                        with ui.column().classes("gap-0"):
                            ui.label(milestone_name).style(
                                "font-size: 1.2rem; font-weight: 700; "
                                "letter-spacing: 0.3px; color: #fff"
                            )
                            ui.label(
                                "Answer the questions below to configure your pipeline run"
                            ).style("font-size: 0.8rem; color: rgba(255,255,255,0.6)")
                    with ui.row().classes("items-center gap-3"):
                        critical_count = sum(
                            1 for q in questions
                            if isinstance(q, dict) and q.get("importance") == "critical"
                        )
                        ui.badge(f"{len(questions)} questions", color="primary") \
                            .props("rounded")
                        if critical_count:
                            ui.badge(f"{critical_count} critical", color="red-5") \
                                .props("rounded")
                        ui.button(icon="close", on_click=dialog.close) \
                            .props("flat round size=sm") \
                            .style("color: rgba(255,255,255,0.7)")

            # ── Scrollable question form — light background ──
            with ui.scroll_area().style("flex:1; min-height:0"):
                with ui.column().classes("w-full gap-5 pa-6").style(
                    "max-width: 880px; margin: 0 auto"
                ):
                    for cat, cat_questions in by_cat.items():
                        label, icon = cat_labels.get(
                            cat, (cat.replace("_", " ").title(), "help")
                        )

                        with ui.element("div").classes("w-full").style(
                            "background: #ffffff; border: 1px solid #e2e5f1; "
                            "border-radius: 12px; overflow: hidden; "
                            "box-shadow: 0 1px 3px rgba(0,0,0,0.06)"
                        ):
                            with ui.element("div").classes("w-full px-5 py-3").style(
                                "background: linear-gradient(90deg, #f0edff 0%, #f8f7ff 100%); "
                                "border-bottom: 1px solid #e2e5f1"
                            ):
                                with ui.row().classes("items-center gap-2"):
                                    ui.icon(icon, size="20px").style("color: #0B5FFF")
                                    ui.label(label).style(
                                        "font-size: 0.95rem; font-weight: 600; color: #2d2b55"
                                    )
                                    ui.badge(f"{len(cat_questions)}", color="primary") \
                                        .props("rounded dense").style("color: #4a4578")

                            with ui.column().classes("w-full gap-0"):
                                for idx, q in enumerate(cat_questions):
                                    if not isinstance(q, dict):
                                        continue
                                    q_id = q.get("id", "")
                                    q_text = q.get("question", "?")
                                    q_type = q.get("input_type", "text")
                                    q_default = q.get("default_value", "")
                                    q_imp = q.get("importance", "optional")
                                    q_context = q.get("context", "")
                                    q_options = q.get("options", [])
                                    q_helper = q.get("helper_text", "")
                                    default_str = str(q_default) if q_default is not None else ""

                                    border_top = (
                                        "border-top: 1px solid #eef0f6;" if idx > 0 else ""
                                    )
                                    with ui.element("div").classes("w-full").style(
                                        f"padding: 16px 20px; {border_top}"
                                    ):
                                        with ui.row().classes("items-start gap-3 w-full"):
                                            ui.badge(
                                                q_imp.upper(),
                                                color=imp_colors.get(q_imp, "grey-6"),
                                            ).props("rounded dense").style(
                                                "margin-top: 3px; font-size: 0.65rem"
                                            )
                                            with ui.column().classes("flex-1 gap-1"):
                                                ui.label(q_text).style(
                                                    "font-size: 0.9rem; font-weight: 500; "
                                                    "color: #1a1a2e; line-height: 1.4"
                                                )
                                                if q_context:
                                                    ui.label(q_context).style(
                                                        "font-size: 0.78rem; color: #6b7280; "
                                                        "line-height: 1.3"
                                                    )

                                        with ui.element("div").classes("w-full").style(
                                            "margin-top: 10px; padding-left: 36px"
                                        ):
                                            if q_type == "boolean" or (
                                                q_options and len(q_options) == 2
                                                and any(
                                                    str(o).lower() in ("yes", "no", "true", "false")
                                                    for o in q_options
                                                )
                                            ):
                                                default_val = default_str.lower() in (
                                                    "true", "yes", "1",
                                                )
                                                inp = ui.switch("Yes", value=default_val) \
                                                    .props("color=primary dense")
                                                self._q_answer_inputs[q_id] = inp

                                            elif q_options and len(q_options) <= 5:
                                                opts_with_other = [str(o) for o in q_options] + ["Other"]
                                                sel = (
                                                    default_str
                                                    if default_str in [str(o) for o in q_options]
                                                    else str(q_options[0])
                                                )
                                                inp = ui.radio(opts_with_other, value=sel) \
                                                    .props("dense color=primary inline")
                                                other_input = (
                                                    ui.input(placeholder="Specify your answer")
                                                    .props("outlined dense")
                                                    .classes("w-full mt-2")
                                                    .style("max-width: 400px")
                                                    .bind_visibility_from(
                                                        inp, "value",
                                                        backward=lambda v: v == "Other",
                                                    )
                                                )
                                                self._q_answer_inputs[q_id] = (inp, other_input)

                                            elif q_options:
                                                opts_with_other = [str(o) for o in q_options] + ["Other"]
                                                sel = (
                                                    default_str
                                                    if default_str in [str(o) for o in q_options]
                                                    else str(q_options[0])
                                                )
                                                inp = (
                                                    ui.select(opts_with_other, value=sel)
                                                    .props("outlined dense")
                                                    .classes("w-full")
                                                    .style("max-width: 400px")
                                                )
                                                other_input = (
                                                    ui.input(placeholder="Specify your answer")
                                                    .props("outlined dense")
                                                    .classes("w-full mt-2")
                                                    .style("max-width: 400px")
                                                    .bind_visibility_from(
                                                        inp, "value",
                                                        backward=lambda v: v == "Other",
                                                    )
                                                )
                                                self._q_answer_inputs[q_id] = (inp, other_input)

                                            elif q_type == "password":
                                                inp = (
                                                    ui.input(
                                                        placeholder=q_helper or "Enter value",
                                                        value=default_str,
                                                        password=True,
                                                    )
                                                    .props("outlined dense")
                                                    .classes("w-full")
                                                    .style("max-width: 400px")
                                                )
                                                self._q_answer_inputs[q_id] = inp

                                            else:
                                                inp = (
                                                    ui.input(
                                                        placeholder=q_helper or "Enter value",
                                                        value=default_str,
                                                    )
                                                    .props("outlined dense")
                                                    .classes("w-full")
                                                    .style("max-width: 400px")
                                                )
                                                self._q_answer_inputs[q_id] = inp

                                            if default_str and q_type not in ("boolean",):
                                                ui.label(f"Default: {default_str}").style(
                                                    "font-size: 0.72rem; color: #0B5FFF; "
                                                    "font-style: italic; margin-top: 4px"
                                                )

            # ── Footer — dark accent bar ──
            with ui.element("div").classes("w-full px-6 py-4").style(
                "border-top: 1px solid #e2e5f1; flex-shrink:0; "
                "background: linear-gradient(135deg, #1e1e32 0%, #2d2b55 100%)"
            ):
                with ui.row().classes("w-full justify-end gap-3"):
                    action(ui.button("Use All Defaults", icon="skip_next",
                              on_click=lambda: self._submit_questionnaire(
                                  dialog, self._q_answer_inputs, use_defaults=True)) \
                        .props("flat no-caps").style("color: rgba(255,255,255,0.6)"), "progress")
                    action(ui.button("Submit Answers", icon="send",
                              on_click=lambda: self._submit_questionnaire(
                                  dialog, self._q_answer_inputs, use_defaults=False)) \
                        .props("unelevated color=primary no-caps"), "progress")

        dialog.open()

    @requires("progress", "questionnaire.submit")
    @_own_run("questionnaire.submit")
    def _submit_questionnaire(self, dialog, answer_inputs: dict, use_defaults: bool = False):
        """Collect answers and respond to the milestone."""
        import json as _json

        answers = {}
        for q_id, inp in answer_inputs.items():
            if isinstance(inp, tuple):
                # Radio/select with "Other" option
                main_inp, other_inp = inp
                val = main_inp.value if hasattr(main_inp, 'value') else ""
                if val == "Other":
                    val = other_inp.value if hasattr(other_inp, 'value') else ""
            else:
                val = inp.value if hasattr(inp, 'value') else ""
            if val is not None and str(val).strip():
                answers[q_id] = val

        dialog.close()

        if not self.runner or not self.active_run_id:
            return

        run_id_str = str(self.active_run_id)

        # Use runner's respond_milestone for step 0 — this handles
        # milestone_decisions, event signaling, and _active_runs status
        answer_str = _json.dumps(answers) if answers else "continue"
        self.runner.respond_milestone(run_id_str, 0, answer_str)

        logger.info("Questionnaire submitted: %d answers for run %s", len(answers), run_id_str[:8])
        record_later("questionnaire.submit", target_type="run", target_id=run_id_str, run_id=run_id_str,
                         details={"defaults": bool(use_defaults), "answered": len(answers),
                                  "answers": {k: str(v)[:300] for k, v in answers.items()}})

        self.status_icon.name = "sync"
        self.status_icon.classes(replace="cr-text-accent cr-spin")
        self.status_label.text = "Processing answers..."
        self.status_detail.text = f"Submitted {len(answers)} answers"
        self.milestone_row.set_visibility(False)
        self.action_btns.set_visibility(True)
        self.banner.style(replace=(
            "background: rgba(11,95,255,0.08); border: 1px solid rgba(11,95,255,0.3)"
        ))
        self.progress_bar.props("stripe animate")

    async def _open_requirements_editor(self):
        """Step 2 review: what is wrong, the CIQ workbook and its sheets, and the captured answers."""
        import json as _json
        run_dir = self._run_dir or (str(Path("data/runs") / str(self.active_run_id)) if self.active_run_id else "")
        try:
            req = _json.loads((Path(run_dir) / "requirements.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            req = {}
        answers = req.get("developer_answers") or {}
        questions = req.get("developer_questions") or {}
        used = list(((req.get("doc_extracted") or {}).get("ciq_sheets") or {}).keys())
        excluded = [x for x in req.get("excluded_ciq_sheets") or [] if x not in used]
        ciq_path = req.get("ciq_file_path") or ""
        if not ciq_path and self.runner and self.runner._db and self.active_run_id:
            rows = await self.runner._db.execute(
                "SELECT d.ciq_file_path FROM pipeline_runs r JOIN uc_document_sets d ON r.document_set_id = d.id "
                "WHERE r.id = ?", (str(self.active_run_id),))
            ciq_path = rows[0]["ciq_file_path"] if rows else ""
        # the CIQ part of the edit: a replacement workbook and its sheet tick boxes
        ciq = {"path": "", "sheets": {}, "excluded": sorted(excluded)}

        dialog, card = self._edit_dialog, self._edit_card
        card.clear()
        with card:
            with ui.column().classes("w-full gap-1 px-5 pt-4 pb-3").style("border-bottom: 1px solid var(--cr-border)"):
                ui.label("Edit requirements").classes("text-h6 font-semibold")
                ui.label("Describe what needs correcting, fix the CIQ, or change an answer that was captured "
                         "wrongly. The requirements are compiled again and shown for review; the changes also "
                         "apply to the next runs of this use case.").classes("text-caption cr-text-dim")
            inputs: dict[str, Any] = {}
            with ui.column().classes("w-full gap-4 px-5 py-4").style("flex: 1; overflow-y: auto; min-height: 0"):
                with ui.column().classes("w-full gap-1"):
                    ui.label("What is wrong or needs clarifying").classes("text-subtitle2 font-semibold")
                    note = ui.textarea(placeholder="e.g. VER 13.0.3.7 loads plain files; every other version, "
                                                   "including 17.x.x.x, loads tar files") \
                        .props("outlined rows=4").classes("w-full cr-boxed")

                with ui.column().classes("w-full gap-2"):
                    ui.label("CIQ workbook").classes("text-subtitle2 font-semibold")
                    ciq_label = ui.label(f"In use: {Path(ciq_path).name if ciq_path else '—'}") \
                        .classes("text-caption cr-text-dim")
                    ui.label("Sheets used by this activity (untick a sheet that is not part of it)") \
                        .classes("text-caption font-medium")
                    sheet_row = ui.row().classes("w-full gap-x-6 gap-y-1")

                    def render_sheets(names: list[str], off: set[str]) -> None:
                        sheet_row.clear()
                        ciq["sheets"].clear()
                        with sheet_row:
                            for n in names:
                                ciq["sheets"][n] = ui.checkbox(n, value=n not in off)
                            if not names:
                                ui.label("No sheets read from the CIQ").classes("text-caption cr-text-dim")

                    render_sheets(used + excluded, set(excluded))
                    action(ui.upload(label="Replace the CIQ workbook (.xlsx)", auto_upload=True, max_files=1,
                                     on_upload=lambda e: self._receive_ciq_upload(e, ciq, ciq_label, render_sheets))
                           .props('accept=".xlsx" flat bordered').classes("w-full"), "progress")

                if answers:
                    ui.label(f"Captured answers ({len(answers)})").classes("text-subtitle2 font-semibold")
                for qid, val in answers.items():
                    with ui.column().classes("w-full gap-1"):
                        ui.label(str(questions.get(qid) or qid)).classes("text-caption font-medium")
                        inputs[qid] = (val, ui.textarea(value="" if val is None else str(val))
                                       .props("outlined autogrow").classes("w-full cr-boxed"))
            with ui.row().classes("w-full justify-end gap-2 px-5 py-3").style("border-top: 1px solid var(--cr-border)"):
                ui.button("Cancel", on_click=dialog.close).props("flat no-caps")
                action(ui.button("Update requirements", icon="check",
                                 on_click=lambda: self._submit_requirement_edits(dialog, inputs, note, run_dir, ciq))
                       .props("unelevated color=primary no-caps"), "progress")
        dialog.open()

    @requires("progress", "requirements.edit")
    async def _receive_ciq_upload(self, e, ciq: dict, ciq_label, render_sheets) -> None:
        """A corrected CIQ workbook: kept in the uploads, its visible sheets offered for ticking."""
        import uuid
        from core.config import PROJECT_ROOT
        from skills.requirement_gathering.parsers import parse_ciq
        uploads = PROJECT_ROOT / "data" / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        name = e.file.name
        if not name.lower().endswith(".xlsx"):
            ui.notify("The CIQ must be an .xlsx workbook", type="warning")
            return
        dest = uploads / f"{str(uuid.uuid4())[:8]}_{name}"
        await e.file.save(dest)
        sheets = list(parse_ciq(ciq_file_path=str(dest)))
        if not sheets:
            ui.notify(f"No sheet could be read from {name}", type="negative")
            return
        ciq["path"] = str(dest)
        ciq_label.text = f"New: {name} (replaces the one in use when you update)"
        render_sheets(sheets, set(ciq["excluded"]) & set(sheets))
        ui.notify(f"Read {len(sheets)} sheets from {name}", type="info")
        await record("document.upload", target_type="document", target_id=name, details={"slot": "ciq_edit"})

    @requires("progress", "requirements.edit")
    @_own_run("requirements.edit")
    async def _submit_requirement_edits(self, dialog, inputs: dict, note, run_dir: str, ciq: dict):
        """Hand the edit to step 1 (disk) and answer the step 2 review with ``edit``."""
        from shared import requirement_edits
        changed = {qid: inp.value.strip() for qid, (old, inp) in inputs.items()
                   if (inp.value or "").strip() != str("" if old is None else old).strip()}
        text = (note.value or "").strip()
        new_ciq = ciq.get("path") or ""
        excluded = sorted(n for n, box in ciq.get("sheets", {}).items() if not box.value)
        sheets_changed = bool(new_ciq) or excluded != ciq.get("excluded", [])
        if ciq.get("sheets") and len(excluded) == len(ciq["sheets"]):
            ui.notify("Keep at least one CIQ sheet", type="warning")
            return
        if not changed and not text and not sheets_changed:
            ui.notify("Describe what needs correcting, change the CIQ or change an answer", type="warning")
            return
        if not self.runner or not self.active_run_id or str(self.active_run_id) not in self.runner._active_runs:
            ui.notify("The run is not waiting for review any more", type="warning")
            return
        user = self._scope.user
        run_id_str = str(self.active_run_id)
        edit = requirement_edits.save(run_dir, changed, text, user.email if user else "", ciq_file_path=new_ciq,
                                      excluded_sheets=excluded if sheets_changed else None)
        if self.runner._db and (text or new_ciq):
            # The run's document set: the next run of this use case starts from the note, and the
            # run's documents name the CIQ it used
            try:
                if text:
                    await self.runner._db.execute(
                        "UPDATE uc_document_sets SET developer_notes = TRIM(COALESCE(developer_notes, '') || ? ) "
                        "WHERE id = (SELECT document_set_id FROM pipeline_runs WHERE id = ?)",
                        ("\n\n" + requirement_edits.note_block(text, edit["by"], edit["at"]), run_id_str))
                if new_ciq:
                    await self.runner._db.execute(
                        "UPDATE uc_document_sets SET ciq_file_path = ? "
                        "WHERE id = (SELECT document_set_id FROM pipeline_runs WHERE id = ?)", (new_ciq, run_id_str))
            except Exception as e:  # noqa: BLE001
                logger.warning("Document set of run %s not updated: %s", run_id_str[:8], e)
        dialog.close()
        self.runner.respond_milestone(run_id_str, 2, "edit")
        await record("requirements.edit", target_type="run", target_id=run_id_str, run_id=run_id_str,
                     details={"answers_changed": sorted(changed), "note": text[:500],
                              "ciq_replaced": Path(new_ciq).name if new_ciq else "",
                              "excluded_sheets": excluded if sheets_changed else None})
        parts = [f"{len(changed)} answer(s) changed"] if changed else []
        parts += ["a note added"] if text else []
        parts += ["CIQ updated"] if sheets_changed else []
        self.milestone_row.set_visibility(False)
        self.action_btns.set_visibility(True)
        self.status_icon.name = "sync"
        self.status_icon.classes(replace="cr-text-accent cr-spin")
        self.status_label.text = "Updating requirements..."
        self.status_detail.text = ", ".join(parts) + " — the document is compiled again for review"

    def _show_artifact_dialog(self):
        import html as html_mod
        from pathlib import Path
        from nicegui import app

        fpath = getattr(self, "_current_artifact_file", "")
        milestone = getattr(self, "_current_milestone", "Artifact")
        is_html = fpath.endswith(".html") if fpath else False

        dialog = ui.dialog().props("maximized")
        with dialog, ui.card().classes("w-full h-full p-0").style(
            "display: flex; flex-direction: column; max-height: 100vh"
        ):
            # Header bar
            with ui.row().classes("items-center justify-between w-full px-4 py-2").style(
                "border-bottom: 1px solid var(--cr-border); flex-shrink: 0"
            ):
                ui.label(f"{milestone} — {Path(fpath).name if fpath else ''}") \
                    .classes("text-subtitle2 font-bold font-mono")
                with ui.row().classes("items-center gap-1"):
                    if fpath and Path(fpath).exists():
                        dl_url = app.add_media_file(local_file=fpath)
                        ui.button("Download", icon="download",
                                  on_click=lambda u=dl_url, f=fpath: (record_document("document.download", f),
                                                           ui.download(u))) \
                            .props("flat color=positive size=sm no-caps")
                    ui.button(icon="close", on_click=dialog.close) \
                        .props("flat round size=sm color=grey-6")

            # Content
            if fpath and Path(fpath).exists():
                if is_html:
                    # Serve the file and show in iframe (no scroll_area wrapper)
                    url = app.add_media_file(local_file=fpath)
                    ui.element("iframe").props(f'src="{url}"').style(
                        "width: 100%; flex: 1; border: none; min-height: 0"
                    )
                else:
                    content = Path(fpath).read_text(encoding="utf-8")
                    with ui.scroll_area().style("flex: 1; min-height: 0"):
                        escaped = html_mod.escape(content)
                        ui.html(
                            f'<pre style="margin:0; padding: 16px; font-size: 12px; '
                            f'line-height: 1.5; font-family: monospace">{escaped}</pre>'
                        )
            else:
                ui.label("No content available").classes("p-4 cr-text-dim")
        dialog.open()

    @requires("progress", "milestone.decision")
    @_own_run("milestone.decision")
    async def _milestone_respond(self, decision: str):
        self.milestone_row.set_visibility(False)
        if not self.runner or not self.active_run_id:
            return

        step_num = getattr(self, "_current_milestone_step", 0)
        run_id_str = str(self.active_run_id)

        # Check if the runner has an active pipeline task for this run
        has_active_task = run_id_str in self.runner._active_runs
        if not has_active_task:
            # Pipeline task is gone (server restart) — handle locally
            if decision == "cancel":
                if self.runner._db:
                    await self.runner._db.execute_insert(
                        "UPDATE pipeline_runs SET status=?, completed_at=? WHERE id=?",
                        ("cancelled", datetime.now(timezone.utc).isoformat(), run_id_str),
                    )
                self._reset_to_default()
                ui.notify("Pipeline cancelled", type="warning")
                return
            else:
                # Cannot resume without active task — notify user
                ui.notify("Pipeline task not found — please start a new run", type="warning")
                self._reset_to_default()
                return

        self.runner.respond_milestone(run_id_str, step_num, decision)
        await record("milestone.decision", target_type="run", target_id=run_id_str, run_id=run_id_str,
                     details={"step": step_num, "decision": decision})

        if decision == "continue":
            self.status_icon.name = "sync"
            self.status_icon.classes(replace="cr-text-accent cr-spin")
            self.status_label.text = "Pipeline Running"
            self.status_detail.text = "Continuing..."
            self.banner.style(replace=(
                "background: rgba(11,95,255,0.08); border: 1px solid rgba(11,95,255,0.3)"
            ))
            self.progress_bar.props("stripe animate")
            self.action_btns.set_visibility(True)
        elif decision == "clarify":
            self.status_icon.name = "quiz"
            self.status_icon.classes(replace="cr-text-accent")
            self.status_label.text = "Back to Step 1 — clarifying requirements"
            self.status_detail.text = "Answer the open questions; the later steps then run again with them"
            for s in range(1, TOTAL_STEPS + 1):
                self._update_step_visual(s, "pending")
            self.action_btns.set_visibility(True)
        elif decision == "rerun":
            self.status_icon.name = "replay"
            self.status_icon.classes(replace="cr-text-accent cr-spin")
            ms_step = step_num
            if ms_step == 5:
                self.status_label.text = "Re-running Steps 3-5"
                self.status_detail.text = "Re-generating validation rules, JSON output, then re-testing..."
                for s in (3, 4, 5):
                    self._update_step_visual(s, "pending")
            elif ms_step == 7:
                self.status_label.text = "Re-running Steps 6-7"
                self.status_detail.text = "Re-generating workflow, then re-testing MOP..."
                for s in (6, 7):
                    self._update_step_visual(s, "pending")
            elif ms_step == 9:
                # Dynamic based on reviewer classification
                rerun_from = 6  # default
                try:
                    import json as _json
                    from shared.state import reviewer_feedback as _fb
                    run_id_str = str(self.active_run_id) if self.active_run_id else ""
                    fb = _fb.get(f"{run_id_str}:9", "")
                    if fb:
                        fb_data = _json.loads(fb) if isinstance(fb, str) else fb
                        if fb_data.get("classification") == "simulator":
                            rerun_from = 8
                except Exception:
                    pass
                if rerun_from == 8:
                    self.status_label.text = "Re-running Steps 8-9"
                    self.status_detail.text = "Re-generating simulator config, then re-testing execution..."
                    for s in (8, 9):
                        self._update_step_visual(s, "pending")
                else:
                    self.status_label.text = "Re-running Steps 6-9"
                    self.status_detail.text = "Re-generating workflow, MOP, simulator config, then re-testing..."
                    for s in (6, 7, 8, 9):
                        self._update_step_visual(s, "pending")
            else:
                self.status_label.text = "Re-running Step"
                self.status_detail.text = "Re-generating artifact..."
            self.banner.style(replace=(
                "background: rgba(11,95,255,0.08); border: 1px solid rgba(11,95,255,0.3)"
            ))
            self.progress_bar.props("stripe animate")
            self.action_btns.set_visibility(True)
        elif decision == "cancel":
            # Fully reset UI — don't rely on run_cancelled event
            self.status_icon.name = "cancel"
            self.status_icon.classes(replace="cr-text-error")
            self.status_label.text = "Pipeline Cancelled"
            self.status_detail.text = "Cancelled by user at milestone review"
            self.banner.style(replace=(
                "background: rgba(248,113,113,0.08); border: 1px solid rgba(248,113,113,0.3)"
            ))
            self.progress_bar.props("color=negative")
            self.progress_bar.props(remove="stripe animate")
            self.action_btns.set_visibility(False)
            self.completion_row.set_visibility(True)
            self.active_run_id = None

    def _reset_to_default(self):
        """Reset progress section to default 'No active pipeline' state."""
        self.active_run_id = None
        self.action_btns.set_visibility(False)
        self.completion_row.set_visibility(False)
        self.btn_finish.set_visibility(False)
        self.milestone_row.set_visibility(False)
        self.progress_bar.set_visibility(False)
        self.status_icon.name = "hourglass_empty"
        self.status_icon.classes(replace="cr-text-dim")
        self.status_label.text = "No active pipeline run"
        self.status_detail.text = "Start a new run from New Run"
        self.banner.style(replace="")
        self._run_t0, self._run_elapsed = None, ""
        # Forget the run: its folder (artifacts, test cases, KPIs), UC and step durations
        self._run_dir, self._uc_name, self._follow_live = "", "", False
        self._step_started.clear()
        for i in range(1, TOTAL_STEPS + 1):
            self._update_step_visual(i, "pending")
            if self.step_widgets.get(i, {}).get("duration") is not None:
                self.step_widgets[i]["duration"].text = ""
        self._set_run_state("idle")
        # The step panel shows "No run selected" instead of the last run's step (e.g. Final Report)
        if hasattr(self, "tabs"):
            self.tabs.value = "overview"
        self._select_step(1)

    @requires("progress", "run.finish")
    @_own_run("run.finish")
    async def _finish(self):
        """The user is done with this completed run: it leaves Live Run (spec 008 U-D7)."""
        if not self.runner or not self.active_run_id:
            return
        run_id = str(self.active_run_id)
        user = self._scope.user
        if not await self.runner.mark_finished(run_id, user.id if user else "cli"):
            ui.notify("Only a completed or failed run that is not finished yet can be marked finished", type="warning")
            return
        await record("run.finish", target_type="run", target_id=run_id, run_id=run_id, uc_name=self._uc_name or "")
        ui.notify(f"{self._uc_name or 'Run'} marked as finished — it stays in Run History", type="positive")
        self._reset_to_default()

    @requires("progress", "run.restart")
    @_own_run("run.restart")
    async def _restart(self):
        """Cancel current run and notify user to start new run."""
        if not self.runner or not self.active_run_id:
            return
        run_id = str(self.active_run_id)
        try:
            self._reset_to_default()
            await self.runner.cancel_run(run_id)
            ui.notify("Pipeline cancelled — start a new run from Upload section", type="warning")
            await record("run.restart", target_type="run", target_id=run_id, run_id=run_id)
        except Exception as exc:
            ui.notify(f"Restart failed: {exc}", type="negative")

    @requires("progress", "run.stop")
    @_own_run("run.stop")
    async def _stop(self):
        """Stop the pipeline — preserves completed artifacts."""
        if not self.runner or not self.active_run_id:
            return
        run_id = str(self.active_run_id)
        self.active_run_id = None

        # Show stopping state
        self.action_btns.set_visibility(False)
        self.milestone_row.set_visibility(False)
        self.completion_row.set_visibility(False)
        self.status_icon.name = "hourglass_top"
        self.status_icon.classes(replace="cr-text-warning cr-spin")
        self.status_label.text = "Stopping Pipeline..."
        self.status_detail.text = "Waiting for current step to finish"
        self.progress_bar.set_visibility(True)
        self.progress_bar.value = 1.0
        self.progress_bar.props("color=warning stripe animate")

        try:
            await self.runner.cancel_run(run_id)
        except Exception:
            pass

        await asyncio.sleep(2)

        self._reset_to_default()
        ui.notify("Pipeline stopped", type="warning")
        await record("run.stop", target_type="run", target_id=run_id, run_id=run_id)

    @requires("progress", "run.cancel")
    @_own_run("run.cancel")
    async def _cancel(self):
        """Cancel the pipeline — shows cancelling state, then resets."""
        if not self.runner or not self.active_run_id:
            return
        run_id = str(self.active_run_id)
        self.active_run_id = None  # prevent events from overwriting

        # Show cancelling state
        self.action_btns.set_visibility(False)
        self.milestone_row.set_visibility(False)
        self.completion_row.set_visibility(False)
        self.status_icon.name = "hourglass_top"
        self.status_icon.classes(replace="cr-text-error cr-spin")
        self.status_label.text = "Cancelling Pipeline..."
        self.status_detail.text = "Waiting for background tasks to stop"
        self.progress_bar.set_visibility(True)
        self.progress_bar.value = 1.0
        self.progress_bar.props("color=negative stripe animate")

        # Cancel backend
        try:
            await self.runner.cancel_run(run_id)
        except Exception:
            pass

        # Show cancelling state for at least 2 seconds
        await asyncio.sleep(2)

        self._reset_to_default()
        ui.notify("Pipeline cancelled", type="negative")
        await record("run.cancel", target_type="run", target_id=run_id, run_id=run_id)

    async def _go_details(self):
        if self.active_run_id and self.on_view_details:
            await self.on_view_details(str(self.active_run_id))

    async def _go_outputs(self):
        if self.active_run_id and self.on_view_outputs:
            await self.on_view_outputs(str(self.active_run_id))


# ---------------------------------------------------------------------------
# Run page helpers
# ---------------------------------------------------------------------------

_STEP_AGENTS = {
    1: "requirements_gatherer", 2: "requirements_gatherer", 3: "ciq_validation_json_gen",
    4: "ciq_validation_json_gen", 5: "ciq_validation_json_qa", 6: "network_config_gen + helper_script_gen",
    7: "network_config_mop_qa", 8: "network_simulator_setup", 9: "network_config_exec_qa",
    10: "report_generator",
}
_STEP_OUTPUTS = {
    1: "Parsed documents", 2: "requirements.json, requirements summary",
    3: "validation-rules.yaml", 4: "json-output.yaml, CIQ JSON data",
    5: "CIQ QA report", 6: "Execution workflow YAML, helper scripts",
    7: "MOP QA report", 8: "Simulator configs, simulator report",
    9: "Test-case report and results", 10: "Final pipeline report",
}
# File-name fragments per step (relative path inside the run folder)
_STEP_FILE_RULES = {
    1: ("requirements.json",), 2: ("requirements-summary", "requirements.json"),
    3: ("validation-rules",), 4: ("json-output", "json_data/"),
    5: ("ciq-qa-report", "json_data/", "ciq_validation/", "qa/"), 6: ("{uc}.yaml", "scripts/"),
    7: ("mop-qa-report", "mop_output/"), 8: ("simulator-report", "simulator-config", "simulator/manifest"),
    9: ("network-qa-report", "test-cases.json", "exec_report/"), 10: ("{uc}_report.",),
}
_STEP_LOG_KEYS = {
    1: ("requirements",), 2: ("requirements",), 3: ("ciq_validation_json_gen", "CIQ_GEN"),
    4: ("ciq_validation_json_gen", "CIQ_GEN"), 5: ("ciq_validation_json_qa", "CIQ_QA"),
    6: ("network_config_gen", "helper_script_gen", "HELPER_SCRIPT"), 7: ("mop_qa", "MOP_QA"),
    8: ("network_simulator_setup", "SIMULATOR"), 9: ("network_config_qa", "NETWORK_CONFIG_QA", "scenarios"),
    10: ("report",),
}


def _humanize(status: str) -> str:
    return str(status or "pending").replace("_", " ").capitalize()


def _empty_state(icon: str, title: str, text: str) -> None:
    with ui.column().classes("cr-empty items-center w-full"):
        ui.icon(icon, size="36px").classes("cr-text-dim")
        ui.label(title).classes("font-semibold")
        ui.label(text).classes("text-caption cr-text-dim")


def _step_files(run_dir: str, uc_name: str, step: int) -> list:
    if not run_dir or not Path(run_dir).is_dir():
        return []
    root = Path(run_dir)
    rules = [r.replace("{uc}", uc_name) for r in _STEP_FILE_RULES.get(step, ())]
    found = []
    for f in sorted(root.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(root).as_posix()
        if rel.count("/") > 1 or rel.startswith(("exec_output/", "simulator/logs", "simulator/outputs",
                                                   "simulator/vfs", "simulator/config")):
            continue
        top_level = "/" not in rel
        if any((r.endswith("/") and rel.startswith(r)) or (not r.endswith("/") and top_level and r in f.name)
               for r in rules):
            found.append(f)
    return found[:60]


def _file_icon(path) -> str:
    suffix = Path(path).suffix.lower()
    return {".html": "web", ".json": "data_object", ".yaml": "code", ".yml": "code",
            ".py": "terminal", ".xml": "code"}.get(suffix, "description")


def _size(n: int) -> str:
    return f"{n} B" if n < 1024 else f"{n / 1024:.1f} KB" if n < 1024 * 1024 else f"{n / 1048576:.1f} MB"


def _open_view(path) -> None:
    from ui.pages.output_viewer_section import OutputViewerSection
    OutputViewerSection._view_dialog(Path(path))


def _esc(text: str) -> str:
    import html as _html
    return _html.escape(text)


def _step_log_lines(step: int, since: str = "") -> list[str]:
    """flow.log lines of a step; with ``since`` ('YYYY-MM-DD HH:MM:SS', local time) only this run's."""
    from core.logging_config import LOG_DIR
    log = Path(LOG_DIR) / "flow.log"
    if not log.exists():
        return []
    try:
        with open(log, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 600_000))
            text = fh.read().decode("utf-8", errors="replace")
    except Exception:
        return []
    keys = _STEP_LOG_KEYS.get(step, ())
    return [l for l in text.splitlines() if any(k.lower() in l.lower() for k in keys)
            and (not since or l[:19] >= since)]


def _fmt_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds}s" if seconds < 60 else f"{seconds // 60}m {seconds % 60:02d}s"


def _fmt_clock(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    return f"{h:d}:{rem // 60:02d}:{rem % 60:02d}" if h else f"{rem // 60:02d}:{rem % 60:02d}"


def _test_summary(run_dir: str, uc_name: str) -> str:
    """'2 / 2 passed' from step 9's test-case results, '' before step 9 ran."""
    import json as _json
    if not run_dir or not uc_name:
        return ""
    path = Path(run_dir) / f"{uc_name}_test-cases.json"
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return f"{data.get('passed', 0)} / {data.get('total', 0)} passed"


_LOG_LINE = re.compile(r"^(\S+ \S+) \[(\w+)\s*\] (\S+): (.*)$")


def _console(lines: list[str], title: str, live: bool, compact: bool = False) -> str:
    """Terminal-style log view: time, level (coloured), logger, message."""
    rows = []
    for line in lines:
        m = _LOG_LINE.match(line)
        if not m:
            rows.append(f"<div class='ln'><span class='msg'>{_esc(line)}</span></div>")
            continue
        ts, level, src, msg = m.groups()
        rows.append(f"<div class='ln'><span class='ts'>{_esc(ts.split(' ')[-1][:8])}</span>"
                    f"<span class='lv lv-{_esc(level.lower())}'>{_esc(level[:5])}</span>"
                    f"<span class='src'>{_esc(src.rsplit('.', 1)[-1])}</span>"
                    f"<span class='msg'>{_esc(msg)}</span></div>")
    tag = "<span class='cr-console-live'><i></i>LIVE</span>" if live else ""
    return (f"<div class='cr-console{' is-compact' if compact else ''}'>"
            f"<div class='cr-console-bar'><span class='dots'><i></i><i></i><i></i></span>"
            f"<span class='title'>{_esc(title)}</span>{tag}</div>"
            f"<div class='cr-console-body'>{''.join(reversed(rows))}</div></div>")


def _elapsed_between(started, completed) -> str:
    """Run duration from the DB timestamps (ISO strings), '' when unknown."""
    try:
        a = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(completed).replace("Z", "+00:00"))
        return _fmt_clock((b - a).total_seconds()) if b >= a else ""
    except (TypeError, ValueError):
        return ""


def _local_since(started) -> str:
    """DB started_at (UTC ISO) → local 'YYYY-MM-DD HH:MM:SS' as flow.log writes it, a few seconds early."""
    try:
        t = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (t.astimezone() - timedelta(seconds=5)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return ""
