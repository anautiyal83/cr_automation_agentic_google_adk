"""Validation rules derived from the CIQ itself (all rows of the uploaded workbook).

The LLM wrote every column as ``type: string, required: true`` and its cross-sheet rules were
dropped as unparseable, so the rules checked almost nothing (e-mail columns as strings, no link
between INDEX.GROUP and the data sheets, none between INDEX.NODE and the node sheet). This module
builds the rules the data itself proves, and merges what the LLM adds only where the CIQ's own
values pass it — the generated rules must accept the CIQ they were made from (ciq-processor
1.0.3 accepts every key used here; JAR probe 2026-11-03).
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

SETTINGS = {"headerRow": 0, "dataStartRow": 1, "trimCellValues": True, "ignoreBlankRows": True}

_IPV4 = re.compile(r"(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}")
_VERSION = re.compile(r"\d+(\.\d+)+")
_EMAIL = re.compile(r"[^@;,\s]+@[^@;,\s]+\.[^@;,\s]+")



# ---------------------------------------------------------------------------
# CIQ rows
# ---------------------------------------------------------------------------

def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return str(v).strip()


def read_ciq_rows(ciq_file_path: str, ciq_sheets: dict) -> dict[str, list[dict[str, str]]]:
    """Every non-blank data row of the CIQ sheets step 1 parsed ({sheet: [{column: value}]}).
    Falls back to step 1's sample rows when the workbook cannot be read."""
    rows: dict[str, list[dict[str, str]]] = {}
    if ciq_file_path and Path(ciq_file_path).is_file():
        try:
            import openpyxl
            wb = openpyxl.load_workbook(ciq_file_path, read_only=True, data_only=True)
            for name in ciq_sheets:
                if name not in wb.sheetnames:
                    continue
                it = wb[name].iter_rows(values_only=True)
                header = [_cell(h) for h in next(it, ())]
                sheet_rows = []
                for r in it:
                    rec = {h: _cell(v) for h, v in zip(header, r) if h}
                    if any(rec.values()):
                        sheet_rows.append(rec)
                rows[name] = sheet_rows
            wb.close()
        except Exception as e:  # unreadable workbook: use the samples
            logger.warning("CIQ rows not read from %s: %s", ciq_file_path, e)
    for name, sd in ciq_sheets.items():
        if name not in rows and isinstance(sd, dict):
            rows[name] = [{k: _cell(v) for k, v in r.items()} for r in sd.get("sample_rows") or []
                          if isinstance(r, dict)]
    return rows


def _columns(ciq_sheets: dict, rows: dict, sheet: str) -> list[str]:
    sd = ciq_sheets.get(sheet)
    cols = list(sd.get("columns") or []) if isinstance(sd, dict) else []
    for r in rows.get(sheet, []):
        cols += [c for c in r if c not in cols]
    return cols


def _values(rows: dict, sheet: str, col: str) -> list[str]:
    return [r.get(col, "") for r in rows.get(sheet, [])]


# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------

def _tokens(name: str) -> set[str]:
    return {t for t in re.split(r"[\s_\-]+", name.lower()) if t}


def sheet_roles(ciq_sheets: dict, rows: dict) -> dict[str, str]:
    """index (INDEX-like name) | node (holds a NIAM column) | data."""
    roles = {}
    for s in ciq_sheets:
        cols = _columns(ciq_sheets, rows, s)
        if "index" in s.lower():
            roles[s] = "index"
        elif any("niam" in c.lower() for c in cols):
            roles[s] = "node"
        else:
            roles[s] = "data"
    return roles


def _column_rule(name: str, values: list[str]) -> dict:
    filled = [v for v in values if v]
    rule: dict = {"type": "string", "required": bool(values) and len(filled) == len(values)}
    if not filled:
        return rule
    upper = name.upper()
    if "MAIL" in upper or all("@" in v for v in filled):
        parts = [p.strip() for v in filled for p in re.split(r"[;,]", v) if p.strip()]
        if parts and all(_EMAIL.fullmatch(p) for p in parts):
            rule["type"] = "email"
            if any(re.search(r"[;,]", v) for v in filled):
                rule["multi"] = True
            return rule
    tokens = _tokens(name)
    # A version (13.0.3.7) is also a valid IPv4 string: the column name decides
    if all(_VERSION.fullmatch(v) for v in filled) and (upper in ("VER", "VERSION") or "VERSION" in upper):
        rule.update(pattern=r"[0-9]+(\.[0-9]+)+", patternMessage="Dotted version such as 13.0.3.7")
    elif all(_IPV4.fullmatch(v) for v in filled) and tokens & {"ip", "addr", "address", "ipaddress"}:
        rule["type"] = "ip"
    elif all(v.startswith("/") for v in filled) and ("PATH" in upper or "DIR" in upper):
        rule.update(pattern=r"/\S*", patternMessage="Absolute path starting with /")
    elif all(v.isdigit() for v in filled) and len(filled) >= 2:
        rule.update(pattern="[0-9]+", patternMessage="Digits only")
    elif {"node", "type"} <= tokens or upper.replace(" ", "_") == "NODE_TYPE":
        # The UC's node type(s); other *TYPE columns take their values from the LLM (one CIQ
        # value, e.g. ipv4, is no proof that ipv6 is invalid)
        distinct = sorted(set(filled))
        if len(distinct) <= 6:
            rule.update(type="enum", values=distinct)
    return rule


