"""Reading the CIQ JSON data written by ciq-processor (step 4).

Two shapes: one file for the whole CIQ with ``crGroups[].nodeGroups[]`` (current, G-14b), and the
earlier one file per CR with ``nodeGroups[]`` at the root (each group carries ``crGroup``).
"""
from __future__ import annotations


def node_groups(doc: dict) -> list[tuple[str, dict]]:
    """[(cr_group, node_group)] of a CIQ JSON document, whichever shape it has."""
    data = doc.get("data", doc) if isinstance(doc, dict) else {}
    out: list[tuple[str, dict]] = []
    for cr in data.get("crGroups") or []:
        if not isinstance(cr, dict):
            continue
        for group in cr.get("nodeGroups") or []:
            if isinstance(group, dict):
                out.append((str(cr.get("crGroup") or group.get("crGroup") or ""), group))
    for group in data.get("nodeGroups") or []:
        if isinstance(group, dict):
            out.append((str(group.get("crGroup") or ""), group))
    return out
