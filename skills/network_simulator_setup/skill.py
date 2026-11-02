"""Network Simulator Setup — main skill orchestrator.

Four phases:
1. Topology — one MockWire instance per remote workflow node, with prompts, users,
   NIAM neIds and `su` escalation built deterministically (topology.py)
2. Command-response mapping — UC command outputs per node (LLM, one call per node)
3. Start simulator — write per-run configs, launch one MockWire process per node
4. Sanity testing — verify each node: SSH connect, prompt, basic cmd, escalation, UC cmd

Two entry points:
- ``setup(input)`` — runs all 4 phases
- ``regenerate(feedback)`` — re-runs setup() with execution test feedback, restarting
  the simulator with the corrected responses

Call ``output.simulator.stop()`` when testing is complete to shut down the simulator.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field, replace
from typing import Awaitable, Callable

from . import prompts
from .autofix import parse_llm_yaml, salvage_yaml_entries
from .simulator_runner import (
    BASE_PORT,
    DEFAULT_TTL_MINUTES,
    STARTUP_TIMEOUT,
    SimulatorInstance,
    SimulatorProcess,
    SimulatorStartError,
    allocate_ports,
    prepare_simulator_dir,
    start_simulator,
    write_instance_files,
)
from .sanity_check import SanityResult, run_sanity_checks
from .topology import NodeSpec, build_instance_config, build_topology
from .report import generate_simulator_report
from .validator import validate_mockwire_config

logger = logging.getLogger(__name__)

LlmFn = Callable[[str, str], Awaitable[str]]

LLM_ATTEMPTS = 2  # per node, when the response is not valid YAML


@dataclass
class SkillInput:
    """Input for network simulator setup."""
    uc_name: str
    workflow_yaml: str = ""
    requirements: dict = field(default_factory=dict)
    generation_hints: dict = field(default_factory=dict)
    ciq_json_files: list[str] = field(default_factory=list)  # CIQ JSON data (niamIDs)
    feedback: str = ""  # execution test failures to fix (retry from step 9)
    run_dir: str = ""
    mockwire_jar: str = "lib/mockwire/mockwire-simulator/mockwire.jar"
    java_home: str | None = None
    auto_start: bool = True  # if False, skip phase 3 (just generate configs)
    host: str = "localhost"
    base_port: int = BASE_PORT
    ttl_minutes: int = DEFAULT_TTL_MINUTES
    startup_timeout: int = STARTUP_TIMEOUT


@dataclass
class RegenerateFeedback:
    """Feedback from execution testing for regeneration."""
    test_results: list[dict] = field(default_factory=list)
    reviewer_analysis: str = ""
    attempt_number: int = 1


@dataclass
class SkillOutput:
    """Output from network simulator setup."""
    simulator_config_yaml: str = ""  # all instance configs, for review
    instance_configs: dict[str, str] = field(default_factory=dict)  # instance → config yaml
    response_configs: dict[str, str] = field(default_factory=dict)  # filename → content
    topology: list[dict] = field(default_factory=list)  # NodeSpec summaries
    config_dir: str = ""  # <run_dir>/simulator
    manifest_path: str = ""
    simulator: SimulatorProcess | None = None  # call .stop() when done
    ports: list[int] = field(default_factory=list)
    sanity: SanityResult | None = None  # phase 4 results
    validation_errors: list[str] = field(default_factory=list)
    report_html: str = ""  # step 8 milestone report
    status: str = "pending"  # "passed" | "failed" | "pending"
    error: str = ""


class NetworkSimulatorSetupSkill:
    """Builds per-node MockWire configs and optionally starts the simulator.

    Stateful: retains the last input so regenerate() can rebuild with feedback.
    """

    def __init__(self, llm_fn: LlmFn):
        self._llm_fn = llm_fn
        self._last_input: SkillInput | None = None
        self._raw_llm_outputs: dict[str, str] = {}  # node → last raw LLM answer

    async def setup(self, inp: SkillInput) -> SkillOutput:
        """Run all phases: topology → responses → write + start → sanity."""
        self._last_input = inp
        if not inp.workflow_yaml:
            return SkillOutput(status="failed", error="No workflow YAML provided")

        # --- Phase 1: Topology (deterministic) ---
        nodes = build_topology(inp.workflow_yaml, inp.requirements, inp.ciq_json_files)
        if not nodes:
            return SkillOutput(status="failed", error="Workflow has no remote nodes to simulate")
        topology = [n.summary() for n in nodes]

        # --- Phase 2: Command responses per node (LLM) ---
        hints = str(inp.generation_hints.get("step_8", "") or "")
        results = await asyncio.gather(
            *(self._generate_node_responses(inp.uc_name, n, hints, inp.feedback) for n in nodes),
            return_exceptions=True,
        )
        errors: list[str] = []
        instance_configs: dict[str, str] = {}
        for node, result in zip(nodes, results):
            if isinstance(result, Exception):
                logger.error("Response generation failed for %s: %s", node.name, result)
                errors.append(f"{node.name}: response generation failed: {result}")
                result = {}
            instance_configs[node.instance] = build_instance_config(node, result)
        review_yaml = _review_yaml(nodes, instance_configs)
        # A node without responses fails validation below; the report still gets written

        # --- Phase 3: Write files and start one MockWire per node ---
        config_dir = ""
        manifest_path = ""
        ports: list[int] = []
        simulator = None
        sanity = None

        if inp.run_dir:
            try:
                sim_dir = prepare_simulator_dir(inp.run_dir, inp.mockwire_jar)
            except SimulatorStartError as e:
                return SkillOutput(simulator_config_yaml=review_yaml, instance_configs=instance_configs,
                                   topology=topology, status="failed", error=str(e))
            config_dir = str(sim_dir)
            for node_name, raw in self._raw_llm_outputs.items():
                (sim_dir / "logs" / f"llm_{node_name}_raw.txt").write_text(raw, encoding="utf-8")
            for node in nodes:
                write_instance_files(sim_dir, node.instance, instance_configs[node.instance])
                errors += [f"{node.instance}: {e}" for e in validate_mockwire_config(
                    instance_configs[node.instance], sim_dir / "outputs" / node.instance / "responses")]

            if inp.auto_start and errors:
                logger.error("Skipping simulator start — config invalid: %s", errors)
                errors.append("Simulator not started: config validation failed")

            elif inp.auto_start:
                try:
                    node_ports = allocate_ports(len(nodes), inp.base_port)
                    instances = [SimulatorInstance(name=n.instance, port=p, nodes=[n.name])
                                 for n, p in zip(nodes, node_ports)]
                    simulator = await start_simulator(
                        sim_dir, instances,
                        java_home=inp.java_home,
                        timeout=inp.startup_timeout,
                        ttl_minutes=inp.ttl_minutes,
                        host=inp.host,
                    )
                    ports = simulator.ports
                    manifest_path = str(sim_dir / "manifest.json")
                    logger.info("Simulator started: %s", ", ".join(
                        f"{i.name}:{i.port}" for i in simulator.instances))
                except SimulatorStartError as e:
                    logger.error("Phase 3 (simulator start) failed: %s", e)
                    errors.append(f"Simulator start failed: {e}")
                except Exception as e:
                    logger.error("Phase 3 (simulator start) failed: %s", e, exc_info=True)
                    errors.append(f"Simulator start failed: {e}")

                # --- Phase 4: Sanity testing ---
                if simulator and simulator.is_running:
                    try:
                        sanity = await run_sanity_checks(nodes, simulator, host=inp.host)
                        if not sanity.all_passed:
                            errors.append(f"Sanity failed: {sanity.summary}")
                            logger.warning("Phase 4 sanity failed: %s", sanity.summary)
                        else:
                            logger.info("Phase 4 sanity passed: %s", sanity.summary)
                    except Exception as e:
                        logger.error("Phase 4 (sanity check) failed: %s", e, exc_info=True)
                        errors.append(f"Sanity check error: {e}")

        return SkillOutput(
            simulator_config_yaml=review_yaml,
            instance_configs=instance_configs,
            topology=topology,
            config_dir=config_dir,
            manifest_path=manifest_path,
            simulator=simulator,
            ports=ports,
            sanity=sanity,
            validation_errors=errors,
            report_html=generate_simulator_report(
                inp.uc_name, nodes, simulator, sanity, errors, config_dir),
            status="failed" if errors else "passed",
        )

    async def regenerate(self, feedback: RegenerateFeedback) -> SkillOutput:
        """Rebuild and restart the simulator using execution test feedback."""
        if not self._last_input:
            return SkillOutput(status="failed", error="No simulator context — call setup() first")
        text = json.dumps(feedback.test_results, indent=2, default=str)[:5000]
        if feedback.reviewer_analysis:
            text += "\n\nReviewer analysis:\n" + feedback.reviewer_analysis[:3000]
        return await self.setup(replace(self._last_input, feedback=text))

    # ------------------------------------------------------------------
    # Phase 2: Command responses
    # ------------------------------------------------------------------

    async def _generate_node_responses(
        self, uc_name: str, node: NodeSpec, hints: str, feedback: str,
    ) -> dict:
        """Ask the LLM for command → response entries for one node."""
        commands = [c for c in node.commands if str(c.get("cmd", "")).strip()]
        if not commands:
            return {}
        lines = []
        for c in commands:
            val = c.get("validation") or {}
            desc = val.get("description", "") if isinstance(val, dict) else ""
            crit = ((val.get("success") or {}).get("criteria") or {}) if isinstance(val, dict) else {}
            regex = crit.get("regex", "") if isinstance(crit, dict) else ""
            lines.append(f"- {c.get('phase', '')} | {c['cmd']} | {desc}"
                         + (f" (regex: {regex})" if regex else ""))

        user_prompt = prompts.NODE_RESPONSES_USER.format(
            uc_name=uc_name,
            node=node.name,
            prompt=node.prompt,
            commands="\n".join(lines),
            hints=hints or "(none)",
            feedback=prompts.FEEDBACK_SECTION.format(feedback=feedback) if feedback else "",
        )
        logger.info("Phase 2: Generating responses for %s (%d commands)", node.name, len(commands))
        parsed = None
        for attempt in range(1, LLM_ATTEMPTS + 1):
            raw = await self._llm_fn(prompts.NODE_RESPONSES_SYSTEM, user_prompt)
            self._raw_llm_outputs[node.name] = raw
            parsed = parse_llm_yaml(raw)
            if isinstance(parsed, dict):
                break
            logger.warning("Phase 2: %s — invalid YAML from LLM (attempt %d/%d)",
                           node.name, attempt, LLM_ATTEMPTS)
            parsed = salvage_yaml_entries(raw) or None
            if parsed:
                break
            user_prompt += prompts.INVALID_YAML_RETRY
        if not isinstance(parsed, dict):
            raise ValueError("LLM did not return a YAML map of command responses "
                             f"(raw output in simulator/logs/llm_{node.name}_raw.txt)")

        entries = {}
        for key, val in parsed.items():
            if isinstance(val, dict) and isinstance(val.get("lines"), str):
                val = {**val, "lines": val["lines"].splitlines()}
            if isinstance(val, dict) and ("lines" in val or "file" in val):
                entries[str(key)] = dict(val)
        logger.info("Phase 2: %s — %d responses for %d commands", node.name, len(entries), len(commands))
        return entries


def _review_yaml(nodes: list[NodeSpec], instance_configs: dict[str, str]) -> str:
    """All instance configs as one multi-document YAML for milestone review."""
    parts = []
    for n in nodes:
        parts.append(f"# ===== Instance {n.instance} — node {n.name} =====\n"
                     f"{instance_configs.get(n.instance, '').rstrip()}\n")
    return "---\n".join(parts)
