"""MOP document parser — deterministic extraction of commands and phases."""
from __future__ import annotations

import re
from typing import Any

# Prefixes that indicate conditional branches or descriptive instructions,
# not real executable commands.
_NON_COMMAND_PREFIXES = (
    "ELSE",
    "IF (",
    "IF(",
    "Success,",
    "ELSE,",
    "Make the new",
    "Compare",
    "Wait for",
    "Add Header",
    "Process response",
    "Ensure that",
    "Check the presence",
)


def _is_real_command(cmd_text: str) -> bool:
    """Return False for conditional-branch keywords and descriptive instructions.

    Filters out:
    1. Conditional branch keywords (ELSE, IF (...), Success, ...)
    2. Descriptive / instructional sentences
    3. Single-word non-commands (e.g. bare "ELSE"), but allows "exit"
    """
    text = cmd_text.strip()
    if not text:
        return False

    # Check against known non-command prefixes (case-insensitive)
    text_upper = text.upper()
    for prefix in _NON_COMMAND_PREFIXES:
        if text_upper.startswith(prefix.upper()):
            return False

    # Single-word entries that are not real CLI commands
    # Allow known short commands like "exit"
    if " " not in text and text.lower() not in ("exit",):
        # If it's a single word that looks like a keyword rather than a command
        if text.upper() in ("ELSE", "IF", "SUCCESS", "ERROR"):
            return False

    return True


def parse_mop(mop_text: str) -> dict[str, Any]:
    """Parse MOP text into structured commands and phases.

    Looks for:
    - Phase headers: ``#1. Pre Health Check``
    - Node context: ``NIAM login to RDS``
    - User changes: ``su - sdfrun``
    - Command lines: ``->command  #validation_comment``

    Returns dict with ``commands`` list and ``phases`` list.
    """
    commands: list[dict[str, str]] = []
    phases_seen: set[str] = set()

    if not mop_text:
        return {"commands": [], "phases": []}

    current_phase = "unknown"
    current_node = "unknown"
    current_user = "oamsys"

    for line in mop_text.splitlines():
        stripped = line.strip()

        # Detect phase headers: #1. Pre Health Check, #3. Activity, etc.
        phase_match = re.match(r'^#\d+\.\s*(.+)', stripped)
        if phase_match:
            current_phase = phase_match.group(1).strip()
            phases_seen.add(current_phase)
            continue

        # Detect node context: NIAM login to RDS/PGW
        # When switching to a new node via NIAM login, reset user to default
        node_match = re.search(r'(?:NIAM\s+)?login\s+to\s+(\w+)', stripped, re.IGNORECASE)
        if node_match:
            current_node = node_match.group(1).upper()
            if re.search(r'NIAM\s+login', stripped, re.IGNORECASE):
                current_user = "oamsys"

        # Detect user changes: su - sdfrun, su - provgw
        su_match = re.search(r'su\s+-\s+(\w+)', stripped)
        if su_match:
            current_user = su_match.group(1)

        # Extract command lines: ->command  #validation_comment
        cmd_match = re.match(r'^->\s*(.+)', stripped)
        if cmd_match:
            cmd_line = cmd_match.group(1).strip()
            comment = ""
            if "#" in cmd_line:
                parts = cmd_line.split("#", 1)
                cmd_line = parts[0].strip()
                comment = parts[1].strip()

            if not cmd_line or cmd_line.lower().startswith("read the ciq"):
                continue

            if not _is_real_command(cmd_line):
                continue

            commands.append({
                "command": cmd_line,
                "description": comment or cmd_line,
                "validation": comment,
                "node": current_node,
                "phase": current_phase,
                "user": current_user,
            })

    return {
        "commands": commands,
        "phases": sorted(phases_seen),
    }
