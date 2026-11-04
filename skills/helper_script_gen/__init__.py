"""Helper Script Generation Skill.

Reads sample request/response XMLs from requirements, analyzes the CIQ-to-XML
mapping, and generates Python scripts that the workflow's LOCAL node steps use:

- create_xml.py — reads CIQ data args, generates SPML modification XMLs
- parse_response.py — parses SPML batchResponse XML for success/failure

Usage::

    from skills.helper_script_gen import HelperScriptGenSkill, SkillInput

    skill = HelperScriptGenSkill(llm_fn=my_async_llm)
    output = await skill.generate(SkillInput(
        uc_name="...", workflow_yaml="...", requirements={...},
    ))
    # output.scripts = {"create_xml.py": "...", "parse_response.py": "..."}
"""
from .skill import HelperScriptGenSkill, SkillInput, SkillOutput

__all__ = ["HelperScriptGenSkill", "SkillInput", "SkillOutput"]
