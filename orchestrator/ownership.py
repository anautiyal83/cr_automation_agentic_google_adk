"""Which server process executes a run (bugs.md OB-10).

A second server on the same ``data/pipeline.db`` (a preview, a second ``start.ps1``) must not
resume the first server's live run at its start nor start a second pipeline next to it. A run
records its owner ``<host>:<pid>:<process start>`` when it starts executing; the start time
tells a live owner from a later process that got the same pid.
"""
from __future__ import annotations

import os
import socket

import psutil


def this_owner() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{int(psutil.Process().create_time())}"


def owner_alive(owner: str | None) -> bool:
    """True while the process that owns a run still runs. Runs from before ownership (no owner)
    and malformed values count as orphaned. Another host's process cannot be checked: its runs
    count as alive (one host per database)."""
    try:
        host, pid, started = str(owner or "").rsplit(":", 2)
        pid, started = int(pid), int(started)
    except ValueError:
        return False
    if host != socket.gethostname():
        return True
    try:
        return int(psutil.Process(pid).create_time()) == started
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        return False
