"""Network Simulator Setup Agent.

Wraps network_simulator_setup skill. Generates MockWire config, starts
the simulator, and runs sanity checks (Step 8).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from google.adk.agents import BaseAgent

from core.config import PROJECT_ROOT
from core.logging_config import get_flow_logger
from shared.state import simulator_processes, stop_simulator

logger = logging.getLogger(__name__)
flow = get_flow_logger(__name__)

_DEFAULT_MOCKWIRE_JAR = "lib/mockwire/mockwire-simulator/mockwire.jar"


def _resolve_path(path: str) -> str:
    """Resolve a config path relative to the project root, not the CWD."""
    p = Path(path)
    return str(p if p.is_absolute() else (PROJECT_ROOT / p).resolve())


def _ciq_json_files(run_dir: str) -> list[str]:
    """CIQ JSON data written by the ciq-processor in steps 5/7 (source of NIAM IDs)."""
    json_dir = Path(run_dir) / "json_data" if run_dir else None
    if not json_dir or not json_dir.exists():
        return []
    return [str(f) for f in sorted(json_dir.glob("*.json"))
            if "VALIDATION_REPORT" not in f.name.upper()]


def _simulator_feedback(run_dir: str) -> str:
    """Failed step 9 commands when step 9 classified the failure as a simulator issue."""
    if not run_dir:
        return ""
    err_file = Path(run_dir) / "step_9_error.json"
    try:
        err = json.loads(err_file.read_text(encoding="utf-8")) if err_file.exists() else {}
    except Exception:
        err = {}
    if err.get("retry_target") != 8:
        return ""

    from skills.network_config_test.result_parser import parse_execution_results
    failed = [
        {"node": c.node, "phase": c.phase, "command": c.command, "output": c.output[:300],
         "validation": c.validation_criteria, "conclusion": c.validation_conclusion}
        for c in parse_execution_results(run_dir)
        if not c.success or c.validation_status == "failed"
    ]
    return json.dumps({
        "classification": err.get("classification"),
        # Exact command text the simulator had no response for — add a response for each
        "commands_without_simulator_response": err.get("unmatched_commands", [])[:40],
        "failed_commands": failed[:40],
    }, indent=2)[:6000]


class NetworkSimulatorSetupAgent(BaseAgent):
    """Generates simulator config, starts MockWire, runs sanity checks."""
    name: str = "network_simulator_setup"
    description: str = "Step 8: Generate MockWire simulator configs, start simulator, sanity test"

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(self, ctx):
        from shared.llm import get_llm_fn
        from agents.requirements_agent import requirements_store
        from skills.network_simulator_setup import (
            NetworkSimulatorSetupSkill, SkillInput,
        )

        state = ctx.session.state
        run_id = state.get("run_id", "")
        run_dir = state.get("run_dir", "")
        uc_name = state.get("uc_name", "")
        config = state.get("pipeline_config", {})
        requirements = requirements_store.get(run_id, {})
        artifacts = state.get("artifacts", {})

        flow.flow("SIMULATOR_SETUP START — uc=%s", uc_name)

        # A re-run must not collide with this run's previous simulator ports
        await stop_simulator(run_id)

        skill = NetworkSimulatorSetupSkill(llm_fn=get_llm_fn(config))

        # On a step 9 → 8 retry, feed the failed commands back (read from disk:
        # ADK session state is unreliable across agents)
        feedback = _simulator_feedback(run_dir)
        if feedback:
            flow.flow("SIMULATOR_SETUP: Regenerating with step 9 feedback (%d chars)", len(feedback))

        inp = SkillInput(
            uc_name=uc_name,
            workflow_yaml=artifacts.get("workflow_yaml", ""),
            requirements=requirements,
            generation_hints=requirements.get("generation_hints", {}),
            ciq_json_files=_ciq_json_files(run_dir),
            feedback=feedback,
            run_dir=run_dir,
            mockwire_jar=_resolve_path(config.get("mockwire_jar") or _DEFAULT_MOCKWIRE_JAR),
            java_home=config.get("java_home"),
            auto_start=True,
            host=config.get("simulator_endpoint") or "localhost",
            base_port=int(config.get("simulator_base_port") or 22200),
            ttl_minutes=int(config.get("simulator_ttl_minutes") or 60),
            startup_timeout=int(config.get("simulator_startup_timeout") or 30),
        )
        output = await skill.setup(inp)

        # Save artifacts
        if output.simulator_config_yaml:
            artifacts["simulator_config_yaml"] = output.simulator_config_yaml
            if run_dir:
                p = Path(run_dir) / f"{uc_name}_simulator-config.yaml"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(output.simulator_config_yaml, encoding="utf-8")
        if output.report_html and run_dir:
            (Path(run_dir) / f"{uc_name}_simulator-report.html").write_text(
                output.report_html, encoding="utf-8")
        state["artifacts"] = artifacts

        # Register the running simulator so every exit path can stop it
        if output.simulator:
            simulator_processes[run_id] = output.simulator
            state["_simulator_ports"] = output.ports

        state["step_8_status"] = output.status

        if output.status == "failed":
            state["step_8_error"] = output.error or "; ".join(output.validation_errors)
            if run_dir:
                import json
                err_file = Path(run_dir) / "step_8_error.json"
                err_file.write_text(json.dumps({
                    "error": state["step_8_error"],
                    "validation_errors": output.validation_errors,
                    "simulator_dir": output.config_dir,
                }, indent=2), encoding="utf-8")

        # Log sanity results
        if output.sanity:
            flow.flow("SIMULATOR_SETUP sanity: %s", output.sanity.summary)

        flow.flow("SIMULATOR_SETUP DONE — status=%s, ports=%s",
                   output.status, output.ports)

        return
        yield
