"""XML sample file parser — deterministic extraction of SPML fields."""
from __future__ import annotations

import json
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

    # Deduplicate samples with identical fields dicts
    seen_keys: set[str] = set()
    unique_results: list[dict[str, Any]] = []
    for entry in results:
        fields = entry.get("fields", {})
        dedup_key = json.dumps(fields, sort_keys=True)
        if dedup_key not in seen_keys:
            seen_keys.add(dedup_key)
            unique_results.append(entry)
    results = unique_results

    return results
