"""Run checkpoints for restart recovery (spec 007).

The pipeline saves one checkpoint per run (table ``run_checkpoints``, overwritten):
- ``kind="agent"`` before an agent runs — resuming re-runs that agent (the step it was in);
- ``kind="milestone"`` when a review pause begins — resuming shows the same pause again.

A checkpoint holds the session state (JSON), the run's requirements and the agent index. It is
deleted when the run ends. The pipeline writes through ``save`` (module-level DB, like the LLM
usage store) because the agents have no DB handle.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

MAX_RESUME_ATTEMPTS = 2
_db = None


def set_db(db) -> None:
    global _db
    _db = db


async def save(run_id: str, agent_index: int, agent_name: str, kind: str, state: dict,
               requirements: dict | None) -> None:
    """Write the run's checkpoint. A checkpoint at a new position resets the resume attempts
    (the run made progress). Never raises: a missing checkpoint only costs the recovery."""
    if _db is None or not run_id:
        return
    try:
        state_json = json.dumps(dict(state), default=str)
        rows = await _db.execute("SELECT agent_index, kind, resume_attempts FROM run_checkpoints WHERE run_id = ?",
                                 (run_id,))
        same = rows and rows[0]["agent_index"] == agent_index and rows[0]["kind"] == kind
        attempts = rows[0]["resume_attempts"] if same else 0
        await _db.execute_insert(
            "INSERT OR REPLACE INTO run_checkpoints (run_id, agent_index, agent_name, kind, state_json, "
            "requirements_json, resume_attempts, saved_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, agent_index, agent_name, kind, state_json,
             json.dumps(requirements, default=str) if requirements else None, attempts,
             datetime.now(timezone.utc).isoformat()))
    except Exception as e:  # noqa: BLE001
        logger.warning("Checkpoint of run %s not saved: %s", run_id[:8], e)


async def load(db, run_id: str) -> dict | None:
    rows = await db.execute("SELECT * FROM run_checkpoints WHERE run_id = ?", (run_id,))
    if not rows:
        return None
    cp = dict(rows[0])
    try:
        cp["state"] = json.loads(cp.pop("state_json") or "{}")
        cp["requirements"] = json.loads(cp.pop("requirements_json") or "null")
    except ValueError:
        return None
    return cp


async def count_attempt(db, run_id: str) -> int:
    await db.execute("UPDATE run_checkpoints SET resume_attempts = resume_attempts + 1 WHERE run_id = ?", (run_id,))
    rows = await db.execute("SELECT resume_attempts FROM run_checkpoints WHERE run_id = ?", (run_id,))
    return int(rows[0]["resume_attempts"]) if rows else 0


async def delete(db, run_id: str) -> None:
    try:
        await db.execute("DELETE FROM run_checkpoints WHERE run_id = ?", (run_id,))
    except Exception as e:  # noqa: BLE001
        logger.debug("Checkpoint of run %s not deleted: %s", run_id[:8], e)


def resume_position(cp: dict, agent_names: list[str], step_of: dict[str, list[int]],
                    first_agent_of_step) -> tuple[int, bool]:
    """(agent index to start at, resume at its milestone pause).

    A milestone checkpoint resumes the pause itself (step 8's re-runs step 8: its simulator is
    gone). An agent checkpoint re-runs that agent —
    unless it needs a live resource an earlier step started: step 9 needs the simulator of step 8,
    so it resumes at step 8's first agent."""
    index = int(cp["agent_index"])
    name = agent_names[index] if 0 <= index < len(agent_names) else ""
    if cp.get("kind") == "milestone":
        # step 8's review waits with the simulator running — gone after a restart: run step 8 again
        return index, 8 not in step_of.get(name, [])
    if 9 in step_of.get(name, []):
        sim = first_agent_of_step(8)
        if sim in agent_names:
            return agent_names.index(sim), False
    return index, False
