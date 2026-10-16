"""Pipeline runner — executes the ADK pipeline with session management."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from src.orchestrator.pipeline_graph import build_pipeline_agent

logger = logging.getLogger(__name__)


class PipelineRunner:
    """Runs the CR Automation pipeline via Google ADK.

    Usage:
        runner = PipelineRunner(config={...})
        run_id = await runner.start(document_set={...})
        # Poll status:
        status = runner.get_status(run_id)
        # Respond to milestone:
        runner.respond_milestone(run_id, step_num=3, decision="continue")
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        on_event: Callable[[str, dict], None] | None = None,
    ):
        self._config = config or {}
        self._on_event = on_event  # callback(event_type, payload)
        self._session_service = InMemorySessionService()
        self._active_runs: dict[str, dict] = {}  # run_id → {session, task, ...}

        model = self._config.get("default_model", "openrouter/google/gemini-2.5-flash")
        api_key = self._config.get("openrouter_api_key", "")
        self._pipeline = build_pipeline_agent(model_name=model, api_key=api_key)

        self._runner = Runner(
            agent=self._pipeline,
            app_name="cr_automation",
            session_service=self._session_service,
        )

    async def start(self, document_set: dict[str, Any]) -> str:
        """Start a new pipeline run. Returns run_id."""
        run_id = str(uuid4())
        user_id = "pipeline_user"

        # Create session with initial state
        session = await self._session_service.create_session(
            app_name="cr_automation",
            user_id=user_id,
            session_id=run_id,
            state=self._build_initial_state(run_id, document_set),
        )

        # Start pipeline in background
        task = asyncio.create_task(self._run_pipeline(run_id, user_id, session))
        self._active_runs[run_id] = {
            "session": session,
            "task": task,
            "status": "running",
            "current_step": 0,
        }

        self._emit("run_started", {"run_id": run_id})
        return run_id

    async def _run_pipeline(self, run_id: str, user_id: str, session):
        """Execute the pipeline and monitor events."""
        try:
            content = types.Content(
                role="user",
                parts=[types.Part(text="Start pipeline execution")],
            )

            async for event in self._runner.run_async(
                user_id=user_id,
                session_id=run_id,
                new_message=content,
            ):
                # Monitor state changes
                state = session.state
                self._active_runs[run_id]["current_step"] = self._detect_current_step(state)

                # Check for milestone pauses
                for step_num in (3, 4, 5, 6, 7, 8, 9):
                    if state.get(f"milestone_{step_num}_waiting"):
                        self._active_runs[run_id]["status"] = "paused_milestone"
                        self._active_runs[run_id]["milestone_step"] = step_num
                        self._emit("milestone_reached", {
                            "run_id": run_id,
                            "step_number": step_num,
                            "step_name": state.get(f"milestone_{step_num}_step_name", ""),
                            "step_status": state.get(f"step_{step_num}_status", "passed"),
                        })

                # Check for cancellation
                if state.get("pipeline_cancelled"):
                    self._active_runs[run_id]["status"] = "cancelled"
                    self._emit("run_cancelled", {"run_id": run_id})
                    return

                # Check for failure
                if state.get("pipeline_failed"):
                    self._active_runs[run_id]["status"] = "failed"
                    self._emit("run_failed", {"run_id": run_id, "error": "Retry limit exhausted"})
                    return

            # Pipeline completed
            self._active_runs[run_id]["status"] = "completed"
            self._emit("run_completed", {"run_id": run_id})

        except Exception as e:
            logger.exception("Pipeline run %s failed", run_id)
            self._active_runs[run_id]["status"] = "failed"
            self._emit("run_failed", {"run_id": run_id, "error": str(e)})

    def respond_milestone(self, run_id: str, step_num: int, decision: str):
        """Respond to a milestone pause: 'continue', 'rerun', or 'cancel'."""
        run_info = self._active_runs.get(run_id)
        if not run_info:
            return

        session = run_info["session"]
        session.state[f"milestone_{step_num}_decision"] = decision
        run_info["status"] = "running"
        logger.info("Milestone %d response: %s", step_num, decision)

    def get_status(self, run_id: str) -> dict[str, Any]:
        """Get current pipeline status."""
        run_info = self._active_runs.get(run_id, {})
        return {
            "run_id": run_id,
            "status": run_info.get("status", "unknown"),
            "current_step": run_info.get("current_step", 0),
            "milestone_step": run_info.get("milestone_step"),
        }

    def get_state(self, run_id: str) -> dict[str, Any]:
        """Get full pipeline state for a run."""
        run_info = self._active_runs.get(run_id, {})
        session = run_info.get("session")
        if session:
            return dict(session.state)
        return {}

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

    def _emit(self, event_type: str, payload: dict):
        """Emit event to external listener."""
        if self._on_event:
            try:
                self._on_event(event_type, payload)
            except Exception:
                pass
        logger.info("Event: %s — %s", event_type, json.dumps(payload, default=str)[:200])
