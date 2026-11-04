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
import re
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
EXTRA_ROUND_IF_IMPROVING = 1  # one more round while the error count still drops

# Validator error kinds, so a correction round sees what to work on (most specific first)
_INCOMPLETE = ("workflow has no", "no workflow step runs on", "looks cut short",
               "skip every step that needs", "builds the request uploaded to the node")
_ERROR_KINDS = [
    ("Incomplete workflow — parts of the MOP are missing", _INCOMPLETE),
    ("Regexes / expr vs the REAL output of the command", ("real output",)),
    ("su/exit prompt_regex vs the real prompt", ("prompt_regex",)),
    ("Variables a CIQ row never gets / unknown expr variables",
     ("registers it", "no step registers", "never registered", "always-empty value")),
    ("Placeholders the JAR cannot resolve", ("Unresolvable placeholder",)),
    ("CIQ columns some rows leave empty", ("sample rows",)),
    ("File transfers", ("LOCAL step", "sftp")),
]


def format_validation_errors(errors: list[str], limit: int = 24000) -> str:
    """Validator errors as a numbered plain-text list grouped by kind (not escaped JSON)."""
    groups: dict[str, list[str]] = {}
    for e in errors:
        kind = next((k for k, keys in _ERROR_KINDS if any(x in e for x in keys)), "Other")
        groups.setdefault(kind, []).append(e)
    lines, n = [], 0
    for kind, _ in _ERROR_KINDS + [("Other", ())]:
        if kind in groups:
            lines.append(f"## {kind} ({len(groups[kind])})")
            for e in groups[kind]:
                n += 1
                lines.append(f"{n}. {e}")
            lines.append("")
    return "\n".join(lines)[:limit]


def _flat(yaml_text: str) -> str:
    """The workflow as the LLM writes it (structure.py loops/added steps removed)."""
    from . import structure
    from .autofix import dump_yaml, parse_yaml
    parsed = parse_yaml(yaml_text)
    return dump_yaml(structure.flattened(parsed)) if parsed else yaml_text


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
    """Fewer errors is better, but YAML that does not parse is worse than any workflow and a
    workflow missing parts of the MOP is worse than a complete one with mistakes (G-6)."""
    if any(e.startswith("Invalid YAML syntax") for e in errors):
        return float("inf")
    return len(errors) + 1000 * sum(any(k in e for k in _INCOMPLETE) for e in errors)


def format_real_outputs(samples: list[dict], per_sample: int = 1200, total: int = 16000) -> str:
    """Recorded command outputs for the FIRST generation prompt (DEP-38): the LLM wrote
    regexes from the wording of the developer answers ("Updates Rejected: 0", "Apache
    status: running") and only saw the real layout in the correction rounds, which did not
    always fix them. One entry per distinct command, trimmed."""
    parts, used, seen = [], 0, set()
    for sample in samples or []:
        if not isinstance(sample, dict):
            continue
        cmd = str(sample.get("command") or "").strip()
        out = str(sample.get("output") or "").replace("\r\n", "\n").strip()
        if not cmd or not out or cmd in seen:
            continue
        seen.add(cmd)
        text = out if len(out) <= per_sample else out[:per_sample] + "\n[…]"
        block = f"$ {cmd}\n{text}\n"
        if used + len(block) > total:
            break
        parts.append(block)
        used += len(block)
    return "\n".join(parts) or "None recorded"


def _expected_coverage(requirements: dict) -> dict:
    """What the workflow must cover, from the requirements: phases and commands per node
    (LOCAL and NIAM helper entries are not nodes the workflow must log in to)."""
    phases = list((requirements.get("category_3_orchestration") or {}).get("phases") or [])
    # Nodes the workflow logs in to: the declared ones. A command step 1 could not attribute
    # ("unknown", MRF_ANNOUNCEMENT_LOADING) belongs to the only declared node, or to none —
    # a node named "unknown" can never have steps (G-15)
    declared = {str(n.get("name")) for n in (requirements.get("category_1_node_activity") or {}).get("nodes") or []
                if isinstance(n, dict) and n.get("name") and str(n.get("name")).upper() != "LOCAL"
                and not str(n.get("name")).lower().startswith("niam")}
    node_commands: dict[str, int] = {}
    for c in (requirements.get("category_2_commands") or {}).get("commands") or []:
        node = str(c.get("node") or "") if isinstance(c, dict) else ""
        if not node or node.upper() == "LOCAL" or node.lower().startswith("niam"):
            continue
        if declared and node not in declared:
            if len(declared) != 1:
                continue
            node = next(iter(declared))
        node_commands[node] = node_commands.get(node, 0) + 1
    return {"phases": phases, "node_commands": node_commands,
            "request_columns": _request_columns(requirements),
            "record_columns": _record_columns(requirements)}