def _cross_refs(ciq_sheets: dict, rows: dict, roles: dict) -> dict[tuple[str, str], dict]:
    """(sheet, column) → crossRef target: a data-sheet column whose values all occur in the
    same-named INDEX column, or an INDEX column whose values all occur in the node sheet's
    column containing its name (INDEX.NODE → Node_Details.NODE_NAME)."""
    targets_for = {"data": ("index", "node"), "index": ("node",), "node": ()}
    refs = {}
    for s, role in roles.items():
        for col in _columns(ciq_sheets, rows, s):
            vals = {v for v in _values(rows, s, col) if v}
            if not vals:
                continue
            found = None
            for target_role in targets_for[role]:
                for t, trole in roles.items():
                    if trole != target_role or t == s:
                        continue
                    for tcol in _columns(ciq_sheets, rows, t):
                        a, b = _tokens(col), _tokens(tcol)
                        if not (a == b or a < b or b < a):
                            continue
                        tvals = {v for v in _values(rows, t, tcol) if v}
                        if tvals and vals <= tvals:
                            found = {"sheet": t, "column": tcol}
                            break
                    if found:
                        break
                if found:
                    break
            if found:
                refs[(s, col)] = found
    return refs


def baseline_rules(ciq_sheets: dict, rows: dict) -> dict:
    roles = sheet_roles(ciq_sheets, rows)
    refs = _cross_refs(ciq_sheets, rows, roles)
    sheets = {}
    for s in ciq_sheets:
        cols = {}
        for col in _columns(ciq_sheets, rows, s):
            vals = _values(rows, s, col)
            rule = _column_rule(col, vals)
            if (s, col) in refs:
                rule["crossRef"] = dict(refs[(s, col)])
            if roles[s] == "node" and "node" in _tokens(col) and "name" in _tokens(col):
                filled = [v for v in vals if v]
                if len(filled) >= 2 and len(set(filled)) == len(filled):
                    rule["unique"] = True
            cols[col] = rule
        sheets[s] = {"required": True, "columns": cols}
    return {"settings": dict(SETTINGS), "sheets": sheets}


# ---------------------------------------------------------------------------
# Merge with the LLM's rules
# ---------------------------------------------------------------------------

def _java_regex_ok(pattern: str, values: list[str]) -> bool:
    try:
        rx = re.compile(re.sub(r"\(\?<([A-Za-z][A-Za-z0-9]*)>", r"(?P<\1>", pattern))
    except re.error:
        return False
    return all(rx.fullmatch(v) for v in values)


def merge_llm_rules(baseline: dict, llm: dict | None, rows: dict) -> dict:
    """Baseline + the LLM's column properties the JAR accepts and the CIQ's values satisfy.
    Data-proven facts win (an empty cell makes a column optional, an e-mail column stays email);
    an LLM ``required: false`` is kept (it may know the column is optional)."""
    llm_sheets = (llm or {}).get("sheets") or {}
    for s, sheet in baseline["sheets"].items():
        llm_cols = ((llm_sheets.get(s) or {}).get("columns") or {}) if isinstance(llm_sheets.get(s), dict) else {}
        for col, rule in sheet["columns"].items():
            extra = llm_cols.get(col)
            if not isinstance(extra, dict):
                continue
            filled = [v for v in (r.get(col, "") for r in rows.get(s, [])) if v]
            for k, v in extra.items():
                if k == "required":
                    if v is False:
                        rule["required"] = False
                elif k == "type":
                    vals = extra.get("values") or extra.get("allowedValues")
                    if v == "enum" and rule["type"] == "string" and isinstance(vals, list) \
                            and {str(x) for x in vals} >= set(filled):
                        rule.update(type="enum", values=[str(x) for x in vals])
                elif k in ("values", "allowedValues"):
                    if rule["type"] == "enum" or not isinstance(v, list) or "values" in rule:
                        continue
                    if k == "allowedValues" and {str(x).lower() for x in v} >= {x.lower() for x in filled}:
                        rule["allowedValues"] = [str(x) for x in v]
                elif k == "pattern":
                    if "pattern" not in rule and isinstance(v, str) and _java_regex_ok(v, filled):
                        rule["pattern"] = v
                        if isinstance(extra.get("patternMessage"), str):
                            rule["patternMessage"] = extra["patternMessage"]
                elif k == "minLength":
                    if isinstance(v, int) and all(len(x) >= v for x in filled):
                        rule[k] = v
                elif k == "maxLength":
                    if isinstance(v, int) and all(len(x) <= v for x in filled):
                        rule[k] = v
                elif k == "unique":
                    if v is True and len(set(filled)) == len(filled):
                        rule["unique"] = True
                elif k == "crossRef":
                    if "crossRef" in rule or not isinstance(v, dict):
                        continue
                    t, tcol = v.get("sheet"), v.get("column")
                    tvals = {r.get(tcol, "") for r in rows.get(t, [])} if t in rows else set()
                    if t != s and tvals and set(filled) <= tvals:
                        rule["crossRef"] = {"sheet": t, "column": tcol}
                elif k == "description":
                    if isinstance(v, str):
                        rule["description"] = v
                elif k in ("ignoreCase", "dropdownDisabled") and isinstance(v, bool):
                    rule[k] = v  # presentation/matching flags; numeric limits are not checked here
    return baseline
