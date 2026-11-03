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
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
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
from .overlay import build_overlay
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
    overlay_workflow_path: str = ""  # <run_dir>/simulator/<uc>.yaml — workflow pointed at the instances
    simulator: SimulatorProcess | None = None  # call .stop() when done
    ports: list[int] = field(default_factory=list)
    sanity: SanityResult | None = None  # phase 4 results
    validation_errors: list[str] = field(default_factory=list)
    report_html: str = ""  # step 8 milestone report
    status: str = "pending"  # "passed" | "failed" | "pending"
    error: str = ""


def _sample_on(sample: dict, node: str) -> bool:
    """A recorded sample of this node (samples without a node count for every node)."""
    where = str(sample.get("node") or "")
    return not where or where.upper() == node.upper()


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

        # Stateful file flows (upload → produced file) via MockWire's VFS + triggers (S8-16)
        from io import StringIO
        from ruamel.yaml import YAML
        from .file_flows import derive_vfs, response_lines
        try:
            workflow_doc = YAML(typ="safe").load(StringIO(inp.workflow_yaml)) or {}
        except Exception:
            workflow_doc = {}
        from tools.command_samples import with_library
        samples = with_library(list((inp.requirements.get("doc_extracted") or {}).get("command_samples") or []))
        produced = response_lines(inp.requirements)
        for n in nodes:
            n.vfs = (derive_vfs(workflow_doc, n.name, produced, [x for x in samples if _sample_on(x, n.name)])
                     if isinstance(workflow_doc, dict) else None)
            if n.vfs:
                logger.info("Topology node %s: vfs dirs=%s triggers=%d", n.name, n.vfs["dirs"],
                            len(n.vfs.get("triggers", [])))

        # --- Phase 2: Command responses per node ---
        # Real outputs recorded on the nodes are served verbatim; the LLM only invents
        # responses for commands nobody recorded
        recorded = {n.name: recorded_responses(n.commands, samples) for n in nodes}
        # Commands answered from real output, incl. placeholder commands as templates (S8-21)
        covered: dict[str, set[str]] = {}
        for n in nodes:
            covered[n.name] = set(recorded[n.name])
            for cmd, (key, entry) in templated_responses(n.commands, samples).items():
                recorded[n.name][key] = entry
                covered[n.name].add(cmd)
        hints = str(inp.generation_hints.get("step_8", "") or "")
        results = await asyncio.gather(
            *(self._generate_node_responses(inp.uc_name, n, hints, inp.feedback,
                                            skip=covered[n.name]) for n in nodes),
            return_exceptions=True,
        )
        errors: list[str] = []
        instance_configs: dict[str, str] = {}
        row = _first_row_values(inp.requirements)
        for node, result in zip(nodes, results):
            if isinstance(result, Exception):
                logger.error("Response generation failed for %s: %s", node.name, result)
                errors.append(f"{node.name}: response generation failed: {result}")
                result = {}
            # File commands on VFS paths are answered by MockWire builtins from file state
            from .file_flows import is_vfs_command
            dropped = [k for k in result if is_vfs_command(k, node.vfs)]
            for k in dropped:
                result.pop(k)
            recorded[node.name] = {k: v for k, v in recorded[node.name].items()
                                   if not is_vfs_command(k, node.vfs)}
            if dropped:
                logger.info("Phase 2: %s — %d file commands left to the VFS builtins", node.name, len(dropped))
            # Invented responses stand for a healthy node: they must pass the step's own
            # success check (recorded real output is the ground truth and is never changed)
            result = await self._conform_synthetic(inp.uc_name, node, result, covered[node.name], row)
            if recorded[node.name]:
                logger.info("Phase 2: %s — %d responses from recorded real output",
                            node.name, len(recorded[node.name]))
            merged = merge_responses(recorded[node.name], result, node.commands, covered[node.name], row)
            # Bash-guarded commands: evaluated per CIQ target, ahead of the generic keys (S8-29)
            from .guards import guarded_responses
            merged = {**guarded_responses(node.commands, merged, inp.ciq_json_files), **merged}
            instance_configs[node.instance] = build_instance_config(node, merged)
        review_yaml = _review_yaml(nodes, instance_configs)
        # A node without responses fails validation below; the report still gets written

        # --- Phase 3: Write files and start one MockWire per node ---
        config_dir = ""
        manifest_path = ""
        overlay_path = ""
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
                        mockwire_jar=inp.mockwire_jar,
                        java_home=inp.java_home,
                        timeout=inp.startup_timeout,
                        ttl_minutes=inp.ttl_minutes,
                        host=inp.host,
                    )
                    ports = simulator.ports
                    manifest_path = str(sim_dir / "manifest.json")
                    # Step 9 executes this copy of the workflow against the instances
                    overlay_path = str(sim_dir / f"{inp.uc_name}.yaml")
                    Path(overlay_path).write_text(build_overlay(
                        inp.workflow_yaml, nodes, {n.name: simulator.port_for(n.name) for n in nodes},
                        host=inp.host), encoding="utf-8")
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
            overlay_workflow_path=overlay_path,
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

    async def _conform_synthetic(self, uc_name: str, node: NodeSpec, entries: dict,
                                 recorded: set[str], row: dict) -> dict:
        """Regenerate invented responses the step's success regex rejects (one round)."""
        from .topology import _validation_regex
        seen: dict[str, set[str]] = {}
        for c in node.commands:
            seen.setdefault(str(c.get("cmd", "")).strip(), set()).add(_validation_regex(c))
        for cmd, regexes in seen.items():
            if len(regexes) > 1 and cmd not in recorded:
                node.warnings.append(f"{cmd!r} is checked with {len(regexes)} different expectations "
                                     "(state changes during the run) — a static response cannot pass all (S8-16)")
        entries = rekey_unanswered(node, entries, recorded, row)
        bad = synthetic_mismatches(node, entries, recorded, row)
        missing = unanswered_commands(node, entries, recorded, row)
        if not bad and not missing:
            return entries
        logger.info("Phase 2: %s — %d invented responses fail their step's success check, %d commands "
                    "have no response; regenerating", node.name, len(bad), len(missing))
        listing = "\n".join(f"- key {b['key']!r} (command: {b['cmd']})\n  must match: /{b['regex']}/\n"
                            f"  your output was: {b['output'][:300]!r}" for b in bad)
        if missing:
            listing += ("\n\nThese commands have NO response yet — add one each (a 'regex:' key matching "
                        "the sent command, with the shown value as an example):\n"
                        + "\n".join(f"- {m['cmd']}  (sent e.g. as: {m['resolved']})"
                                    + (f"\n  must match: /{m['regex']}/" if m["regex"] else "")
                                    for m in missing))
        user = prompts.CONFORM_USER.format(node=node.name, uc_name=uc_name, listing=listing)
        try:
            fixed = parse_llm_yaml(await self._llm_fn(prompts.NODE_RESPONSES_SYSTEM, user))
        except Exception as e:
            logger.warning("Phase 2: %s — conformance regeneration failed: %s", node.name, e)
            fixed = None
        from .file_flows import is_vfs_command
        from .topology import RESERVED_KEYS, _is_builtin_key
        entries = dict(entries)
        for key, new in (fixed.items() if isinstance(fixed, dict) else []):
            key = str(key)
            if key in RESERVED_KEYS or _is_builtin_key(key) or is_vfs_command(key, node.vfs) or key in recorded:
                continue
            if isinstance(new, dict) and isinstance(new.get("lines"), str):
                new = {**new, "lines": new["lines"].splitlines()}
            if isinstance(new, dict) and isinstance(new.get("lines"), list):
                entries[key] = new
        entries = rekey_unanswered(node, entries, recorded, row)
        entries = fill_unanswered(node, entries, recorded, row)
        entries = repair_literal_mismatches(node, entries, recorded, row)
        for b in synthetic_mismatches(node, entries, recorded, row):
            node.warnings.append(f"invented response for {b['cmd']!r} still fails /{b['regex']}/")
        for m in unanswered_commands(node, entries, recorded, row):
            node.warnings.append(f"no response for {m['cmd']!r}")
        return entries

    # ------------------------------------------------------------------
    # Phase 2: Command responses
    # ------------------------------------------------------------------

    async def _generate_node_responses(
        self, uc_name: str, node: NodeSpec, hints: str, feedback: str, skip: set[str] = frozenset(),
    ) -> dict:
        """Ask the LLM for command → response entries for one node (``skip``: already answered)."""
        commands = [c for c in node.commands
                    if str(c.get("cmd", "")).strip() and str(c["cmd"]).strip() not in skip]
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


