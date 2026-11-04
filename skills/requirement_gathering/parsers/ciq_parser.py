"""CIQ Excel parser — deterministic extraction of sheet/column structure."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def parse_ciq(ciq_file_path: str | None = None,
              ciq_data: dict | None = None,
              constraints_types: dict[str, str] | None = None) -> dict[str, dict[str, Any]]:
    """Parse CIQ into per-sheet dict with headers, row counts, sample rows, and column types.

    Args:
        ciq_file_path: Path to CIQ Excel file.
        ciq_data: Pre-parsed CIQ data dict.
        constraints_types: Explicit column type overrides from constraints file.
            ``{column_name: type_string}`` — takes priority over auto-detection.

    Returns: ``{sheet_name: {"columns": [...], "column_types": {...}, "row_count": N, "sample_rows": [...]}}``
    """
    sheets: dict[str, dict[str, Any]] = {}
    constraints_types = constraints_types or {}

    # Try pre-parsed data first
    if ciq_data:
        if isinstance(ciq_data, str):
            try:
                ciq_data = json.loads(ciq_data)
            except Exception:
                ciq_data = {}

        per_sheet = ciq_data.get("per_sheet", {})
        if per_sheet:
            for sname, sdata in per_sheet.items():
                if isinstance(sdata, dict):
                    headers = sdata.get("headers", [])
                    sample_rows = sdata.get("sample_rows", [])[:2]
                    # Build tuples for type detection from sample dicts
                    data_tuples = [
                        tuple(row.get(h) for h in headers)
                        for row in sample_rows if isinstance(row, dict)
                    ]
                    col_types = _detect_column_types(headers, data_tuples)
                    col_types.update({k: v for k, v in constraints_types.items() if k in headers})
                    sheets[sname] = {
                        "columns": headers,
                        "column_types": col_types,
                        "row_count": sdata.get("row_count", 0),
                        "sample_rows": sample_rows,
                    }
            return sheets

        # Fallback: top-level headers/rows
        sheet_names = ciq_data.get("sheet_names", [])
        headers = ciq_data.get("headers", [])
        rows = ciq_data.get("rows", [])
        if sheet_names and headers:
            col_types = _detect_column_types(headers, [])
            col_types.update({k: v for k, v in constraints_types.items() if k in headers})
            for sn in sheet_names:
                sheets[sn] = {"columns": headers, "column_types": col_types, "row_count": len(rows), "sample_rows": []}
            return sheets
        if headers:
            col_types = _detect_column_types(headers, [])
            col_types.update({k: v for k, v in constraints_types.items() if k in headers})
            sheets["Data"] = {"columns": headers, "column_types": col_types, "row_count": len(rows), "sample_rows": []}
            return sheets

    # Read from Excel file directly
    if ciq_file_path and Path(ciq_file_path).exists():
        try:
            import openpyxl
            wb = openpyxl.load_workbook(ciq_file_path, read_only=True, data_only=True)
            for sn in wb.sheetnames:
                ws = wb[sn]
                rows_list = list(ws.iter_rows(min_row=1, max_row=12, values_only=True))
                if not rows_list:
                    continue
                hdrs = [str(c or "") for c in rows_list[0]]
                data_rows = rows_list[1:]
                row_count = max((ws.max_row or 1) - 1, 0)

                # Detect column types from sample data
                col_types = _detect_column_types(hdrs, data_rows)
                sample_rows = [
                    {hdrs[i]: str(v) for i, v in enumerate(row) if i < len(hdrs) and v is not None}
                    for row in data_rows[:2]
                ]

                sheets[sn] = {
                    "columns": [h for h in hdrs if h],
                    "column_types": col_types,
                    "row_count": row_count,
                    "sample_rows": sample_rows,
                }
            wb.close()
        except Exception as e:
            logger.debug("Could not read CIQ Excel %s: %s", ciq_file_path, e)

    return sheets


def _detect_column_types(headers: list[str], data_rows: list[tuple]) -> dict[str, str]:
    """Infer column type purely from sample values.

    Returns: {column_name: type_string}
    Types: text, number, email, boolean
    Header names are NOT used for inference — only actual data values matter.
    """
    col_types: dict[str, str] = {}

    for col_idx, hdr in enumerate(headers):
        if not hdr:
            continue

        # Collect non-empty sample values for this column
        values = []
        for row in data_rows:
            if col_idx < len(row) and row[col_idx] is not None and str(row[col_idx]).strip():
                values.append(row[col_idx])

        if not values:
            col_types[hdr] = "text"
            continue

        # Check if all values are numeric (int or float)
        all_numeric = all(
            isinstance(v, (int, float))
            or (isinstance(v, str) and v.strip().replace(".", "", 1).replace("-", "", 1).isdigit())
            for v in values
        )
        if all_numeric:
            col_types[hdr] = "number"
            continue

        # Check if all values look like emails
        all_email = all(
            isinstance(v, str) and "@" in v and "." in v.split("@")[-1]
            for v in values
        )
        if all_email:
            col_types[hdr] = "email"
            continue

        # Check if all values are boolean-like
        bool_vals = {"true", "false", "yes", "no", "1", "0", "y", "n"}
        all_bool = all(str(v).strip().lower() in bool_vals for v in values)
        if all_bool:
            col_types[hdr] = "boolean"
            continue

        col_types[hdr] = "text"

    return col_types
