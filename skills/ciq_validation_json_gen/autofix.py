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

    cleaned = "\n".join(lines[yaml_start:]).rstrip()
    return sanitize_yaml(cleaned)


def sanitize_yaml(text: str) -> str:
    """Fix common LLM YAML mistakes before parsing.

    Handles:
    - Unquoted values containing colons (e.g., ``type: _col: $X`` → ``type: '_col: $X'``)
    - List items with colons in values
    """
    fixed_lines = []
    for line in text.split("\n"):
        stripped = line.lstrip()
        if ":" in stripped and not stripped.startswith("#"):
            indent = len(line) - len(stripped)
            # Handle list items: "- key: value"
            list_prefix = ""
            content = stripped
            if content.startswith("- "):
                list_prefix = "- "
                content = content[2:]
            match = re.match(r'^([\w_.-]+)\s*:\s*(.+)$', content)
            if match:
                key, val = match.group(1), match.group(2)
                if ":" in val and not val.startswith(('"', "'", "[", "{", "|", ">")):
                    val = val.replace("'", "''")
                    val = f"'{val}'"
                    line = " " * indent + f"{list_prefix}{key}: {val}"
        fixed_lines.append(line)
    return "\n".join(fixed_lines)


def parse_yaml(text: str) -> dict | None:
    """Parse YAML text, returning dict or None on failure.

    Tries raw parse first, then sanitized parse if raw fails.
    """
    from ruamel.yaml import YAML
    # Try raw first
    try:
        parsed = YAML().load(StringIO(text))
        return dict(parsed) if parsed else None
    except Exception:
        pass
    # Try after sanitizing
    try:
        sanitized = sanitize_yaml(text)
        parsed = YAML().load(StringIO(sanitized))
        if parsed:
            logger.info("YAML parsed after sanitizing (fixed quoting issues)")
            return dict(parsed)
    except Exception:
        pass
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


def _strip_broken_sections(yaml_text: str) -> str:
    """Strip non-essential top-level sections that often cause YAML parse errors.

    Keeps only 'sheets:' and 'settings:' sections from the raw text.
    """
    import re
    lines = yaml_text.split("\n")
    result = []
    in_safe_section = False
    # Top-level keys we want to keep
    safe_keys = {"sheets", "settings"}

    for line in lines:
        # Detect top-level key (no leading whitespace, ends with colon)
        m = re.match(r"^(\w[\w_]*):", line)
        if m:
            key = m.group(1)
            in_safe_section = key in safe_keys
        if in_safe_section:
            result.append(line)

    stripped = "\n".join(result)
    if stripped != yaml_text:
        logger.info("Stripped broken sections from validation-rules, kept: sheets + settings")
    return stripped


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
        # Try stripping problematic top-level sections that LLM often breaks
        yaml_text = _strip_broken_sections(yaml_text)
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

    # Remove workbook_rules — LLM generates broken structures that cause
    # both YAML parse failures and JAR deserialization errors
    parsed.pop("workbook_rules", None)

    # Remove other non-essential LLM-generated sections
    for key in list(parsed.keys()):
        if key not in ("sheets", "settings"):
            parsed.pop(key)

    return dump_yaml(parsed)


# ---------------------------------------------------------------------------
# JSON output auto-fix
# ---------------------------------------------------------------------------

