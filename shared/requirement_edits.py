"""Edits the developer makes to the requirements at the step 2 review ("Edit requirements").

The Live Run page writes them to ``<run_dir>/requirement_edits.json`` and answers the step 2 pause
with ``edit``; step 1 applies them (changed answers replace the saved ones, the note becomes an
authoritative developer note), compiles the requirements again and pauses at step 2 once more
(disk, not ADK session state, which does not survive between agents).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

FILE = "requirement_edits.json"


def save(run_dir: str, answers: dict, note: str, by: str) -> dict:
    edit = {"answers": {k: str(v) for k, v in (answers or {}).items()}, "note": (note or "").strip(),
            "by": by or "", "at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
    if run_dir:
        (Path(run_dir) / FILE).write_text(json.dumps(edit, indent=2), encoding="utf-8")
    return edit


def take(run_dir: str) -> dict:
    """The pending edit, removed from disk (applied once)."""
    path = Path(run_dir) / FILE if run_dir else None
    if not path or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("Requirement edits of %s unreadable: %s", run_dir, e)
        data = {}
    path.unlink(missing_ok=True)
    return data if isinstance(data, dict) else {}


def note_block(note: str, by: str, at: str) -> str:
    """How an edit's note is kept in the developer notes (step 6 follows them as authoritative)."""
    who = f", {by}" if by else ""
    return f"=== Requirement update ({at}{who}) — authoritative ===\n{note.strip()}"
