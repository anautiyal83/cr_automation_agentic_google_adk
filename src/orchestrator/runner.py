"""PipelineRunner — executes the ADK pipeline with DB persistence and events."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable
from uuid import UUID, uuid4

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from src.core.events import PipelineEvent, event_bus
from src.core.logging_config import get_flow_logger
from src.nodes.milestone_nodes import milestone_events, milestone_decisions, milestone_waiting
from src.orchestrator.pipeline_agent import build_pipeline

logger = logging.getLogger(__name__)
flow = get_flow_logger(__name__)


class PipelineRunner:
    """Runs the CR Automation pipeline via Google ADK with DB persistence.

    Usage:
        runner = PipelineRunner(config={...}, db=db)
        run_id = await runner.start(document_set={...})
        status = runner.get_status(run_id)
        runner.respond_milestone(run_id, step_num=3, decision="continue")
    """

    def __init__(
        self,
        config: dict[str, Any] | Any | None = None,
        db: Any | None = None,
        artifact_store: Any | None = None,
    ):
        # Accept both dict and Pydantic model
        if config and hasattr(config, "model_dump"):
            self._config = config.model_dump()
        elif isinstance(config, dict):
            self._config = config
        else:
            self._config = {}
        self._db = db
        self._artifact_store = artifact_store
        self._session_service = InMemorySessionService()
        self._active_runs: dict[str, dict] = {}  # run_id → {session, task, state}
        self._pipeline = build_pipeline(self._config)

        self._runner = Runner(
            agent=self._pipeline,
            app_name="cr_automation",
            session_service=self._session_service,
        )

    @property
    def event_bus(self) -> EventBus:
        return event_bus

    async def start(self, document_set: dict[str, Any]) -> str:
        """Start a new pipeline run. Returns run_id."""
        run_id = str(uuid4())
        flow.flow("RUN START — run_id=%s, uc=%s", run_id[:8], document_set.get("uc_name", "?"))
        user_id = "pipeline_user"

        # Build initial state
        initial_state = self._build_initial_state(run_id, document_set)

        # Create ADK session
        session = await self._session_service.create_session(
            app_name="cr_automation",
            user_id=user_id,
            session_id=run_id,
            state=initial_state,
        )
        self._sessions = getattr(self, '_sessions', {})
        self._sessions[run_id] = session

        # Create milestone events for this run
        # Step 0 = requirements questionnaire, Step 2 = requirements summary review
        for step in (0, 2, 3, 4, 5, 6, 7, 8, 9):
            milestone_events[step] = asyncio.Event()

        # Persist document set + run to DB
        if self._db:
            try:
                from datetime import datetime, timezone
                now = datetime.now(timezone.utc).isoformat()
                doc_set_id = document_set.get("document_set_id", run_id)

                # Save document set for re-run support
                # Coerce None → "" for NOT NULL columns
                await self._db.execute_insert(
                    "INSERT OR REPLACE INTO uc_document_sets "
                    "(id, uc_name, ciq_file_path, mop_file_path, sample_io_xml_paths, "
                    "constraints_file_path, command_outputs_path, uploaded_at, additional_documents, selected_sheets) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (doc_set_id,
                     document_set.get("uc_name") or "",
                     document_set.get("ciq_file_path") or "",
                     document_set.get("mop_file_path") or "",
                     json.dumps(document_set.get("sample_io_xml_paths") or []),
                     document_set.get("constraints_file_path") or "",
                     document_set.get("command_outputs_path") or "",
                     now,
                     json.dumps(document_set.get("additional_documents") or {}),
                     json.dumps(document_set.get("selected_sheets") or [])),
                )

                # Save pipeline run
                await self._db.execute_insert(
                    "INSERT INTO pipeline_runs (id, document_set_id, status, current_step, started_at, config_snapshot) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (run_id, doc_set_id, "running", 0, now,
                     json.dumps(self._config, default=str)),
                )
            except Exception:
                logger.exception("Failed to persist pipeline run to DB")

        self._active_runs[run_id] = {
            "status": "running",
            "current_step": 0,
            "started_at": time.monotonic(),
            "session_id": run_id,
        }

        # Subscribe to own events to persist step results to DB
        async def _persist_events(event: PipelineEvent):
            try:
                if event.event_type in ("step_completed", "step_failed") and event.step_number:
                    step_status = event.payload.get("status", "passed")
                    await self._persist_step_result(run_id, event.step_number, step_status)
                    # Update current_step in pipeline_runs
                    if self._db:
                        await self._db.execute_insert(
                            "UPDATE pipeline_runs SET current_step=? WHERE id=?",
                            (event.step_number, run_id),
                        )
            except Exception:
                pass
        event_bus.subscribe(_persist_events, UUID(run_id))

        # Start pipeline + milestone monitor in background
        pipeline_task = asyncio.create_task(self._run_pipeline(run_id, user_id))
        monitor_task = asyncio.create_task(self._monitor_milestones(run_id))
        self._active_runs[run_id]["pipeline_task"] = pipeline_task
        self._active_runs[run_id]["monitor_task"] = monitor_task

        await event_bus.publish(PipelineEvent(
            run_id=UUID(run_id), event_type="run_started",
            payload={"run_id": run_id},
        ))
        return run_id

    async def _run_pipeline(self, run_id: str, user_id: str):
        """Execute the pipeline. Milestone monitoring handled by _monitor_milestones."""
        try:
            content = types.Content(
                role="user",
                parts=[types.Part(text="Execute the CR Automation pipeline")],
            )

            async for event in self._runner.run_async(
                user_id=user_id,
                session_id=run_id,
                new_message=content,
            ):
                pass  # Events processed by monitor task

            # Pipeline completed — determine final state
            # Check multiple sources since ADK session state may not reflect changes
            state = await self.get_state(run_id)
            run_dir = state.get("run_dir", "")

            is_cancelled = (
                state.get("pipeline_cancelled")
                or self._active_runs.get(run_id, {}).get("status") == "cancelled"
            )

            # Check for failure: session state, _active_runs, or step 10 not completed
            is_failed = (
                state.get("pipeline_failed")
                or self._active_runs.get(run_id, {}).get("status") == "failed"
            )
            # Also check if pipeline ended before step 10 (final report)
            if not is_failed and not is_cancelled and run_dir:
                step_10_done = state.get("step_10_status") or (
                    Path(run_dir) / "step_10_error.json"
                ).exists()
                if not step_10_done:
                    # Pipeline ended early without reaching step 10
                    is_failed = True
                    state["pipeline_error"] = state.get("pipeline_error", "Pipeline ended before completing all steps")

            if is_cancelled:
                flow.flow("RUN CANCELLED — run_id=%s", run_id[:8])
                self._active_runs[run_id]["status"] = "cancelled"
                await self._persist_run_status(run_id, "cancelled")
                await event_bus.publish(PipelineEvent(
                    run_id=UUID(run_id), event_type="run_cancelled",
                    payload={"run_id": run_id},
                ))
            elif is_failed:
                error = state.get("pipeline_error", "Pipeline failed")
                flow.flow("RUN FAILED — run_id=%s — %s", run_id[:8], error[:200])
                self._active_runs[run_id]["status"] = "failed"
                await self._persist_run_status(run_id, "failed", error)
                await event_bus.publish(PipelineEvent(
                    run_id=UUID(run_id), event_type="run_failed",
                    payload={"run_id": run_id, "error": error},
                ))
            else:
                elapsed = time.monotonic() - self._active_runs[run_id]["started_at"]
                flow.flow("RUN COMPLETED — run_id=%s — %.1fs", run_id[:8], elapsed)
                self._active_runs[run_id]["status"] = "completed"
                await self._persist_run_status(run_id, "completed")
                await event_bus.publish(PipelineEvent(
                    run_id=UUID(run_id), event_type="run_completed",
                    payload={"run_id": run_id, "duration": elapsed},
                ))

        except Exception as e:
            logger.exception("Pipeline run %s failed", run_id)
            flow.flow("RUN EXCEPTION — run_id=%s — %s", run_id[:8], e)
            self._active_runs[run_id]["status"] = "failed"
            await self._persist_run_status(run_id, "failed", str(e))
            await event_bus.publish(PipelineEvent(
                run_id=UUID(run_id), event_type="run_failed",
                payload={"run_id": run_id, "error": str(e)},
            ))

    async def _monitor_milestones(self, run_id: str):
        """Monitor session state for milestone pauses and update runner status."""
        try:
            while self._active_runs.get(run_id, {}).get("status") not in ("completed", "failed", "cancelled"):
                await asyncio.sleep(0.3)

                # Read session state from cached session
                session = getattr(self, '_sessions', {}).get(run_id)
                if not session or not session.state:
                    continue
                state = session.state

                # Detect current step
                current_step = self._detect_current_step(state)
                if current_step > 0:
                    self._active_runs[run_id]["current_step"] = current_step

                # Detect milestone pauses via shared global dict
                # Step 0 = requirements questionnaire, Step 2 = requirements summary
                for step_num in (0, 2, 3, 4, 5, 6, 7, 8, 9):
                    mw = milestone_waiting.get(step_num)
                    if mw and mw.get("waiting"):
                        if self._active_runs[run_id].get("status") != "paused_milestone":
                            self._active_runs[run_id]["status"] = "paused_milestone"
                            self._active_runs[run_id]["milestone_step"] = step_num

                            # Resolve artifact file for this milestone
                            artifact_file, extra_files = self._resolve_milestone_artifacts(
                                state, step_num
                            )

                            await event_bus.publish(PipelineEvent(
                                run_id=UUID(run_id), event_type="milestone_reached",
                                step_number=step_num,
                                payload={
                                    "milestone": mw.get("name", f"Step {step_num}"),
                                    "test_status": mw.get("status") if mw.get("status") == "failed" else None,
                                    "artifact_file": artifact_file,
                                    "extra_artifact_files": extra_files,
                                },
                            ))

                # Detect termination
                if state.get("pipeline_cancelled"):
                    self._active_runs[run_id]["status"] = "cancelled"
                    break
                if state.get("pipeline_failed"):
                    self._active_runs[run_id]["status"] = "failed"
                    break

        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Milestone monitor error for run %s", run_id)

    def respond_milestone(self, run_id: str, step_num: int, decision: str):
        """Respond to a milestone pause: 'continue', 'rerun', or 'cancel'."""
        flow.flow("MILESTONE RESPOND — run=%s step=%d decision=%s", run_id[:8], step_num, decision)

        # Set decision in milestone_decisions for the node to read
        milestone_decisions[step_num] = decision

        run_info = self._active_runs.get(run_id, {})
        if decision == "cancel":
            run_info["status"] = "cancelled"
        else:
            run_info["status"] = "running"

        # Update session state asynchronously
        asyncio.create_task(self._set_milestone_decision(run_id, step_num, decision))

        # Signal the milestone event
        evt = milestone_events.get(step_num)
        if evt:
            evt.set()
            logger.info("Milestone %d: signaled with decision=%s", step_num, decision)

    async def _set_milestone_decision(self, run_id: str, step_num: int, decision: str):
        """Set milestone decision in session state."""
        session = getattr(self, '_sessions', {}).get(run_id)
        if session and session.state:
            session.state[f"milestone_{step_num}_decision"] = decision
            session.state[f"milestone_{step_num}_decision_action"] = decision

    def get_status(self, run_id: str) -> dict[str, Any]:
        """Get current pipeline status."""
        run_info = self._active_runs.get(run_id, {})
        return {
            "run_id": run_id,
            "status": run_info.get("status", "unknown"),
            "current_step": run_info.get("current_step", 0),
            "milestone_step": run_info.get("milestone_step"),
        }

    async def get_state(self, run_id: str) -> dict[str, Any]:
        """Get full pipeline state for a run."""
        # Try session service first (has the latest state from ADK runner)
        try:
            session = await self._session_service.get_session(
                app_name="cr_automation", user_id="pipeline_user", session_id=run_id
            )
            if session and session.state:
                return dict(session.state)
        except Exception:
            pass
        # Fallback to cached session reference
        session = getattr(self, '_sessions', {}).get(run_id)
        if session and session.state:
            return dict(session.state)
        return {}

    async def cancel_run(self, run_id: str):
        """Cancel a running pipeline."""
        self.respond_milestone(run_id, 0, "cancel")
        try:
            sessions = await self._session_service.list_sessions(
                app_name="cr_automation", user_id="pipeline_user")
            for s in sessions.sessions:
                if s.id == run_id:
                    s.state["pipeline_cancelled"] = True
                    break
        except Exception:
            pass

    def _build_initial_state(self, run_id: str, document_set: dict) -> dict:
        """Build initial session state from document set and config."""
        run_dir = str(Path(self._config.get("output_directory", "./data/runs")) / run_id)
        Path(run_dir).mkdir(parents=True, exist_ok=True)

        uc_name = document_set.get("uc_name", "UC")

        return {
            "run_id": run_id,
            "run_dir": run_dir,
            "uc_name": uc_name,
            "ciq_file_path": document_set.get("ciq_file_path", ""),
            "document_set": document_set,
            "pipeline_config": self._config,
            "artifacts": {},
            "step_results": [],
            "qa_feedback": [],
            "exec_feedback": [],
            "run_data": {"uc_name": uc_name, "status": "running"},
        }

    def _resolve_milestone_artifacts(
        self, state: dict, step_num: int
    ) -> tuple[str, list[str]]:
        """Find the primary artifact file and any extra files for a milestone step."""
        run_dir = state.get("run_dir", "")
        uc_name = state.get("uc_name", "UC")
        if not run_dir:
            return "", []

        artifact_map = {
            2: f"{uc_name}_requirements-summary.html",
            3: f"{uc_name}_validation-rules.yaml",
            4: f"{uc_name}_json-output.yaml",
            5: "qa/qa-execution-report.html",
            6: f"{uc_name}.yaml",
            7: f"{uc_name}_mop-generation-report.html",
            8: f"{uc_name}_simulator-config.yaml",
            9: f"exec_report/{uc_name}_execution-report.html",
        }

        # Also check artifacts stored in session state
        artifacts = state.get("artifacts", {})
        state_artifact_map = {
            3: "validation_rules_yaml",
            4: "json_template_yaml",
            6: "workflow_yaml",
            8: "simulator_config_yaml",
        }

        primary = ""
        fname = artifact_map.get(step_num, "")
        if fname:
            candidate = Path(run_dir) / fname
            if candidate.exists():
                primary = str(candidate)

        # If no file on disk, check if content exists in state artifacts
        # and write it to disk so the UI can display it
        if not primary and step_num in state_artifact_map:
            content = artifacts.get(state_artifact_map[step_num])
            if content:
                out_path = Path(run_dir) / fname
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(content, encoding="utf-8")
                primary = str(out_path)

        # Collect extra artifact files (e.g. report + source yaml)
        extra: list[str] = []
        extra_map = {
            2: ["requirements.json"],
            5: [f"{uc_name}_validation-rules.yaml", f"{uc_name}_json-output.yaml"],
            7: [f"{uc_name}.yaml"],
            8: ["simulator/simulator_manifest.yaml"],
            9: [f"{uc_name}.yaml", f"{uc_name}_simulator-config.yaml"],
        }
        for ef in extra_map.get(step_num, []):
            ep = Path(run_dir) / ef
            if ep.exists() and str(ep) != primary:
                extra.append(str(ep))
        # For step 8, also add per-node config files
        if step_num == 8:
            sim_config_dir = Path(run_dir) / "simulator" / "config"
            if sim_config_dir.exists():
                for cf in sorted(sim_config_dir.glob("*.yaml")):
                    if str(cf) != primary:
                        extra.append(str(cf))

        return primary, extra

    def _detect_current_step(self, state: dict) -> int:
        """Detect current step from state."""
        for i in range(10, 0, -1):
            if state.get(f"step_{i}_status"):
                return i
        return 0

    async def _persist_step_result(self, run_id: str, step_num: int, status: str):
        """Save step result to DB."""
        if not self._db:
            return
        try:
            from datetime import datetime, timezone
            await self._db.execute_insert(
                "INSERT OR REPLACE INTO step_results (id, run_id, step_number, step_name, status, completed_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid4()), run_id, step_num, f"Step {step_num}", status,
                 datetime.now(timezone.utc).isoformat()),
            )
        except Exception:
            logger.exception("Failed to persist step result")

    async def _persist_run_status(self, run_id: str, status: str, error: str = ""):
        """Update run status in DB."""
        if not self._db:
            return
        try:
            from datetime import datetime, timezone
            elapsed = time.monotonic() - self._active_runs.get(run_id, {}).get("started_at", time.monotonic())
            await self._db.execute_insert(
                "UPDATE pipeline_runs SET status=?, completed_at=?, duration_seconds=?, error_summary=? WHERE id=?",
                (status, datetime.now(timezone.utc).isoformat(), elapsed, error or None, run_id),
            )
        except Exception:
            logger.exception("Failed to persist run status")
