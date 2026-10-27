"""MOP document parser — deterministic extraction of commands and phases."""
from __future__ import annotations

import re
from typing import Any


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
        node_match = re.search(r'(?:NIAM\s+)?login\s+to\s+(\w+)', stripped, re.IGNORECASE)
        if node_match:
            current_node = node_match.group(1).upper()

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
