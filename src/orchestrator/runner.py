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

from src.core.events import EventBus, PipelineEvent
from src.nodes.milestone_nodes import milestone_events, milestone_decisions, milestone_waiting
from src.orchestrator.pipeline_agent import build_pipeline

logger = logging.getLogger(__name__)

# Singleton event bus
event_bus = EventBus()


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
        config: dict[str, Any] | None = None,
        db: Any | None = None,
        artifact_store: Any | None = None,
    ):
        self._config = config or {}
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
        for step in (3, 4, 5, 6, 7, 8, 9):
            milestone_events[step] = asyncio.Event()

        # Persist run to DB
        if self._db:
            try:
                from datetime import datetime, timezone
                await self._db.execute_insert(
                    "INSERT INTO pipeline_runs (id, document_set_id, status, current_step, started_at, config_snapshot) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (run_id, document_set.get("document_set_id", run_id), "running", 0,
                     datetime.now(timezone.utc).isoformat(),
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

            # Pipeline completed — check final state
            state = await self.get_state(run_id)
            if state.get("pipeline_cancelled"):
                self._active_runs[run_id]["status"] = "cancelled"
                await self._persist_run_status(run_id, "cancelled")
                await event_bus.publish(PipelineEvent(
                    run_id=UUID(run_id), event_type="run_cancelled",
                    payload={"run_id": run_id},
                ))
            elif state.get("pipeline_failed"):
                error = state.get("pipeline_error", "Retry limit exhausted")
                self._active_runs[run_id]["status"] = "failed"
                await self._persist_run_status(run_id, "failed", error)
                await event_bus.publish(PipelineEvent(
                    run_id=UUID(run_id), event_type="run_failed",
                    payload={"run_id": run_id, "error": error},
                ))
            else:
                elapsed = time.monotonic() - self._active_runs[run_id]["started_at"]
                self._active_runs[run_id]["status"] = "completed"
                await self._persist_run_status(run_id, "completed")
                await event_bus.publish(PipelineEvent(
                    run_id=UUID(run_id), event_type="run_completed",
                    payload={"run_id": run_id, "duration": elapsed},
                ))

        except Exception as e:
            logger.exception("Pipeline run %s failed", run_id)
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
                for step_num in (3, 4, 5, 6, 7, 8, 9):
                    mw = milestone_waiting.get(step_num)
                    if mw and mw.get("waiting"):
                        if self._active_runs[run_id].get("status") != "paused_milestone":
                            self._active_runs[run_id]["status"] = "paused_milestone"
                            self._active_runs[run_id]["milestone_step"] = step_num
                            await event_bus.publish(PipelineEvent(
                                run_id=UUID(run_id), event_type="milestone_reached",
                                step_number=step_num,
                                payload={
                                    "milestone": mw.get("name", f"Step {step_num}"),
                                    "test_status": mw.get("status") if mw.get("status") == "failed" else None,
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
        # Set decision in milestone_decisions for the node to read
        milestone_decisions[step_num] = decision

        # Also set in session state (for routers to read)
        # The milestone node reads from the asyncio.Event, but routers
        # need the decision in state to know if user chose "rerun"
        run_info = self._active_runs.get(run_id, {})
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
