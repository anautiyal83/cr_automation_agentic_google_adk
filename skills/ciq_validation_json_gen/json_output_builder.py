"""json-output template built from the CIQ's layout (sheet roles, column names and values).

The previous builder assumed the PGW_RDS CIQ (an ``Index`` sheet with ZONE, an ``IP`` sheet with
Node_Name + NIAM_NAME1..3): for MRF_ANNOUNCEMENT_LOADING it split CRs by NODE, iterated the
non-existent INDEX.ZONE, filtered records by a non-existent Zone column and emitted no NIAM IDs
or e-mail (G-14). Two layouts are recognised:

* group targets (PGW_RDS): the node sheet has one row per (group, node type) — every group is one
  execution target whose node entry carries each node type's NIAM IDs;
* node targets (MRF): the node sheet has one row per named node and the index lists each group's
  nodes — every node is an execution target with its own NIAM IDs.

ciq-processor 1.0.3 accepts both (JAR-verified 2026-11-03).
"""
from __future__ import annotations

import logging
import re

from .rules_baseline import _columns, _tokens, _values, sheet_roles

logger = logging.getLogger(__name__)


def split_uc_name(uc_name: str) -> tuple[str, str]:
    """``PGW_RDS_1058_IFC_X`` → (PGW_RDS, 1058_IFC_X); ``MRF_ANNOUNCEMENT_LOADING`` → (MRF, ANNOUNCEMENT_LOADING)."""
    m = re.match(r"^([A-Za-z_]+?)_(\d+_.+)$", uc_name)
    if m:
        return m.group(1), m.group(2)
    m = re.match(r"^([A-Za-z]+)_(.+)$", uc_name)
    return (m.group(1), m.group(2)) if m else (uc_name, uc_name)


def node_field_key(col: str) -> str:
    """JSON key of a node-sheet column on the node entry: ``NODE TYPE`` → ``NODE_TYPE``."""
    return re.sub(r"\s+", "_", col.strip())


def node_fields(template: dict) -> dict[str, list[str]]:
    """{node type key: [field keys other than niamID-n]} of a built template (step 6 uses them as
    ``${<TYPE>.<field>}``)."""
    try:
        nodes = template["data"]["crGroups"]["nodeGroups"]["nodes"]
    except (KeyError, TypeError):
        return {}
    entries = [nodes] if isinstance(nodes, dict) else list(nodes or [])
    out: dict[str, list[str]] = {}
    for e in entries:
        for k, v in e.items():
            if isinstance(v, dict) and not k.startswith("_"):
                out.setdefault(k, []).extend(f for f in v if not f.startswith("niamID-") and f not in out.get(k, []))
    return {k: v for k, v in out.items() if v}


def _q(col: str) -> str:
    """Column reference in a lookup expression: quoted when it has a space ('NIAM NAME')."""
    return f"'{col}'" if " " in col else col


def _find(cols: list[str], pred) -> str:
    return next((c for c in cols if pred(c)), "")


def _same_name(a: str, b: str) -> bool:
    return _tokens(a) == _tokens(b)


