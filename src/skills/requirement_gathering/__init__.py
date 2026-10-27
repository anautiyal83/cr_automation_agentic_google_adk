"""Requirement Gathering Skill.

Reusable skill for gathering network activity requirements from
documents (CIQ, MOP, SSH logs, XML samples). Produces a complete
specification for downstream YAML generation.

Usage::

    from src.skills.requirement_gathering import TelecomReqSkill, SkillInput, SkillOutput

    skill = TelecomReqSkill(llm_fn=my_async_llm)
    output = await skill.parse_and_analyze(SkillInput(uc_name="...", ...))

    while output.questions:
        answers = get_user_answers(output.questions)
        output = await skill.incorporate_answers(answers)

    output = await skill.compile_final()
"""
from .skill import TelecomReqSkill, SkillInput, SkillOutput
from .schema_discovery import discover_schemas, DiscoveredSchemas
from .schema_validator import (
    validate_validation_rules,
    validate_json_output,
    check_requirements_coverage,
    ValidationResult,
)

__all__ = [
    "TelecomReqSkill", "SkillInput", "SkillOutput",
    "discover_schemas", "DiscoveredSchemas",
    "validate_validation_rules", "validate_json_output",
    "check_requirements_coverage", "ValidationResult",
]
