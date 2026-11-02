"""Structural validation for workflow YAML.

Checks structure against cli-automation framework schema.
Treats ${...} placeholders as valid — they resolve at runtime.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from .autofix import parse_yaml, is_placeholder

logger = logging.getLogger(__name__)


def validate_workflow(yaml_text: str, command_samples: list[dict] | None = None) -> list[str]:
    """Validate workflow YAML structure. Returns list of error strings.

    With ``command_samples`` (real ``{command, output}`` pairs from uploaded transcripts,
    requirements ``doc_extracted.command_samples``) every validation/register regex is
    also checked against the real output of the same command.
    """
    errors = []
    parsed = parse_yaml(yaml_text)
    if parsed is None:
        return [f"Invalid YAML syntax{_yaml_error(yaml_text)}"]

    # Must have nodes (or nodeGroups for backwards compat)
    nodes = parsed.get("nodes", parsed.get("nodeGroups", {}))
    if not nodes:
        errors.append("Missing 'nodes' section")
    elif not isinstance(nodes, dict):
        errors.append("'nodes' must be a mapping")

    # Collect known node names
    known_nodes = set()
    if isinstance(nodes, dict):
        known_nodes = set(nodes.keys())

    # Must have phases
    phases = parsed.get("phases", {})
    if not phases:
        errors.append("Missing 'phases' section")
    elif not isinstance(phases, (dict, list)):
        errors.append("'phases' must be a mapping or list")

    # Validate each phase
    known_registers: set[str] = set()
    if isinstance(phases, dict):
        for phase_name, phase_body in phases.items():
            if not isinstance(phase_body, dict):
                errors.append(f"Phase '{phase_name}' is not a mapping")
                continue
            steps = phase_body.get("steps", [])
            if not steps:
                errors.append(f"Phase '{phase_name}' has no steps")
                continue
            for j, step in enumerate(steps):
                if not isinstance(step, dict):
                    continue
                step_errors = _validate_step(step, j, phase_name, known_nodes, known_registers)
                errors.extend(step_errors)
                # Track registered variables
                for reg in step.get("register", []):
                    if isinstance(reg, dict):
                        known_registers.add(reg.get("name", ""))
                    elif isinstance(reg, str):
                        known_registers.add(reg)
    elif isinstance(phases, list):
        # Accept list format too (autofix should convert, but validate anyway)
        for i, phase in enumerate(phases):
            if not isinstance(phase, dict):
                continue
            phase_name = phase.get("name", f"phase_{i}")
            steps = phase.get("steps", phase.get("commands", []))
            for j, step in enumerate(steps):
                if isinstance(step, dict):
                    step_errors = _validate_step(step, j, phase_name, known_nodes, known_registers)
                    errors.extend(step_errors)

    errors.extend(check_placeholders(parsed))
    if command_samples:
        errors.extend(check_against_samples(parsed, command_samples))
    return errors


_PROMPT_STEP = re.compile(r"^(su(\s+-)?\s+\S+|exit|logout)$")
_SAMPLE_EXCERPT_LINES = 8


def _yaml_error(yaml_text: str) -> str:
    """': <problem> at line N: <line>' for the first YAML error, so a regenerate can fix it."""
    from io import StringIO
    from ruamel.yaml import YAML
    try:
        YAML(typ="safe").load(StringIO(yaml_text))
        return ""
    except Exception as e:
        mark = getattr(e, "problem_mark", None)
        problem = getattr(e, "problem", None) or str(e).splitlines()[0]
        if mark is None:
            return f": {problem}"
        lines = yaml_text.splitlines()
        line = lines[mark.line].strip() if mark.line < len(lines) else ""
        return f": {problem} at line {mark.line + 1}: {line[:200]}"


def check_against_samples(parsed: dict, command_samples: list[dict]) -> list[str]:
    """Report regexes that do not match the real output recorded for the same command.

    A step matches a sample when its ``send`` equals the recorded command (whitespace
    normalised) or, with ``${…}`` placeholders treated as wildcards, matches it. The
    success-criteria regex must match at least one matching sample's output and every
    register regex must capture from one — otherwise the step can never pass on the real
    node (spec 004 DEP-5). Prompt-only steps (su/exit) are skipped: prompts are not part of
    captured output.
    """
    samples = [s for s in command_samples
               if isinstance(s, dict) and s.get("command") and s.get("output", "").strip()]
    if not samples:
        return []
    problems: list[str] = []
    for phase_name, step in _iter_steps(parsed.get("phases") or {}):
        send = str(step.get("send") or "").strip()
        if send and _PROMPT_STEP.match(send):
            problem = _check_prompt_change(step, send, phase_name, command_samples)
            if problem:
                problems.append(problem)
            continue
        if not send:
            continue
        matching = [s for s in samples if _command_matches(send, s["command"])]
        if not matching:
            continue
        outputs = [s["output"] for s in matching]

        regex = _success_regex(step)
        if regex:
            verdict = _regex_matches_any(regex, outputs)
            if verdict is not True:
                problems.append(
                    f"Step '{send[:80]}' ({phase_name}): validation regex /{regex}/ "
                    f"{verdict or 'does not match'} the real output recorded for this command. "
                    f"Real output (relevant lines):\n{_excerpt(matching[0]['output'], regex)}\n"
                    "Write the regex against this real output.")
        for reg in step.get("register") or []:
            rx = reg.get("regex") if isinstance(reg, dict) else None
            if not isinstance(rx, str):
                continue
            verdict = _regex_matches_any(rx, outputs)
            if verdict is not True:
                problems.append(
                    f"Step '{send[:80]}' ({phase_name}): register regex /{rx}/ "
                    f"{verdict or 'captures nothing from'} the real output recorded for this command. "
                    f"Real output (relevant lines):\n{_excerpt(matching[0]['output'], rx)}")
    return problems


def _check_prompt_change(step: dict, send: str, phase_name: str, samples: list[dict]) -> str:
    """A su/exit step's prompt_regex must match the prompt the real node showed next.

    In a transcript the prompt after ``su - x`` is the prompt of the following command.
    """
    regex = str(step.get("prompt_regex") or "")
    if not regex:
        return ""
    seen = [str(samples[i + 1].get("prompt") or "") for i in range(len(samples) - 1)
            if isinstance(samples[i], dict) and _command_matches(send, str(samples[i].get("command", "")))]
    seen = [p for p in seen if p]
    if not seen:
        return ""
    if _regex_matches_any(regex, seen + [p + " " for p in seen]) is True:
        return ""
    return (f"Step '{send}' ({phase_name}): prompt_regex /{regex}/ does not match the prompt the "
            f"real node shows after this command: {', '.join(repr(p) for p in dict.fromkeys(seen))}. "
            "Write the prompt_regex for that prompt.")


def _iter_steps(phases: Any):
    items = phases.items() if isinstance(phases, dict) else [
        (p.get("name", f"phase_{i}"), p) for i, p in enumerate(phases) if isinstance(p, dict)]
    for name, body in items:
        steps = body.get("steps", []) if isinstance(body, dict) else body
        for step in steps or []:
            if isinstance(step, dict):
                yield name, step
                for nested in step.get("steps") or []:
                    if isinstance(nested, dict):
                        yield name, nested


def _normalise(cmd: str) -> str:
    return re.sub(r"\s+", " ", cmd.strip())


def _command_matches(send: str, recorded: str) -> bool:
    send, recorded = _normalise(send), _normalise(recorded)
    if "${" not in send:
        return send == recorded
    parts = re.split(r"\$\{[^}]*\}", send)
    pattern = ".+?".join(re.escape(p) for p in parts)
    return re.fullmatch(pattern, recorded) is not None


def _success_regex(step: dict) -> str:
    validation = step.get("validation")
    if not isinstance(validation, dict):
        return ""
    success = validation.get("success")
    criteria = success.get("criteria") if isinstance(success, dict) else None
    regex = criteria.get("regex") if isinstance(criteria, dict) else None
    return regex if isinstance(regex, str) else ""


def _regex_matches_any(java_regex: str, outputs: list[str]):
    """True if the (Java) regex finds a match in any output, else a short reason string.

    Mirrors the cli-automation JAR (probed 2026-11-02): find(), MULTILINE (``^``/``$`` per
    line), no DOTALL. ``${…}`` inside a regex is NOT interpolated (DEP-13).
    """
    if "${" in java_regex:
        return "contains ${…}, which the JAR does not interpolate in regexes (PatternSyntaxException), so it cannot match"
    py_regex = re.sub(r"\(\?<([A-Za-z][A-Za-z0-9]*)>", r"(?P<\1>", java_regex)
    try:
        compiled = re.compile(py_regex, re.MULTILINE)
    except re.error as e:
        return f"is not a valid regex ({e}) for"
    return True if any(compiled.search(o.replace("\r\n", "\n")) for o in outputs) else ""


def _excerpt(output: str, regex: str = "") -> str:
    """The whole output when short; else lines sharing words with the regex (what it was
    aiming at) plus the line after each (a table header's values), else the last lines."""
    lines = [l for l in output.splitlines() if l.strip()]
    if len(lines) <= _SAMPLE_EXCERPT_LINES:
        return "\n".join(f"    {l[:160]}" for l in lines)
    words = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z_%]{3,}", re.sub(r"\\[A-Za-z]", " ", regex))}
    hits = [i for i, l in enumerate(lines) if words and any(w in l.lower() for w in words)]
    picked = sorted({j for i in hits for j in (i, i + 1) if j < len(lines)})
    chosen = [lines[j] for j in picked][:_SAMPLE_EXCERPT_LINES] if picked else lines[-_SAMPLE_EXCERPT_LINES:]
    return "\n".join(f"    {l[:160]}" for l in chosen)


