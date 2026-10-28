"""Auto-fix utilities for LLM-generated YAML artifacts.

Fixes common LLM mistakes:
- Column names that don't match actual CIQ headers (fuzzy matching)
- Sheet names that don't exist in CIQ
- Invalid YAML syntax (markdown fencing, trailing garbage)
- Structural issues (wrong types, missing required fields)

CIQ structure is passed as ``ciq_sheets: dict[str, dict]`` — the same
format produced by requirement_gathering's CIQ parser:
    {"SheetName": {"columns": [...], "sample_rows": [...]}, ...}
"""
from __future__ import annotations

import logging
import re
from io import StringIO
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# YAML cleanup
# ---------------------------------------------------------------------------

def clean_yaml_response(text: str) -> str:
    """Strip markdown code fences and leading/trailing noise from LLM YAML output."""
    text = re.sub(r"^```(?:ya?ml)?\s*\n?", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n?```\s*$", "", text, flags=re.MULTILINE)

    lines = text.split("\n")
    yaml_start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and (stripped.startswith(("#", "---", "version", "settings", "output_mode",
                                             "segregate_by", "data:", "sheets:", "workbook",
                                             "node_id", "validateI", "validateN", "skipV",
                                             "report:")) or ":" in stripped):
            yaml_start = i
            break

    return "\n".join(lines[yaml_start:]).rstrip()


def parse_yaml(text: str) -> dict | None:
    """Parse YAML text, returning dict or None on failure."""
    try:
        from ruamel.yaml import YAML
        parsed = YAML().load(StringIO(text))
        return dict(parsed) if parsed else None
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


# ---------------------------------------------------------------------------
# Fuzzy matching helpers
# ---------------------------------------------------------------------------

def _normalize(name: str) -> str:
    return name.lower().replace("_", " ").replace("-", " ").strip()


def find_closest_column(target: str, available: list[str]) -> str | None:
    """Find the closest matching column name (case-insensitive, underscore/space tolerant)."""
    target_norm = _normalize(target)
    for col in available:
        if _normalize(col) == target_norm:
            return col
    for col in available:
        col_norm = _normalize(col)
        if target_norm in col_norm or col_norm in target_norm:
            return col
    return None


def _find_closest_sheet(target: str, sheet_names: list[str]) -> str | None:
    """Find closest matching sheet name (case-insensitive)."""
    target_norm = _normalize(target)
    for s in sheet_names:
        if _normalize(s) == target_norm:
            return s
    for s in sheet_names:
        if target_norm in _normalize(s) or _normalize(s) in target_norm:
            return s
    return None


def _get_sheet_names(ciq_sheets: dict) -> list[str]:
    return list(ciq_sheets.keys())


def _get_columns(ciq_sheets: dict, sheet_name: str) -> list[str]:
    sheet = ciq_sheets.get(sheet_name, {})
    return sheet.get("columns", sheet.get("headers", []))


def _ciq_prompt_summary(ciq_sheets: dict) -> str:
    """Build a concise CIQ summary for LLM prompts from the ciq_sheets dict."""
    if not ciq_sheets:
        return "No CIQ data"
    parts = []
    for sname, sdata in ciq_sheets.items():
        cols = _get_columns(ciq_sheets, sname)
        col_str = ", ".join(cols[:30])
        if len(cols) > 30:
            col_str += f" ... +{len(cols) - 30} more"
        parts.append(f'Sheet "{sname}": [{col_str}]')
        samples = sdata.get("sample_rows", [])
        if samples:
            for i, row in enumerate(samples[:2]):
                if isinstance(row, dict):
                    vals = ", ".join(f"{k}={v!r}" for k, v in list(row.items())[:8])
                    parts.append(f"  sample[{i}]: {vals}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Validation rules auto-fix
# ---------------------------------------------------------------------------

def fix_validation_rules(yaml_text: str, ciq_sheets: dict) -> str:
    """Auto-fix validation-rules YAML against actual CIQ structure.

    Args:
        yaml_text: LLM-generated YAML string.
        ciq_sheets: CIQ structure from requirements.doc_extracted.ciq_sheets.
    """
    yaml_text = clean_yaml_response(yaml_text)
    parsed = parse_yaml(yaml_text)
    if not parsed:
        return yaml_text

    sheet_names = _get_sheet_names(ciq_sheets)

    sheets_section = parsed.get("sheets", {})
    if not isinstance(sheets_section, dict):
        return yaml_text

    fixed_sheets = {}
    for sheet_name, sheet_config in sheets_section.items():
        actual_sheet = _find_closest_sheet(sheet_name, sheet_names)
        if not actual_sheet:
            logger.debug("Removing unknown sheet '%s' from validation rules", sheet_name)
            continue

        if isinstance(sheet_config, dict):
            sheet_config = _fix_column_refs(sheet_config, _get_columns(ciq_sheets, actual_sheet))
        fixed_sheets[actual_sheet] = sheet_config

    parsed["sheets"] = fixed_sheets

    wb_rules = parsed.get("workbook_rules", [])
    if isinstance(wb_rules, list):
        parsed["workbook_rules"] = _fix_workbook_rule_refs(wb_rules, ciq_sheets)

    return dump_yaml(parsed)


# ---------------------------------------------------------------------------
# JSON output auto-fix
# ---------------------------------------------------------------------------

def fix_json_output(yaml_text: str, ciq_sheets: dict) -> str:
    """Auto-fix json-output YAML against actual CIQ structure.

    Args:
        yaml_text: LLM-generated YAML string.
        ciq_sheets: CIQ structure from requirements.doc_extracted.ciq_sheets.
    """
    yaml_text = clean_yaml_response(yaml_text)
    parsed = parse_yaml(yaml_text)
    if not parsed:
        return yaml_text

    sheet_names = _get_sheet_names(ciq_sheets)

    seg = parsed.get("segregate_by", {})
    if isinstance(seg, dict):
        seg_sheet = seg.get("sheet", "")
        if seg_sheet:
            actual = _find_closest_sheet(seg_sheet, sheet_names)
            if actual:
                seg["sheet"] = actual
                seg_col = seg.get("column", "")
                if seg_col:
                    actual_col = find_closest_column(seg_col, _get_columns(ciq_sheets, actual))
                    if actual_col:
                        seg["column"] = actual_col
        parsed["segregate_by"] = seg

    data = parsed.get("data", {})
    if isinstance(data, dict):
        parsed["data"] = _fix_dotted_refs(data, ciq_sheets)

    return dump_yaml(parsed)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _fix_column_refs(sheet_config: dict, actual_columns: list[str]) -> dict:
    """Fix column references within a sheet's validation config."""
    columns = sheet_config.get("columns", {})
    if not isinstance(columns, dict):
        return sheet_config

    fixed_columns = {}
    for col_name, col_config in columns.items():
        actual = find_closest_column(col_name, actual_columns)
        if actual:
            fixed_columns[actual] = col_config
        else:
            logger.debug("Removing unknown column '%s' from validation rules", col_name)

    sheet_config["columns"] = fixed_columns
    return sheet_config


def _fix_workbook_rule_refs(rules: list, ciq_sheets: dict) -> list:
    fixed = []
    for rule in rules:
        if isinstance(rule, dict):
            rule = _fix_sheet_col_in_dict(rule, ciq_sheets)
        fixed.append(rule)
    return fixed


def _fix_sheet_col_in_dict(d: dict, ciq_sheets: dict) -> dict:
    result = {}
    for k, v in d.items():
        if isinstance(v, str) and "." in v and not v.startswith("$"):
            result[k] = _fix_dotted_ref_string(v, ciq_sheets)
        elif isinstance(v, dict):
            result[k] = _fix_sheet_col_in_dict(v, ciq_sheets)
        elif isinstance(v, list):
            result[k] = [
                _fix_dotted_ref_string(item, ciq_sheets) if isinstance(item, str) and "." in item
                else _fix_sheet_col_in_dict(item, ciq_sheets) if isinstance(item, dict)
                else item
                for item in v
            ]
        else:
            result[k] = v
    return result


def _fix_dotted_refs(data: Any, ciq_sheets: dict) -> Any:
    if isinstance(data, str):
        if "." in data and not data.startswith("$") and not data.startswith('"'):
            return _fix_dotted_ref_string(data, ciq_sheets)
        return data
    elif isinstance(data, dict):
        result = {}
        for k, v in data.items():
            if k == "_each" and isinstance(v, str):
                result[k] = _fix_each_ref(v, ciq_sheets)
            else:
                result[k] = _fix_dotted_refs(v, ciq_sheets)
        return result
    elif isinstance(data, list):
        return [_fix_dotted_refs(item, ciq_sheets) for item in data]
    return data


def _fix_dotted_ref_string(ref: str, ciq_sheets: dict) -> str:
    where_match = re.match(r"^(.+?)\s+WHERE\s+(.+)$", ref, re.IGNORECASE)
    if where_match:
        main_ref = where_match.group(1).strip()
        where_clause = where_match.group(2).strip()
        fixed_main = _fix_simple_dotted_ref(main_ref, ciq_sheets)
        return f"{fixed_main} WHERE {where_clause}"
    return _fix_simple_dotted_ref(ref, ciq_sheets)


def _fix_simple_dotted_ref(ref: str, ciq_sheets: dict) -> str:
    parts = ref.split(".", 1)
    if len(parts) != 2:
        return ref

    sheet_part, col_part = parts[0].strip(), parts[1].strip()
    sheet_names = _get_sheet_names(ciq_sheets)
    actual_sheet = _find_closest_sheet(sheet_part, sheet_names)
    if not actual_sheet:
        return ref

    actual_col = find_closest_column(col_part, _get_columns(ciq_sheets, actual_sheet))
    if actual_col:
        return f"{actual_sheet}.{actual_col}"

    return f"{actual_sheet}.{col_part}"


def _fix_each_ref(ref: str, ciq_sheets: dict) -> str:
    sheet_names = _get_sheet_names(ciq_sheets)

    distinct_match = re.match(
        r"DISTINCT\s+(?:FROM\s+)?(.+?)(?:\s+WHERE\s+(.+?))?(?:\s+AS\s+(.+))?$",
        ref, re.IGNORECASE
    )
    if distinct_match:
        main_ref = _fix_simple_dotted_ref(distinct_match.group(1).strip(), ciq_sheets)
        result = f"DISTINCT {main_ref}"
        if distinct_match.group(2):
            result += f" WHERE {distinct_match.group(2).strip()}"
        if distinct_match.group(3):
            result += f" AS {distinct_match.group(3).strip()}"
        return result

    where_match = re.match(r"^(.+?)\s+WHERE\s+(.+)$", ref, re.IGNORECASE)
    if where_match:
        sheet = where_match.group(1).strip()
        actual = _find_closest_sheet(sheet, sheet_names)
        if actual:
            return f"{actual} WHERE {where_match.group(2).strip()}"
        return ref

    actual = _find_closest_sheet(ref.strip(), sheet_names)
    return actual if actual else ref
