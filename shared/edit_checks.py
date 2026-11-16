"""Checks for a generated file edited at a review (spec 012, features C1–C3).

Each file is checked with the checks its own step uses — the workflow with the step 6 validator
(the run's CIQ sheets and recorded command outputs), the validation rules and the CIQ JSON with
their schema validators, a helper script with Python syntax and the step 6 script contract — and
every problem comes back in plain language with the line it points at, the technical text, and a
suggested fix where one is known.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass

import yaml

from shared.plain_errors import split_reasons, summarize

_REC_PREFIX = "configSequences.0.records.0.data."


@dataclass
class Problem:
    line: int           # 1-based; 0 = the whole file
    plain: str          # what a reviewer reads
    technical: str      # the step's own message
    section: str = ""   # where it is fixed (shared.plain_errors sections)
    fix: str = ""       # a suggested fix ``apply_fix`` knows ("" = none)
    target: str = ""    # what the fix acts on (a step's command)


# ---------------------------------------------------------------- lines

def _norm(text: str) -> str:
    return " ".join(str(text).replace(_REC_PREFIX, "rec.data.").split())


def _line_containing(lines: list[str], needle: str, must: str = "") -> int:
    needle = _norm(needle)
    if not needle:
        return 0
    for i, line in enumerate(lines, 1):
        if needle in _norm(line) and (not must or must in line):
            return i
    return 0


def _send_line(lines: list[str], cmd: str) -> int:
    """The line whose `send:` value starts with the step's command (as the message quotes it)."""
    want = _norm(cmd)[:40]
    for i, line in enumerate(lines, 1):
        m = re.match(r"\s*-?\s*send:\s*(.*)", line)
        if m and want and _norm(m.group(1).strip("'\"")).startswith(want):
            return i
    return _line_containing(lines, cmd[:20])


def line_of(reason: str, lines: list[str]) -> int:
    """The line a step message points at: its step's `send`, a placeholder, a CIQ column, an expr."""
    text = " ".join(reason.split())
    if m := re.match(r"Step '(.+?)' \(", text):
        return _send_line(lines, m.group(1).rstrip("…"))
    if m := re.search(r"step '(.+?)' uses \$\{", text):
        return _send_line(lines, m.group(1).rstrip("…"))
    if m := re.match(r"Unresolvable placeholder (\$\{[^}\[]+)", text):
        return _line_containing(lines, m.group(1))
    if "never uses it" in text:
        return 0  # about the whole workflow, not a line
    if m := re.search(r"CIQ column '([^']+)'", text):
        return _line_containing(lines, m.group(1))
    if m := re.search(r"expr '([^']{1,30})", text):
        return _line_containing(lines, m.group(1), "expr")
    return 0


def _plain(step: int, reason: str) -> tuple[str, str]:
    _, sections, _ = summarize(step, [reason])
    for section, items in sections:
        if items:
            return section, items[0]
    first = re.split(r"(?<=[.!?])\s", " ".join(reason.split()))[0]
    return "", first[:200]


# ---------------------------------------------------------------- checks

def _yaml_problem(text: str) -> Problem | None:
    try:
        yaml.safe_load(text)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        what = getattr(e, "problem", None) or str(e).splitlines()[0]
        return Problem(line=(mark.line + 1) if mark else 0,
                       plain=f"This is not valid YAML here: {what}. Check the indentation and quotes.",
                       technical=str(e))
    return None


def check(kind: str, text: str, requirements: dict | None = None, workflow_text: str = "",
          rel: str = "") -> list[Problem]:
    """Problems of one edited file. ``kind``: workflow | validation_rules | json_output | script."""
    if kind == "script":
        return _check_script(text, workflow_text, rel)
    bad = _yaml_problem(text)
    if bad:
        return [bad]
    if kind == "workflow":
        return _check_workflow(text, requirements or {})
    if kind in ("validation_rules", "json_output"):
        return _check_schema(kind, text)
    return []


