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

from core.events import PipelineEvent, event_bus
from core.logging_config import get_flow_logger
from shared.state import (
    milestone_events, milestone_decisions, milestone_waiting, pipeline_actions, stop_simulator,
)
from orchestrator.pipeline_agent import build_pipeline

logger = logging.getLogger(__name__)
flow = get_flow_logger(__name__)


class QueueRefused(ValueError):
    """A submit the queue rules refuse (spec 007); the message is shown to the user."""


# Statuses of a run that holds or waits for the pipeline
_LIVE = ("queued", "resuming", "running", "paused_milestone", "paused_hil")
_LIVE_SQL = ", ".join(f"'{s}'" for s in (*_LIVE, "PipelineStatus.RUNNING", "PipelineStatus.PAUSED_HIL",
                                          "PipelineStatus.PAUSED_MILESTONE"))


def _utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _status_word(status: str) -> str:
    s = str(status or "").replace("PipelineStatus.", "").lower()
    return {"queued": "waiting", "resuming": "resuming", "paused_milestone": "paused",
            "paused_hil": "paused"}.get(s, s)


def _document_set_from_row(row: dict) -> dict:
    """uc_document_sets row → the dict ``submit`` was given."""
    def loads(value, default):
        try:
            return json.loads(value) if value else default
        except (TypeError, ValueError):
            return default
    return {
        "document_set_id": row.get("document_set_id") or row.get("id"),
        "uc_name": row.get("uc_name") or "",
        "ciq_file_path": row.get("ciq_file_path") or "",
        "mop_file_path": row.get("mop_file_path") or "",
        "sample_io_xml_paths": loads(row.get("sample_io_xml_paths"), []),
        "constraints_file_path": row.get("constraints_file_path") or "",
        "command_outputs_path": row.get("command_outputs_path") or "",
        "additional_documents": loads(row.get("additional_documents"), {}),
        "selected_sheets": loads(row.get("selected_sheets"), []),
        "developer_notes": row.get("developer_notes") or "",
        "vendor_profile": loads(row.get("vendor_profile"), {}),
        "run_options": loads(row.get("run_options"), {}),
    }


