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
import re
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


_ASK_PREFIX = re.compile(r"^Step \d+ could not settle this from the documents:\s*")
_ASK_SUFFIX = " What should the activity do here?"


def key(problem: str) -> str:
    """What identifies a problem across runs: its wording without numbers and quoted CIQ values —
    "The requirements list 49 / 63 / 37 commands on node EIR …" is one question (EIR: asked three
    times, answered three times)."""
    text = _ASK_PREFIX.sub("", str(problem or "")).replace(_ASK_SUFFIX, "")
    text = re.sub(r"CIQ values?:.*$", "", text)
    text = re.sub(r"\d+", "#", text)
    return " ".join(text.lower().split())


def plain(problem: str) -> str:
    """The problem as a reviewer reads it: no question framing, no "— use ${…}" hint, no CIQ values."""
    text = _ASK_PREFIX.sub("", str(problem or "")).replace(_ASK_SUFFIX, "")
    text = re.sub(r"\s*CIQ values?:.*$", "", text)
    text = re.split(r"\s+[—-]\s+(?:use|include|add)\b", text)[0]
    return " ".join(text.split()).rstrip(" .") + "."


def answered_keys(requirements: dict | None) -> set[str]:
    """Keys of the step questions the developer has answered (requirements: developer_questions +
    developer_answers, kept per UC)."""
    reqs = requirements or {}
    answers = reqs.get("developer_answers") or {}
    texts = reqs.get("developer_questions") or {}
    return {key(texts.get(qid, "")) for qid, answer in answers.items()
            if str(qid).startswith("clarify_") and str(answer or "").strip() and texts.get(qid)}


def from_errors(step: int, errors: list[str], limit: int = 5, answered: set[str] | None = None) -> list[dict]:
    """One question per problem the step could not fix — none for a problem the developer has
    already answered (stable ids from ``key``: answered once per UC)."""
    questions, seen = [], set()
    for err in errors:
        text = str(err).strip()
        if not text or key(text) in seen or key(text) in (answered or set()):
            continue
        seen.add(key(text))
        qid = f"clarify_s{step}_" + hashlib.sha1(key(text).encode("utf-8")).hexdigest()[:10]
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
