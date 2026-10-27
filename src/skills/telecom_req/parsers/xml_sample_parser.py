"""XML sample file parser — deterministic extraction of SPML fields."""
from __future__ import annotations

import re
from typing import Any


def parse_xml_samples(xml_text: str) -> list[dict[str, Any]]:
    """Parse concatenated XML sample text into structured field mappings.

    Expects sections delimited by ``=== filename ===`` markers.
    Returns list of dicts with ``content``, ``imsi_example``, and ``fields``.
    """
    results: list[dict[str, Any]] = []

    if not xml_text:
        return results

    for section in re.split(r'===\s+(\S+)\s+===', xml_text):
        section = section.strip()
        if not section or not section.startswith("<"):
            continue

        entry: dict[str, Any] = {"content": section[:2000]}

        # Find IMSI identifier
        imsi_match = re.search(r'<identifier\s+alias="IMSI">([^<]+)', section)
        if imsi_match:
            entry["imsi_example"] = imsi_match.group(1)

        # Find modification fields: <tag>value</tag>
        mods = re.findall(r'<(\w+)>([^<]+)</\1>', section)
        if mods:
            entry["fields"] = {k: v for k, v in mods}

        results.append(entry)

    return results
