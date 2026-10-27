"""Report Generation Skill.

Generates the final pipeline report in HTML and Excel formats.
Summarizes all step results, artifacts, test outcomes, and recommendations.

Usage::

    from src.skills.report_generation import ReportGenerationSkill, SkillInput, SkillOutput

    skill = ReportGenerationSkill()
    output = await skill.generate(SkillInput(
        uc_name="...",
        run_dir="/path/to/run/dir",
        step_results=[...],
        artifacts={...},
    ))
    # output.html_report — full pipeline report
    # output.excel_path — path to Excel file (if generated)
"""
from .skill import ReportGenerationSkill, SkillInput, SkillOutput

__all__ = ["ReportGenerationSkill", "SkillInput", "SkillOutput"]
