"""Negative CIQ test cases that are actually executed: each breaks one rule in a copy of the CIQ
and runs the ciq-processor JAR on that copy.

Before, every negative case was evaluated against the JAR run on the unmodified CIQ, so the
invalid data never existed and the case could not pass; the report then showed those cases as
warnings and the summary as 0 failed (G-13).
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
from pathlib import Path

from .test_case_gen import TestCase

logger = logging.getLogger(__name__)

_NOT_FOUND = "ZZ_NOT_IN_REFERENCED_SHEET"
_PATTERN_PROBES = ("@@invalid@@", "x y", "0", "/", "-", "ABC", "1.2.3.4.5.x")


def _java_rx(pattern: str):
    try:
        return re.compile(re.sub(r"\(\?<([A-Za-z][A-Za-z0-9]*)>", r"(?P<\1>", pattern))
    except re.error:
        return None


def rule_negative_cases(rules: dict, rows: dict[str, list[dict[str, str]]]) -> list[TestCase]:
    """One negative case per rule of the validation-rules YAML, breaking the first data row
    (row 2 for uniqueness)."""
    cases: list[TestCase] = []

    def add(sheet, col, kind, value, description, category="column_validation", severity="major", row=1):
        cases.append(TestCase(
            id=f"TC_R{len(cases) + 1:02d}", category=category, severity=severity, test_type="negative",
            target_sheet=sheet, target_columns=[col], description=description,
            scenario=f"Set {sheet}.{col} row {row} to {value!r} in a copy of the CIQ ({kind})",
            expected_outcome=f"FAIL - the {kind} rule on {sheet}.{col} rejects the value",
            mutations=[{"sheet": sheet, "row": row, "column": col, "value": value}]))

    for sheet, sdef in ((rules or {}).get("sheets") or {}).items():
        sheet_rows = rows.get(sheet) or []
        if not sheet_rows or not isinstance(sdef, dict):
            continue
        for col, c in (sdef.get("columns") or {}).items():
            if not isinstance(c, dict):
                continue
            if c.get("required") is True:
                add(sheet, col, "required", "", f"{sheet}.{col} is required: a blank cell is rejected",
                    severity="critical")
            ref = c.get("crossRef")
            if isinstance(ref, dict) and ref.get("sheet") and ref.get("column"):
                add(sheet, col, "crossRef", _NOT_FOUND,
                    f"{sheet}.{col}: a value missing from {ref['sheet']}.{ref['column']} is rejected",
                    category="cross_sheet", severity="critical")
            typ = c.get("type")
            if typ == "email":
                add(sheet, col, "email", "not-an-email", f"{sheet}.{col}: an invalid e-mail address is rejected")
            elif typ == "ip":
                add(sheet, col, "ip", "999.1.1.1", f"{sheet}.{col}: an invalid IPv4 address is rejected")
            elif typ == "enum" and isinstance(c.get("values"), list):
                add(sheet, col, "enum", "INVALID_ENUM_VALUE",
                    f"{sheet}.{col}: a value outside {c['values']} is rejected")
            if isinstance(c.get("pattern"), str):
                rx = _java_rx(c["pattern"])
                bad = next((p for p in _PATTERN_PROBES if rx and not rx.fullmatch(p)), None)
                if bad is not None:
                    add(sheet, col, "pattern", bad, f"{sheet}.{col}: a value not matching {c['pattern']} is rejected")
            if c.get("unique") is True and len(sheet_rows) >= 2 and sheet_rows[0].get(col):
                add(sheet, col, "unique", sheet_rows[0][col],
                    f"{sheet}.{col} is unique: a duplicate value is rejected", severity="critical", row=2)
    return cases


def apply_mutations(ciq_path: str, mutations: list[dict], out_path: Path) -> str:
    """Copy the CIQ and write the mutated cells; '' if a sheet/column/row does not exist."""
    import openpyxl
    out_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ciq_path, out_path)
    wb = openpyxl.load_workbook(out_path)
    try:
        for m in mutations:
            sheet = next((s for s in wb.sheetnames if s.lower() == str(m.get("sheet", "")).lower()), None)
            if not sheet:
                return ""
            ws = wb[sheet]
            header = [str(c.value).strip() if c.value is not None else "" for c in ws[1]]
            col = next((i for i, h in enumerate(header) if h.lower() == str(m.get("column", "")).strip().lower()), None)
            row = int(m.get("row") or 1)
            if col is None or row < 1:
                return ""
            value = m.get("value")
            # ws.cell(..., value=None) leaves the cell unchanged: blank it by assignment
            ws.cell(row=row + 1, column=col + 1).value = None if value in ("", None) else value
        wb.save(out_path)
    finally:
        wb.close()
    return str(out_path)


async def run_negative_cases(cases: list[TestCase], *, jar_path: str, ciq_path: str, rules_path: str,
                             out_root: str | Path, java_home: str | None, uc_name: str,
                             concurrency: int = 4) -> dict[str, list[dict] | None]:
    """{case id: JAR errors on its mutated CIQ} (None when the mutation could not be applied)."""
    from .jar_executor import run_ciq_processor
    from .result_parser import parse_jar_output

    root = Path(out_root)
    shutil.rmtree(root, ignore_errors=True)
    sem = asyncio.Semaphore(concurrency)

    async def one(tc: TestCase):
        async with sem:
            ciq = apply_mutations(ciq_path, tc.mutations, root / tc.id / "ciq.xlsx")
            if not ciq:
                return tc.id, None
            res = await run_ciq_processor(jar_path=jar_path, ciq_file_path=ciq, validation_rules_path=rules_path,
                                          json_output_path="", output_dir=str(root / tc.id),
                                          java_home=java_home, uc_name=uc_name)
            return tc.id, parse_jar_output(res.get("output_dir", ""))

    done = await asyncio.gather(*(one(tc) for tc in cases if tc.mutations))
    logger.info("Negative cases: %d executed on mutated CIQ copies", len(done))
    return dict(done)
