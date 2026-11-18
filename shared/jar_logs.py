"""Every JAR call's full console output, kept in the run folder (OB-105).

The logs only had the first 500 characters of a failed call and nothing of a passed one.
``<run_dir>/jar_logs/step<N>_<NN>_<label>.log`` holds the command line, working folder, exit code,
time taken, stdout and stderr of one call — numbered in call order per step, never overwritten
(a retried step adds its calls after the earlier ones). The pipeline names the run folder and the
display step before each agent (``set_context``); without one (CLI skill tests) nothing is written.
"""
from __future__ import annotations

import logging
import re
import shlex
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

FOLDER = "jar_logs"

_context: dict[str, object] = {"run_dir": "", "step": 0}


def set_context(run_dir: str = "", step: int = 0) -> None:
    _context["run_dir"] = run_dir or ""
    _context["step"] = step or 0


def _slug(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9.]+", "-", label).strip("-")[:80] or "jar"


def write(label: str, cmd: list[str], cwd: str | None, exit_code: int, elapsed: float,
          stdout: str, stderr: str) -> str | None:
    """Write one call's log; returns its path, or None when no run is set or the write failed."""
    run_dir, step = str(_context["run_dir"]), int(_context["step"] or 0)
    if not run_dir:
        return None
    body = (f"JAR call: {label}\n"
            f"Step: {step}\n"
            f"Finished: {datetime.now().isoformat(timespec='seconds')}\n"
            f"Exit code: {exit_code}\n"
            f"Duration: {elapsed:.1f}s\n"
            f"Working folder: {cwd or '(server folder)'}\n"
            f"Command:\n{' '.join(shlex.quote(str(c)) for c in cmd)}\n"
            f"\n===== stdout =====\n{stdout or ''}\n"
            f"\n===== stderr =====\n{stderr or ''}\n")
    try:
        folder = Path(run_dir) / FOLDER
        folder.mkdir(parents=True, exist_ok=True)
        prefix = f"step{step}_"
        n = len(list(folder.glob(prefix + "*.log")))
        while True:  # calls of one step run in parallel (step 5 negative cases): claim a free number
            n += 1
            path = folder / f"{prefix}{n:02d}_{_slug(label)}.log"
            try:
                with open(path, "x", encoding="utf-8", newline="") as fh:  # Windows output: one line end
                    fh.write(body.replace("\r\n", "\n"))
                return str(path)
            except FileExistsError:
                continue
    except OSError as e:  # a log must never fail the step
        logger.warning("JAR log not written for %s: %s", label, e)
        return None
