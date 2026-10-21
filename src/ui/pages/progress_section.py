"""Pipeline progress tracking — polished 10-step visual tracker."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from uuid import UUID

logger = logging.getLogger(__name__)

from nicegui import ui, context

from src.core.events import PipelineEvent
from src.core.events import event_bus

STATUS_CONFIG = {
    "pending":     {"color": "#94A3B8", "icon": "radio_button_unchecked", "text": "#94A3B8"},
    "in_progress": {"color": "#6C63FF", "icon": "sync",                  "text": "#6C63FF"},
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
        with ui.column().classes("w-full gap-4"):
            # Status banner
            self.banner = ui.element("div").classes("w-full p-4 rounded-xl cr-card-inner").style(
                "background: rgba(108,99,255,0.06)"
            )
            with self.banner:
                with ui.row().classes("items-center justify-between w-full"):
                    with ui.row().classes("items-center gap-3"):
                        self.status_icon = ui.icon("hourglass_empty", size="28px") \
                            .classes("cr-text-dim")
                        with ui.column().classes("gap-0"):
                            self.status_label = ui.label("No active pipeline run") \
                                .classes("text-subtitle1 font-semibold")
                            self.status_detail = ui.label("Start a new run from the upload section") \
                                .classes("text-caption cr-text-dim")
                    with ui.row().classes("gap-1") as self.action_btns:
                        self.restart_btn = ui.button("Restart", icon="restart_alt",
                            on_click=self._restart) \
                            .props("flat color=deep-purple-6 size=sm no-caps")
                        self.stop_btn = ui.button("Stop", icon="pause",
                            on_click=self._stop) \
                            .props("flat color=warning size=sm no-caps")
                        self.cancel_btn = ui.button("Cancel", icon="cancel",
                            on_click=self._cancel) \
                            .props("flat color=red-4 size=sm no-caps")
                    self.action_btns.set_visibility(False)

            # Progress bar
            self.progress_bar = ui.linear_progress(value=0, show_value=False) \
                .props("color=deep-purple-6 stripe animate rounded size=8px")
            self.progress_bar.set_visibility(False)

            # Step tracker — single-row compact timeline
            with ui.row().classes("w-full gap-0 justify-between py-1 px-2"):
                for i in range(1, 11):
                    self._step_widget(i)
                    if i < 10:
                        connector = ui.element("div").classes("cr-step-connector")
                        self.step_widgets[i]["connector"] = connector

            # Error / retry detail area
            self.detail_container = ui.column().classes("w-full")

            # Milestone review panel (hidden until milestone reached)
            self.milestone_row = ui.column().classes("w-full gap-3 mt-2")
            self.milestone_row.set_visibility(False)
            with self.milestone_row:
                # Artifact info row
                with ui.row().classes("w-full items-center gap-3 cr-card-inner p-3"):
                    ui.icon("description", size="20px").classes("cr-text-accent")
                    self.ms_artifact_label = ui.label("").classes("text-body2 font-semibold flex-1")
                    self.ms_view_btn = ui.button("View Artifact", icon="visibility",
                        on_click=self._show_artifact_dialog) \
                        .props("flat color=deep-purple-6 size=sm no-caps")
                # Extra artifact files (view/download) — populated dynamically
                self.ms_extra_files_row = ui.row().classes("w-full items-center gap-3 cr-card-inner p-3")
                self.ms_extra_files_row.set_visibility(False)
                # Action buttons
                with ui.row().classes("w-full gap-3"):
                    self.ms_continue_btn = ui.button("Continue", icon="play_arrow",
                        on_click=lambda: self._milestone_respond("continue")) \
                        .props("unelevated color=positive size=sm no-caps")
                    self.ms_skip_btn = ui.button("Skip & Continue", icon="skip_next",
                        on_click=lambda: self._milestone_respond("continue")) \
                        .props("unelevated color=warning size=sm no-caps")
                    self.ms_skip_btn.set_visibility(False)
                    self.ms_rerun_btn = ui.button("Re-run Step", icon="replay",
                        on_click=lambda: self._milestone_respond("rerun")) \
                        .props("unelevated color=deep-purple-6 size=sm no-caps")
                    self.ms_cancel_btn = ui.button("Cancel Pipeline", icon="stop",
                        on_click=lambda: self._milestone_respond("cancel")) \
                        .props("unelevated color=negative size=sm no-caps")

            # Completion actions (hidden until pipeline finishes)
            self.completion_row = ui.row().classes("w-full items-center gap-3 mt-2")
            self.completion_row.set_visibility(False)
            with self.completion_row:
                self.btn_details = ui.button("View Full Details", icon="info_outline",
                                             on_click=self._go_details) \
                    .props("unelevated color=deep-purple-6 size=sm no-caps")
                self.btn_outputs = ui.button("Browse Outputs", icon="source",
                                             on_click=self._go_outputs) \
                    .props("unelevated color=teal-6 size=sm no-caps")

    def _step_widget(self, step_num: int):
        with ui.column().classes("items-center gap-0 flex-shrink-0 flex-1") as col:
            # Circle icon
            icon_container = ui.element("div").classes("cr-step-circle")
            with icon_container:
                step_icon = ui.icon(
                    STEP_ICONS.get(step_num, "circle"), size="14px"
                ).classes("cr-text-dim")

            # Step label
            label = ui.label(f"{step_num}").classes("text-[9px] cr-text-dim font-semibold")
            name = ui.label(_SHORT_NAMES.get(step_num, "")) \
                .classes("text-[9px] cr-text-dim text-center") \
                .style("white-space: nowrap; overflow: visible")
            status_label = ui.label("pending") \
                .classes("text-[9px] cr-text-dim") \
                .style("white-space: nowrap")

            self.step_widgets[step_num] = {
                "col": col, "icon_container": icon_container, "step_icon": step_icon,
                "label": label, "name": name, "status": status_label,
            }

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
                self.progress_bar.props("color=deep-purple-6 stripe animate")
                self.banner.style(replace=(
                    "background: rgba(108,99,255,0.08); border: 1px solid rgba(108,99,255,0.3)"
                ))
                event_bus.subscribe(self._on_event, run_id)
            elif status == "paused_milestone":
                self.status_icon.name = "rate_review"
                self.status_icon.classes(replace="cr-text-warning")
                self.status_label.text = f"Milestone Review — {uc_name}"
                self.status_detail.text = f"Step {step}/{TOTAL_STEPS} completed — awaiting review"
                self.progress_bar.value = step / TOTAL_STEPS
                self.progress_bar.props("color=deep-purple-6")
                self.progress_bar.props(remove="animate")
                self.banner.style(replace=(
                    "background: rgba(251,191,36,0.08); border: 1px solid rgba(251,191,36,0.3)"
                ))

                # Resolve milestone artifacts
                milestone_name = _MILESTONE_NAMES.get(step, f"Step {step}")
                artifact_file = ""
                extra_files = []
                try:
                    ms_state = await self.runner.get_state(str(run_id))
                    artifact_file, extra_files = self.runner._resolve_milestone_artifacts(
                        ms_state, step
                    )
                except Exception:
                    pass

                # Check for test failures
                has_failures = False
                if step in (5, 7, 9):
                    try:
                        ms_state = ms_state if 'ms_state' in dir() else await self.runner.get_state(str(run_id))
                        has_failures = ms_state.get(f"step_{step}_status") == "failed"
                    except Exception:
                        pass

                self._show_milestone_review(step, milestone_name, artifact_file,
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
        self.progress_bar.props("color=deep-purple-6 stripe animate indeterminate")
        self.status_icon.name = "hourglass_top"
        self.status_icon.classes(replace="cr-text-accent cr-spin")
        self.status_label.text = "Pipeline Starting..."
        self.status_detail.text = "Initializing agents and loading models — this may take a few seconds"
        self.banner.style(replace=(
            "background: rgba(108,99,255,0.08); border: 1px solid rgba(108,99,255,0.3)"
        ))
        # Reset all steps to pending first
        for i in range(1, TOTAL_STEPS + 1):
            self._update_step_visual(i, "pending")

        # Subscribe to events FIRST (before any steps can complete)
        event_bus.subscribe(self._on_event, run_id)

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

    async def _on_event(self, event: PipelineEvent):
        # Ignore events if run was already cleared (cancelled/stopped)
        if not self.active_run_id:
            return
        # Run UI updates within NiceGUI client context (needed for background task events)
        if self._client:
            try:
                with self._client:
                    await self._handle_event(event)
            except Exception:
                await self._handle_event(event)
        else:
            await self._handle_event(event)

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
            self._update_step_visual(event.step_number, "retrying")
            self.status_detail.text = f"Retrying step {event.step_number} (attempt {n})..."
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
                # LLM generation step had validation issues but produced output
                self._update_step_visual(event.step_number, "failed")
                self.status_icon.name = "warning"
                self.status_icon.classes(replace="cr-text-warning")
                self.status_detail.text = "Generated artifact has validation warnings — review below, then continue or re-run."
                self.banner.style(replace=(
                    "background: rgba(251,191,36,0.08); border: 1px solid rgba(251,191,36,0.3)"
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

            # Requirements Gathering (step 0) — show interactive questionnaire dialog
            if event.step_number == 0:
                # Get questions from event payload or milestone_waiting
                questions = event.payload.get("questions", [])
                if not questions:
                    from src.nodes.milestone_nodes import milestone_waiting as _mw
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
        elif event.event_type == "run_cancelled":
            self._reset_to_default()
            self.progress_bar.props("color=warning")
            self.progress_bar.props(remove="stripe animate")

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
        w["status"].text = status
        w["status"].classes(replace=f"text-[10px] text-[{cfg['text']}] font-semibold")

        # Update connector line color for completed steps
        if base in ("passed", "in_progress") and "connector" in w:
            w["connector"].style(replace=f"background: {cfg['color']}")

    def _show_milestone_review(self, step_num: int, milestone: str, artifact_file: str,
                               has_failures: bool = False,
                               extra_artifact_files: list[str] | None = None):
        self.milestone_row.set_visibility(True)
        self.action_btns.set_visibility(False)
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
                from src.nodes.milestone_nodes import reviewer_feedback as _fb
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

        # For test steps with failures: disable Continue, show Skip & Continue
        if has_failures and step_num in (5, 7, 9):
            self.ms_continue_btn.set_visibility(False)
            self.ms_skip_btn.set_visibility(True)
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
                                      on_click=lambda u=serve_url: ui.navigate.to(u, new_tab=True)) \
                                .props("flat color=deep-purple-6 size=sm no-caps")
                            ui.button("Download", icon="download",
                                      on_click=lambda u=serve_url: ui.download(u)) \
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

        # Add Answer Questions button in the action row
        with self.milestone_row:
            with ui.row().classes("w-full gap-3"):
                ui.button("Answer Questions", icon="quiz",
                          on_click=lambda q=questions, m=milestone_name: self._open_questionnaire_dialog(q, m)) \
                    .props("unelevated color=deep-purple-6 size=sm no-caps")

        # Auto-open the dialog on first show
        self._open_questionnaire_dialog(questions, milestone_name)

    def _open_questionnaire_dialog(self, questions: list, milestone_name: str):
        """Open a maximized dialog with interactive question form — dark themed."""
        import json as _json

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

        # Group questions by category
        by_cat: dict[str, list] = {}
        for q in questions:
            if isinstance(q, dict):
                cat = q.get("category", "other")
                by_cat.setdefault(cat, []).append(q)

        # Track answer inputs
        answer_inputs: dict[str, Any] = {}

        dialog = ui.dialog().props("maximized")
        with dialog, ui.card().classes("w-full h-full p-0").style(
            "display: flex; flex-direction: column; max-height: 100vh; "
            "background: var(--cr-surface); color: var(--cr-text)"
        ):
            # Header
            with ui.row().classes("items-center justify-between w-full px-5 py-3").style(
                "border-bottom: 1px solid var(--cr-border); flex-shrink: 0; "
                "background: var(--cr-surface-light)"
            ):
                with ui.row().classes("items-center gap-2"):
                    ui.icon("quiz", size="24px").classes("cr-text-accent")
                    ui.label(milestone_name).classes("text-subtitle1 font-bold cr-stat-value")
                with ui.row().classes("items-center gap-3"):
                    critical_count = sum(1 for q in questions if isinstance(q, dict) and q.get("importance") == "critical")
                    ui.badge(f"{len(questions)} questions", color="deep-purple-6").props("rounded")
                    if critical_count:
                        ui.badge(f"{critical_count} critical", color="negative").props("rounded")
                    ui.button(icon="close", on_click=dialog.close) \
                        .props("flat round size=sm color=grey-6")

            # Scrollable question form
            with ui.scroll_area().style("flex: 1; min-height: 0"):
                with ui.column().classes("w-full gap-3 pa-4").style("max-width: 800px; margin: 0 auto"):
                    for cat, cat_questions in by_cat.items():
                        label, icon = cat_labels.get(cat, (cat.replace("_", " ").title(), "help"))
                        with ui.expansion(label, icon=icon).classes("w-full").style(
                            "background: var(--cr-surface-light); border: 1px solid var(--cr-border); border-radius: 8px"
                        ).props("default-opened dense header-class='text-subtitle2 font-bold'"):
                            for q in cat_questions:
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

                                with ui.element("div").classes("w-full").style(
                                    "background: var(--cr-surface); border: 1px solid var(--cr-border); "
                                    "border-radius: 6px; padding: 12px; margin: 4px 0"
                                ):
                                    # Question header
                                    with ui.row().classes("items-start gap-2 w-full"):
                                        ui.badge(q_imp.upper(), color=imp_colors.get(q_imp, "grey-6")) \
                                            .props("rounded").style("margin-top: 2px")
                                        with ui.column().classes("flex-1 gap-1"):
                                            ui.label(q_text).classes("text-body2 font-semibold")
                                            if q_context:
                                                ui.label(q_context).classes("text-caption").style(
                                                    "color: var(--cr-text-dim)"
                                                )

                                    # Input — auto-detect best type, pre-fill defaults
                                    default_str = str(q_default) if q_default is not None else ""

                                    if q_type == "boolean" or (q_options and len(q_options) == 2 and
                                            any(str(o).lower() in ("yes", "no", "true", "false") for o in q_options)):
                                        default_val = default_str.lower() in ("true", "yes", "1")
                                        inp = ui.switch("Yes", value=default_val) \
                                            .props("color=deep-purple-6 dense")
                                        answer_inputs[q_id] = inp

                                    elif q_options and len(q_options) <= 5:
                                        # Pre-select default or first option
                                        sel = default_str if default_str in [str(o) for o in q_options] else str(q_options[0])
                                        inp = ui.radio(q_options, value=sel) \
                                            .props("dense color=deep-purple-6")
                                        answer_inputs[q_id] = inp

                                    elif q_options:
                                        sel = default_str if default_str in [str(o) for o in q_options] else str(q_options[0])
                                        inp = ui.select(q_options, value=sel) \
                                            .props("outlined dense dark filled").classes("w-full")
                                        answer_inputs[q_id] = inp

                                    elif q_type == "password":
                                        with ui.element("div").classes("w-full").style(
                                            "background: #2a2a3e; border: 1px solid #3a3a50; border-radius: 4px; padding: 6px 10px"
                                        ):
                                            inp = ui.input(placeholder=q_helper or "Enter value",
                                                           value=default_str,
                                                           password=True, password_toggle=True) \
                                                .props("borderless dense").classes("w-full") \
                                                .style("color: #e8e8f0 !important")
                                        answer_inputs[q_id] = inp

                                    else:
                                        with ui.element("div").classes("w-full").style(
                                            "background: #2a2a3e; border: 1px solid #3a3a50; border-radius: 4px; padding: 6px 10px"
                                        ):
                                            inp = ui.input(placeholder=q_helper or "Enter value",
                                                           value=default_str) \
                                                .props("borderless dense").classes("w-full") \
                                                .style("color: #e8e8f0 !important")
                                        answer_inputs[q_id] = inp

                                    # Show pre-filled indicator
                                    if default_str and q_type not in ("boolean",):
                                        ui.label(f"Default: {default_str}").classes("text-caption") \
                                            .style("color: var(--cr-accent); font-style: italic; margin-top: -4px")

            # Footer
            with ui.row().classes("w-full justify-end gap-2 px-5 py-3").style(
                "border-top: 1px solid var(--cr-border); flex-shrink: 0; "
                "background: var(--cr-surface-light)"
            ):
                ui.button("Use All Defaults", icon="skip_next",
                          on_click=lambda: self._submit_questionnaire(dialog, answer_inputs, use_defaults=True)) \
                    .props("flat color=grey-6 no-caps size=sm")
                ui.button("Submit Answers", icon="send",
                          on_click=lambda: self._submit_questionnaire(dialog, answer_inputs, use_defaults=False)) \
                    .props("unelevated color=deep-purple-6 no-caps size=sm")

        dialog.open()

    def _submit_questionnaire(self, dialog, answer_inputs: dict, use_defaults: bool = False):
        """Collect answers and respond to the milestone."""
        import json as _json

        answers = {}
        for q_id, inp in answer_inputs.items():
            if use_defaults:
                # Keep default value (already in the input)
                pass
            val = inp.value if hasattr(inp, 'value') else ""
            if val is not None and str(val).strip():
                answers[q_id] = val

        dialog.close()

        # Send answers to the milestone via respond_milestone
        if self.runner and self.active_run_id:
            # Store answers as JSON string in milestone_decisions
            from src.nodes.milestone_nodes import milestone_decisions, milestone_events
            milestone_decisions[0] = _json.dumps(answers) if answers else "continue"
            evt = milestone_events.get(0)
            if evt:
                evt.set()

            self.status_icon.name = "sync"
            self.status_icon.classes(replace="cr-text-accent cr-spin")
            self.status_label.text = "Processing answers..."
            self.status_detail.text = f"Submitted {len(answers)} answers"
            self.milestone_row.set_visibility(False)
            self.action_btns.set_visibility(True)
            self.banner.style(replace=(
                "background: rgba(108,99,255,0.08); border: 1px solid rgba(108,99,255,0.3)"
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
                "background: rgba(108,99,255,0.08); border: 1px solid rgba(108,99,255,0.3)"
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
                    from src.nodes.milestone_nodes import reviewer_feedback as _fb
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
                "background: rgba(108,99,255,0.08); border: 1px solid rgba(108,99,255,0.3)"
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
        self.status_detail.text = "Start a new run from the upload section"
        self.banner.style(replace="background: rgba(108,99,255,0.06)")
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