def fix_json_output(yaml_text: str, ciq_sheets: dict, uc_name: str = "") -> str:
    """Build deterministic json-output YAML from CIQ structure.

    The json-output template is too complex for reliable LLM generation.
    We build it programmatically from the CIQ sheet structure:
    - Index sheet → segregate_by CRGroup, nodeGroup per ZONE
    - IP sheet → node niamIDs per Node_Name
    - Data sheet (Profile Modification etc.) → configSequences records
    """
    sheet_names = _get_sheet_names(ciq_sheets)

    # Find key sheets
    index_sheet = _find_closest_sheet("Index", sheet_names) or "Index"
    ip_sheet = _find_closest_sheet("IP", sheet_names) or "IP"
    index_cols = _get_columns(ciq_sheets, index_sheet)
    ip_cols = _get_columns(ciq_sheets, ip_sheet)

    # Find the data sheet (not Index, not IP — the one with subscriber data)
    data_sheet = ""
    data_cols = []
    for sn in sheet_names:
        if sn.lower() not in ("index", "ip", "node_id", "node_details"):
            data_sheet = sn
            data_cols = _get_columns(ciq_sheets, sn)
            break

    # Determine zone column in Index
    zone_col = "ZONE"
    for c in index_cols:
        if c.upper() in ("ZONE", "ZONE_NAME", "NODE_GROUP"):
            zone_col = c
            break

    # Determine grouping column in data sheet
    data_zone_col = "Zone"
    for c in data_cols:
        if c.lower() in ("zone", "zone_name"):
            data_zone_col = c
            break

    # Find node names from IP sheet
    node_names = []
    ip_data = ciq_sheets.get(ip_sheet, {})
    sample_rows = ip_data.get("sample_rows", [])
    node_col = "Node_Name"
    for c in ip_cols:
        if "node" in c.lower() and "name" in c.lower():
            node_col = c
            break
    for row in sample_rows:
        if isinstance(row, dict):
            nn = row.get(node_col, "")
            if nn and nn not in node_names:
                node_names.append(str(nn))

    # Find NIAM columns from IP sheet
    niam_cols = [c for c in ip_cols if c.upper().startswith("NIAM_NAME") or c.upper().startswith("NIAM_ID")]
    if not niam_cols:
        niam_cols = [c for c in ip_cols if "niam" in c.lower()]

    # Build nodes block — one per node type with niamIDs
    nodes_block = {
        "_each": f"DISTINCT {ip_sheet}.{node_col} WHERE {ip_sheet}.{zone_col} = $zone AS $node",
        "node": "$zone",
    }
    for nn in node_names:
        node_niam = {}
        for i, nc in enumerate(niam_cols, 1):
            node_niam[f"niamID-{i}"] = f"{ip_sheet}.{nc} WHERE {ip_sheet}.{node_col} = {nn} AND {ip_sheet}.{zone_col} = $zone"
        nodes_block[nn] = node_niam

    # Build records data block — all columns from data sheet
    records_data = {}
    for col in data_cols:
        records_data[col] = col  # Inside _each row iteration, plain column name

    # Build the complete template
    template = {
        "output_mode": "individual",
        "segregate_by": {
            "sheet": index_sheet,
            "column": "CRGroup" if "CRGroup" in index_cols else index_cols[0] if index_cols else "CRGroup",
            "as": "$cr",
        },
        "data": {
            "nodeGroups": {
                "_each": f"DISTINCT {index_sheet}.{zone_col} WHERE {index_sheet}.CRGroup = $cr AS $zone"
                         if "CRGroup" in index_cols
                         else f"DISTINCT {index_sheet}.{zone_col} AS $zone",
                "nodeGroup": "$zone",
                "crGroup": "$cr",
                "email": f"{index_sheet}.Email WHERE {index_sheet}.{zone_col} = $zone"
                         if "Email" in index_cols else "",
                "nodes": nodes_block,
                "configSequences": {
                    "_each": f"DISTINCT {index_sheet}.{zone_col} WHERE {index_sheet}.{zone_col} = $zone AS $seq",
                    "configSeq": "Step1",
                    "records": {
                        "_each": f"{data_sheet} WHERE {data_zone_col} = $zone" if data_sheet else "Data",
                        "data": records_data,
                    },
                },
            },
        },
    }

    # Remove empty email if not available
    ng = template["data"]["nodeGroups"]
    if not ng.get("email"):
        ng.pop("email", None)

    result = dump_yaml(template)
    logger.info("Built deterministic json-output template: %d chars, %d data columns, %d nodes",
                len(result), len(data_cols), len(node_names))
    return result


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