def _natural(c: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", c.lower())]


def build_json_output(ciq_sheets: dict, rows: dict, uc_name: str, hints: dict | None = None) -> dict:
    hints = hints or {}
    roles = sheet_roles(ciq_sheets, rows)
    index = next((s for s, r in roles.items() if r == "index"), "")
    node = next((s for s, r in roles.items() if r == "node"), "")
    data_sheets = [s for s, r in roles.items() if r == "data"]
    if not index:
        raise ValueError("CIQ has no INDEX sheet")
    icols = _columns(ciq_sheets, rows, index)
    ncols = _columns(ciq_sheets, rows, node) if node else []

    cr_col = _find(icols, lambda c: re.search(r"cr[\s_\-]*group", c, re.I)) or \
        _find(icols, lambda c: "cr" in _tokens(c)) or icols[0]
    email_col = _find(icols, lambda c: "mail" in c.lower())
    niam_cols = sorted((c for c in ncols if "niam" in c.lower()), key=_natural)

    # Node targets: an index column whose values are all names in the node sheet's unique node column
    node_name_col, index_node_col = "", ""
    for c in ncols:
        vals = [v for v in _values(rows, node, c) if v]
        if {"node", "name"} <= _tokens(c) and vals and len(set(vals)) == len(vals):
            node_name_col = c
            break
    if node_name_col:
        names = {v for v in _values(rows, node, node_name_col) if v}
        index_node_col = _find(icols, lambda c: c not in (cr_col, email_col) and "node" in _tokens(c)
                               and {v for v in _values(rows, index, c) if v} <= names
                               and any(_values(rows, index, c)))
    node_targets = bool(index_node_col)

    # Group column: an index column a data sheet shares by name (the records' link)
    hinted = str((hints.get("grouping") or {}).get("column") or "")
    candidates = [c for c in icols if c not in (cr_col, email_col, index_node_col)]
    group_col, data_sheet, data_link = "", "", ""
    preferred = [s for s in data_sheets if s == hints.get("data_sheet")] + data_sheets
    for c in sorted(candidates, key=lambda c: not _same_name(c, hinted)):
        for s in preferred:
            link = _find(_columns(ciq_sheets, rows, s), lambda d: _same_name(d, c))
            if link:
                group_col, data_sheet, data_link = c, s, link
                break
        if group_col:
            break
    if not group_col:
        group_col = _find(candidates, lambda c: _same_name(c, hinted)) or (candidates[0] if candidates else cr_col)
    if not data_sheet and data_sheets:
        data_sheet = preferred[0]

    node_type, activity = split_uc_name(uc_name)
    var = "$group" if node_targets else "$zone"

    if node_targets:
        type_col = _find(ncols, lambda c: {"node", "type"} <= _tokens(c))
        types = sorted({v for v in _values(rows, node, type_col) if v}) if type_col else []
        key = types[0] if len(types) == 1 else node_type
        if len(types) == 1:
            node_type = key
        entry = {f"niamID-{i}": f"{node}.{_q(nc)} WHERE {node}.{_q(node_name_col)} = {index_node_col}"
                 for i, nc in enumerate(niam_cols, 1)}
        # The node's other CIQ values (VER, Circle, …) as ${<TYPE>.<field>} — the records hold only
        # the data sheet, so an MRF version check had nothing to compare (G-15, JAR-verified)
        for c in ncols:
            if c not in niam_cols:
                entry[node_field_key(c)] = f"{node}.{_q(c)} WHERE {node}.{_q(node_name_col)} = {index_node_col}"
        nodes = {
            "_each": f"{index} WHERE {group_col} = {var}",
            "node": index_node_col,
            key: entry,
        }
    else:
        # One target per group; each node type of the node sheet (its type column) is a sub-key
        node_group_col = _find(ncols, lambda c: _same_name(c, group_col))
        type_col = _find(ncols, lambda c: c not in niam_cols and c != node_group_col
                         and 0 < len({v for v in _values(rows, node, c) if v}) <= 6)
        types = []
        for v in _values(rows, node, type_col) if type_col else []:
            if v and v not in types:
                types.append(v)
        entry = {"node": var}
        for t in types:
            entry[t] = {f"niamID-{i}": f"{node}.{_q(nc)} WHERE {node}.{_q(type_col)} = {t}"
                        + (f" AND {node}.{_q(node_group_col)} = {var}" if node_group_col else "")
                        for i, nc in enumerate(niam_cols, 1)}
        nodes = [entry]

    node_groups = {
        "_each": f"DISTINCT {index}.{group_col} WHERE {index}.{cr_col} = $cr AS {var}",
        "nodeGroup": var,
        "crGroup": "$cr",
    }
    if email_col:
        node_groups["email"] = f"{index}.{_q(email_col)} WHERE {index}.{group_col} = {var}"
    node_groups["nodes"] = nodes
    node_groups["configSequences"] = {
        "_each": f"DISTINCT {index}.{group_col} WHERE {index}.{group_col} = {var} AS $seq",
        "configSeq": "Step1",
        "records": {
            "_each": f"{data_sheet} WHERE {data_link or group_col} = {var}" if data_sheet else index,
            "data": {c: c for c in (_columns(ciq_sheets, rows, data_sheet) if data_sheet else [])},
        },
    }
    logger.info("json-output: %s targets, index %s (CR %s, group %s), node sheet %s, data %s, %d NIAM column(s)",
                "node" if node_targets else "group", index, cr_col, group_col, node or "-", data_sheet or "-",
                len(niam_cols))
    # One JSON for the whole CIQ: crGroups[] at the top, each CR holding its nodeGroups[]
    # (user decision 2026-11-03; no segregate_by — cli-automation generate/execute accept it)
    cr_groups = {"_each": f"DISTINCT {index}.{cr_col} AS $cr", "crGroup": "$cr"}
    if email_col:
        cr_groups["email"] = f"{index}.{_q(email_col)} WHERE {index}.{cr_col} = $cr"
    cr_groups["nodeGroups"] = node_groups
    return {
        "output_mode": "single",
        "data": {"schemaVersion": "2", "meta": {"nodeType": node_type, "activity": activity},
                 "crGroups": cr_groups},
    }
