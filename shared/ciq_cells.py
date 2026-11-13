"""What special CIQ cells mean — one confirmed table for every step (user, 2026-11-13).

The developer says it in free text ("If 'UNCHANGED' or blank, the attribute … should be omitted for 'UNCHANGED'
indicating no change, and create empty tag in case of blank to delete …"); step 6's scripts and step 8's simulator
each read it differently (run 6570694d: the report took a blank cell as "unchanged", the simulator as "delete"). This
module derives the table once from the answers and notes, step 2 shows it, Edit requirements corrects it (a later
specific sentence overrides), and step 6 (helper scripts) and step 8 / 9 (simulator) apply the same rules.

    {"unchanged": "keep", "blank": "delete"}

Meanings: ``keep`` — the field is not changed; ``delete`` — the attribute is removed (absent afterwards).
"""
from __future__ import annotations

import re

CELLS = ("unchanged", "blank")
MEANINGS = ("keep", "delete")
DEFAULT = {"unchanged": "keep", "blank": "keep"}
LABELS = {"unchanged": "UNCHANGED", "blank": "blank / empty"}
DESCRIPTIONS = {"keep": "keep the current value (no change)",
                "delete": "delete the attribute (absent after the activity)"}

_CELL = {"unchanged": re.compile(r"\bunchanged\b", re.I),
         "blank": re.compile(r"\b(?:blank|empty)\b", re.I)}
_DELETE = re.compile(r"\b(?:delete|deleted|remove|removed|clear|cleared)\b", re.I)
_KEEP = re.compile(r"\b(?:no change|not changed|unchanged|remain|remains|keep|kept|same|ignore|ignored|skip|skipped|"
                   r"omit|omitted|untouched)\b", re.I)


def _sentences(requirements: dict) -> list[str]:
    texts: list[str] = []

    def walk(value):
        if isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
        elif isinstance(value, str):
            texts.append(value)
    walk((requirements or {}).get("developer_answers") or {})
    walk((requirements or {}).get("developer_notes") or "")
    out = []
    for text in texts:
        out += [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+|,\s+(?=and\b)|\band\s+(?=create\b)", text) if s.strip()]
    return out


def derive(requirements: dict) -> dict[str, str]:
    """The table from the developer's answers and notes, in order: a sentence about one kind of cell decides it
    (a later one overrides); a sentence naming both kinds only fills what no specific sentence gave."""
    specific: dict[str, str] = {}
    general: dict[str, str] = {}
    for sentence in _sentences(requirements):
        named = [c for c in CELLS if _CELL[c].search(sentence)]
        if not named:
            continue
        meaning = "delete" if _DELETE.search(sentence) else "keep" if _KEEP.search(sentence) else None
        if meaning is None:
            continue
        target = specific if len(named) == 1 else general
        for cell in named:
            target[cell] = meaning
    return {**DEFAULT, **general, **specific}


def rules(requirements: dict) -> dict[str, str]:
    """The confirmed table: ``ciq_cell_rules`` of the requirements when step 1 / an edit stored it, else derived."""
    stored = (requirements or {}).get("ciq_cell_rules")
    if isinstance(stored, dict) and stored:
        return {**DEFAULT, **{k: v for k, v in stored.items() if k in CELLS and v in MEANINGS}}
    return derive(requirements)


def describe(table: dict[str, str]) -> list[str]:
    """One line per cell kind: `UNCHANGED → keep the current value (no change)`."""
    return [f"{LABELS[c]} → {DESCRIPTIONS[table.get(c, 'keep')]}" for c in CELLS]


def kind(value) -> str:
    """``unchanged`` / ``blank`` / ``value`` for a CIQ cell."""
    text = "" if value is None else str(value).strip()
    if not text or text.lower() in ("none", "null", "nan"):
        return "blank"
    if text.lower() == "unchanged":
        return "unchanged"
    return "value"