def synthetic_mismatches(node: NodeSpec, entries: dict, recorded: set[str], row: dict) -> list[dict]:
    """Invented responses that the workflow step's own success regex rejects (S8-18)."""
    from skills.network_config_test.scenarios import java_regex_matches
    from .topology import _validation_regex
    out = []
    expectations: dict[str, set[str]] = {}
    for c in node.commands:
        expectations.setdefault(str(c.get("cmd", "")).strip(), set()).add(_validation_regex(c))
    for c in node.commands:
        cmd = str(c.get("cmd", "")).strip()
        regex = _validation_regex(c)
        if not cmd or not regex or cmd in recorded or re.match(r"^(su(\s+-)?\s+\S+|exit|logout)$", cmd):
            continue
        if len(expectations.get(cmd, ())) > 1:
            # Checked again later with another expectation (file absent → present): one
            # static answer cannot be right; needs MockWire state/VFS (S8-16)
            continue
        key, entry = _find_entry(entries, cmd, resolve_command(cmd, row))
        if not isinstance(entry, dict) or not isinstance(entry.get("lines"), list):
            continue
        text = "\n".join(str(x) for x in entry["lines"])
        if not java_regex_matches(regex, text):
            out.append({"key": key, "cmd": cmd, "regex": regex, "output": text})
    return out