def _record_columns(requirements: dict) -> dict:
    """{"sheet", "columns", "node_fields"} of the CIQ JSON as step 3's json-output builder writes
    it: record columns (``configSequences.N.records.M.data``) and node fields
    (``${<TYPE>.<field>}``, e.g. MRF.VER); {} when the CIQ layout is not recognised."""
    from skills.ciq_validation_json_gen.json_output_builder import build_json_output, node_fields
    from skills.ciq_validation_json_gen.rules_baseline import read_ciq_rows
    sheets = (requirements.get("doc_extracted") or {}).get("ciq_sheets") or {}
    try:
        tpl = build_json_output(sheets, read_ciq_rows("", sheets), requirements.get("uc_name") or "UC")
        recs = tpl["data"]["crGroups"]["nodeGroups"]["configSequences"]["records"]
    except (ValueError, KeyError, TypeError):
        return {}
    each = str(recs.get("_each", ""))
    sheet = each.split(" WHERE ")[0]
    link = each.split(" WHERE ", 1)[1].split("=")[0].strip() if " WHERE " in each else ""
    rows = read_ciq_rows("", sheets).get(sheet) or []
    # Columns every CIQ row fills: data the activity needs, so the workflow must use them
    filled = [c for c in (recs.get("data") or {}) if c != link and rows and all(r.get(c) for r in rows)]
    return {"sheet": sheet, "columns": list(recs.get("data") or {}), "link": link, "filled": filled,
            "samples": {c: [r.get(c, "") for r in rows] for c in (recs.get("data") or {})},
            "node_fields": node_fields(tpl)}


