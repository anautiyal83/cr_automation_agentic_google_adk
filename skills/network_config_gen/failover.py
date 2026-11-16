"""NIAM failover as production writes it (spec 013 B-R3) — generic, for every CIQ node type with several NIAM
entries (``niamID-1…n``).

One node per entry (``niam_<type>_1…n``, ``ne_id ${<TYPE>.niamID-k}``). At the start of each phase that works on
the node, a login probe per entry (``echo <TYPE>_LOGIN_OK``, ``on_failure: warning``) — the first that answers is
registered as ``Target<TYPE>``; every probe runs only while it is empty (once per run — again in a phase that
starts without one, e.g. a rollback-only run), then a guard stops when none answered.
The phase's steps run on ``node: ${Target<TYPE>}`` (the JAR resolves the node at run time — probed 2026-11-11).

Applied last by ``structure.apply`` (after the execution contract: the probes keep ``on_failure: warning``) and
removed again by ``structure.flatten`` (``undo``): step 6's checks and the LLM see one node per type.
"""
from __future__ import annotations

import copy
import re
from typing import Any

from .sessions import ciq_type

AUTO = "nc_fo_"


def _var(typ: str) -> str:
    return "Target" + re.sub(r"\W", "", typ.upper())


def _base(node: str) -> str:
    return re.sub(r"_server$", "", node)


def _walk(steps: Any):
    for step in steps or []:
        if isinstance(step, dict):
            yield step
            yield from _walk(step.get("steps"))


def _probes(typ: str, names: list[str]) -> list[dict]:
    var, ok = _var(typ), re.sub(r"\W", "", typ.upper()) + "_LOGIN_OK"
    steps = []
    for k, name in enumerate(names, 1):
        step = {"id": f"{AUTO}{typ.lower()}_{k}", "node": name,
                "command_description": f"{typ} login detection — trying niamID-{k}: ${{{typ}.niamID-{k}}}",
                "send": f"echo {ok}", "when": f'${{{var} == ""}}', "hide_when_skipped": True, "on_failure": "warning",
                "register": [{"name": "LOGIN", "value": ""}, {"regex": f"(?<LOGIN>{ok})"},
                             {"name": var, "when": f'${{LOGIN == "{ok}"}}', "value": name}]}
        steps.append(step)
    steps.append({"id": f"{AUTO}{typ.lower()}_guard", "node": "local",
                  "command_description": f"Stop when no NIAM entry of {typ} answered",
                  "send": "echo niam-login-guard", "when": f'${{{var} == ""}}', "hide_when_skipped": True,
                  "on_failure": "stop",
                  "validation": {"enabled": True, "description": f"A NIAM entry of {typ} answered",
                                 "success": {"criteria": {"expr": f'${{{var} != ""}}'},
                                             "message": f"{typ} reached through ${{{var}}}."},
                                 "failure": {"message": f"No NIAM entry of {typ} answered (niamID-1…{len(names)})."}}})
    return steps


def apply(parsed: dict, node_types: dict[str, int] | None) -> int:
    nodes, phases = parsed.get("nodes"), parsed.get("phases")
    if not node_types or not isinstance(nodes, dict) or not isinstance(phases, dict):
        return 0
    changes = 0
    for node in [n for n in nodes if str(n).startswith("niam_") and str(n).endswith("_server")]:
        typ = ciq_type(parsed, node)
        count = int(node_types.get(typ) or 1)
        if count < 2:
            continue
        names = [f"{_base(node)}_{k}" for k in range(1, count + 1)]
        spec = nodes.pop(node)
        for k, name in enumerate(names, 1):
            entry = copy.deepcopy(spec)
            ssh = entry.setdefault("ssh", {})
            ssh["host"] = ssh["ne_id"] = "${" + typ + ".niamID-" + str(k) + "}"
            nodes[name] = entry
        var = _var(typ)
        parsed.setdefault("globals", {}).setdefault("vars", {})[var] = ""
        for body in phases.values():
            if not isinstance(body, dict):
                continue
            used = False
            for step in _walk(body.get("steps")):
                if step.get("node") == node:
                    step["node"] = "${" + var + "}"
                    used = True
            if used:
                body["steps"] = _probes(typ, names) + list(body.get("steps") or [])
        changes += 1
    return changes


def undo(parsed: dict) -> int:
    """Back to one ``niam_<type>_server`` node per type (the form step 6 checks)."""
    nodes, phases = parsed.get("nodes"), parsed.get("phases")
    if not isinstance(nodes, dict) or not isinstance(phases, dict):
        return 0
    changes = 0
    for first in [n for n in nodes if re.fullmatch(r"niam_\w+_1", str(n))]:
        typ = ciq_type(parsed, first)
        if not typ:
            continue
        base = first[:-2]
        node, var = base + "_server", _var(typ)
        spec = nodes[first]
        for name in [n for n in list(nodes) if re.fullmatch(re.escape(base) + r"_\d+", str(n))]:
            del nodes[name]
        ssh = spec.setdefault("ssh", {})
        ssh["host"] = ssh["ne_id"] = "${" + typ + ".niamID-1}"
        nodes[node] = spec
        vars_ = (parsed.get("globals") or {}).get("vars") or {}
        vars_.pop(var, None)
        for body in phases.values():
            if not isinstance(body, dict):
                continue
            body["steps"] = [s for s in body.get("steps") or []
                             if not (isinstance(s, dict) and str(s.get("id", "")).startswith(AUTO))]
            for step in _walk(body["steps"]):
                if step.get("node") == "${" + var + "}":
                    step["node"] = node
        changes += 1
    return changes
