"""Responses for the bash-guarded commands step 6 writes (``if [[ … ]]; then CMD; else echo
SKIPPED_BY_CONDITION; fi``).

MockWire answers from a fixed table and does not evaluate bash, so every branch got the same
fixed reply: MRF1 (VER 13.0.3.7) answered SKIPPED_BY_CONDITION for its copy branch and step 9
passed without anything being copied (S8-29, false green). Step 8 evaluates the condition for
every CIQ target (its record + node values) and adds an exact response per target: the inner
command's response when the condition holds, SKIPPED_BY_CONDITION otherwise.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

SKIPPED = "SKIPPED_BY_CONDITION"
_GUARD = re.compile(r"^if (\[\[ .+? \]\]); then\s*(.*?)\s*;?\s*else echo " + SKIPPED + r";\s*fi\s*$", re.S)
_QUOTED = re.compile(r"'([^']*)'")


def _glob_regex(pattern: str) -> str:
    out, i = [], 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "'":
            j = pattern.index("'", i + 1)
            out.append(re.escape(pattern[i + 1:j]))
            i = j + 1
            continue
        out.append(".*" if ch == "*" else "." if ch == "?" else re.escape(ch))
        i += 1
    return "".join(out)


def _split_top(expr: str, op: str) -> list[str]:
    """Split at `` && `` / `` || `` outside single quotes."""
    parts, depth_q, start, i = [], False, 0, 0
    while i < len(expr):
        if expr[i] == "'":
            depth_q = not depth_q
        elif not depth_q and expr.startswith(op, i):
            parts.append(expr[start:i])
            start = i + len(op)
            i += len(op)
            continue
        i += 1
    parts.append(expr[start:])
    return [p.strip() for p in parts]


def eval_test(test: str) -> bool:
    """Evaluate the ``[[ … ]]`` subset step 6 generates: quoted left operand, ==/!= glob,
    =~ ERE, ``!``, && and ||. Raises ValueError for anything else."""
    body = test.strip()
    if not (body.startswith("[[") and body.endswith("]]")):
        raise ValueError(test)
    body = body[2:-2].strip()
    return any(all(_term(t) for t in _split_top(alt, " && ")) for alt in _split_top(body, " || "))


def _term(term: str) -> bool:
    neg = term.startswith("! ")
    if neg:
        term = term[2:].strip()
    m = re.match(r"^'([^']*)'\s*(==|!=|=~)\s*(.*)$", term, re.S)
    if not m:
        raise ValueError(term)
    left, op, right = m.group(1), m.group(2), m.group(3).strip()
    if op == "=~":
        result = re.search(right, left) is not None
    else:
        result = re.fullmatch(_glob_regex(right), left, re.S) is not None
        result = result if op == "==" else not result
    return not result if neg else result


def targets(ciq_json_files: list[str]) -> list[dict]:
    """[{"node", "nodeGroup", "crGroup", "record", "fields"}] for every node of the CIQ JSON."""
    from tools.ciq_json import node_groups
    out = []
    for jf in ciq_json_files or []:
        try:
            doc = json.loads(Path(jf).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for cr, group in node_groups(doc):
            seqs = group.get("configSequences") or [{}]
            record = (((seqs[0] or {}).get("records") or [{}])[0] or {}).get("data") or {}
            for n in group.get("nodes") or []:
                fields = {f"{k}.{f}": v for k, d in n.items() if isinstance(d, dict) for f, v in d.items()}
                out.append({"node": str(n.get("node", "")), "nodeGroup": str(group.get("nodeGroup", "")),
                            "crGroup": cr, "record": record, "fields": fields})
    return out


def _resolve(cmd: str, t: dict) -> str:
    from core.config import runtime_params
    params = runtime_params()

    def value(m):
        expr = m.group(1).strip()
        if expr.startswith("configSequences.") and ".data." in expr:
            v = t["record"].get(expr.split(".data.", 1)[1].strip())
        elif expr in t["fields"]:
            v = t["fields"][expr]
        elif expr in ("node", "nodeGroup", "crGroup"):
            v = t[expr]
        else:
            v = params.get(expr)
        return m.group(0) if v is None else str(v)
    return re.sub(r"\$\{([^{}]+)\}", value, cmd)


def guarded_responses(commands: list[dict], entries: dict, ciq_json_files: list[str]) -> dict:
    """{key: entry} answering each guarded command per CIQ target (to be placed first)."""
    from .skill import _find_entry, command_key_regex
    out: dict = {}
    tgts = targets(ciq_json_files)
    for c in commands:
        cmd = str(c.get("cmd", "")).strip()
        if SKIPPED not in cmd or not _GUARD.match(cmd):
            continue
        for t in tgts:
            resolved = _resolve(cmd, t)
            m = _GUARD.match(resolved)
            if not m or "${" in m.group(1):
                continue
            try:
                holds = eval_test(m.group(1))
            except (ValueError, re.error):
                continue
            if holds:
                inner = m.group(2)
                entry = (_find_entry(entries, inner, inner)[1] or {}) if inner else {}
                lines = list(entry.get("lines") or []) if isinstance(entry, dict) else []
            else:
                lines = [SKIPPED]
            out["regex:" + command_key_regex(resolved)] = {"lines": lines}
    if out:
        logger.info("Phase 2: %d guarded-command responses evaluated per CIQ target", len(out))
    return out
