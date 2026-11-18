"""The CIQ's Constraints sheet (user 2026-11-18): one row per CIQ column, what its values must be.

    Sheet | Column | Required | Type | Allowed values | Rule
    NTP Config | NTP Server 1 | Yes | IPv4 | | must differ from NTP Server 2

Step 1 reads it (``read``) and keeps it out of the CIQ's data sheets — it is no data, so neither in the CIQ JSON
nor among the sheets the rules check. Step 3 applies Required / Type / Allowed values to the validation rules in
code (``apply``: the customer's word wins over what the values suggest); the free-text Rule goes to the rule
writer (``describe``). Type: Text | Number | Decimal | IPv4 | IPv6 | IP | Email | List | Date | Hostname | FQDN.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

SHEET = re.compile(r"^\s*constraints?\s*$", re.I)
_FIELDS = {"sheet": "sheet", "column": "column", "required": "required", "mandatory": "required", "type": "type",
           "allowed values": "allowed", "allowed": "allowed", "values": "allowed", "rule": "rule", "rules": "rule",
           "remarks": "rule"}
_TYPES = {"text": {"type": "string"}, "string": {"type": "string"}, "number": {"type": "integer"},
          "integer": {"type": "integer"}, "decimal": {"type": "decimal"},
          "ipv4": {"type": "ip", "accepts": "ipv4"}, "ipv6": {"type": "ip", "accepts": "ipv6"},
          "ip": {"type": "ip", "accepts": "both"}, "email": {"type": "email"}, "e-mail": {"type": "email"},
          "list": {"type": "enum"}, "enum": {"type": "enum"}, "date": {"type": "date"},
          "hostname": {"type": "hostname"}, "fqdn": {"type": "fqdn"}}
_YES = {"yes", "y", "true", "mandatory", "required", "1"}
_NO = {"no", "n", "false", "optional", "0"}


def is_sheet(name: str) -> bool:
    return bool(SHEET.match(str(name or "")))


def _key(text) -> str:
    return " ".join(str(text or "").split()).lower()


def read(ciq_file_path: str) -> list[dict]:
    """The rows of the workbook's visible Constraints sheet: ``[{sheet, column, required, type, allowed, rule}]``."""
    if not ciq_file_path or not Path(ciq_file_path).is_file():
        return []
    try:
        import openpyxl
        wb = openpyxl.load_workbook(ciq_file_path, read_only=True, data_only=True)
    except Exception as e:  # noqa: BLE001
        logger.warning("Constraints sheet not read: %s", e)
        return []
    ws = next((wb[n] for n in wb.sheetnames if is_sheet(n) and getattr(wb[n], "sheet_state", "visible") == "visible"),
              None)
    if ws is None:
        return []
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    fields = [_FIELDS.get(_key(h)) for h in rows[0]]
    out = []
    for r in rows[1:]:
        rec = {f: ("" if v is None else str(v).strip()) for f, v in zip(fields, r) if f}
        if rec.get("column"):
            out.append({k: rec.get(k, "") for k in ("sheet", "column", "required", "type", "allowed", "rule")})
    logger.info("CIQ Constraints sheet: %d column constraints", len(out))
    return out


def _allowed(text: str) -> list[str]:
    return [v.strip() for v in re.split(r"[,;\n|]", text or "") if v.strip()]


def apply(rules: dict, constraints: list[dict] | None) -> int:
    """Required / Type / Allowed values of each constraint on its column's rule; the Rule text as the column's
    description. Returns the number of columns changed."""
    sheets = (rules or {}).get("sheets") or {}
    by_name = {_key(s): s for s in sheets}
    changed = 0
    for c in constraints or []:
        sheet = by_name.get(_key(c.get("sheet")))
        cols = (sheets.get(sheet) or {}).get("columns") or {} if sheet else {}
        col = next((k for k in cols if _key(k) == _key(c.get("column"))), None)
        if col is None:  # no sheet given, or another spelling: the one column of that name
            hits = [(s, k) for s, b in sheets.items() for k in (b.get("columns") or {})
                    if _key(k) == _key(c.get("column")) and (not c.get("sheet") or _key(s) == _key(c.get("sheet")))]
            if len(hits) != 1:
                logger.warning("Constraint for %s / %s: no such CIQ column", c.get("sheet"), c.get("column"))
                continue
            sheet, col = hits[0]
            cols = sheets[sheet]["columns"]
        rule = cols[col] if isinstance(cols[col], dict) else {}
        before = dict(rule)
        req = _key(c.get("required"))
        if req in _YES:
            rule["required"] = True
        elif req in _NO:
            rule["required"] = False
        typed = _TYPES.get(_key(c.get("type")))
        values = _allowed(c.get("allowed", ""))
        if typed:
            for k in ("type", "accepts", "values", "pattern", "patternMessage", "multi", "minValue", "maxValue"):
                if k in rule and k != "type":
                    rule.pop(k)  # what the values suggested gives way to the stated type
            rule.update(typed)
        rng = re.fullmatch(r"\s*(-?\d+)\s*(?:-|\.\.|to)\s*(-?\d+)\s*", c.get("allowed", "")) \
            if rule.get("type") in ("integer", "decimal") else None
        if rng:
            rule["minValue"], rule["maxValue"] = int(rng.group(1)), int(rng.group(2))
        elif values and rule.get("type") in ("enum", "string", None):
            rule["type"], rule["values"] = "enum", values
        if c.get("rule") and not rule.get("description"):
            rule["description"] = c["rule"]
        if rule.get("type") == "enum" and not rule.get("values"):
            rule["type"] = "string"  # a list without values: nothing to choose from
        cols[col] = rule
        changed += rule != before
        changed += _differ_rule(rules, sheet, col, c.get("rule", ""))
    return changed


# "must differ from NTP Server 2" / "not the same as …" / "!= …"
_DIFFER = re.compile(r"(?:differ(?:ent)?\s+from|not\s+(?:be\s+)?(?:equal\s+to|the\s+same\s+as)|!=|≠)\s+(?P<other>.+?)"
                     r"\s*\.?$", re.I)


def _differ_rule(rules: dict, sheet: str, col: str, text: str) -> int:
    """A Rule "must differ from <column>" as the JAR's cross_check (relation not_equals, probed 2026-11-18: two
    equal NTP servers fail "… must differ (NG1)"), rows joined on the sheet's first column (its key)."""
    m = _DIFFER.search(text or "")
    cols = (rules["sheets"].get(sheet) or {}).get("columns") or {}
    other = next((k for k in cols if m and _key(k) == _key(m.group("other"))), None)
    key = next(iter(cols), None)
    if not other or other == col or key in (None, col, other):
        return 0
    check = {"cross_check": {"on": [key], "columns": [[sheet, col], [sheet, other]], "relation": "not_equals",
                             "message": f"{col} and {other} must differ ({{{key}}})"}}
    wr = rules.setdefault("workbook_rules", [])
    if check in wr:
        return 0
    wr.append(check)
    return 1


def describe(constraints: list[dict] | None) -> str:
    """The constraints as prompt lines: the free-text rules for the rule writer, everything as decided data."""
    lines = []
    for c in constraints or []:
        bits = [f"required: {c['required']}" if c.get("required") else "", f"type: {c['type']}" if c.get("type") else "",
                f"allowed: {c['allowed']}" if c.get("allowed") else "", f"rule: {c['rule']}" if c.get("rule") else ""]
        lines.append(f"- {c.get('sheet') or '?'} / {c.get('column')}: " + "; ".join(b for b in bits if b))
    return "\n".join(lines)