class PipelineRunner:
    """Runs the NetConfig pipeline via Google ADK with DB persistence.

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
        if db is not None:
            # LLM usage rows go into the same DB as the runs (table llm_usage, by run_id)
            from shared.llm import set_usage_db
            set_usage_db(db)
            from orchestrator import checkpoints
            checkpoints.set_db(db)  # the pipeline saves restart checkpoints (spec 007)
        self._artifact_store = artifact_store
        self._session_service = InMemorySessionService()
        self._active_runs: dict[str, dict] = {}  # run_id → {session, task, state}
        self._queue_lock = asyncio.Lock()  # one dispatcher decision at a time (spec 007)
        self._pipeline = build_pipeline(self._config)

        self._runner = Runner(
            agent=self._pipeline,
            app_name="netconfig",
            session_service=self._session_service,
        )

    @property
    def event_bus(self) -> EventBus:
        return event_bus

    async def start(self, document_set: dict[str, Any], review_milestones: bool = True,
                    started_by: str = "cli") -> str:
        """Submit a run (spec 007: it waits in the queue while another run executes). Returns run_id;
        raises ``QueueRefused`` when the queue rules refuse it."""
        return (await self.submit(document_set, review_milestones, started_by))["run_id"]

    # ------------------------------------------------------------------ run queue (spec 007)
    async def submit(self, document_set: dict[str, Any], review_milestones: bool = True,
                     started_by: str = "cli") -> dict:
        """Put a run in the queue and start it at once when the pipeline is free.

        Returns ``{"run_id", "status": "running" | "queued", "position"}`` (position 0 = running).
        Refused (``QueueRefused``) when the use case already has a job waiting, running or paused, or
        the user already has ``execution.max_queued_per_user`` jobs waiting. Without a DB (CLI) the run
        starts directly."""
        run_id = str(uuid4())
        if not self._db:
            await self._launch(run_id, document_set, review_milestones, started_by)
            return {"run_id": run_id, "status": "running", "position": 0}
        from datetime import datetime, timezone
        from core.config import max_queued_per_user
        uc_name = document_set.get("uc_name") or ""
        async with self._queue_lock:
            busy = await self._db.execute(
                "SELECT r.status FROM pipeline_runs r JOIN uc_document_sets d ON d.id = r.document_set_id "
                f"WHERE d.uc_name = ? AND r.status IN ({_LIVE_SQL}) LIMIT 1", (uc_name,))
            if busy or any(info.get("uc_name") == uc_name and info.get("status") in _LIVE
                           for info in self._active_runs.values()):
                status = busy[0]["status"] if busy else "running"
                raise QueueRefused(f"{uc_name} already has a job in the pipeline ({_status_word(status)}) — "
                                   "one job per use case at a time")
            limit = max_queued_per_user()
            waiting = await self._db.execute(
                "SELECT COUNT(*) AS n FROM pipeline_runs WHERE status = 'queued' AND started_by = ?",
                (started_by or "cli",))
            if int(waiting[0]["n"]) >= limit:
                raise QueueRefused(f"You already have {limit} jobs waiting in the queue (the limit) — "
                                   "start another one when one of them has started")
            now = datetime.now(timezone.utc).isoformat()
            doc_set_id = document_set.get("document_set_id") or run_id
            await self._save_document_set(doc_set_id, document_set, now)
            await self._db.execute_insert(
                "INSERT INTO pipeline_runs (id, document_set_id, status, current_step, started_at, queued_at, "
                "config_snapshot, started_by, review_milestones) VALUES (?, ?, 'queued', 0, ?, ?, ?, ?, ?)",
                (run_id, doc_set_id, now, now, json.dumps(self._config, default=str), started_by or "cli",
                 int(bool(review_milestones))))
            flow.flow("RUN QUEUED — run_id=%s, uc=%s, by=%s", run_id[:8], uc_name, started_by)
        await self.dispatch()
        if run_id in self._active_runs:
            return {"run_id": run_id, "status": "running", "position": 0}
        return {"run_id": run_id, "status": "queued", "position": await self.queue_position(run_id)}

    async def dispatch(self) -> str | None:
        """Start the oldest queued run when the pipeline is free (phase 1: one run at a time). Called
        after a submit, after every run end and at server start. Returns the started run id."""
        if not self._db:
            return None
        async with self._queue_lock:
            if self.active_run() or await self._held_elsewhere():
                return None
            rows = await self._db.execute(
                "SELECT r.id AS run_id, r.status, r.document_set_id, r.review_milestones, r.started_by, d.uc_name, "
                "d.ciq_file_path, d.mop_file_path, d.sample_io_xml_paths, d.constraints_file_path, "
                "d.command_outputs_path, d.additional_documents, d.selected_sheets, d.developer_notes, "
                "d.vendor_profile, d.run_options FROM pipeline_runs r "
                "LEFT JOIN uc_document_sets d ON d.id = r.document_set_id "
                "WHERE r.status IN ('resuming', 'queued') "
                "ORDER BY CASE r.status WHEN 'resuming' THEN 0 ELSE 1 END, r.queued_at, r.rowid LIMIT 1")
            if not rows:
                return None
            row = rows[0]
            run_id = row["run_id"]
            from datetime import datetime, timezone
            from orchestrator import checkpoints
            resume = await checkpoints.load(self._db, run_id) if row["status"] == "resuming" else None
            if row["status"] == "resuming" and resume is None:
                await self._persist_run_status(run_id, "failed", "Server restart: no checkpoint to resume from")
                asyncio.ensure_future(self.dispatch())
                return None
            # Claimed atomically with this process as owner: another server on the same database
            # may dispatch the same row (OB-10)
            from orchestrator.ownership import this_owner
            claim = ("UPDATE pipeline_runs SET status = 'running', owner = ?{} WHERE id = ? "
                     "AND status IN ('queued', 'resuming') RETURNING id")
            if resume is None:  # a resumed run keeps its original start time
                claimed = await self._db.execute(claim.format(", started_at = ?"),
                                                 (this_owner(), datetime.now(timezone.utc).isoformat(), run_id))
            else:
                claimed = await self._db.execute(claim.format(""), (this_owner(), run_id))
            if not claimed:
                return None
            try:
                await self._launch(run_id, _document_set_from_row(row), bool(row["review_milestones"]),
                                   row["started_by"] or "cli", resume=resume)
            except Exception as e:  # never leave the queue stuck on a run that could not start
                logger.exception("Queued run %s could not start", run_id[:8])
                await self._persist_run_status(run_id, "failed", f"Could not start: {e}")
                asyncio.ensure_future(self.dispatch())
                return None
        flow.flow("RUN %s — run_id=%s", "RESUMED" if resume else "DEQUEUED", run_id[:8])
        return run_id

    async def _held_elsewhere(self) -> bool:
        """Another live server process executes a run on this database (OB-10)."""
        from orchestrator.ownership import owner_alive, this_owner
        rows = await self._db.execute(
            f"SELECT owner FROM pipeline_runs WHERE status IN ({_LIVE_SQL}) "
            "AND status NOT IN ('queued', 'resuming') AND owner IS NOT NULL AND owner != ?", (this_owner(),))
        return any(owner_alive(r["owner"]) for r in rows)

    async def queue_position(self, run_id: str) -> int:
        """1-based position of a queued run (0 when it is not waiting)."""
        rows = await self._db.execute(
            "SELECT id FROM pipeline_runs WHERE status = 'queued' ORDER BY queued_at, rowid") if self._db else []
        ids = [r["id"] for r in rows]
        return ids.index(run_id) + 1 if run_id in ids else 0

    async def mark_finished(self, run_id: str, user_id: str) -> bool:
        """The user is done with a completed run: it leaves Live Run (spec 008 U-D7). Its status
        stays ``completed``. False when the run is not completed or already finished."""
        if not self._db:
            return False
        rows = await self._db.execute("SELECT status, finished_at FROM pipeline_runs WHERE id = ?", (run_id,))
        if (not rows or rows[0]["finished_at"]
                or str(rows[0]["status"]).replace("PipelineStatus.", "").lower() != "completed"):
            return False
        await self._db.execute("UPDATE pipeline_runs SET finished_at = ?, finished_by = ? WHERE id = ?",
                               (_utc_now(), user_id, run_id))
        flow.flow("RUN FINISHED — run_id=%s marked finished by the user", run_id[:8])
        return True

    async def remove_queued(self, run_id: str) -> bool:
        """Take a waiting run out of the queue (status ``removed``). False when it is not waiting."""
        if not self._db:
            return False
        from datetime import datetime, timezone
        async with self._queue_lock:
            rows = await self._db.execute("SELECT status FROM pipeline_runs WHERE id = ?", (run_id,))
            if not rows or rows[0]["status"] != "queued":
                return False
            await self._db.execute("UPDATE pipeline_runs SET status = 'removed', completed_at = ? WHERE id = ?",
                                   (datetime.now(timezone.utc).isoformat(), run_id))
        flow.flow("RUN REMOVED FROM QUEUE — run_id=%s", run_id[:8])
        # a Live Run page following the waiting run clears it, as for a cancel
        await event_bus.publish(PipelineEvent(run_id=UUID(run_id), event_type="run_cancelled",
                                              payload={"run_id": run_id, "removed": True}))
        return True

    async def resume_queue(self) -> str | None:
        """Server start: a run the restart interrupted resumes from its checkpoint, ahead of the
        waiting runs (status ``resuming``); without a checkpoint, a run folder, or after
        ``MAX_RESUME_ATTEMPTS`` resumes at the same point it fails. Then the queue goes on."""
        if not self._db:
            return None
        from orchestrator import checkpoints
        # interrupted = was executing (running / paused / resuming), never the waiting ones
        from orchestrator.ownership import owner_alive, this_owner
        lost = await self._db.execute(
            "SELECT r.id, r.status, r.owner, d.uc_name FROM pipeline_runs r LEFT JOIN uc_document_sets d "
            f"ON d.id = r.document_set_id WHERE r.status IN ({_LIVE_SQL}) AND r.status != 'queued'")
        for row in lost:
            run_id = row["id"]
            if run_id in self._active_runs:
                continue
            if row["status"] != "resuming" and row["owner"] != this_owner() and owner_alive(row["owner"]):
                # another server on this database executes it (OB-10): not interrupted
                flow.flow("RUN %s belongs to live server %s — not resumed", run_id[:8], row["owner"])
                continue
            cp = await checkpoints.load(self._db, run_id)
            reason = ""
            if cp is None:
                reason = "no checkpoint"
            elif not Path(str(cp["state"].get("run_dir") or "")).is_dir():
                reason = "run folder missing"
            else:
                attempts = await checkpoints.count_attempt(self._db, run_id)
                if attempts > checkpoints.MAX_RESUME_ATTEMPTS:
                    reason = f"interrupted {attempts} times at {cp.get('agent_name')}"
            if reason:
                await self._persist_run_status(run_id, "failed", f"Server restarted — could not resume ({reason})")
                await checkpoints.delete(self._db, run_id)
                continue
            await self._db.execute("UPDATE pipeline_runs SET status = 'resuming' WHERE id = ?", (run_id,))
            flow.flow("RUN INTERRUPTED — run_id=%s resumes at %s (%s)", run_id[:8], cp.get("agent_name"), cp.get("kind"))
        return await self.dispatch()

    @staticmethod
    def _resume_state(run_id: str, resume: dict) -> dict:
        """Session state of a resumed run: the checkpoint's, with where the pipeline continues."""
        from agents.requirements_agent import requirements_store
        from orchestrator import checkpoints
        from orchestrator.pipeline_agent import _AGENT_DISPLAY_STEPS, _AGENT_NAMES, _first_agent_of_step
        state = dict(resume["state"])
        for key in ("pipeline_cancelled", "pipeline_failed", "pipeline_error"):
            state.pop(key, None)
        if resume.get("requirements"):
            requirements_store[run_id] = resume["requirements"]
        index, at_milestone = checkpoints.resume_position(resume, _AGENT_NAMES, _AGENT_DISPLAY_STEPS,
                                                          _first_agent_of_step)
        state["_resume_index"] = index
        if at_milestone:
            state["_resume_milestone_at"] = index
        state["resumed_after_restart"] = True
        return state

    async def _save_document_set(self, doc_set_id: str, document_set: dict, now: str) -> None:
        """The run's documents (re-run support and starting a queued run)."""
        # Coerce None → "" for NOT NULL columns
        await self._db.execute_insert(
            "INSERT OR REPLACE INTO uc_document_sets "
            "(id, uc_name, ciq_file_path, mop_file_path, sample_io_xml_paths, "
            "constraints_file_path, command_outputs_path, uploaded_at, additional_documents, "
            "selected_sheets, developer_notes, vendor_profile, run_options) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (doc_set_id,
             document_set.get("uc_name") or "",
             document_set.get("ciq_file_path") or "",
             document_set.get("mop_file_path") or "",
             json.dumps(document_set.get("sample_io_xml_paths") or []),
             document_set.get("constraints_file_path") or "",
             document_set.get("command_outputs_path") or "",
             now,
             json.dumps(document_set.get("additional_documents") or {}),
             json.dumps(document_set.get("selected_sheets") or []),
             document_set.get("developer_notes") or "",
             json.dumps(document_set.get("vendor_profile") or {}),
             json.dumps(document_set.get("run_options") or {})),
        )

    async def _launch(self, run_id: str, document_set: dict[str, Any], review_milestones: bool,
                      started_by: str, resume: dict | None = None) -> None:
        """Execute a run now (the pipeline is free: the dispatcher decided, or there is no DB).
        ``resume``: the run's checkpoint — the session continues from it (restart recovery)."""
        flow.flow("RUN START — run_id=%s, uc=%s", run_id[:8], document_set.get("uc_name", "?"))
        user_id = "pipeline_user"

        # The previous run has ended: clear its milestone state (keyed by step, not run_id)
        await self._cancel_active_runs()

        # Build initial state
        initial_state = self._resume_state(run_id, resume) if resume else self._build_initial_state(run_id, document_set)
        initial_state["review_milestones"] = review_milestones

        # Create ADK session
        session = await self._session_service.create_session(
            app_name="netconfig",
            user_id=user_id,
            session_id=run_id,
            state=initial_state,
        )
        self._sessions = getattr(self, '_sessions', {})
        self._sessions[run_id] = session

        # Rebuild pipeline to avoid stale agent state from previous runs
        self._pipeline = build_pipeline(self._config)
        self._runner = Runner(
            agent=self._pipeline,
            app_name="netconfig",
            session_service=self._session_service,
        )

        # Create milestone events for this run
        # Step 0 = requirements questionnaire, Step 2 = requirements summary review
        for step in (0, 2, 3, 4, 5, 6, 7, 8, 9):
            milestone_events[step] = asyncio.Event()

        self._active_runs[run_id] = {
            "status": "running",
            "current_step": 0,
            "started_at": time.monotonic(),
            "session_id": run_id,
            "started_by": started_by or "cli",
            "uc_name": document_set.get("uc_name", ""),
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

    async def _run_pipeline(self, run_id: str, user_id: str):
        """Execute the pipeline. Milestone monitoring handled by _monitor_milestones."""
        try:
            content = types.Content(
                role="user",
                parts=[types.Part(text="Execute the NetConfig pipeline")],
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
                uc_name = state.get("uc_name", "")
                step_10_done = (
                    state.get("step_10_status")
                    or (Path(run_dir) / "step_10_error.json").exists()
                    or (Path(run_dir) / f"{uc_name}_report.html").exists()
                    or any(Path(run_dir).glob("*_report.html"))
                )
                if not step_10_done:
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
        finally:
            # Completed, failed, cancelled or superseded — never leave MockWire running
            try:
                await stop_simulator(run_id)
            except Exception as e:
                logger.warning("Simulator stop failed for run %s: %s", run_id[:8], e)
            # The pipeline is free: the next queued run starts (spec 007). A task cancelled outright
            # never reached a final status — without one it would hold the queue forever
            info = self._active_runs.get(run_id, {})
            if info.get("status") in _LIVE:
                info["status"] = "cancelled"
                await self._persist_run_status(run_id, "cancelled", "Pipeline task stopped")
            if self._db:  # the run is over: nothing to resume
                from orchestrator import checkpoints
                await checkpoints.delete(self._db, run_id)
            asyncio.ensure_future(self.dispatch())

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
                            # review time is not working time (queue estimates, spec 007)
                            self._active_runs[run_id]["paused_since"] = time.monotonic()
                            self._active_runs[run_id]["paused_at"] = _utc_now()
                            self._active_runs[run_id]["milestone_step"] = step_num

                            # Resolve artifact file for this milestone
                            # Check milestone_waiting for artifact first (used by RequirementsGatherer)
                            artifact_file = mw.get("artifact_file", "")
                            extra_files = []
                            if not artifact_file:
                                artifact_file, extra_files = self._resolve_milestone_artifacts(
                                    state, step_num
                                )

                            payload = {
                                "milestone": mw.get("name", f"Step {step_num}"),
                                "test_status": mw.get("status") if mw.get("status") == "failed" else None,
                                "artifact_file": artifact_file,
                                "extra_artifact_files": extra_files,
                            }
                            # Include questions for questionnaire milestone (step 0)
                            if mw.get("questions"):
                                payload["questions"] = mw["questions"]

                            await event_bus.publish(PipelineEvent(
                                run_id=UUID(run_id), event_type="milestone_reached",
                                step_number=step_num,
                                payload=payload,
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
        if run_info.get("paused_since"):
            run_info["paused_total"] = run_info.get("paused_total", 0.0) + time.monotonic() - run_info["paused_since"]
            run_info["paused_since"] = None
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
                app_name="netconfig", user_id="pipeline_user", session_id=run_id
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

    def work_seconds(self, run_id: str) -> float:
        """Time the run has been working: since its start, without review pauses (spec 007)."""
        info = self._active_runs.get(run_id, {})
        if not info.get("started_at"):
            return 0.0
        now = time.monotonic()
        paused = info.get("paused_total", 0.0) + (now - info["paused_since"] if info.get("paused_since") else 0.0)
        return max(0.0, now - info["started_at"] - paused)

    def active_run(self) -> dict | None:
        """The run in progress (``{"run_id", "started_by", "status"}``), if any. Only one pipeline
        runs at a time: milestone state is global (spec 006 — a new start must not silently
        cancel another user's run)."""
        for rid, info in self._active_runs.items():
            if info.get("status") in ("running", "paused_milestone", "paused_hil"):
                return {"run_id": rid, "started_by": info.get("started_by", "cli"), "status": info["status"]}
        return None

    async def _cancel_active_runs(self):
        """Cancel all active pipeline/monitor tasks and clear stale milestone state."""
        for rid, info in list(self._active_runs.items()):
            if info.get("status") in ("running", "paused_milestone"):
                flow.flow("Cancelling stale run %s before new start", rid[:8])
                # Cancel background tasks
                for task_key in ("pipeline_task", "monitor_task"):
                    task = info.get(task_key)
                    if task and not task.done():
                        task.cancel()
                        try:
                            await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
                        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                            pass
                info["status"] = "cancelled"
                await self._persist_run_status(rid, "cancelled", "Superseded by new run")

        # Clear all global shared state
        milestone_events.clear()
        milestone_decisions.clear()
        milestone_waiting.clear()
        pipeline_actions.clear()

        # Clear stale requirements store
        try:
            from agents.requirements_agent import requirements_store
            requirements_store.clear()
        except ImportError:
            pass

        # Clear stale reviewer feedback
        try:
            from shared.state import reviewer_feedback
            reviewer_feedback.clear()
        except ImportError:
            pass

    async def cancel_run(self, run_id: str):
        """Cancel a running pipeline."""
        self.respond_milestone(run_id, 0, "cancel")
        try:
            sessions = await self._session_service.list_sessions(
                app_name="netconfig", user_id="pipeline_user")
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
            5: f"{uc_name}_ciq-qa-report.html",
            6: f"{uc_name}.yaml",
            7: f"{uc_name}_mop-qa-report.html",
            8: f"{uc_name}_simulator-report.html",
            9: f"{uc_name}_network-qa-report.html",
        }

        # Also check artifacts stored in session state
        artifacts = state.get("artifacts", {})
        state_artifact_map = {
            3: "validation_rules_yaml",
            4: "json_template_yaml",
            6: "workflow_yaml",
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
            8: [f"{uc_name}_simulator-config.yaml", "simulator/manifest.json"],
            9: [f"{uc_name}.yaml", f"{uc_name}_simulator-config.yaml"],
        }
        for ef in extra_map.get(step_num, []):
            ep = Path(run_dir) / ef
            if ep.exists() and str(ep) != primary:
                extra.append(str(ep))

        # Step 5: the CIQ JSON the json-output template produced
        if step_num == 5:
            extra += [str(f) for f in sorted((Path(run_dir) / "json_data").glob("*.json"))]
        # Step 9: the execution-report-generator HTML of every executed case
        if step_num == 9:
            extra += [str(f) for f in sorted((Path(run_dir) / "exec_report").glob("*.html"))]
        # Step 7: include MOP output HTMLs from mop_output/ subdir
        if step_num == 7:
            mop_dir = Path(run_dir) / "mop_output"
            if mop_dir.exists():
                for f in mop_dir.glob("*.html"):
                    if str(f) != primary:
                        extra.append(str(f))
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
            info = self._active_runs.get(run_id, {})
            elapsed = time.monotonic() - info.get("started_at", time.monotonic())
            await self._db.execute_insert(
                "UPDATE pipeline_runs SET status=?, completed_at=?, duration_seconds=?, work_seconds=?, "
                "error_summary=? WHERE id=?",
                (status, datetime.now(timezone.utc).isoformat(), elapsed,
                 self.work_seconds(run_id) if info else None, error or None, run_id),
            )
        except Exception:
            logger.exception("Failed to persist run status")
