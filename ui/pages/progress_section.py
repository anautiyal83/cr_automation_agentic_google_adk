"""Pipeline progress tracking — polished 10-step visual tracker."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any
from pathlib import Path
from uuid import UUID

logger = logging.getLogger(__name__)

from nicegui import ui, context

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
        with ui.column().classes("w-full gap-3"):
            # Run header: status, actions, progress
            self.banner = ui.element("div").classes("w-full cr-run-banner")
            with self.banner:
                with ui.row().classes("items-center justify-between w-full no-wrap"):
                    with ui.row().classes("items-center gap-3 no-wrap"):
                        self.status_icon = ui.icon("hourglass_empty", size="26px") \
                            .classes("cr-text-dim")
                        with ui.column().classes("gap-0"):
                            self.status_label = ui.label("No active pipeline run") \
                                .classes("text-subtitle1 font-semibold")
                            self.status_detail = ui.label("Start a new run from New Run") \
                                .classes("text-caption cr-text-dim")
                    with ui.row().classes("gap-2 no-wrap") as self.action_btns:
                        self.restart_btn = ui.button("Restart", icon="restart_alt",
                            on_click=self._restart) \
                            .props("outline color=primary size=sm no-caps")
                        self.stop_btn = ui.button("Stop", icon="pause",
                            on_click=self._stop) \
                            .props("outline color=warning size=sm no-caps")
                        self.cancel_btn = ui.button("Cancel", icon="close",
                            on_click=self._cancel) \
                            .props("outline color=negative size=sm no-caps")
                    self.action_btns.set_visibility(False)
                # Progress bar
                self.progress_bar = ui.linear_progress(value=0, show_value=False) \
                    .props("color=primary rounded size=6px").classes("mt-3")
                self.progress_bar.set_visibility(False)

            # Run page: step list (left) + selected step detail (right)
            with ui.element("div").classes("cr-run-layout"):
                with ui.element("div").classes("cr-run-steps"):
                    ui.label("Pipeline steps").classes("cr-run-steps-title")
                    for i in range(1, 11):
                        self._step_widget(i)

                with ui.element("div").classes("cr-run-detail"):
                    with ui.row().classes("items-center justify-between w-full no-wrap cr-run-detail-head"):
                        with ui.column().classes("gap-0 min-w-0"):
                            self.sel_title = ui.label("").classes("text-subtitle1 font-semibold")
                            self.sel_meta = ui.label("").classes("text-caption cr-text-dim")
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
        self._select_step(1, user=False)

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
                self.ms_continue_btn = ui.button("Approve & continue", icon="check",
                    on_click=lambda: self._milestone_respond("continue")) \
                    .props("unelevated color=primary size=sm no-caps")
                self.ms_skip_btn = ui.button("Skip & continue", icon="skip_next",
                    on_click=lambda: self._milestone_respond("continue")) \
                    .props("unelevated color=warning size=sm no-caps")
                self.ms_skip_btn.set_visibility(False)
                self.ms_rerun_btn = ui.button("Re-run step", icon="replay",
                    on_click=lambda: self._milestone_respond("rerun")) \
                    .props("outline color=primary size=sm no-caps")
                self.ms_cancel_btn = ui.button("Cancel pipeline", icon="stop",
                    on_click=lambda: self._milestone_respond("cancel")) \
                    .props("outline color=negative size=sm no-caps")
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

    def _step_widget(self, step_num: int):
        with ui.element("div").classes("cr-step-row") as box:
            box.on("click", lambda _, n=step_num: self._select_step(n, user=True))
            icon_container = ui.element("div").classes("cr-step-circle")
            with icon_container:
                step_icon = ui.icon(STEP_ICONS.get(step_num, "circle"), size="14px").classes("cr-text-dim")
            with ui.column().classes("gap-0 min-w-0 flex-1"):
                label = ui.label(f"Step {step_num}").classes("cr-step-num")
                name = ui.label(_SHORT_NAMES.get(step_num, "")).classes("cr-step-row-name")
            with ui.column().classes("items-end gap-0"):
                status_label = ui.label("Pending").classes("cr-step-status")
                duration = ui.label("").classes("cr-step-duration")

        self.step_widgets[step_num] = {
            "col": box, "box": box, "icon_container": icon_container, "step_icon": step_icon,
            "label": label, "name": name, "status": status_label, "duration": duration,
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
        self.sel_title.text = f"Step {n} · {_SHORT_NAMES.get(n, '')}"
        meta = [f"Duration {w['duration'].text}" if w.get("duration") and w["duration"].text else ""]
        if self._uc_name:
            meta.append(self._uc_name)
        self.sel_meta.text = " · ".join(m for m in meta if m)
        base = raw.split("(")[0].strip()
        self.sel_status.content = (f"<span class='cr-status cr-status-{base.replace('_', '-')}'>"
                                   f"{_humanize(raw)}</span>")

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
            with ui.element("div").classes("cr-kv"):
                for k, v in (("Step", f"{n} — {_SHORT_NAMES.get(n, '')}"),
                             ("Status", _humanize(raw)),
                             ("Duration", w["duration"].text if w.get("duration") and w["duration"].text else "—"),
                             ("Agent", _STEP_AGENTS.get(n, "—")),
                             ("Produces", _STEP_OUTPUTS.get(n, "—"))):
                    ui.label(k).classes("cr-kv-key")
                    ui.label(v).classes("cr-kv-value")

    def _render_artifacts(self):
        self.artifacts_box.clear()
        with self.artifacts_box:
            files = _step_files(self._run_dir, self._uc_name, self._selected_step)
            if not files:
                _empty_state("description", "No artifacts yet",
                             "Files this step writes to the run folder appear here.")
                return
            for f in files:
                with ui.row().classes("cr-file-row items-center no-wrap"):
                    ui.icon(_file_icon(f), size="18px").classes("cr-text-accent")
                    with ui.column().classes("gap-0 flex-1 min-w-0"):
                        ui.label(str(f.relative_to(self._run_dir))).classes("cr-file-name")
                        ui.label(_size(f.stat().st_size)).classes("text-caption cr-text-dim")
                    ui.button("View", icon="visibility", on_click=lambda _, p=f: _open_view(p)) \
                        .props("flat dense color=primary size=sm no-caps")
                    ui.button("Download", icon="download", on_click=lambda _, p=f: ui.download(str(p))) \
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
            lines = _step_log_lines(self._selected_step)
            if not lines:
                _empty_state("subject", "No log lines", "Agent log lines for this step appear here.")
                return
            ui.html("<pre class='cr-log'>" + "\n".join(_esc(l) for l in lines[-300:]) + "</pre>")

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
        self._refresh_selected_header()
        self._refresh_tab()

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
                    "ORDER BY r.started_at DESC LIMIT 1", ()
                )
                if not rows:
                    return
                run = rows[0]
                db_run_id = run["id"]

                # Check if this run is actually still alive in the runner
                # If not, it's an orphaned record from a crashed/restarted server
                if db_run_id not in self.runner._active_runs:
                    logger.info("_restore: orphaned DB run %s — marking as failed", db_run_id[:8])
                    try:
                        from datetime import datetime, timezone
                        await self.runner._db.execute_insert(
                            "UPDATE pipeline_runs SET status='failed', error_summary='Server restarted — pipeline lost', completed_at=? WHERE id=?",
                            (datetime.now(timezone.utc).isoformat(), db_run_id),
                        )
                    except Exception:
                        pass
                    return

                run_id = UUID(db_run_id)
                status = str(run.get("status") or "").replace("PipelineStatus.", "").lower()
                step = run.get("current_step", 0)
                uc_name = (run.get("uc_name") or "")

            if not run_id:
                logger.info("_restore: no active run found in memory or DB")
                return

            logger.info("_restore: found run %s, status=%s, step=%d, uc=%s",
                        str(run_id)[:8], status, step, uc_name)
            self.active_run_id = run_id
            self.action_btns.set_visibility(True)
            self.progress_bar.set_visibility(True)
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

    async def start_tracking(self, run_id: UUID):
        self.active_run_id = run_id
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

        # Subscribe to events FIRST (before any steps can complete)
        event_bus.subscribe(self._on_event, run_id)
        self._run_dir, self._uc_name, self._follow_live = "", "", True
        ui.timer(0.5, self._load_run_info, once=True)

        # Periodic sync: catch up with steps that completed before subscription
        async def _sync_steps():
            if not self.active_run_id or self.active_run_id != run_id:
                return
            try:
                state = await self.runner.get_state(str(run_id))
                for i in range(1, TOTAL_STEPS + 1):
                    ss = state.get(f"step_{i}_status")
                    if ss and i in self.step_widgets:
                        self._update_step_visual(i, ss)
            except Exception:
                pass

        # Run sync after a brief delay to catch fast steps
        ui.timer(1.0, _sync_steps, once=True)
        ui.timer(3.0, _sync_steps, once=True)

    async def view_run(self, run_id: str):
        """Open a finished (or any) run on the run page: steps, artifacts, test cases, logs."""
        live = self.runner._active_runs.get(str(run_id), {}) if self.runner else {}
        if live.get("status") in ("running", "paused_milestone", "paused_hil"):
            await self.start_tracking(UUID(str(run_id)))
            return
        self.active_run_id = UUID(str(run_id))
        self._run_dir, self._uc_name, self._follow_live = "", "", False
        self._step_started.clear()
        self.action_btns.set_visibility(False)
        self.milestone_row.set_visibility(False)
        self.completion_row.set_visibility(True)
        status, error, statuses = "unknown", "", {}
        if self.runner and self.runner._db:
            try:
                rows = await self.runner._db.execute(
                    "SELECT status, error_summary, current_step FROM pipeline_runs WHERE id = ?", (str(run_id),))
                if rows:
                    status = str(rows[0].get("status") or "").replace("PipelineStatus.", "").lower()
                    error = rows[0].get("error_summary") or ""
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
        self.status_icon.name = "check_circle" if ok else "error" if status == "failed" else "history"
        self.status_icon.classes(replace="cr-text-success" if ok else "cr-text-error" if status == "failed"
                                 else "cr-text-dim")
        self.status_label.text = f"Run {str(run_id)[:8]} — {_humanize(status)}"
        self.status_detail.text = error or f"{done}/{TOTAL_STEPS} steps passed"
        self.banner.style(replace="")
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
        elif event.event_type == "run_completed":
            self.status_icon.name = "check_circle"
            self.status_icon.classes(replace="cr-text-success")
            self.status_label.text = "Pipeline Completed"
            dur = event.payload.get("duration", 0)
            self.status_detail.text = f"All 10 steps completed in {dur:.0f}s"
            self.banner.style(replace=(
                "background: rgba(74,222,128,0.08); border: 1px solid rgba(74,222,128,0.3)"
            ))
            self.action_btns.set_visibility(False)
            self.completion_row.set_visibility(True)
            self.progress_bar.value = 1.0
            self.progress_bar.props("color=positive")
        elif event.event_type == "run_failed":
            self.status_icon.name = "error"
            self.status_icon.classes(replace="cr-text-error")
            self.status_label.text = "Pipeline Failed"
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
        active_cls = "cr-step-circle-active" if base == "in_progress" else ""
        w["icon_container"].classes(replace=f"cr-step-circle {active_cls}")
        w["icon_container"].style(replace=f"border-color: {cfg['color']}")
        w["step_icon"].name = cfg["icon"]
        w["step_icon"].classes(replace=f"text-[{cfg['text']}]")
        w["raw_status"] = status
        w["status"].text = _humanize(status)
        w["status"].style(replace=f"color: {cfg['text']}")

        # Step timing from the live events (restored runs have no start time)
        if base == "in_progress":
            self._step_started[step_num] = time.monotonic()
            w["duration"].text = ""
        elif base in ("passed", "failed") and step_num in self._step_started:
            w["duration"].text = _fmt_duration(time.monotonic() - self._step_started.pop(step_num))
        elif base == "pending":
            w["duration"].text = ""

        # Row state: same size in every state; only colours change (CSS .cr-step-row.is-*)
        state = base if base in ("in_progress", "passed", "failed", "retrying") else ""
        selected = " is-selected" if step_num == getattr(self, "_selected_step", 0) else ""
        w["box"].classes(replace=f"cr-step-row {('is-' + state.replace('_', '-')) if state else ''}{selected}")

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
        self.ms_rerun_btn.set_visibility(True)

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
        if has_failures:
            self._show_step_questions(step_num)

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
                                      on_click=lambda u=serve_url: ui.navigate.to(u, new_tab=True)) \
                                .props("flat color=primary size=sm no-caps")
                            ui.button("Download", icon="download",
                                      on_click=lambda u=serve_url: ui.download(u)) \
                                .props("flat color=positive size=sm no-caps")
            self.ms_extra_files_row.set_visibility(True)
        else:
            self.ms_extra_files_row.set_visibility(False)

    def _show_step_questions(self, step_num: int) -> None:
        """Questions a failed generation step left in its error file (OB-13): the developer
        answers here, the answers become developer notes and the step regenerates."""
        import json as _json
        from pathlib import Path
        if not self.runner or not self.active_run_id:
            return
        run_dir = Path(self.runner._config.get("output_directory", "./data/runs")) / str(self.active_run_id)
        try:
            questions = _json.loads((run_dir / f"step_{step_num}_error.json").read_text(encoding="utf-8")) \
                .get("questions") or []
        except (OSError, ValueError):
            questions = []
        if not questions:
            return
        self.ms_questionnaire_row.clear()
        inputs = []
        with self.ms_questionnaire_row:
            with ui.column().classes("w-full gap-2 cr-card-inner p-3"):
                with ui.row().classes("items-center gap-2"):
                    ui.icon("help_outline", size="18px").classes("cr-text-warning")
                    ui.label("The documents don't say how to get these values — your answer is added "
                             "to the UC's developer notes and this step regenerates.") \
                        .classes("text-body2 font-semibold")
                for q in questions:
                    ui.label(q.get("question", "")).classes("text-body2")
                    inputs.append((q, ui.textarea(placeholder="Command or CIQ column, e.g. a lookup command")
                                   .props("outlined dense autogrow").classes("w-full font-mono")))

                async def submit():
                    answers = [{"question": q.get("question", ""), "answer": box.value or ""} for q, box in inputs]
                    if not any(a["answer"].strip() for a in answers):
                        ui.notify("Enter at least one answer", type="warning")
                        return
                    self.runner.answer_milestone_questions(str(self.active_run_id), step_num, answers,
                                                           rerun=False)
                    ui.notify("Answers saved to the developer notes — regenerating", type="positive")
                    await self._milestone_respond("rerun")

                ui.button("Answer & regenerate", icon="send", on_click=submit) \
                    .props("unelevated color=primary size=sm no-caps")
        self.ms_questionnaire_row.set_visibility(True)

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
                    ui.button("Use All Defaults", icon="skip_next",
                              on_click=lambda: self._submit_questionnaire(
                                  dialog, self._q_answer_inputs, use_defaults=True)) \
                        .props("flat no-caps").style("color: rgba(255,255,255,0.6)")
                    ui.button("Submit Answers", icon="send",
                              on_click=lambda: self._submit_questionnaire(
                                  dialog, self._q_answer_inputs, use_defaults=False)) \
                        .props("unelevated color=primary no-caps")

        dialog.open()

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
                                  on_click=lambda u=dl_url: ui.download(u)) \
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
        self.milestone_row.set_visibility(False)
        self.progress_bar.set_visibility(False)
        self.status_icon.name = "hourglass_empty"
        self.status_icon.classes(replace="cr-text-dim")
        self.status_label.text = "No active pipeline run"
        self.status_detail.text = "Start a new run from New Run"
        self.banner.style(replace="")
        for i in range(1, TOTAL_STEPS + 1):
            self._update_step_visual(i, "pending")

    async def _restart(self):
        """Cancel current run and notify user to start new run."""
        if not self.runner or not self.active_run_id:
            return
        run_id = str(self.active_run_id)
        try:
            self._reset_to_default()
            await self.runner.cancel_run(run_id)
            ui.notify("Pipeline cancelled — start a new run from Upload section", type="warning")
        except Exception as exc:
            ui.notify(f"Restart failed: {exc}", type="negative")

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
    5: ("ciq-qa-report", "ciq_validation/", "qa/"), 6: ("{uc}.yaml", "scripts/"),
    7: ("mop-qa-report", "mop_output/"), 8: ("simulator-report", "simulator-config", "simulator/manifest"),
    9: ("network-qa-report", "test-cases.json"), 10: ("pipeline-report", "final-report"),
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


def _step_log_lines(step: int) -> list[str]:
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
    return [l for l in text.splitlines() if any(k.lower() in l.lower() for k in keys)]


def _fmt_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds}s" if seconds < 60 else f"{seconds // 60}m {seconds % 60:02d}s"