# Answered by MockWire itself (shell builtins / prompt changes); everything else needs a response
_SIM_BUILTINS = {"echo", "true", "false", ":", "pwd", "whoami", "id", "hostname", "cd", "stty", "su",
                 "exit", "logout", "sleep"}


def command_key_regex(cmd: str) -> str:
    """A ``regex:`` key body for a workflow command: literal parts escaped, ``${…}`` → ``\\S+``,
    shell substitutions ``$(…)`` → ``.+?`` (``$(date +%Y%m%d%H%M%S)`` holds a space: the LLM's
    ``\\S+`` never matched and every MRF test case got "tar: command not found", S8-26)."""
    out = []
    for part in re.split(r"(\$\{[^{}]+\}|\$\([^()]*\))", cmd):
        if part.startswith("${"):
            out.append(r"\S+")
        elif part.startswith("$("):
            out.append(r".+?")
        else:
            out.append(re.sub(r"([\\.^$|?*+()\[\]{}])", r"\\\1", part))
    return "".join(out)


def rekey_unanswered(node: NodeSpec, entries: dict, recorded: set[str], row: dict) -> dict:
    """Give an unanswered command the response the LLM meant for it: an LLM ``regex:`` key whose
    literal start the command begins with is replaced by a key built from the command."""
    entries = dict(entries)
    for m in unanswered_commands(node, entries, recorded, row):
        body = command_key_regex(m["cmd"])
        try:
            if not re.fullmatch(body, m["resolved"]):
                continue
        except re.error:
            continue
        for key in list(entries):
            if not str(key).startswith("regex:"):
                continue
            head = re.split(r"\\[SdwsDWb]|\.\*|\.\+|\[|\(|\|", str(key)[6:])[0]
            head = re.sub(r"\\(.)", r"\1", head)
            if len(head) >= 8 and m["resolved"].startswith(head):
                entries["regex:" + body] = entries.pop(key)
                logger.info("Phase 2: %s — response key rebuilt from the command: %s", node.name, m["cmd"][:80])
                break
    return entries


def _regex_literal(regex: str) -> str | None:
    """The text a regex made only of literal characters matches (``\\'`` → ``'``); None when it
    has classes, quantifiers, groups or anchors."""
    if re.search(r"\\[dDsSwWbBAZzQEpPn]|(?<!\\)[\^$*+?()\[\]{}|]", regex):
        return None
    return re.sub(r"\\(.)", r"\1", regex)


