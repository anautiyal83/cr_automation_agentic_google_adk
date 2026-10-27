"""CIQ Validation & JSON Output Testing Skill.

Professional QA engine that:
1. Generates test cases via LLM (positive, negative, edge cases, cross-sheet)
2. Executes tests via ciq-processor JAR
3. Evaluates results against expected outcomes
4. Produces a professional HTML QA report

Usage::

    from skills.ciq_validation_json_test import CiqValidationJsonTestSkill, SkillInput, SkillOutput

    skill = CiqValidationJsonTestSkill(
        llm_fn=my_async_llm,
        jar_path="lib/ciq-processor-1.0.3-cli.jar",
    )
    output = await skill.test(SkillInput(
        uc_name="...",
        ciq_file_path="/path/to/ciq.xlsx",
        validation_rules_yaml="...",
        json_output_yaml="...",
        run_dir="/path/to/run/dir",
    ))
    # output.report_html — full professional QA report
    # output.test_cases — per-TC structured results
"""
from .skill import CiqValidationJsonTestSkill, SkillInput, SkillOutput

__all__ = ["CiqValidationJsonTestSkill", "SkillInput", "SkillOutput"]