def _check_workflow(text: str, reqs: dict) -> list[Problem]:
    from skills.network_config_gen.autofix import parse_yaml, shell_variables_unbraced
    from skills.network_config_gen.skill import _expected_coverage
    from skills.network_config_gen.validator import validate_workflow
    from tools.command_samples import with_library
    doc = reqs.get("doc_extracted") or {}
    errors = validate_workflow(text, with_library(list(doc.get("command_samples") or [])),
                               dict(doc.get("ciq_sheets") or {}), _expected_coverage(reqs) if reqs else None)
    lines = text.splitlines()
    problems: list[Problem] = []
    for err in errors:
        for reason in split_reasons(err):
            section, plain = _plain(6, reason)
            p = Problem(line=line_of(reason, lines), plain=plain, technical=reason, section=section)
            m = re.match(r"Step '(.+?)' \([^)]*\): validation regex .* does not match the real output", " ".join(reason.split()))
            if m and _first_worded_output(reason):
                p.fix, p.target = "success_from_log", m.group(1).rstrip("…")
            problems.append(p)
    # shell variables written ${NAME}: the framework empties them before the node sees them
    parsed = parse_yaml(text)
    if isinstance(parsed, dict):
        probe = copy.deepcopy(parsed)
        if shell_variables_unbraced(probe):
            for i, line in enumerate(lines, 1):
                names = [n for n in re.findall(r"\$\{([A-Za-z_]\w*)\}", line)
                         if re.search(rf"(?:^|[;&|(`\s]){n}=|\bfor\s+{n}\b", line)]
                if names and "send" in line:
                    problems.append(Problem(
                        line=i, section="Nothing to change in your documents (Re-run fixes it)",
                        plain=f"Shell variable {', '.join(names)} is written ${{…}}: the framework empties it "
                              f"before the node sees it. Write ${names[0]} instead.",
                        technical="shell variable in ${…} form", fix="unbrace"))
    return sorted(problems, key=lambda p: (p.line == 0, p.line))


def _check_schema(kind: str, text: str) -> list[Problem]:
    from skills.requirement_gathering.schema_validator import validate_json_output, validate_validation_rules
    result = validate_validation_rules(text) if kind == "validation_rules" else validate_json_output(text)
    lines = text.splitlines()
    out = []
    for issue in result.issues:
        if issue.severity != "error":
            continue
        last = [p for p in re.split(r"[.\[\]]", issue.path or "") if p and not p.isdigit()]
        line = _line_containing(lines, last[-1]) if last else 0
        where = f"{issue.path}: " if issue.path else ""
        out.append(Problem(line=line, plain=f"{where}{issue.message}", technical=str(issue)))
    return out


def _check_script(code: str, workflow_text: str, rel: str) -> list[Problem]:
    from skills.helper_script_gen.skill import (HelperScriptGenSkill, check_output_contract,
                                                check_script_contract, contract_flags)
    try:
        compile(code, rel or "<script>", "exec")
    except SyntaxError as e:
        return [Problem(line=e.lineno or 0, plain=f"Python error: {e.msg}.", technical=str(e))]
    name = rel.rsplit("/", 1)[-1]
    calls = HelperScriptGenSkill._find_needed_scripts(workflow_text).get(name, []) if workflow_text else []
    regexes = HelperScriptGenSkill._script_output_regexes(workflow_text).get(name, []) if workflow_text else []
    out = []
    for msg in check_script_contract(code, contract_flags(calls)):
        out.append(Problem(line=0, plain="The script does not accept every option the workflow passes it: "
                                         + msg.split(" — ")[0].replace("argparse does not define ", "") + ".",
                           technical=msg))
    for msg in check_output_contract(code, regexes):
        out.append(Problem(line=0, plain="Nothing the script prints matches the workflow's success check.",
                           technical=msg))
    return out


# ---------------------------------------------------------------- suggested fixes

def _first_worded_output(reason: str) -> str:
    m = re.search(r"Real output[^:]*:\s*\n(.*)", reason, re.S)
    for line in (m.group(1).splitlines() if m else []):
        line = line.strip()
        if line and not line.lower().startswith("write the regex") and len(re.findall(r"[A-Za-z]", line)) >= 4 \
                and not line.startswith(('"', "{", "[")):
            return line
    return ""


def apply_fix(text: str, problem: Problem) -> str:
    """The file with ``problem``'s suggested fix applied (unchanged when it has none)."""
    from skills.network_config_gen.autofix import dump_yaml, parse_yaml, shell_variables_unbraced
    parsed = parse_yaml(text)
    if not isinstance(parsed, dict):
        return text
    if problem.fix == "unbrace":
        shell_variables_unbraced(parsed)
        return dump_yaml(parsed)
    if problem.fix == "success_from_log":
        line = _first_worded_output(problem.technical)
        target = _norm(problem.target)[:40]
        for step in _all_steps(parsed):
            if target and _norm(step.get("send") or "").startswith(target):
                validation = step.setdefault("validation", {})
                success = validation.setdefault("success", {})
                success["criteria"] = {"regex": re.escape(line)}
                return dump_yaml(parsed)
    return text


def _all_steps(parsed: dict):
    def walk(steps):
        for s in steps or []:
            if isinstance(s, dict):
                yield s
                yield from walk(s.get("steps"))
    for body in (parsed.get("phases") or {}).values():
        yield from walk(body.get("steps") if isinstance(body, dict) else body)
