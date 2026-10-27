"""Network Configuration Testing Skill.

Professional QA engine for workflow + simulator testing:
1. Generate test cases via LLM (MOP, execution, integration)
2. Run MOP generation test via cli-automation JAR
3. Run execution test against MockWire simulator
4. Evaluate results, classify failures (workflow vs simulator)
5. Generate professional HTML QA report

Usage::

    from skills.network_config_test import NetworkConfigTestSkill, SkillInput, SkillOutput

    skill = NetworkConfigTestSkill(
        llm_fn=my_async_llm,
        cli_automation_jar="lib/cli-automation-standalone.jar",
    )
    output = await skill.test(SkillInput(
        uc_name="...",
        workflow_yaml="...",
        simulator_config_yaml="...",
        json_output_yaml="...",
        ciq_file_path="/path/to/ciq.xlsx",
        run_dir="/path/to/run/dir",
    ))
    # output.report_html — full QA report
    # output.failure_classification — {"classification": "workflow"|"simulator", "retry_target": 6|8}
"""
from .skill import NetworkConfigTestSkill, SkillInput, SkillOutput

__all__ = ["NetworkConfigTestSkill", "SkillInput", "SkillOutput"]
