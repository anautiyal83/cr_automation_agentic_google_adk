"""CIQ Validation & JSON Output Generation Skill.

Generates two YAML artifacts from requirements + CIQ data:
1. validation-rules.yaml — CIQ column validation rules
2. json-output.yaml — CIQ data extraction template

Usage::

    from skills.ciq_validation_json_gen import CiqValidationJsonGenSkill, SkillInput, SkillOutput

    skill = CiqValidationJsonGenSkill(llm_fn=my_async_llm)
    output = await skill.generate(SkillInput(
        uc_name="...",
        requirements={...},
        ciq_file_path="/path/to/ciq.xlsx",
    ))

    # After test failures — regenerate with feedback:
    from skills.ciq_validation_json_gen import RegenerateFeedback

    output = await skill.regenerate(RegenerateFeedback(
        test_results=[...],
        reviewer_analysis="...",
    ))
"""
from .skill import CiqValidationJsonGenSkill, SkillInput, SkillOutput, RegenerateFeedback

__all__ = ["CiqValidationJsonGenSkill", "SkillInput", "SkillOutput", "RegenerateFeedback"]
