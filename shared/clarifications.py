"""Open questions a later step could not resolve from the documents.

A step (e.g. step 6 after its correction rounds) records them in ``<run_dir>/clarifications.json``;
its milestone offers "Clarify in requirements": the run goes back to step 1, which asks them with
the other questions, compiles the requirements again and re-runs the later steps (disk, not ADK
session state, which does not survive between agents).
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

FILE = "clarifications.json"


def save(run_dir: str, step: int, questions: list[dict]) -> None:
    if not run_dir:
        return
    path = Path(run_dir) / FILE
    if questions:
        path.write_text(json.dumps({"step": step, "questions": questions}, indent=2), encoding="utf-8")
    elif path.exists():
        path.unlink()


def load(run_dir: str) -> dict:
    try:
        data = json.loads((Path(run_dir) / FILE).read_text(encoding="utf-8")) if run_dir else {}
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) and data.get("questions") else {}


def clear(run_dir: str) -> None:
    save(run_dir, 0, [])


def from_errors(step: int, errors: list[str], limit: int = 5) -> list[dict]:
    """One question per problem the step could not fix (stable ids: answered once per UC)."""
    questions, seen = [], set()
    for err in errors:
        text = str(err).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        qid = f"clarify_s{step}_" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
        questions.append({
            "id": qid,
            "category": "orchestration",
            "question": (f"Step {step} could not settle this from the documents: {text[:400]} "
                         "What should the activity do here?"),
            "importance": "critical",
            "input_type": "text",
            "default_value": "",
            "context": f"Raised by step {step} after its automatic correction rounds.",
            "helper_text": "Describe the expected behaviour, value or command.",
        })
        if len(questions) >= limit:
            break
    return questions
