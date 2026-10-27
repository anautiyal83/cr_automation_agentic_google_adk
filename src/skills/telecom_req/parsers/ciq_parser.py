"""CIQ Excel parser — deterministic extraction of sheet/column structure."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def parse_ciq(ciq_file_path: str | None = None,
              ciq_data: dict | None = None) -> dict[str, dict[str, Any]]:
    """Parse CIQ into per-sheet dict with headers, row counts, and sample rows.

    Accepts either a file path (reads via openpyxl) or pre-parsed ciq_data dict.
    Returns: ``{sheet_name: {"columns": [...], "row_count": N, "sample_rows": [...]}}``
    """
    sheets: dict[str, dict[str, Any]] = {}

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
                    sheets[sname] = {
                        "columns": sdata.get("headers", []),
                        "row_count": sdata.get("row_count", 0),
                        "sample_rows": sdata.get("sample_rows", [])[:2],
                    }
            return sheets

        # Fallback: top-level headers/rows
        sheet_names = ciq_data.get("sheet_names", [])
        headers = ciq_data.get("headers", [])
        rows = ciq_data.get("rows", [])
        if sheet_names and headers:
            for sn in sheet_names:
                sheets[sn] = {"columns": headers, "row_count": len(rows), "sample_rows": []}
            return sheets
        if headers:
            sheets["Data"] = {"columns": headers, "row_count": len(rows), "sample_rows": []}
            return sheets

    # Read from Excel file directly
    if ciq_file_path and Path(ciq_file_path).exists():
        try:
            import openpyxl
            wb = openpyxl.load_workbook(ciq_file_path, read_only=True, data_only=True)
            for sn in wb.sheetnames:
                ws = wb[sn]
                rows_iter = ws.iter_rows(min_row=1, max_row=1)
                first_row = next(rows_iter, None)
                hdrs = [str(c.value or "") for c in first_row] if first_row else []
                sheets[sn] = {
                    "columns": [h for h in hdrs if h],
                    "row_count": max((ws.max_row or 1) - 1, 0),
                    "sample_rows": [],
                }
            wb.close()
        except Exception as e:
            logger.debug("Could not read CIQ Excel %s: %s", ciq_file_path, e)

    return sheets