# Context the cli-automation JAR provides for every node group (spec 004 probes)
_JAR_BUILTINS = {"node", "nodeGroup", "crGroup", "email", "NODE_TYPE", "SUB_ACTIVITY_NAME",
                 "configSequences", "http_status"}
_DOTTED_ROOTS = {"configSequences", "ENV", "SECRET", "vars"}
_DEAD_SYNTAX = [
    (re.compile(r"\$\{\{"), "uses '${{…}}' — write '${…}'"),
    (re.compile(r"\$\{configData\."), "uses 'configData…' — CIQ values are "
                                       "'${configSequences.0.records.0.data.<Column>}'"),
    (re.compile(r"\$\{node_row\."), "uses 'node_row…' — NIAM IDs are '${<NODE>.niamID-<n>}'; "
                                     "other node fields are not in the CIQ JSON"),
    (re.compile(r"\$\{(register|env)\."), "uses 'register.'/'env.' — registered values and "
                                           "--var values are plain '${name}'"),
    (re.compile(r"\$\{configSequences\.[^}]*\.data\.['\"]"), "quotes the column name, which resolves "
                                                            "to empty — write it bare (spaces are fine)"),
]
_PLACEHOLDER = re.compile(r"\$\{([^{}]+)\}")
_NAMED_GROUP = re.compile(r"\(\?P?<([A-Za-z][A-Za-z0-9]*)>")


