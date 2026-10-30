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

    return errors


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
