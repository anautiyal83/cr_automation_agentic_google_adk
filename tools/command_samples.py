"""Library of real command outputs learned across runs (spec 004 DEP-20).

Every run's uploaded session logs are parsed into ``{command, output, prompt}`` samples
(requirement gathering). They are also kept here, so later runs — of any use case — can check
regexes against, and simulate, commands whose output nobody uploaded this time.

    python -m tools.command_samples --import data/uploads/*.txt   # seed from existing uploads
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAX_SAMPLES = 3000


def library_path() -> Path:
    return Path(os.environ.get("NETCONFIG_SAMPLE_LIBRARY")
                or PROJECT_ROOT / "data" / "knowledge" / "command_samples.json")


def _norm(command: str) -> str:
    return re.sub(r"\s+", " ", str(command or "").strip())


def _key(sample: dict) -> str:
    out = str(sample.get("output", ""))[:400]
    return _norm(sample.get("command", "")) + "\x00" + hashlib.sha1(out.encode("utf-8", "replace")).hexdigest()


def load() -> list[dict]:
    path = library_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [s for s in data.get("samples", []) if isinstance(s, dict) and s.get("command")]
    except Exception as e:
        logger.warning("Command sample library unreadable (%s): %s", path, e)
        return []


def add(samples: list[dict], source: str = "") -> int:
    """Add samples (deduplicated by command + output); returns how many were new."""
    samples = [s for s in samples or [] if isinstance(s, dict) and s.get("command") and str(s.get("output", "")).strip()]
    if not samples:
        return 0
    existing = load()
    seen = {_key(s) for s in existing}
    new = []
    for s in samples:
        k = _key(s)
        if k not in seen:
            seen.add(k)
            new.append({"command": s["command"], "output": s["output"], "prompt": s.get("prompt", ""),
                        "source": source})
    if not new:
        return 0
    merged = (existing + new)[-MAX_SAMPLES:]
    path = library_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"samples": merged}, indent=1), encoding="utf-8")
        tmp.replace(path)
    except Exception as e:
        logger.warning("Could not update command sample library %s: %s", path, e)
        return 0
    logger.info("Command sample library: +%d samples (%d total)", len(new), len(merged))
    return len(new)


def with_library(run_samples: list[dict]) -> list[dict]:
    """This run's samples first; library samples only for commands this run did not record."""
    have = {_norm(s.get("command", "")) for s in run_samples or [] if isinstance(s, dict)}
    extra, added = [], set()
    for s in load():
        cmd = _norm(s.get("command", ""))
        if cmd and cmd not in have and cmd not in added:
            extra.append(s)
            added.add(cmd)
    return list(run_samples or []) + extra


def _main() -> None:
    import argparse
    import glob
    from skills.requirement_gathering.parsers.command_output_parser import parse_command_outputs
    parser = argparse.ArgumentParser(description="Seed the command sample library from session logs")
    parser.add_argument("--import", dest="files", nargs="+", required=True)
    args = parser.parse_args()
    total = 0
    for pattern in args.files:
        for f in sorted(glob.glob(pattern)):
            text = Path(f).read_text(encoding="utf-8", errors="replace")
            total += add(parse_command_outputs(text), source=Path(f).name)
    print(f"{total} new samples; library {library_path()} has {len(load())}")


if __name__ == "__main__":
    _main()
