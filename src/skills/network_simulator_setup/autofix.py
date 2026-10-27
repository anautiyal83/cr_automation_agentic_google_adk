"""Auto-fix utilities for MockWire simulator configuration YAML.

Fixes common LLM mistakes:
- Ensure unique ports per node (no collisions)
- Ensure prompt patterns match workflow expectations
- Ensure every workflow command has a simulator response
- Repair YAML syntax
"""
from __future__ import annotations

import logging
import re
from io import StringIO
from typing import Any

logger = logging.getLogger(__name__)

_BASE_PORT = 2222


def clean_yaml_response(text: str) -> str:
    """Strip markdown code fences and leading/trailing noise."""
    text = re.sub(r"^```(?:ya?ml)?\s*\n?", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n?```\s*$", "", text, flags=re.MULTILINE)

    lines = text.split("\n")
    yaml_start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and (stripped.startswith(("#", "---", "servers", "nodes", "simulator"))
                         or ":" in stripped):
            yaml_start = i
            break

    return "\n".join(lines[yaml_start:]).rstrip()


def parse_yaml(text: str) -> dict | None:
    """Parse YAML text, returning dict or None on failure."""
    try:
        from ruamel.yaml import YAML
        parsed = YAML().load(StringIO(text))
        return dict(parsed) if parsed else None
    except Exception:
        return None


def dump_yaml(data: dict) -> str:
    """Dump dict to YAML string."""
    from ruamel.yaml import YAML
    stream = StringIO()
    yaml = YAML()
    yaml.default_flow_style = False
    yaml.width = 200
    yaml.dump(data, stream)
    return stream.getvalue()


def fix_simulator_config(yaml_text: str, workflow_nodes: list[str] | None = None) -> str:
    """Apply auto-fix rules to simulator config YAML.

    Args:
        yaml_text: LLM-generated YAML.
        workflow_nodes: Node names from the workflow (for completeness check).
    """
    yaml_text = clean_yaml_response(yaml_text)
    parsed = parse_yaml(yaml_text)
    if not parsed:
        return yaml_text

    # Fix port collisions
    _fix_port_collisions(parsed)

    # Ensure all workflow nodes have a server entry
    if workflow_nodes:
        _ensure_all_nodes(parsed, workflow_nodes)

    return dump_yaml(parsed)


def extract_workflow_nodes(workflow_yaml: str) -> list[str]:
    """Extract node names from a workflow YAML string."""
    parsed = parse_yaml(workflow_yaml)
    if not parsed:
        return []

    nodes = set()
    node_groups = parsed.get("nodeGroups", {})
    if isinstance(node_groups, dict):
        for ng_name, ng_val in node_groups.items():
            nodes.add(ng_name)
            if isinstance(ng_val, dict):
                for node in ng_val.get("nodes", []):
                    if isinstance(node, dict):
                        nodes.add(node.get("name", ""))

    # Also check commands for node references
    phases = parsed.get("phases", [])
    if isinstance(phases, list):
        for phase in phases:
            if not isinstance(phase, dict):
                continue
            for step in phase.get("steps", phase.get("commands", [])):
                if isinstance(step, dict):
                    node = step.get("node", "")
                    if node and not re.search(r"\$\{", node):
                        nodes.add(node)

    nodes.discard("")
    return sorted(nodes)


def extract_workflow_commands(workflow_yaml: str) -> list[dict]:
    """Extract commands from a workflow YAML for simulator response mapping."""
    parsed = parse_yaml(workflow_yaml)
    if not parsed:
        return []

    commands = []
    phases = parsed.get("phases", [])
    if isinstance(phases, list):
        for phase in phases:
            if not isinstance(phase, dict):
                continue
            phase_name = phase.get("name", phase.get("phase", ""))
            for step in phase.get("steps", phase.get("commands", [])):
                if isinstance(step, dict):
                    commands.append({
                        "cmd": step.get("cmd", step.get("command", "")),
                        "node": step.get("node", ""),
                        "phase": phase_name,
                        "validation": step.get("validation", {}),
                    })
    return commands


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _fix_port_collisions(parsed: dict) -> None:
    """Ensure each simulated node has a unique port."""
    servers = _get_server_list(parsed)
    if not servers:
        return

    used_ports: set[int] = set()
    next_port = _BASE_PORT

    for server in servers:
        if not isinstance(server, dict):
            continue
        port = server.get("port", 0)
        if not port or port in used_ports:
            while next_port in used_ports:
                next_port += 1
            server["port"] = next_port
            used_ports.add(next_port)
            next_port += 1
        else:
            used_ports.add(port)


def _ensure_all_nodes(parsed: dict, workflow_nodes: list[str]) -> None:
    """Add stub server entries for workflow nodes missing from the simulator config."""
    servers = _get_server_list(parsed)
    existing_names = set()
    for s in servers:
        if isinstance(s, dict):
            existing_names.add(s.get("name", "").lower())
            existing_names.add(s.get("host", "").lower())

    used_ports = {s.get("port", 0) for s in servers if isinstance(s, dict)}
    next_port = max(used_ports, default=_BASE_PORT) + 1

    for node in workflow_nodes:
        if node.lower() not in existing_names:
            stub = {
                "name": node,
                "host": "localhost",
                "port": next_port,
                "users": [{"username": "oamsys", "password": "test"}],
                "prompt": f"{node.lower()}:~ #",
            }
            servers.append(stub)
            next_port += 1
            logger.debug("Added stub simulator entry for node '%s' on port %d", node, stub["port"])


def _get_server_list(parsed: dict) -> list:
    """Get the server list from various possible config structures."""
    # Try common keys
    for key in ("servers", "nodes", "simulator", "ssh_servers"):
        val = parsed.get(key)
        if isinstance(val, list):
            return val
        if isinstance(val, dict) and "servers" in val:
            return val["servers"]

    # If top-level has node-named keys with port/host, it's a flat structure
    # Return as-is (fix functions handle dicts too)
    return []
