"""Structural validation for workflow YAML.

Checks structure against cli-automation framework schema.
Treats ${...} placeholders as valid — they resolve at runtime.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from .autofix import parse_yaml, is_placeholder

logger = logging.getLogger(__name__)


def validate_workflow(yaml_text: str) -> list[str]:
    """Validate workflow YAML structure. Returns list of error strings."""
    errors = []
    parsed = parse_yaml(yaml_text)
    if parsed is None:
        return ["Invalid YAML syntax"]

    # Must have nodes (or nodeGroups for backwards compat)
    nodes = parsed.get("nodes", parsed.get("nodeGroups", {}))
    if not nodes:
        errors.append("Missing 'nodes' section")
    elif not isinstance(nodes, dict):
        errors.append("'nodes' must be a mapping")

    # Collect known node names
    known_nodes = set()
    if isinstance(nodes, dict):
        known_nodes = set(nodes.keys())

    # Must have phases
    phases = parsed.get("phases", {})
    if not phases:
        errors.append("Missing 'phases' section")
    elif not isinstance(phases, (dict, list)):
        errors.append("'phases' must be a mapping or list")

    # Validate each phase
    known_registers: set[str] = set()
    if isinstance(phases, dict):
        for phase_name, phase_body in phases.items():
            if not isinstance(phase_body, dict):
                errors.append(f"Phase '{phase_name}' is not a mapping")
                continue
            steps = phase_body.get("steps", [])
            if not steps:
                errors.append(f"Phase '{phase_name}' has no steps")
                continue
            for j, step in enumerate(steps):
                if not isinstance(step, dict):
                    continue
                step_errors = _validate_step(step, j, phase_name, known_nodes, known_registers)
                errors.extend(step_errors)
                # Track registered variables
                for reg in step.get("register", []):
                    if isinstance(reg, dict):
                        known_registers.add(reg.get("name", ""))
                    elif isinstance(reg, str):
                        known_registers.add(reg)
    elif isinstance(phases, list):
        # Accept list format too (autofix should convert, but validate anyway)
        for i, phase in enumerate(phases):
            if not isinstance(phase, dict):
                continue
            phase_name = phase.get("name", f"phase_{i}")
            steps = phase.get("steps", phase.get("commands", []))
            for j, step in enumerate(steps):
                if isinstance(step, dict):
                    step_errors = _validate_step(step, j, phase_name, known_nodes, known_registers)
                    errors.extend(step_errors)

    errors.extend(check_placeholders(parsed))
    return errors


# Context the cli-automation JAR provides for every node group (spec 004 probes)
_JAR_BUILTINS = {"node", "nodeGroup", "crGroup", "email", "NODE_TYPE", "SUB_ACTIVITY_NAME",
                 "configSequences", "http_status"}
_DOTTED_ROOTS = {"configSequences", "ENV", "SECRET", "vars"}
_DEAD_SYNTAX = [
    (re.compile(r"\$\{\{"), "uses '${{…}}' — write '${…}'"),
    (re.compile(r"\$\{configData\."), "uses 'configData…' — CIQ values are "
                                       "'${configSequences.0.records.0.data.<Column>}'"),
    (re.compile(r"\$\{node_row\."), "uses 'node_row…' — NIAM IDs are '${<NODE>.niamID-<n>}'; "
                                     "other node fields are not in the CIQ JSON"),
    (re.compile(r"\$\{(register|env)\."), "uses 'register.'/'env.' — registered values and "
                                           "--var values are plain '${name}'"),
]
_PLACEHOLDER = re.compile(r"\$\{([^{}]+)\}")
_NAMED_GROUP = re.compile(r"\(\?P?<([A-Za-z][A-Za-z0-9]*)>")


def check_placeholders(parsed: dict) -> list[str]:
    """Report ``${…}`` placeholders the cli-automation JAR cannot resolve."""
    nodes = parsed.get("nodes") or {}
    node_names = set(nodes) if isinstance(nodes, dict) else set()
    defined = set(_JAR_BUILTINS)
    defined |= set(((parsed.get("globals") or {}).get("vars") or {}).keys())
    phases = parsed.get("phases") or {}
    for body in (phases.values() if isinstance(phases, dict) else []):
        if isinstance(body, dict):
            defined |= set((body.get("vars") or {}).keys())
    for regex in _iter_register_regexes(phases):
        defined |= set(_NAMED_GROUP.findall(regex))

    problems: dict[str, str] = {}
    for text in _iter_strings(parsed):
        for pattern, why in _DEAD_SYNTAX:
            for m in pattern.finditer(text):
                ref = text[m.start():text.find("}", m.start()) + 1]
                problems.setdefault(ref, why)
        for m in _PLACEHOLDER.finditer(text):
            expr = m.group(1).strip()
            root = expr.split(".", 1)[0]
            if "." in expr:
                if root in _DOTTED_ROOTS or (root in node_names and re.match(
                        rf"^{re.escape(root)}\.niamID-\d+$", expr)):
                    continue
                problems.setdefault(m.group(0), "unknown placeholder root "
                                    f"'{root}' (not a node NIAM ID, configSequences, ENV, SECRET or vars)")
            elif expr not in defined:
                problems.setdefault(m.group(0), "variable is never registered by a step "
                                    "(add a register with a (?<name>…) group on the step that produces it)")
    return [f"Unresolvable placeholder {ref}: {why}" for ref, why in problems.items()]


def _iter_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _iter_strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _iter_strings(v)


def _iter_register_regexes(value: Any):
    if isinstance(value, dict):
        for reg in value.get("register") or []:
            if isinstance(reg, dict) and isinstance(reg.get("regex"), str):
                yield reg["regex"]
        for v in value.values():
            yield from _iter_register_regexes(v)
    elif isinstance(value, list):
        for v in value:
            yield from _iter_register_regexes(v)


def _validate_step(step: dict, index: int, phase: str, known_nodes: set, known_registers: set) -> list[str]:
    """Validate a single step within a phase."""
    errors = []
    # Accept both "send" (correct) and "cmd"/"command" (will be auto-fixed)
    cmd = step.get("send", step.get("cmd", step.get("command", "")))
    node = step.get("node", "")
    step_id = f"Phase '{phase}' step {index}"

    if not cmd:
        errors.append(f"{step_id}: missing 'send' (command to execute)")

    # Node reference check (skip placeholders)
    if node and not is_placeholder(node) and node not in known_nodes:
        errors.append(f"{step_id}: references unknown node '{node}'")

    return errors