def repair_literal_mismatches(node: NodeSpec, entries: dict, recorded: set[str], row: dict) -> dict:
    """An invented response the LLM could not make satisfy a plain-text success regex gets that
    text as its line: the MRF backup answered ``…`/\\' from member names`` (a literal backslash)
    where tar prints ``…`/' …`` and the step checks for it, three regenerations in a row (S8-27)."""
    import difflib
    entries = dict(entries)
    for b in synthetic_mismatches(node, entries, recorded, row):
        text = _regex_literal(str(b.get("regex") or ""))
        entry = entries.get(b.get("key"))
        if not text or not isinstance(entry, dict) or not isinstance(entry.get("lines"), list):
            continue
        lines = [str(x) for x in entry["lines"]]
        scores = [difflib.SequenceMatcher(None, x, text).ratio() for x in lines]
        if scores and max(scores) >= 0.8:
            lines[scores.index(max(scores))] = text
        else:
            lines.insert(0, text)
        entries[b["key"]] = {**entry, "lines": lines}
        logger.info("Phase 2: %s — response for %r set to its success text", node.name, str(b.get("cmd"))[:60])
    return entries


def fill_unanswered(node: NodeSpec, entries: dict, recorded: set[str], row: dict) -> dict:
    """Commands still without a response after the LLM rounds get one built from the workflow:
    key from the command, output = the step's plain-text success text (or nothing). The MRF LLM
    returned invalid YAML, then left 6 of 14 commands unanswered — every case failed on
    "command not found" (S8-28)."""
    entries = dict(entries)
    for m in unanswered_commands(node, entries, recorded, row):
        body = command_key_regex(m["cmd"])
        try:
            key = "regex:" + body if re.fullmatch(body, m["resolved"]) else m["cmd"]
        except re.error:
            key = m["cmd"]
        text = _regex_literal(str(m.get("regex") or "")) if m.get("regex") else None
        entries[key] = {"lines": [text] if text else []}
        logger.info("Phase 2: %s — no response from the LLM for %r: built from the workflow", node.name, m["cmd"][:70])
    return entries


def unanswered_commands(node: NodeSpec, entries: dict, recorded: set[str], row: dict) -> list[dict]:
    """Node commands no response entry answers (the simulator would say "command not found")."""
    from .file_flows import is_vfs_command
    from .topology import _validation_regex
    out, seen = [], set()
    for c in node.commands:
        cmd = str(c.get("cmd", "")).strip()
        first = cmd.split(" ", 1)[0] if cmd else ""
        if not cmd or cmd in seen or cmd in recorded or first in _SIM_BUILTINS or is_vfs_command(cmd, node.vfs):
            continue
        seen.add(cmd)
        resolved = resolve_command(cmd, row)
        if _find_entry(entries, cmd, resolved)[1] is None:
            out.append({"cmd": cmd, "resolved": resolved, "regex": _validation_regex(c)})
    return out


def resolve_command(cmd: str, row: dict) -> str:
    """The command as the JAR would send it: runtime params → test values, CIQ columns →
    first sample row, anything else → a token."""
    from core.config import runtime_params
    params = runtime_params()

    def value(m: re.Match) -> str:
        expr = m.group(1).strip()
        if expr in params:
            return params[expr]
        if expr.startswith("configSequences.") and ".data." in expr:
            return str(row.get(expr.split(".data.", 1)[1].strip(), "") or "1")
        return "1"
    return re.sub(r"\$\{([^{}]+)\}", value, cmd)


def merge_responses(recorded: dict, synthetic: dict, commands: list[dict], covered: set[str],
                    row: dict) -> dict:
    """Recorded/templated responses first, then the LLM's. MockWire answers with the first
    matching key: an invented ``regex:ldapsearch … imsi=\\S+ …`` placed before the template
    built from the real output answered the IMSI dump in a made-up format, and the workflow's
    regex (written from the real output) failed (S8-25). Invented keys that only answer
    commands real output covers are dropped."""
    texts = [str(c.get("cmd", "")) for c in commands if isinstance(c, dict)]
    uncovered = [t for t in texts if t not in covered]
    kept = dict(synthetic)
    for cmd in covered:
        while True:
            key, _ = _find_entry(kept, cmd, resolve_command(cmd, row))
            if key is None:
                break
            others = [t for t in uncovered if _find_entry({key: 1}, t, resolve_command(t, row))[0] is not None]
            if others:
                break  # it also answers a command nobody recorded: keep it, behind the real one
            kept.pop(key)
            logger.info("Dropped invented response %r: real output covers %r", str(key)[:80], cmd[:80])
    merged = dict(recorded)
    for key, entry in kept.items():
        merged.setdefault(key, entry)
    return merged


