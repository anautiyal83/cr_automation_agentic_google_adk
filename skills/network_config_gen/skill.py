"""Network Configuration Generation — main skill orchestrator.

Generates an execution workflow YAML with runtime ${...} placeholders
that the cli-automation JAR resolves against CIQ JSON data at execution time.

Two entry points:
- ``generate(input)`` — first run from requirements
- ``regenerate(feedback)`` — correct after test failures

Stateful: retains context between calls.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from core.config import runtime_params

from . import prompts
from .autofix import clean_yaml_response, fix_workflow
from .validator import validate_workflow

logger = logging.getLogger(__name__)

LlmFn = Callable[[str, str], Awaitable[str]]

SELF_CORRECTION_ROUNDS = 2  # regenerate with validator errors as feedback


@dataclass
class SkillInput:
    """Input for network configuration generation."""
    uc_name: str
    requirements: dict = field(default_factory=dict)
    generation_hints: dict = field(default_factory=dict)
    schema_dir: str | None = None


@dataclass
class RegenerateFeedback:
    """Feedback from testing for regeneration."""
    test_results: list[dict] = field(default_factory=list)
    reviewer_analysis: str = ""
    attempt_number: int = 1


@dataclass
class SkillOutput:
    """Output from network configuration generation."""
    workflow_yaml: str = ""
    helper_scripts: dict[str, str] = field(default_factory=dict)  # filename → script content
    validation_errors: list[str] = field(default_factory=list)
    status: str = "pending"  # "passed" | "failed" | "pending"
    error: str = ""


def _rank(errors: list[str]) -> float:
    """Fewer errors is better, but YAML that does not parse is worse than any workflow."""
    if any(e.startswith("Invalid YAML syntax") for e in errors):
        return float("inf")
    return len(errors)


class NetworkConfigGenSkill:
    """Generates execution workflow YAML from requirements.

    The workflow uses ${...} placeholders for runtime values:
    - ${nodeGroups.<Node>.<field>} — node properties
    - ${configData.tables.<Sheet>.<Column>} — CIQ data
    - ${register.<varName>} — command output captures
    - ${env.<key>} — environment variables

    Stateful: retains context between generate() and regenerate() calls.
    """

    def __init__(self, llm_fn: LlmFn):
        self._llm_fn = llm_fn
        self._uc_name = ""
        self._requirements: dict = {}
        self._schema_summary = ""
        self._last_yaml = ""
        self._samples: list[dict] = []

    async def generate(self, inp: SkillInput) -> SkillOutput:
        """Generate workflow YAML from requirements.

        Deterministic validation errors (unresolvable placeholders, regexes that don't
        match the real outputs in ``doc_extracted.command_samples``, …) are fed back to
        the LLM for up to SELF_CORRECTION_ROUNDS rounds; the version with the fewest
        errors is kept.
        """
        self._uc_name = inp.uc_name
        self._requirements = inp.requirements
        self._samples = list((inp.requirements.get("doc_extracted") or {}).get("command_samples") or [])

        self._schema_summary = inp.generation_hints.get("step_6", "")
        if not self._schema_summary:
            self._schema_summary = self._discover_workflow_schema(inp.schema_dir)

        # Build prompt sections from requirements
        ctx = self._build_prompt_context()

        user_prompt = prompts.WORKFLOW_GEN_USER.format(
            uc_name=self._uc_name,
            nodes_summary=ctx["nodes"],
            commands_summary=ctx["commands"],
            orchestration_summary=ctx["orchestration"],
            ciq_summary=ctx["ciq"],
            mop_text=ctx["mop"],
            ssh_summary=ctx["ssh"],
            xml_summary=ctx["xml"],
            developer_answers=ctx["answers"],
            schema_summary=self._schema_summary,
            phases=ctx["phases_str"],
            runtime_params=ctx["runtime_params"],
        )

        logger.info("Generating workflow YAML via LLM (%d char prompt, %d real output samples)",
                    len(user_prompt), len(self._samples))
        try:
            raw = await self._llm_fn(prompts.WORKFLOW_GEN_SYSTEM, user_prompt)
            yaml_text = clean_yaml_response(raw)
            yaml_text = fix_workflow(yaml_text)
            errors = validate_workflow(yaml_text, self._samples)
            self._last_yaml = yaml_text
        except Exception as e:
            logger.error("Workflow generation failed: %s", e, exc_info=True)
            return SkillOutput(status="failed", error=f"Workflow generation failed: {e}")

        yaml_text, errors = await self._self_correct(yaml_text, errors)
        status = "failed" if errors else "passed"
        logger.info("Workflow generated: %d chars, %d errors", len(yaml_text), len(errors))

        return SkillOutput(
            workflow_yaml=yaml_text,
            validation_errors=errors,
            status=status,
        )

    async def regenerate(self, feedback: RegenerateFeedback) -> SkillOutput:
        """Regenerate workflow YAML using test failure feedback."""
        if not self._last_yaml:
            return SkillOutput(status="failed", error="No workflow context — call generate() first")

        ctx = self._build_prompt_context()
        test_failures_text = json.dumps(feedback.test_results, indent=2, default=str)[:15000]

        # The LLM must return the COMPLETE workflow, so it must see all of it
        # (workflows are often > 10 KB; a cut-off input produced a cut-off output)
        user_prompt = prompts.WORKFLOW_REGENERATE.format(
            current_yaml=self._last_yaml[:60000],
            test_failures=test_failures_text,
            reviewer_analysis=feedback.reviewer_analysis[:3000],
            nodes_summary=ctx["nodes"],
            phases=ctx["phases_str"],
            developer_answers=ctx["answers"],
            runtime_params=ctx["runtime_params"],
        )

        logger.info("Regenerating workflow YAML with feedback (%d char prompt)", len(user_prompt))
        try:
            raw = await self._llm_fn(prompts.WORKFLOW_GEN_SYSTEM, user_prompt)
            yaml_text = clean_yaml_response(raw)
            yaml_text = fix_workflow(yaml_text)
            errors = validate_workflow(yaml_text, self._samples)
            self._last_yaml = yaml_text
        except Exception as e:
            logger.error("Workflow regeneration failed: %s", e, exc_info=True)
            return SkillOutput(status="failed", error=f"Workflow regeneration failed: {e}")

        status = "failed" if errors else "passed"
        logger.info("Workflow regenerated: %d chars, %d errors", len(yaml_text), len(errors))

        return SkillOutput(
            workflow_yaml=yaml_text,
            validation_errors=errors,
            status=status,
        )

    async def _self_correct(self, yaml_text: str, errors: list[str]) -> tuple[str, list[str]]:
        """Feed deterministic validation errors back to the LLM; keep the best version."""
        best_yaml, best_errors = yaml_text, errors
        for attempt in range(1, SELF_CORRECTION_ROUNDS + 1):
            if not best_errors:
                break
            logger.info("Self-correction %d/%d: %d validation errors", attempt, SELF_CORRECTION_ROUNDS,
                        len(best_errors))
            self._last_yaml = best_yaml
            out = await self.regenerate(RegenerateFeedback(
                test_results=[{"validation_error": e} for e in best_errors],
                reviewer_analysis=(
                    "The workflow failed deterministic validation. Fix every listed problem; "
                    "where a real output is shown, write validation and register regexes that "
                    "match that output — test only the line that shows the condition, not the "
                    "whole output layout. Keep everything else unchanged."),
                attempt_number=attempt,
            ))
            if out.workflow_yaml and _rank(out.validation_errors) < _rank(best_errors):
                best_yaml, best_errors = out.workflow_yaml, out.validation_errors
        self._last_yaml = best_yaml
        return best_yaml, best_errors

    # ------------------------------------------------------------------
    # Prompt context builders
    # ------------------------------------------------------------------

    def _build_prompt_context(self) -> dict[str, str]:
        """Extract prompt sections from requirements."""
        req = self._requirements
        doc = req.get("doc_extracted", {})
        hints = req.get("generation_hints", {})

        # Nodes
        cat1 = req.get("category_1_node_activity", {})
        nodes = cat1.get("nodes", [])
        nodes_lines = []
        for n in nodes:
            if isinstance(n, dict):
                nodes_lines.append(
                    f"  {n.get('name', '?')}: type={n.get('type', 'remote')}, "
                    f"role={n.get('role', '?')}, user={n.get('ssh', {}).get('user', 'oamsys')}, "
                    f"prompt_regex={n.get('ssh', {}).get('prompt_regex', n.get('prompt_regex', '?'))}"
                )

        # Commands grouped by phase/purpose
        cat2 = req.get("category_2_commands", {})
        commands = cat2.get("commands", [])
        cmd_lines = []
        for c in commands:
            if isinstance(c, dict):
                cmd_lines.append(
                    f"  {c.get('cmd', '?')} on {c.get('node', '?')} "
                    f"[{c.get('purpose', '?')}] user={c.get('target_user', '?')} "
                    f"validation={c.get('validation_criteria', c.get('validation_rules', '?'))}"
                )

        # Orchestration
        cat3 = req.get("category_3_orchestration", {})
        phases = cat3.get("phases", [])
        orch_lines = [
            f"Phases: {', '.join(phases)}",
            f"Grouping: {cat3.get('grouping', {})}",
            f"NIAM failover: {cat3.get('niam_failover', False)}",
            f"SSH global: {cat3.get('ssh', {})}",
        ]

        # Phase overrides from hints
        wf_hints = hints.get("workflow_yaml", {})
        overrides = wf_hints.get("phase_overrides", [])
        if overrides:
            orch_lines.append(f"Phase overrides: {overrides}")

        # CIQ
        ciq_sheets = doc.get("ciq_sheets", {})
        ciq_lines = []
        for sname, sdata in ciq_sheets.items():
            cols = sdata.get("columns", sdata.get("headers", []))
            ciq_lines.append(f'  Sheet "{sname}": [{", ".join(cols[:15])}]')

        # Raw MOP (truncated)
        mop_text = wf_hints.get("raw_mop_text", "")
        if not mop_text:
            mop_text = doc.get("mop_commands", "")
            if isinstance(mop_text, list):
                mop_text = "\n".join(
                    f"-> {c.get('command', c) if isinstance(c, dict) else c}" for c in mop_text
                )

        # SSH
        ssh_prompts = doc.get("ssh_prompts", [])
        ssh_creds = doc.get("ssh_credentials", [])
        ssh_lines = []
        if ssh_prompts:
            ssh_lines.append(f"Prompts: {ssh_prompts[:5]}")
        if ssh_creds:
            ssh_lines.append(f"Credentials: {json.dumps(ssh_creds[:3], default=str)}")

        # XML
        xml_summary = wf_hints.get("xml_samples", "")
        if not xml_summary:
            xml_files = doc.get("xml_files", [])
            if xml_files:
                xml_summary = json.dumps(xml_files[:3], indent=2, default=str)[:2000]

        # Developer answers
        answers = req.get("developer_answers", {})
        answers_text = json.dumps(answers, indent=2, default=str) if answers else "None"

        return {
            "nodes": "\n".join(nodes_lines) or "No node definitions",
            "commands": "\n".join(cmd_lines) or "No commands",
            "orchestration": "\n".join(orch_lines),
            "ciq": "\n".join(ciq_lines) or "No CIQ data",
            "mop": str(mop_text)[:10000] or "No MOP data",
            "ssh": "\n".join(ssh_lines) or "No SSH data",
            "xml": str(xml_summary)[:5000] or "No XML samples",
            "answers": answers_text,
            "phases_str": ", ".join(phases) if phases else "preNodeHealthCheck, activity, postNodeHealthCheck",
            "runtime_params": ", ".join(f"${{{p}}}" for p in runtime_params()) or "none",
        }

    @staticmethod
    def _discover_workflow_schema(schema_dir: str | None = None) -> str:
        """Build workflow schema summary if not provided via generation_hints."""
        from pathlib import Path
        if schema_dir:
            schema_path = Path(schema_dir) / "cli-automation-workflow-schema_v1.2.yaml"
        else:
            schema_path = Path(__file__).resolve().parent.parent.parent / "schemas" / "cli-automation-workflow-schema_v1.2.yaml"

        if schema_path.exists():
            try:
                return schema_path.read_text(encoding="utf-8")[:6000]
            except Exception:
                pass
        return ""
