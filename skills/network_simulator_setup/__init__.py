"""Network Simulator Setup Skill.

Four phases:
1. Generate MockWire server config (hosts, ports, users, prompts) via LLM
2. Generate UC-specific command-response mappings via LLM
3. Write configs to disk and start MockWire JAR process
4. Run sanity checks: SSH connect, prompt match, basic cmd, escalation, UC command

Usage::

    from skills.network_simulator_setup import NetworkSimulatorSetupSkill, SkillInput, SkillOutput

    skill = NetworkSimulatorSetupSkill(llm_fn=my_async_llm)
    output = await skill.setup(SkillInput(
        uc_name="...", workflow_yaml="...", requirements={...},
        run_dir="/path/to/run/dir",
    ))

    # Check sanity results
    if output.sanity and output.sanity.all_passed:
        print("Simulator ready for testing")

    # When done:
    if output.simulator:
        await output.simulator.stop()
"""
from .skill import NetworkSimulatorSetupSkill, SkillInput, SkillOutput, RegenerateFeedback
from .simulator_runner import SimulatorProcess
from .sanity_check import SanityResult, NodeSanityResult

__all__ = [
    "NetworkSimulatorSetupSkill", "SkillInput", "SkillOutput",
    "RegenerateFeedback", "SimulatorProcess",
    "SanityResult", "NodeSanityResult",
]
