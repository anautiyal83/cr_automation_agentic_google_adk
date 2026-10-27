"""SSH session log parser — deterministic extraction of prompts and credentials."""
from __future__ import annotations

import re
from typing import Any


def parse_ssh_logs(ssh_logs: str) -> dict[str, Any]:
    """Parse SSH session logs for shell prompts and LDAP credentials.

    Returns dict with ``prompts``, ``credentials``, and ``ldap_port``.
    """
    prompts: list[str] = []
    credentials: list[dict[str, str]] = []
    ldap_port = ""

    if not ssh_logs:
        return {"prompts": [], "credentials": [], "ldap_port": ""}

    # Prompts: [user@host ~]$ or hostname$
    for m in re.finditer(
        r'^(\[?\w+@[\w.-]+\s*[~\w/]*\]?\s*[$#>]\s*)$',
        ssh_logs, re.MULTILINE,
    ):
        prompt = m.group(1).strip()
        if len(prompt) > 3 and prompt not in prompts:
            prompts.append(prompt)

    for m in re.finditer(
        r'^([\w.-]+[$:#~>]+\s*)$',
        ssh_logs, re.MULTILINE,
    ):
        prompt = m.group(1).strip()
        if len(prompt) > 3 and prompt not in prompts:
            prompts.append(prompt)

    # LDAP credentials: -D 'cn=user' -w password
    for m in re.finditer(r"-D\s+'([^']+)'\s+-w\s+(\S+)", ssh_logs):
        cred = {"bind_dn": m.group(1), "password": m.group(2)}
        if cred not in credentials:
            credentials.append(cred)

    # LDAP port
    port_match = re.search(r'-p\s+(\d+)', ssh_logs)
    if port_match:
        ldap_port = port_match.group(1)

    return {
        "prompts": prompts,
        "credentials": credentials,
        "ldap_port": ldap_port,
    }
