"""Run phase options (spec 010): which phases a run's workflow has, and how files travel.

Nine phases in a fixed order (O-D1); every phase and sub-phase can be switched off (O-D5).
"Via repo server" exists only while Backup is on: every file (input files, backups, restores)
then goes through the repo server (O-D2/O-D4 — the route itself is built in a later step).

    {"phases": {"preNodeHealthCheck": true, …}, "backup_via_repo": false}
"""
from __future__ import annotations

import re
from typing import Any

# (key, label, group) in execution order — the keys are the workflow's phase keys
PHASES: list[tuple[str, str, str]] = [
    ("preNodeHealthCheck", "PreNodeHealthCheck", ""),
    ("backup", "Backup", ""),
    ("activityPrecheck", "ActivityPrecheck", "activity"),
    ("activityConfiguration", "ActivityConfiguration", "activity"),
    ("activityPostCheck", "ActivityPostCheck", "activity"),
    ("postNodeHealthCheck", "PostNodeHealthCheck", ""),
    ("rollbackPrecheck", "RollbackPrecheck", "rollback"),
    ("rollbackConfiguration", "RollbackConfiguration", "rollback"),
    ("rollbackPostCheck", "RollbackPostCheck", "rollback"),
]
KEYS = [k for k, _, _ in PHASES]
LABELS = {k: label for k, label, _ in PHASES}
GROUPS = {"activity": "Activity", "rollback": "Rollback"}
# What each phase is for — the step 6 prompt tells the LLM where steps belong
PURPOSE = {
    "preNodeHealthCheck": "health checks of every node before anything changes",
    "backup": "save what the activity will overwrite",
    "activityPrecheck": "checks right before the change (inputs present, current values read)",
    "activityConfiguration": "the change itself",
    "activityPostCheck": "verify the change (values, files, permissions)",
    "postNodeHealthCheck": "health checks of every node after the change",
    "rollbackPrecheck": "checks before restoring (backup present, current state)",
    "rollbackConfiguration": "undo the change: remove what was loaded / restore the backup",
    "rollbackPostCheck": "verify the node is back to its previous state",
}


def default() -> dict[str, Any]:
    return {"phases": {k: True for k in KEYS}, "backup_via_repo": False}


def normalize(raw: Any) -> dict[str, Any]:
    """Any stored / submitted value → a complete options dict (unknown keys dropped; via repo
    only with Backup; nothing given → the default)."""
    opts = default()
    if not isinstance(raw, dict):
        return opts
    phases = raw.get("phases") if isinstance(raw.get("phases"), dict) else {}
    for k in KEYS:
        if k in phases:
            opts["phases"][k] = bool(phases[k])
    opts["backup_via_repo"] = bool(raw.get("backup_via_repo")) and opts["phases"]["backup"]
    return opts


def selected(options: Any) -> list[str]:
    """Selected phase keys in execution order."""
    opts = normalize(options)
    return [k for k in KEYS if opts["phases"][k]]


def is_default(options: Any) -> bool:
    return normalize(options) == default()


def summary(options: Any) -> str:
    """One line for the review summary / run details."""
    opts = normalize(options)
    off = [LABELS[k] for k in KEYS if not opts["phases"][k]]
    text = "All phases" if not off else f"Without {', '.join(off)}"
    if opts["phases"]["backup"]:
        text += " · backup via repo server" if opts["backup_via_repo"] else " · backup kept on the node"
    return text


# --------------------------------------------------------------------------- workflow phases

def canonical_phase(key: str, body: dict | None) -> str:
    """The model phase a generated workflow phase belongs to (its key or name decides)."""
    from skills.network_config_gen.contract import phase_kind
    body = body if isinstance(body, dict) else {}
    kind = phase_kind(str(key), body)
    text = f"{key} {body.get('name') or ''}"
    if kind == "pre_health":
        return "preNodeHealthCheck"
    if kind == "post_health":
        return "postNodeHealthCheck"
    if kind == "backup":
        return "backup"
    sub = ("Precheck" if re.search(r"pre.?check|pre[-_ ]?verif", text, re.I)
           else "PostCheck" if re.search(r"post.?check|verif|validat", text, re.I)
           else "Configuration")
    return ("rollback" if kind == "rollback" else "activity") + sub