def _find_entry(entries: dict, cmd: str, resolved: str):
    for key in (cmd, resolved):
        if key in entries:
            return key, entries[key]
    for key, entry in entries.items():
        if str(key).startswith("regex:"):
            try:
                if re.search(str(key)[6:], resolved):
                    return key, entry
            except re.error:
                continue
    # MockWire's longest-prefix fallback ("cmd" answers "cmd -x"), not across | ; > &
    for key, entry in entries.items():
        k = str(key)
        rest = resolved[len(k):]
        if not k.startswith("regex:") and resolved.startswith(k) and (not rest or rest[0] == " ") \
                and not re.search(r"[|;>&]", rest):
            return key, entry
    return None, None


def _first_row_values(requirements: dict) -> dict:
    """First non-empty value per CIQ column across the sample rows (test values)."""
    values: dict = {}
    for sheet in ((requirements.get("doc_extracted") or {}).get("ciq_sheets") or {}).values():
        for r in (sheet or {}).get("sample_rows") or []:
            if isinstance(r, dict):
                for k, v in r.items():
                    if str(v or "").strip() and k not in values:
                        values[k] = v
    return values


_PH = re.compile(r"\$\{([^{}]+)\}")


def templated_responses(commands: list[dict], samples: list[dict]) -> dict[str, tuple[str, dict]]:
    """Recorded real output as a template for commands with ``${…}`` (S8-21).

    The workflow's ``ldapsearch … imsi=${x} …`` matches the recorded
    ``ldapsearch … imsi=404530000000132 …``; the MockWire key becomes a ``regex:`` with a named
    capture per placeholder, and the recorded values in the output become ``${name}`` — so any
    queried value is answered with real formatting. Returns {command: (key, entry)}.
    """
    out: dict[str, tuple[str, dict]] = {}
    for c in commands:
        cmd = str(c.get("cmd", "")).strip()
        if "${" not in cmd or cmd in out:
            continue
        parts = _PH.split(cmd)
        names: list[str] = []
        py, java = "", "^"
        for k, part in enumerate(parts):
            if k % 2:
                base = re.sub(r"[^A-Za-z0-9]", "", part.split(".")[-1]) or "v"
                name = base if base[0].isalpha() and base not in names else f"v{len(names)}"
                names.append(name)
                py += f"(?P<{name}>.+?)"
                java += f"(?<{name}>.+?)"
            else:
                lit = "".join(r"\s+" if t.isspace() else re.escape(t) for t in re.split(r"(\s+)", part) if t)
                py += lit
                java += lit
        try:
            matcher = re.compile(py + r"\s*$")
        except re.error:
            continue
        for sample in samples:
            m = matcher.match(" ".join(str(sample.get("command", "")).split()))
            output = str(sample.get("output") or "")
            if not m or not output.strip():
                continue
            text = output
            for name in sorted(names, key=lambda n: -len(m.group(n) or "")):
                value = (m.group(name) or "").strip("'\"")
                if len(value) >= 4:
                    text = text.replace(value, "${" + name + "}")
            out[cmd] = ("regex:" + java + "$", {"lines": text.splitlines()})
            break
    return out


def recorded_responses(commands: list[dict], samples: list[dict]) -> dict[str, dict]:
    """MockWire entries for node commands whose real output was recorded (spec 004 S8-07).

    Only commands without ``${…}`` placeholders: their text is exactly what the JAR sends.
    Output is served verbatim, so the workflow's regexes meet the same text as on a real node.
    """
    by_command: dict[str, str] = {}
    for sample in samples:
        cmd = " ".join(str(sample.get("command", "")).split())
        if cmd and sample.get("output") and cmd not in by_command:
            by_command[cmd] = str(sample["output"])
    entries: dict[str, dict] = {}
    for c in commands:
        cmd = str(c.get("cmd", "")).strip()
        output = by_command.get(" ".join(cmd.split())) if cmd and "${" not in cmd else None
        if output is not None:
            entries[cmd] = {"lines": output.splitlines()}
    return entries


def _review_yaml(nodes: list[NodeSpec], instance_configs: dict[str, str]) -> str:
    """All instance configs as one multi-document YAML for milestone review."""
    parts = []
    for n in nodes:
        parts.append(f"# ===== Instance {n.instance} — node {n.name} =====\n"
                     f"{instance_configs.get(n.instance, '').rstrip()}\n")
    return "---\n".join(parts)
