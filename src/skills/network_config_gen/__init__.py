"""Network Configuration Generation Skill.

Generates the execution workflow YAML with runtime ${...} placeholders
from requirements, CIQ data, MOP commands, and SSH/XML extractions.

Usage::

    from src.skills.network_config_gen import NetworkConfigGenSkill, SkillInput, SkillOutput

    skill = NetworkConfigGenSkill(llm_fn=my_async_llm)
    output = await skill.generate(SkillInput(uc_name="...", requirements={...}))

    # After test failures:
    from src.skills.network_config_gen import RegenerateFeedback
    output = await skill.regenerate(RegenerateFeedback(test_results=[...]))
"""
from .skill import NetworkConfigGenSkill, SkillInput, SkillOutput, RegenerateFeedback

__all__ = ["NetworkConfigGenSkill", "SkillInput", "SkillOutput", "RegenerateFeedback"]
