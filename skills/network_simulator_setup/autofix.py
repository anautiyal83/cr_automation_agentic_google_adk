"""Auto-fix utilities for MockWire simulator configuration YAML.

Fixes common LLM mistakes:
- Repair YAML syntax (truncation, list root, old hosts format)
- Add the runtime entries the cli-automation JAR depends on
"""
from __future__ import annotations

import logging
import re
from io import StringIO

logger = logging.getLogger(__name__)

# cli-automation sends "stty -echo" once per session, then "<cmd>; echo __CMD_DONE__:$?"
# for every command. MockWire answers the __CMD_DONE__ marker natively (do not map
# it: an explicit entry would mask real exit codes); "stty -echo" needs a silent entry.
RUNTIME_COMMANDS: dict[str, dict] = {
    "stty -echo": {"lines": []},
}


def clean_yaml_response(text: str) -> str:
    """Strip markdown code fences, multi-doc separators, and leading/trailing noise."""
    text = re.sub(r"^```(?:ya?ml)?\s*\n?", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n?```\s*$", "", text, flags=re.MULTILINE)

    # Strip multi-document separators (LLM sometimes generates separate configs per node)
    # Keep only the first document — MockWire uses one config file per server
    if "\n---\n" in text:
        text = text.split("\n---\n")[0]
        logger.info("Stripped multi-document YAML — keeping first document only")

    lines = text.split("\n")
    yaml_start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and (stripped.startswith(("#", "servers", "nodes", "simulator"))
                         or ":" in stripped):
            yaml_start = i
            break

    return "\n".join(lines[yaml_start:]).rstrip()


def parse_yaml(text: str) -> dict | list | None:
    """Parse YAML text, returning dict/list or None on failure.

    Allows duplicate keys (common in MockWire configs where command strings repeat).
    """
    try:
        from ruamel.yaml import YAML
        yaml = YAML()
        yaml.allow_duplicate_keys = True
        parsed = yaml.load(StringIO(text))
        if isinstance(parsed, dict):
            return dict(parsed)
        if isinstance(parsed, list):
            return list(parsed)
        return None
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


def _repair_truncated_yaml(yaml_text: str) -> str:
    """Attempt to repair YAML truncated by LLM token limits.

    Fixes:
    - Unclosed quotes (single or double)
    - Incomplete list items
    - Missing trailing newline
    """
    lines = yaml_text.split("\n")

    # Remove trailing incomplete lines (no colon, not a valid list item)
    while lines:
        last = lines[-1].rstrip()
        if not last:
            lines.pop()
            continue
        # Check for unclosed quotes on the last line
        for q in ("'", '"'):
            count = last.count(q)
            if count % 2 != 0:
                # Try closing the quote
                lines[-1] = last + q
                logger.info("Repaired unclosed %s quote on last line", q)
                return "\n".join(lines)
        # If last line looks incomplete (no value after colon, or bare text)
        stripped = last.strip()
        if stripped and not stripped.endswith(":") and ":" not in stripped and not stripped.startswith("-"):
            # Likely a truncated value — remove the line
            logger.info("Removed truncated line: %s", stripped[:60])
            lines.pop()
            continue
        break

    return "\n".join(lines)


def fix_simulator_config(yaml_text: str) -> str:
    """Apply auto-fix rules to simulator config YAML.

    Handles:
    - Truncated YAML repair (unclosed quotes, incomplete lines)
    - List-at-root → extract first map element
    - hosts/servers nested format → flatten to MockWire format
    - Runtime entries required by the cli-automation JAR (see ensure_runtime_entries)
    """
    yaml_text = clean_yaml_response(yaml_text)
    parsed = parse_yaml(yaml_text)
    if parsed is None:
        # Try repairing truncated YAML
        yaml_text = _repair_truncated_yaml(yaml_text)
        parsed = parse_yaml(yaml_text)
        if parsed is None:
            logger.warning("YAML repair failed — returning raw text")
            return yaml_text

    # Fix: if root is a list, extract the first dict element
    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict):
                parsed = item
                logger.info("Extracted dict from root list")
                break
        else:
            return yaml_text

    # Fix: if root has "hosts" key with nested structure, flatten
    if "hosts" in parsed and isinstance(parsed["hosts"], list):
        parsed = _flatten_hosts_format(parsed)

    # Ensure "config" section exists
    if "config" not in parsed:
        parsed["config"] = {"prompt": "#", "delayMs": 100, "jitterMs": 20, "idleTimeoutSec": 300}

    ensure_runtime_entries(parsed)
    return dump_yaml(parsed)


def ensure_runtime_entries(parsed: dict) -> None:
    """Add what the cli-automation JAR needs from every MockWire instance.

    - ``config.terminalMode: PLAIN`` — echoed input breaks the JAR's output capture
    - ``stty -echo`` and the ``__CMD_DONE__`` sentinel echo
    - ``exit`` → ``popPrompt`` when the config escalates with ``su``, so leaving
      the escalated shell returns to the login prompt instead of closing the session
    """
    config = parsed.setdefault("config", {})
    if isinstance(config, dict):
        config["terminalMode"] = "PLAIN"

    for key, value in RUNTIME_COMMANDS.items():
        parsed.setdefault(key, dict(value))

    has_su = any(str(k).startswith(("su ", "regex:su")) for k in parsed)
    if has_su and "exit" not in parsed:
        parsed["exit"] = {"lines": [], "popPrompt": True}


def _flatten_hosts_format(parsed: dict) -> dict:
    """Convert old hosts-based format to MockWire flat format.

    Input:  {hosts: [{hostname, port, users: {...}, commands: {...}}]}
    Output: {config: {...}, auth: {...}, "cmd1": {lines: [...]}, ...}
    """
    result = {}
    hosts = parsed.get("hosts", [])
    if not hosts or not isinstance(hosts, list):
        return parsed

    host = hosts[0] if isinstance(hosts[0], dict) else {}

    # Config
    prompt = "#"
    users_data = host.get("users", {})
    if isinstance(users_data, dict):
        first_user = next(iter(users_data.values()), {})
        if isinstance(first_user, dict):
            prompt = first_user.get("prompt", "#")

    result["config"] = {
        "prompt": prompt,
        "delayMs": 100,
        "jitterMs": 20,
        "idleTimeoutSec": 300,
    }

    # Auth
    auth_users = []
    if isinstance(users_data, dict):
        for username, uconfig in users_data.items():
            if isinstance(uconfig, dict):
                auth_users.append({
                    "username": username,
                    "password": uconfig.get("password", "*"),
                    "prompt": uconfig.get("prompt", prompt),
                })
    result["auth"] = {"enabled": True, "users": auth_users}

    # Commands
    commands = host.get("commands", {})
    if isinstance(commands, dict):
        for cmd_key, cmd_val in commands.items():
            if isinstance(cmd_val, dict):
                output = cmd_val.get("output", "")
                if isinstance(output, str):
                    result[cmd_key] = {"lines": output.strip().split("\n")}
                elif isinstance(cmd_val.get("lines"), list):
                    result[cmd_key] = {"lines": cmd_val["lines"]}

    logger.info("Flattened hosts format → MockWire format (%d commands)", len(commands))
    return result


def extract_workflow_nodes(workflow_yaml: str) -> list[str]:
    """Extract the remote node names a workflow connects to.

    Local nodes (``type: local`` / ``plugin: local``) run commands natively and
    are not simulated.
    """
    parsed = parse_yaml(workflow_yaml)
    if not parsed:
        return []

    nodes = set()
    local_nodes = set()
    # Support both "nodes" (new) and "nodeGroups" (old)
    node_groups = parsed.get("nodes", parsed.get("nodeGroups", {}))
    if isinstance(node_groups, dict):
        for ng_name, ng_val in node_groups.items():
            if isinstance(ng_val, dict) and "local" in (ng_val.get("type"), ng_val.get("plugin")):
                local_nodes.add(ng_name)
                continue
            nodes.add(ng_name)
            if isinstance(ng_val, dict):
                for node in ng_val.get("nodes", []):
                    if isinstance(node, dict):
                        nodes.add(node.get("name", ""))

    # Also check commands for node references
    for _phase_name, steps in _iter_phase_steps(parsed):
        for step in steps:
            node = step.get("node", "")
            if node and not re.search(r"\$\{", node) and node not in local_nodes:
                nodes.add(node)

    nodes.discard("")
    return sorted(nodes)


def extract_workflow_commands(workflow_yaml: str) -> list[dict]:
    """Extract commands from a workflow YAML for simulator response mapping."""
    parsed = parse_yaml(workflow_yaml)
    if not parsed:
        return []

    commands = []
    for phase_name, steps in _iter_phase_steps(parsed):
        for step in steps:
            commands.append({
                "cmd": step.get("send", step.get("cmd", step.get("command", ""))),
                "node": step.get("node", ""),
                "phase": phase_name,
                "validation": step.get("validation", {}),
            })
    return commands


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _iter_phase_steps(parsed: dict):
    """Yield (phase_name, [step dicts]) for phases given as a map or a list."""
    phases = parsed.get("phases", [])
    if isinstance(phases, dict):
        items = list(phases.items())
    elif isinstance(phases, list):
        items = [(p.get("name", p.get("phase", "")), p) for p in phases if isinstance(p, dict)]
    else:
        items = []
    for key, phase in items:
        if not isinstance(phase, dict):
            continue
        steps = phase.get("steps", phase.get("commands", []))
        yield key, [s for s in steps or [] if isinstance(s, dict)]
