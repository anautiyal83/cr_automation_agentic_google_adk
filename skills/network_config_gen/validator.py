"""Structural validation for workflow YAML.

Checks structure against workflow schema expectations.
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

    # Must have nodeGroups
    node_groups = parsed.get("nodeGroups", {})
    if not node_groups:
        errors.append("Missing 'nodeGroups' section")
    elif not isinstance(node_groups, dict):
        errors.append("'nodeGroups' must be a mapping")

    # Collect known node names
    known_nodes = set()
    if isinstance(node_groups, dict):
        for ng_name, ng_val in node_groups.items():
            known_nodes.add(ng_name)
            if isinstance(ng_val, dict):
                for node in ng_val.get("nodes", []):
                    if isinstance(node, dict):
                        known_nodes.add(node.get("name", ""))

    # Must have phases
    phases = parsed.get("phases", [])
    if not phases:
        errors.append("Missing 'phases' section")
    elif not isinstance(phases, list):
        errors.append("'phases' must be a list")

    # Validate each phase
    known_registers: set[str] = set()
    if isinstance(phases, list):
        for i, phase in enumerate(phases):
            if not isinstance(phase, dict):
                errors.append(f"Phase {i} is not a mapping")
                continue

            phase_name = phase.get("name", phase.get("phase", f"phase_{i}"))
            steps = phase.get("steps", phase.get("commands", []))

            if not steps:
                errors.append(f"Phase '{phase_name}' has no steps/commands")
                continue

            for j, step in enumerate(steps):
                if not isinstance(step, dict):
                    continue
                step_errors = _validate_step(step, j, phase_name, known_nodes, known_registers)
                errors.extend(step_errors)

                # Track registered variables
                regs = step.get("register", [])
                if isinstance(regs, list):
                    known_registers.update(regs)
                elif isinstance(regs, str):
                    known_registers.add(regs)

    # Check for unresolved placeholder references (warnings, not errors)
    _check_placeholder_refs(parsed, known_nodes, known_registers)

    return errors


def _validate_step(step: dict, index: int, phase: str, known_nodes: set, known_registers: set) -> list[str]:
    """Validate a single step/command within a phase."""
    errors = []
    cmd = step.get("cmd", step.get("command", ""))
    node = step.get("node", "")
    step_id = f"Phase '{phase}' step {index} (cmd='{cmd[:30]}')"

    if not cmd:
        errors.append(f"{step_id}: missing 'cmd' or 'command'")

    # Node reference check (skip placeholders)
    if node and not is_placeholder(node) and node not in known_nodes:
        errors.append(f"{step_id}: references unknown node '{node}'")

    # Validation block
    validation = step.get("validation", {})
    if isinstance(validation, dict) and validation:
        if "description" not in validation:
            errors.append(f"{step_id}: validation missing 'description' (required by JAR)")

        criteria = validation.get("criteria", {})
        if isinstance(criteria, dict):
            expr = criteria.get("expr", "")
            if not expr:
                errors.append(f"{step_id}: validation.criteria.expr is empty")
        elif criteria:
            errors.append(f"{step_id}: validation.criteria must be a mapping with 'expr'")

    return errors


def _check_placeholder_refs(parsed: dict, known_nodes: set, known_registers: set) -> None:
    """Log warnings for potentially unresolvable placeholders (informational only)."""
    yaml_str = str(parsed)

    # Check ${register.xxx} references
    for match in re.finditer(r"\$\{register\.(\w+)\}", yaml_str):
        var = match.group(1)
        if var not in known_registers:
            logger.debug("Placeholder ${register.%s} not yet registered — may resolve at runtime", var)

    # Check ${nodeGroups.xxx} references
    for match in re.finditer(r"\$\{nodeGroups\.(\w+)\.", yaml_str):
        node = match.group(1)
        if node not in known_nodes:
            logger.debug("Placeholder ${nodeGroups.%s...} references unknown node group", node)