def _request_columns(requirements: dict) -> dict[str, str]:
    """CIQ columns whose sample value appears in a sample REQUEST document of the UC (the
    IMSI in the SPML modify request): the script building the request must be given them."""
    doc = requirements.get("doc_extracted") or {}
    requests = [str(x.get("content", "") if isinstance(x, dict) else x) for x in doc.get("xml_files") or []]
    requests = [t for t in requests if t and not re.search(r"Response\b", t)]
    found: dict[str, str] = {}
    for sheet in (doc.get("ciq_sheets") or {}).values():
        for row in (sheet.get("sample_rows") or []) if isinstance(sheet, dict) else []:
            for col, value in (row.items() if isinstance(row, dict) else []):
                text = str(value or "").strip()
                if len(text) >= 6 and any(ch.isdigit() for ch in text) and any(text in t for t in requests):
                    found.setdefault(str(col), text)
    return found


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
        self._ciq_sheets: dict = {}
        self._expected: dict = {}

    async def generate(self, inp: SkillInput) -> SkillOutput:
        """Generate workflow YAML from requirements.

        Deterministic validation errors (unresolvable placeholders, regexes that don't
        match the real outputs in ``doc_extracted.command_samples``, …) are fed back to
        the LLM for up to SELF_CORRECTION_ROUNDS rounds; the version with the fewest
        errors is kept.
        """
        self._uc_name = inp.uc_name
        self._requirements = inp.requirements
        doc = inp.requirements.get("doc_extracted") or {}
        # Real outputs of this run plus those learned from earlier runs (DEP-20)
        from tools.command_samples import with_library
        self._samples = with_library(list(doc.get("command_samples") or []))
        self._ciq_sheets = dict(doc.get("ciq_sheets") or {})
        self._expected = _expected_coverage(inp.requirements)

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
            real_outputs=format_real_outputs(self._samples),
            xml_summary=ctx["xml"],
            developer_answers=ctx["answers"],
            developer_notes=ctx["notes"],
            schema_summary=self._schema_summary,
            phases=ctx["phases_str"],
            runtime_params=ctx["runtime_params"],
        )

        logger.info("Generating workflow YAML via LLM (%d char prompt, %d real output samples)",
                    len(user_prompt), len(self._samples))
        try:
            raw = await self._llm_fn(prompts.WORKFLOW_GEN_SYSTEM, user_prompt)
            yaml_text = clean_yaml_response(raw)
            yaml_text = self._ciq_fixes(fix_workflow(yaml_text))
            errors = validate_workflow(yaml_text, self._samples, self._ciq_sheets, self._expected)
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
        if feedback.test_results and all(set(r) == {"validation_error"} for r in feedback.test_results
                                         if isinstance(r, dict)):
            test_failures_text = format_validation_errors([r["validation_error"] for r in feedback.test_results])
        else:
            test_failures_text = json.dumps(feedback.test_results, indent=2, default=str)[:15000]

        # The LLM must return the COMPLETE workflow, so it must see all of it
        # (workflows are often > 10 KB; a cut-off input produced a cut-off output)
        user_prompt = prompts.WORKFLOW_REGENERATE.format(
            current_yaml=_flat(self._last_yaml)[:60000],
            test_failures=test_failures_text,
            reviewer_analysis=feedback.reviewer_analysis[:3000],
            nodes_summary=ctx["nodes"],
            phases=ctx["phases_str"],
            developer_answers=ctx["answers"],
            developer_notes=ctx["notes"],
            runtime_params=ctx["runtime_params"],
        )

        logger.info("Regenerating workflow YAML with feedback (%d char prompt)", len(user_prompt))
        try:
            raw = await self._llm_fn(prompts.WORKFLOW_GEN_SYSTEM, user_prompt)
            yaml_text = clean_yaml_response(raw)
            yaml_text = self._ciq_fixes(fix_workflow(yaml_text))
            errors = validate_workflow(yaml_text, self._samples, self._ciq_sheets, self._expected)
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

    def _ciq_fixes(self, yaml_text: str) -> str:
        """Autofix steps that need the CIQ (the MOP's example directory → the CIQ path column),
        then the pipeline's structure: every CIQ record, backups on the repo server, transfer
        checksums (structure.py)."""
        from . import structure
        from .autofix import dump_yaml, parse_yaml, use_path_columns
        parsed = parse_yaml(yaml_text)
        if not parsed:
            return yaml_text
        changed = use_path_columns(parsed, (self._expected or {}).get("record_columns"))
        changed += structure.apply(parsed, self._requirements)
        return dump_yaml(parsed) if changed else yaml_text

    def _repair_against_samples(self, yaml_text: str, errors: list[str]) -> tuple[str, list[str]]:
        """Deterministic repairs against recorded real output (no LLM): widen line joins
        (DEP-22), shift miscounted capture columns (DEP-26) and set su/exit prompt_regexes to
        the recorded prompt (DEP-34). Kept only if errors drop."""
        if not self._samples or not any("real output" in e or "prompt_regex" in e for e in errors):
            return yaml_text, errors
        from .autofix import dump_yaml, parse_yaml
        from .validator import relax_line_joins, repair_capture_columns, repair_prompt_regexes
        parsed = parse_yaml(yaml_text)
        if not parsed:
            return yaml_text, errors
        changed = (relax_line_joins(parsed, self._samples) + repair_capture_columns(parsed, self._samples)
                   + repair_prompt_regexes(parsed, self._samples))
        if not changed:
            return yaml_text, errors
        repaired = dump_yaml(parsed)
        new_errors = validate_workflow(repaired, self._samples, self._ciq_sheets, self._expected)
        if _rank(new_errors) < _rank(errors):
            logger.info("Repaired %d regexes against real output: %d -> %d errors",
                        changed, len(errors), len(new_errors))
            self._last_yaml = repaired
            return repaired, new_errors
        return yaml_text, errors

    async def _self_correct(self, yaml_text: str, errors: list[str]) -> tuple[str, list[str]]:
        """Feed deterministic validation errors back to the LLM; keep the best version.

        Deterministic repairs run first and on every candidate, so the LLM only sees what
        they cannot fix. Rounds: SELF_CORRECTION_ROUNDS, plus EXTRA_ROUND_IF_IMPROVING while
        the last round still reduced the errors.
        """
        best_yaml, best_errors = self._repair_against_samples(yaml_text, errors)
        rounds = SELF_CORRECTION_ROUNDS
        attempt = 0
        while attempt < rounds and best_errors:
            attempt += 1
            logger.info("Self-correction %d/%d: %d validation errors", attempt, rounds, len(best_errors))
            self._last_yaml = best_yaml
            out = await self.regenerate(RegenerateFeedback(
                test_results=[{"validation_error": e} for e in best_errors],
                reviewer_analysis=(
                    "The workflow failed deterministic validation (list below, grouped by kind). Fix "
                    "EVERY listed problem; where a real output is shown, write the regex against it — "
                    "test only the line that shows the condition and count its columns. Keep everything "
                    "else unchanged."),
                attempt_number=attempt,
            ))
            if not out.workflow_yaml:
                continue
            cand_yaml, cand_errors = self._repair_against_samples(out.workflow_yaml, out.validation_errors)
            improved = _rank(cand_errors) < _rank(best_errors)
            if improved:
                best_yaml, best_errors = cand_yaml, cand_errors
            if attempt == rounds and improved and rounds < SELF_CORRECTION_ROUNDS + EXTRA_ROUND_IF_IMPROVING:
                rounds += 1
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
        rec = _record_columns(req)
        if rec.get("columns"):
            ciq_lines.append(f'  CIQ JSON records (${{configSequences.0.records.0.data.<Col>}}) come from sheet '
                             f'"{rec["sheet"]}" and hold ONLY: {", ".join(rec["columns"])} — use these per-row '
                             'values (paths, file names, parameters) instead of fixed values from the MOP')
        for ntype, fields in (rec.get("node_fields") or {}).items():
            ciq_lines.append(f'  Node values of the current {ntype} node: '
                             + ", ".join(f"${{{ntype}.{f}}}" for f in fields)
                             + " (use them for node-specific decisions, e.g. by version inside a case)")

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
        asked = req.get("developer_questions") or {}
        if answers and asked:  # question + answer: an answer alone often makes no sense
            answers_text = "\n".join(f"- Q: {asked[k]}\n  A: {v}" if k in asked else f"- {k}: {v}"
                                     for k, v in answers.items())
        else:
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
            "notes": str(req.get("developer_notes") or "").strip()[:6000] or "None",
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
