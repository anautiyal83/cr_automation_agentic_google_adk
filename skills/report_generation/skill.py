"""Report Generation Skill — final pipeline report in HTML + Excel."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from .html_report import generate_pipeline_report
from .excel_report import generate_excel_report

logger = logging.getLogger(__name__)


@dataclass
class SkillInput:
    """Input for report generation."""
    uc_name: str
    run_dir: str = ""
    step_results: list[dict] = field(default_factory=list)
    artifacts: dict = field(default_factory=dict)
    requirements: dict = field(default_factory=dict)
    pipeline_start_time: float = 0  # monotonic time when pipeline started


@dataclass
class SkillOutput:
    """Output from report generation."""
    html_report: str = ""
    html_path: str = ""
    excel_path: str = ""
    status: str = "pending"
    error: str = ""


class ReportGenerationSkill:
    """Generates final pipeline report in HTML and Excel formats.

    No LLM required — purely deterministic report generation.
    """

    async def generate(self, inp: SkillInput) -> SkillOutput:
        """Generate HTML + Excel pipeline reports."""
        t0 = time.monotonic()
        duration = t0 - inp.pipeline_start_time if inp.pipeline_start_time else 0

        try:
            html = generate_pipeline_report(
                uc_name=inp.uc_name,
                step_results=inp.step_results,
                artifacts=inp.artifacts,
                run_dir=inp.run_dir,
                requirements=inp.requirements,
                duration_sec=duration,
            )
        except Exception as e:
            logger.error("HTML report generation failed: %s", e, exc_info=True)
            return SkillOutput(status="failed", error=f"HTML report failed: {e}")

        html_path = ""
        if inp.run_dir:
            html_path = str(Path(inp.run_dir) / f"{inp.uc_name}_report.html")
            try:
                Path(html_path).write_text(html, encoding="utf-8")
            except Exception as e:
                logger.error("Failed to write HTML report: %s", e)

        excel_path = ""
        try:
            excel_path = generate_excel_report(
                uc_name=inp.uc_name,
                step_results=inp.step_results,
                artifacts=inp.artifacts,
                run_dir=inp.run_dir,
                requirements=inp.requirements,
            )
        except Exception as e:
            logger.error("Excel report generation failed: %s", e)

        logger.info("Reports generated: html=%s, excel=%s", html_path, excel_path)
        return SkillOutput(
            html_report=html,
            html_path=html_path,
            excel_path=excel_path,
            status="passed",
        )
