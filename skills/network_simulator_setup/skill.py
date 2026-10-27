"""Network Simulator Setup — main skill orchestrator.

Four phases:
1. Config generation — MockWire server config (hosts, ports, users, prompts)
2. Command-response mapping — UC-specific command → output configs per node
3. Start simulator — launch MockWire JAR, health-check, return process handle
4. Sanity testing — verify each node: SSH connect, prompt, basic cmd, escalation, UC cmd

Two entry points:
- ``setup(input)`` — runs all 4 phases: generate → write → start → sanity test
- ``regenerate(feedback)`` — regenerate configs after execution test failures (phases 1+2 only)

Call ``output.simulator.stop()`` when testing is complete to shut down the simulator.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from . import prompts
from .autofix import (
    clean_yaml_response,
    fix_simulator_config,
    extract_workflow_nodes,
    extract_workflow_commands,
    parse_yaml,
)
from .simulator_runner import (
    SimulatorProcess,
    start_simulator,
    write_config_files,
)
from .sanity_check import SanityResult, run_sanity_checks

logger = logging.getLogger(__name__)

LlmFn = Callable[[str, str], Awaitable[str]]


@dataclass
class SkillInput:
    """Input for network simulator setup."""
    uc_name: str
    workflow_yaml: str = ""
    requirements: dict = field(default_factory=dict)
    generation_hints: dict = field(default_factory=dict)
    run_dir: str = ""
    mockwire_jar: str = "lib/mockwire/mockwire-simulator/mockwire.jar"
    java_home: str | None = None
    schema_dir: str | None = None
    auto_start: bool = True  # if False, skip phase 3 (just generate configs)


@dataclass
class RegenerateFeedback:
    """Feedback from execution testing for regeneration."""
    test_results: list[dict] = field(default_factory=list)
    reviewer_analysis: str = ""
    attempt_number: int = 1


@dataclass
class SkillOutput:
    """Output from network simulator setup."""
    simulator_config_yaml: str = ""
    response_configs: dict[str, str] = field(default_factory=dict)  # filename → yaml content
    config_dir: str = ""
    simulator: SimulatorProcess | None = None  # call .stop() when done
    ports: list[int] = field(default_factory=list)
    sanity: SanityResult | None = None  # phase 4 results
    validation_errors: list[str] = field(default_factory=list)
    status: str = "pending"  # "passed" | "failed" | "pending"
    error: str = ""


class NetworkSimulatorSetupSkill:
    """Generates MockWire configs and optionally starts the simulator.

    Phase 1: Generate simulator server config (LLM)
    Phase 2: Generate UC-specific command-response mappings (LLM)
    Phase 3: Write files to disk and start MockWire JAR (deterministic)

    Stateful: retains context between setup() and regenerate() calls.
    """

    def __init__(self, llm_fn: LlmFn):
        self._llm_fn = llm_fn
        self._uc_name = ""
        self._workflow_yaml = ""
        self._requirements: dict = {}
        self._workflow_nodes: list[str] = []
        self._schema_summary = ""
        self._last_config_yaml = ""
        self._last_response_configs: dict[str, str] = {}

    async def setup(self, inp: SkillInput) -> SkillOutput:
        """Run all 3 phases: generate configs → write to disk → start simulator."""
        self._uc_name = inp.uc_name
        self._workflow_yaml = inp.workflow_yaml
        self._requirements = inp.requirements

        if not inp.workflow_yaml:
            return SkillOutput(status="failed", error="No workflow YAML provided")

        # Extract nodes and commands from workflow
        self._workflow_nodes = extract_workflow_nodes(inp.workflow_yaml)
        workflow_commands = extract_workflow_commands(inp.workflow_yaml)

        self._schema_summary = inp.generation_hints.get("step_8", "")
        if not self._schema_summary:
            self._schema_summary = self._discover_simulator_schema(inp.schema_dir)

        # --- Phase 1: Generate simulator server config ---
        ctx = self._build_prompt_context(workflow_commands)
        try:
            config_yaml = await self._generate_config(ctx)
        except Exception as e:
            logger.error("Phase 1 (config generation) failed: %s", e, exc_info=True)
            return SkillOutput(status="failed", error=f"Config generation failed: {e}")

        # --- Phase 2: Generate command-response mappings ---
        try:
            response_configs = await self._generate_responses(ctx, workflow_commands)
        except Exception as e:
            logger.error("Phase 2 (response generation) failed: %s", e, exc_info=True)
            # Still return config even if responses fail
            return SkillOutput(
                simulator_config_yaml=config_yaml,
                status="failed",
                error=f"Response generation failed: {e}",
            )

        self._last_config_yaml = config_yaml
        self._last_response_configs = response_configs

        # Extract ports for health checking
        ports = self._extract_ports(config_yaml)

        # Validate
        errors = self._validate(config_yaml)

        # --- Phase 3: Write files and start simulator ---
        config_dir = ""
        simulator = None
        sanity = None

        if inp.run_dir:
            config_dir = write_config_files(
                inp.run_dir, inp.uc_name, config_yaml, response_configs
            )

            if inp.auto_start:
                try:
                    simulator = await start_simulator(
                        mockwire_jar=inp.mockwire_jar,
                        config_dir=config_dir,
                        java_home=inp.java_home,
                        ports=ports,
                    )
                    logger.info("Simulator started: %d nodes on ports %s", len(ports), ports)
                except Exception as e:
                    logger.error("Phase 3 (simulator start) failed: %s", e, exc_info=True)
                    errors.append(f"Simulator start failed: {e}")

                # --- Phase 4: Sanity testing ---
                if simulator and simulator.is_running:
                    try:
                        cat1 = self._requirements.get("category_1_node_activity", {})
                        node_configs = cat1.get("nodes", [])
                        sanity = await run_sanity_checks(
                            ports=ports,
                            node_configs=node_configs,
                            workflow_commands=workflow_commands,
                        )
                        if not sanity.all_passed:
                            errors.append(f"Sanity failed: {sanity.summary}")
                            logger.warning("Phase 4 sanity failed: %s", sanity.summary)
                        else:
                            logger.info("Phase 4 sanity passed: %s", sanity.summary)
                    except Exception as e:
                        logger.error("Phase 4 (sanity check) failed: %s", e, exc_info=True)
                        errors.append(f"Sanity check error: {e}")

        status = "failed" if errors else "passed"
        return SkillOutput(
            simulator_config_yaml=config_yaml,
            response_configs=response_configs,
            config_dir=config_dir,
            simulator=simulator,
            ports=ports,
            sanity=sanity,
            validation_errors=errors,
            status=status,
        )

    async def regenerate(self, feedback: RegenerateFeedback) -> SkillOutput:
        """Regenerate configs using execution test feedback (phases 1+2 only, no restart)."""
        if not self._last_config_yaml:
            return SkillOutput(status="failed", error="No simulator context — call setup() first")

        workflow_commands = extract_workflow_commands(self._workflow_yaml)
        ctx = self._build_prompt_context(workflow_commands)
        test_failures_text = json.dumps(feedback.test_results, indent=2, default=str)[:5000]

        user_prompt = prompts.SIMULATOR_REGENERATE.format(
            current_yaml=self._last_config_yaml[:8000],
            test_failures=test_failures_text,
            reviewer_analysis=feedback.reviewer_analysis[:3000],
            workflow_summary=ctx["workflow"],
        )

        try:
            raw = await self._llm_fn(prompts.SIMULATOR_GEN_SYSTEM, user_prompt)
            config_yaml = clean_yaml_response(raw)
            config_yaml = fix_simulator_config(config_yaml, self._workflow_nodes)
            self._last_config_yaml = config_yaml
        except Exception as e:
            logger.error("Simulator config regeneration failed: %s", e, exc_info=True)
            return SkillOutput(status="failed", error=f"Regeneration failed: {e}")

        errors = self._validate(config_yaml)
        ports = self._extract_ports(config_yaml)

        return SkillOutput(
            simulator_config_yaml=config_yaml,
            response_configs=self._last_response_configs,
            ports=ports,
            validation_errors=errors,
            status="failed" if errors else "passed",
        )

    # ------------------------------------------------------------------
    # Phase 1: Config generation
    # ------------------------------------------------------------------

    async def _generate_config(self, ctx: dict[str, str]) -> str:
        """Phase 1: Generate simulator server config via LLM."""
        user_prompt = prompts.SIMULATOR_GEN_USER.format(
            uc_name=self._uc_name,
            workflow_summary=ctx["workflow"],
            nodes_summary=ctx["nodes"],
            requirements_summary=ctx["requirements"],
            schema_summary=self._schema_summary,
        )

        logger.info("Phase 1: Generating simulator config (%d char prompt)", len(user_prompt))
        raw = await self._llm_fn(prompts.SIMULATOR_GEN_SYSTEM, user_prompt)
        yaml_text = clean_yaml_response(raw)
        yaml_text = fix_simulator_config(yaml_text, self._workflow_nodes)
        return yaml_text

    # ------------------------------------------------------------------
    # Phase 2: Command-response mapping
    # ------------------------------------------------------------------

    async def _generate_responses(
        self, ctx: dict[str, str], workflow_commands: list[dict]
    ) -> dict[str, str]:
        """Phase 2: Generate UC-specific command-response configs.

        Returns dict of {filename: yaml_content} for response files per node.
        Currently generates responses as part of the main config.
        Can be extended to produce separate per-node response files.
        """
        # For now, responses are embedded in the main config YAML
        # generated in phase 1. This method is a hook for future
        # separation into per-node response files.
        #
        # When MockWire supports external response directories:
        # - Generate per-node response YAML files
        # - Return {"RDS_responses.yaml": "...", "PGW_responses.yaml": "..."}
        return {}

    # ------------------------------------------------------------------
    # Prompt context builders
    # ------------------------------------------------------------------

    def _build_prompt_context(self, workflow_commands: list[dict]) -> dict[str, str]:
        """Build prompt sections from workflow and requirements."""
        wf_lines = [f"Nodes: {', '.join(self._workflow_nodes)}"]
        by_phase: dict[str, list[str]] = {}
        for c in workflow_commands:
            phase = c.get("phase", "unknown")
            cmd = c.get("cmd", "?")
            node = c.get("node", "?")
            val = c.get("validation", {})
            val_desc = ""
            if isinstance(val, dict):
                val_desc = val.get("description", val.get("criteria", {}).get("expr", ""))
            by_phase.setdefault(phase, []).append(f"  {cmd} on {node} — expects: {val_desc[:80]}")
        for phase, cmds in by_phase.items():
            wf_lines.append(f"\nPhase: {phase}")
            wf_lines.extend(cmds)

        cat1 = self._requirements.get("category_1_node_activity", {})
        nodes = cat1.get("nodes", [])
        node_lines = []
        for n in nodes:
            if isinstance(n, dict):
                node_lines.append(
                    f"  {n.get('name', '?')}: user={n.get('ssh', {}).get('user', 'oamsys')}, "
                    f"prompt={n.get('ssh', {}).get('prompt_regex', n.get('prompt_regex', '?'))}, "
                    f"escalation={n.get('escalation', {}).get('method', 'none')}"
                )

        cat2 = self._requirements.get("category_2_commands", {})
        commands = cat2.get("commands", [])
        req_lines = [f"Total commands: {len(commands)}"]
        ldap = cat2.get("ldap", {})
        if ldap:
            req_lines.append(f"LDAP: host={ldap.get('host')}, port={ldap.get('port')}")

        return {
            "workflow": "\n".join(wf_lines),
            "nodes": "\n".join(node_lines) or "No node details",
            "requirements": "\n".join(req_lines),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _validate(self, yaml_text: str) -> list[str]:
        """Basic structural validation."""
        errors = []
        parsed = parse_yaml(yaml_text)
        if parsed is None:
            return ["Invalid YAML syntax"]

        has_servers = any(k in parsed for k in ("servers", "nodes", "simulator", "ssh_servers"))
        if not has_servers:
            errors.append("No server definitions found")
        return errors

    @staticmethod
    def _extract_ports(yaml_text: str) -> list[int]:
        """Extract configured ports from simulator config YAML."""
        parsed = parse_yaml(yaml_text)
        if not parsed:
            return []

        ports = []
        for key in ("servers", "nodes", "simulator", "ssh_servers"):
            val = parsed.get(key)
            if isinstance(val, list):
                for item in val:
                    if isinstance(item, dict) and "port" in item:
                        try:
                            ports.append(int(item["port"]))
                        except (ValueError, TypeError):
                            pass
        return sorted(set(ports))

    @staticmethod
    def _discover_simulator_schema(schema_dir: str | None = None) -> str:
        """Read simulator config schema if available."""
        from pathlib import Path
        if schema_dir:
            schema_path = Path(schema_dir) / "simulator_config.schema.yaml"
        else:
            schema_path = Path(__file__).resolve().parent.parent.parent / "schemas" / "simulator_config.schema.yaml"
        if schema_path.exists():
            try:
                return schema_path.read_text(encoding="utf-8")[:4000]
            except Exception:
                pass
        return ""