def check_placeholders(parsed: dict, runtime_params: list[str] | None = None) -> list[str]:
    """Report ``${…}`` placeholders the cli-automation JAR cannot resolve.

    ``runtime_params`` (default: config ``execution.runtime_params``) are passed by the
    framework with ``--var`` at execution, so they count as defined.
    """
    if runtime_params is None:
        from core.config import runtime_params as configured
        runtime_params = list(configured())
    nodes = parsed.get("nodes") or {}
    node_names = set(nodes) if isinstance(nodes, dict) else set()
    defined = set(_JAR_BUILTINS) | set(runtime_params)
    defined |= set(((parsed.get("globals") or {}).get("vars") or {}).keys())
    phases = parsed.get("phases") or {}
    for body in (phases.values() if isinstance(phases, dict) else []):
        if isinstance(body, dict):
            defined |= set((body.get("vars") or {}).keys())
    for regex in _iter_register_regexes(phases):
        defined |= set(_NAMED_GROUP.findall(regex))

    problems: dict[str, str] = {}
    for text in _iter_strings(parsed):
        for pattern, why in _DEAD_SYNTAX:
            for m in pattern.finditer(text):
                ref = text[m.start():text.find("}", m.start()) + 1]
                problems.setdefault(ref, why)
        for m in _PLACEHOLDER.finditer(text):
            expr = m.group(1).strip()
            root = expr.split(".", 1)[0]
            if "." in expr:
                if root in _DOTTED_ROOTS or (root in node_names and re.match(
                        rf"^{re.escape(root)}\.niamID-\d+$", expr)):
                    continue
                problems.setdefault(m.group(0), "unknown placeholder root "
                                    f"'{root}' (not a node NIAM ID, configSequences, ENV, SECRET or vars)")
            elif expr not in defined:
                problems.setdefault(m.group(0), "variable is never registered by a step "
                                    "(add a register with a (?<name>…) group on the step that produces it)")
    return [f"Unresolvable placeholder {ref}: {why}" for ref, why in problems.items()]


def _iter_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _iter_strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _iter_strings(v)


def _iter_register_regexes(value: Any):
    if isinstance(value, dict):
        for reg in value.get("register") or []:
            if isinstance(reg, dict) and isinstance(reg.get("regex"), str):
                yield reg["regex"]
        for v in value.values():
            yield from _iter_register_regexes(v)
    elif isinstance(value, list):
        for v in value:
            yield from _iter_register_regexes(v)


def _validate_step(step: dict, index: int, phase: str, known_nodes: set, known_registers: set) -> list[str]:
    """Validate a single step within a phase."""
    errors = []
    # Accept both "send" (correct) and "cmd"/"command" (will be auto-fixed)
    cmd = step.get("send", step.get("cmd", step.get("command", "")))
    node = step.get("node", "")
    step_id = f"Phase '{phase}' step {index}"

    if not cmd:
        errors.append(f"{step_id}: missing 'send' (command to execute)")

    # Node reference check (skip placeholders)
    if node and not is_placeholder(node) and node not in known_nodes:
        errors.append(f"{step_id}: references unknown node '{node}'")

    return errors
