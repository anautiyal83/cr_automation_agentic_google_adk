"""The run's uploaded documents as stand-ins for an activity's input files (OB-50, OB-58, OB-59).

A CIQ names an input file (``DELTA_TAC_FILE: SD_TAC_Addition_150926.xlsx``) that no test run has; the document of
the same kind uploaded with the run (``SD_TAC_Addition_230926.xlsx``) stands for it: step 9 stages it, step 6 shows
its layout to the helper script generator, step 8 derives what the node holds after loading it.
"""
from __future__ import annotations

import csv
import difflib
import re
from pathlib import Path


def sample_for(path: str, samples: list[str] | None) -> str:
    """The uploaded document that stands for input file ``path``: the same extension, the name most alike
    (``SD_TAC_Addition_230926.xlsx`` for the CIQ's ``SD_TAC_Addition_150926.xlsx``); '' when none."""
    want = Path(path)
    best, score = "", 0.0
    for s in samples or []:
        p = Path(s)
        name = re.sub(r"^[0-9a-f]{8}_", "", p.name)  # uploads are stored as <id>_<name>
        if p.suffix.lower() != want.suffix.lower() or not p.is_file():
            continue
        ratio = difflib.SequenceMatcher(None, name.lower(), want.name.lower()).ratio()
        if ratio > score:
            best, score = str(p), ratio
    return best if score >= 0.6 else ""


def read_tables(path: str, max_rows: int | None = None) -> dict[str, list[list[str]]]:
    """{sheet: rows (header first)} of a workbook / CSV, cells as text; a text file is one sheet of lines.
    {} when the file cannot be read."""
    p = Path(path)
    suffix = p.suffix.lower()
    try:
        if suffix in (".xlsx", ".xlsm"):
            import openpyxl
            wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
            try:
                out = {}
                for ws in wb.worksheets:
                    if ws.sheet_state != "visible":
                        continue
                    rows = []
                    for row in ws.iter_rows(values_only=True):
                        cells = ["" if c is None else _cell(c) for c in row]
                        if any(cells):
                            rows.append(cells)
                        if max_rows and len(rows) >= max_rows:
                            break
                    out[ws.title] = rows
                return out
            finally:
                wb.close()
        if suffix in (".csv", ".tsv"):
            with p.open(encoding="utf-8", errors="replace", newline="") as f:
                rows = [r for r in csv.reader(f, delimiter="\t" if suffix == ".tsv" else ",") if any(r)]
            return {p.stem: rows[:max_rows] if max_rows else rows}
        if suffix in (".txt", ".log", ".dat", ".lst"):
            lines = [l for l in p.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
            return {p.stem: [[l] for l in (lines[:max_rows] if max_rows else lines)]}
    except Exception:  # noqa: BLE001 — a document we cannot read is no stand-in
        return {}
    return {}


def _cell(value) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def preview(path: str, rows: int = 6) -> str:
    """A few rows of each sheet, for a prompt: ``sheet "White" (601 rows): TAC | Action | Revert Plan``."""
    full = read_tables(path)
    parts = []
    for sheet, table in full.items():
        parts.append(f'  sheet "{sheet}" ({max(len(table) - 1, 0)} data rows):')
        parts += [f"    {' | '.join(r)}" for r in table[:rows]]
    return "\n".join(parts)


def uploaded_documents(state) -> list[str]:
    """Paths of the files uploaded with the run's documents (``document_set.additional_documents``)."""
    import json
    docs = (state.get("document_set") or {}).get("additional_documents") or []
    if isinstance(docs, str):
        try:
            docs = json.loads(docs)
        except ValueError:
            docs = []
    return [str(d.get("path")) for d in docs if isinstance(d, dict) and d.get("path")]
