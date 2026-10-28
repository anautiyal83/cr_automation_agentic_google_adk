"""Excel pipeline report generation."""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def generate_excel_report(
    uc_name: str,
    step_results: list[dict],
    artifacts: dict,
    run_dir: str = "",
    requirements: dict | None = None,
) -> str:
    """Generate an Excel report with step results and artifact summary.

    Returns the file path of the generated .xlsx file, or "" on failure.
    """
    if not run_dir:
        return ""

    try:
        import xlsxwriter
    except ImportError:
        logger.warning("xlsxwriter not installed — skipping Excel report")
        return ""

    output_path = Path(run_dir) / f"{uc_name}_report.xlsx"

    try:
        wb = xlsxwriter.Workbook(str(output_path))

        # Formats
        header_fmt = wb.add_format({"bold": True, "bg_color": "#1e1b4b", "font_color": "#ffffff", "border": 1})
        pass_fmt = wb.add_format({"bg_color": "#dcfce7", "border": 1})
        fail_fmt = wb.add_format({"bg_color": "#fce4ec", "border": 1})
        cell_fmt = wb.add_format({"border": 1, "text_wrap": True})

        # Sheet 1: Step Results
        ws = wb.add_worksheet("Step Results")
        ws.set_column("A:A", 8)
        ws.set_column("B:B", 30)
        ws.set_column("C:C", 10)
        ws.set_column("D:D", 60)

        headers = ["Step #", "Step Name", "Status", "Error"]
        for col, h in enumerate(headers):
            ws.write(0, col, h, header_fmt)

        for row, sr in enumerate(step_results, 1):
            ws.write(row, 0, sr.get("step_number", ""), cell_fmt)
            ws.write(row, 1, sr.get("step_name", ""), cell_fmt)
            status = sr.get("status", "")
            fmt = pass_fmt if status == "passed" else fail_fmt
            ws.write(row, 2, status.upper(), fmt)
            ws.write(row, 3, sr.get("error_message", "")[:200], cell_fmt)

        # Sheet 2: Artifacts
        ws2 = wb.add_worksheet("Artifacts")
        ws2.set_column("A:A", 50)
        ws2.set_column("B:B", 15)

        ws2.write(0, 0, "Artifact", header_fmt)
        ws2.write(0, 1, "Size", header_fmt)

        if run_dir:
            rd = Path(run_dir)
            row = 1
            for f in sorted(rd.iterdir()):
                if f.is_file() and f.suffix in (".yaml", ".html", ".json", ".xlsx", ".txt"):
                    ws2.write(row, 0, f.name, cell_fmt)
                    ws2.write(row, 1, f.stat().st_size, cell_fmt)
                    row += 1

        # Sheet 3: Requirements Summary
        req = requirements or {}
        ws3 = wb.add_worksheet("Requirements")
        ws3.set_column("A:A", 20)
        ws3.set_column("B:B", 60)

        ws3.write(0, 0, "Property", header_fmt)
        ws3.write(0, 1, "Value", header_fmt)

        cat1 = req.get("category_1_node_activity", {})
        nodes = cat1.get("nodes", [])
        node_names = [n.get("name", "?") for n in nodes if isinstance(n, dict)]
        phases = req.get("category_3_orchestration", {}).get("phases", [])
        commands = req.get("category_2_commands", {}).get("commands", [])

        summary_rows = [
            ("UC Name", uc_name),
            ("Nodes", ", ".join(node_names)),
            ("Phases", ", ".join(phases)),
            ("Commands", str(len(commands))),
            ("Activity Type", cat1.get("activity_type", "")),
        ]
        for row, (k, v) in enumerate(summary_rows, 1):
            ws3.write(row, 0, k, cell_fmt)
            ws3.write(row, 1, v, cell_fmt)

        wb.close()
        logger.info("Excel report written to %s", output_path)
        return str(output_path)

    except Exception as e:
        logger.error("Excel report generation failed: %s", e)
        return ""
