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


def validate_workflow(yaml_text: str, command_samples: list[dict] | None = None,
                      ciq_sheets: dict | None = None, expected: dict | None = None) -> list[str]:
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
    errors.extend(check_file_flows(parsed))
    errors.extend(check_expr_vars(parsed))
    errors.extend(check_empty_registers(parsed))
    errors.extend(check_file_listing_regexes(parsed))
    errors.extend(check_script_arguments(parsed))
    errors.extend(check_unbalanced_quotes(parsed))
    errors.extend(check_completeness(parsed, expected))
    if command_samples:
        errors.extend(check_against_samples(parsed, command_samples))
    if ciq_sheets:
        errors.extend(check_ciq_coverage(parsed, ciq_sheets))
        errors.extend(check_alternative_columns(parsed, ciq_sheets))
        errors.extend(check_request_inputs(parsed, ciq_sheets, (expected or {}).get("request_columns") or {}))
        errors.extend(check_row_variables(parsed, ciq_sheets))
    return errors


_PROMPT_STEP = re.compile(r"^(su(\s+-)?\s+\S+|exit|logout)$")
_SAMPLE_EXCERPT_LINES = 8


_CIQ_COLUMN = re.compile(r"\$\{configSequences\.\d+\.records\.\d+\.data\.([^}]+)\}")


def check_ciq_coverage(parsed: dict, ciq_sheets: dict) -> list[str]:
    """Steps that use a CIQ column which is empty for some rows, without a ``when`` guard.

    The JAR sends such a command with an empty value (``imsi=,dc=…``) for those node groups.
    The message names the columns those rows do fill, so the generator can resolve the value
    from them (DEP-19) — e.g. rows give either an IMSI or an MSISDN.
    """
    columns: dict[str, list[dict]] = {}
    for sheet in (ciq_sheets or {}).values():
        if not isinstance(sheet, dict):
            continue
        rows = [r for r in sheet.get("sample_rows") or [] if isinstance(r, dict)]
        for col in sheet.get("columns") or sheet.get("headers") or []:
            columns.setdefault(str(col).strip(), rows)
    if not columns:
        return []

    def empty(row: dict, col: str) -> bool:
        return str(row.get(col, "") if row.get(col) is not None else "").strip() == ""

    problems: dict[str, str] = {}
    for phase_name, step in _iter_steps(parsed.get("phases") or {}):
        send = str(step.get("send") or "")
        guard = str(step.get("when") or "")
        for col in {c.strip() for c in _CIQ_COLUMN.findall(send)}:
            rows = columns.get(col)
            if not rows or col in guard or col in problems:
                continue
            missing = [r for r in rows if empty(r, col)]
            # Empty everywhere = an optional value passed through (scripts skip it); only a
            # column some rows fill and others leave empty breaks those node groups
            if not missing or len(missing) == len(rows):
                continue
            sheet_cols = [c for c in columns if columns[c] is rows]
            words = set(re.findall(r"[a-z]+", col.lower()))
            # Candidates filled wherever this column is empty (not the sheet's key column),
            # most similar names first ('Test MSISDN' for 'Test IMSI')
            alternatives = sorted(
                (c for c in sheet_cols[1:] if c != col and all(not empty(r, c) for r in missing)),
                key=lambda c: (-len(words & set(re.findall(r"[a-z]+", c.lower()))), sheet_cols.index(c)))[:3]
            where = ", ".join(str(next((v for k, v in r.items() if not empty(r, k)), "?")) for r in missing[:3])
            problems[col] = (
                f"Step '{send[:80]}' ({phase_name}) uses CIQ column '{col}', which is empty in "
                f"{len(missing)} of {len(rows)} sample rows (e.g. {where}): those node groups would send "
                "an empty value. "
                + (f"Those rows fill {', '.join(repr(a) for a in alternatives)}: resolve '{col}' from it "
                   "into a registered variable with when-guarded steps (one-of-several-columns rule) and "
                   "use that variable." if alternatives else
                   f"Guard the step with when: \"configSequences.0.records.0.data.{col} != ''\"."))
    return list(problems.values())


_EMPTY_GROUP = re.compile(r"\(\?<([A-Za-z][A-Za-z0-9]*)>\)")


def check_empty_registers(parsed: dict) -> list[str]:
    """``register: [{regex: (?<x>)}]`` always stores "": every later ``${x}`` then runs with an
    empty value (``ldapsearch -b "imsi=,…"`` for CIQ rows without a Test IMSI, DEP-31). The
    value has to come from somewhere real for those rows — another CIQ column or a lookup."""
    problems: list[str] = []
    for phase_name, step in _iter_steps(parsed.get("phases") or {}):
        for reg in step.get("register") or []:
            regex = str(reg.get("regex") or "") if isinstance(reg, dict) else ""
            for var in _EMPTY_GROUP.findall(regex):
                users = [str(s.get("send") or "")[:70] for _, s in _iter_steps(parsed.get("phases") or {})
                         if "${" + var + "}" in str(s.get("send") or "")]
                if users:
                    problems.append(
                        f"Step '{str(step.get('send') or step.get('command_description') or '')[:60]}' "
                        f"({phase_name}) registers {var} as an always-empty value ((?<{var}>)), and "
                        f"{len(users)} step(s) use ${{{var}}} (e.g. '{users[0]}') — they would run with an "
                        f"empty value. Obtain {var} for those CIQ rows from real data (another CIQ column or "
                        "a lookup command whose output registers it), or skip the steps with `when`.")
    return problems


def check_alternative_columns(parsed: dict, ciq_sheets: dict) -> list[str]:
    """CIQ rows give either column A or B (step 1's either-or pairs: Test IMSI / Test
    MSISDN). A workflow that uses A but never B makes rows that give only B skip every A
    step: EAST "passed" without any LDAP query and built its XML without an IMSI (DEP-46)."""
    from skills.requirement_gathering.questionnaire import ciq_alternative_columns
    sends = " ".join(str(s.get("send") or "") for _, s in _iter_steps(parsed.get("phases") or {}))

    def uses(col: str) -> bool:
        return re.search(r"\$\{configSequences\.\d+\.records\.\d+\.data\." + re.escape(col) + r"\s*\}", sends) is not None

    problems = []
    for p in ciq_alternative_columns(ciq_sheets or {}):
        for used, unused, rows in ((p["a"], p["b"], p["rows_b"]), (p["b"], p["a"], p["rows_a"])):
            if uses(used) and not uses(unused):
                problems.append(
                    f"CIQ rows give either '{p['a']}' or '{p['b']}', but the workflow never uses '{unused}': rows "
                    f"like {', '.join(rows[:2])} (only '{unused}') skip every step that needs '{used}'. Derive "
                    f"'{used}' from '{unused}' for those rows into a registered variable (as the developer "
                    f"answers describe) and use that variable for all rows.")
    return problems


def check_request_inputs(parsed: dict, ciq_sheets: dict, request_columns: dict[str, str]) -> list[str]:
    """The LOCAL script that builds the request file uploaded to a node must be given every CIQ
    value the sample request carries (``request_columns``: column → sample value), directly,
    via its either-or alternative, or via a variable registered from one of them. create_xml.py
    got no IMSI and defaulted to the sample's IMSI — EAST's request targeted NORTH's subscriber,
    which the simulator cannot notice (DEP-47)."""
    if not request_columns:
        return []
    steps = [s for _, s in _iter_steps(parsed.get("phases") or {})]
    uploads = {str(s["sftp"].get("local_path") or "").rsplit("/", 1)[-1] for s in steps
               if isinstance(s.get("sftp"), dict) and s["sftp"].get("operation") == "upload"}
    def arg_files(send: str) -> set[str]:
        return {t.strip("'\"").rsplit("/", 1)[-1] for t in str(send).split()}

    # the script writing the uploaded file: one of its arguments IS that file name
    builders = [s for s in steps if str(s.get("node", "")).upper() == "LOCAL"
                and re.match(r"\s*python3?\s+\S+\.py\b", str(s.get("send") or ""))
                and arg_files(s.get("send")) & {u for u in uploads if u}]
    if not builders:
        return []
    from skills.requirement_gathering.questionnaire import ciq_alternative_columns
    alternatives: dict[str, set[str]] = {}
    for p in ciq_alternative_columns(ciq_sheets or {}):
        alternatives.setdefault(p["a"], set()).add(p["b"])
        alternatives.setdefault(p["b"], set()).add(p["a"])

    def ref(col: str) -> str:
        return r"\$\{configSequences\.\d+\.records\.\d+\.data\." + re.escape(col) + r"\s*\}"

    problems = []
    for col, value in request_columns.items():
        cols = {col} | alternatives.get(col, set())
        derived = {n for s in steps if any(re.search(ref(c), str(s.get("send") or "")) for c in cols)
                   for r in s.get("register") or [] if isinstance(r, dict)
                   for n in _NAMED_GROUP.findall(str(r.get("regex") or ""))}
        for b in builders:
            send = str(b.get("send"))
            if any(re.search(ref(c), send) for c in cols) or any("${" + n + "}" in send for n in derived):
                continue
            via = f" (rows without it: ${{{sorted(derived)[0]}}})" if derived else ""
            problems.append(
                f"LOCAL step '{send[:70]}' builds the request uploaded to the node, but is not given "
                f"'{col}' — the sample request carries it ({value}). Pass it to the script{via}; a script "
                "must never fall back to a sample value.")
    return problems


def check_unbalanced_quotes(parsed: dict) -> list[str]:
    """A command whose quotes don't close makes the shell wait for more input: the step never
    gets its prompt back and times out (DEP-50)."""
    from .autofix import unbalanced_quotes
    return [f"Step '{str(step.get('send'))[:80]}' ({phase_name}): its quotes are not balanced — the shell "
            "would wait for the closing quote and the step hangs. Close or remove the stray quote."
            for phase_name, step in _iter_steps(parsed.get("phases") or {})
            if isinstance(step.get("send"), str) and unbalanced_quotes(step["send"])]


def check_script_arguments(parsed: dict) -> list[str]:
    """A LOCAL ``python <script>.py --flag value …`` call must reach the script as flag/value
    pairs. ``--ciq_data '{'Mandatory Cap': '…'}'`` nests quotes: the shell splits the value
    into ``{Mandatory``, ``Cap:``, … and the script got ``{Mandatory`` (DEP-42)."""
    import shlex
    problems = []
    for phase_name, step in _iter_steps(parsed.get("phases") or {}):
        send = str(step.get("send") or "")
        if str(step.get("node", "")).upper() != "LOCAL" or not re.match(r"\s*python3?\s+\S+\.py\b", send):
            continue
        if '"' in send:
            problems.append(f"LOCAL step '{send[:80]}' ({phase_name}): contains a double quote — the framework "
                            "breaks LOCAL commands at double quotes (even inside single quotes); use single "
                            "quotes only and no quotes inside values.")
            continue
        try:
            argv = shlex.split(_PLACEHOLDER.sub("X", send), posix=True)
        except ValueError as e:
            problems.append(f"LOCAL step '{send[:80]}' ({phase_name}): unbalanced quotes ({e}).")
            continue
        stray, prev = [], ""
        for token in argv[2:]:
            if not token.startswith("-") and not prev.startswith("-"):
                stray.append(token)
            prev = token
        if stray:
            flag = next((t for t in reversed(argv[:argv.index(stray[0])]) if t.startswith("-")), "an argument")
            problems.append(
                f"LOCAL step '{send[:80]}' ({phase_name}): the shell splits the value of {flag} into several "
                f"words ({', '.join(repr(t) for t in stray[:3])} …) — quotes are nested inside quotes. Pass one "
                "value per flag, each in single quotes without quotes inside (e.g. one flag per CIQ column).")
    return problems


_LS_ONE_FILE = re.compile(r"^\s*ls\s+-\w*l\w*\s+['\"]?(/\S*?/[^/\s'\"]*\.[^/\s'\"]+)['\"]?(?:\s+2>&1)?(?:\s*\|\|\s*true)?\s*$")


def check_file_listing_regexes(parsed: dict) -> list[str]:
    """``ls -l <one file>`` prints just that file's line — no ``total N`` (that is printed for
    directories). A success regex requiring ``total`` never matches, on the node or the
    simulator, and a poll on it retries until it times out (DEP-32)."""
    problems = []
    for phase_name, step in _iter_steps(parsed.get("phases") or {}):
        m = _LS_ONE_FILE.match(str(step.get("send") or ""))
        if not m:
            continue
        from .autofix import _criteria_blocks
        for criteria in _criteria_blocks(step):
            regex = str(criteria.get("regex") or "")
            if re.search(r"(?<![A-Za-z])total\b", regex):
                problems.append(
                    f"Step '{step.get('send')}' ({phase_name}): `ls -l` of a single file prints only the "
                    f"file's line (e.g. '-rw-r--r-- 1 user group 312 Nov  3 00:45 {m.group(1)}'), never a "
                    f"'total' line — its regex {regex!r} can never match. Match the file's line instead.")
    return problems


def check_file_flows(parsed: dict) -> list[str]:
    """LOCAL steps run on the automation host: a path that only exists on a node (it is
    listed/read there or is an sftp remote_path) must be downloaded first (S8-16, DEP-29)."""
    from .autofix import remote_file_reads
    return [f"LOCAL step '{hit['send'][:80]}' ({hit['phase']}) reads {hit['token']}, which exists on "
            f"{hit['node']} ({hit['remote_path']}), not on the automation host: add an sftp download step "
            "(sftp: {operation: download, remote_path: …, local_path: …}) before it and use the local copy."
            for hit in remote_file_reads(parsed)]


_WHEN_TERM = re.compile(r"^\s*configSequences\.\d+\.records\.\d+\.data\.(.+?)\s*(==|!=)\s*'([^']*)'\s*$")


def check_row_variables(parsed: dict, ciq_sheets: dict, runtime_params: list[str] | None = None) -> list[str]:
    """Per CIQ sample row: ``${var}`` used by a step that runs while no step that ran before
    registered ``var`` (its registering steps were skipped by their ``when``) — DEP-21.

    Only simple guards (``<Col> == '…'`` / ``!= '…'`` joined by ``&&``) are evaluated; any
    other guard counts as true, so the check never reports a guess.
    """
    return [
        f"For CIQ rows like {g['label']}, step '{g['send'][:80]}' uses ${{{g['variable']}}} but no "
        f"step that runs for that row registers it (the steps registering it are skipped by "
        f"their when conditions, or register it without a (?<{g['variable']}>…) group). Register it for "
        "every row — e.g. look the value up from a column that row fills, as the developer "
        "notes / MOP describe."
        for g in row_variable_gaps(parsed, ciq_sheets, runtime_params)]


def row_variable_gaps(parsed: dict, ciq_sheets: dict, runtime_params: list[str] | None = None) -> list[dict]:
    """The gaps behind :func:`check_row_variables`: ``variable``, the CIQ ``row`` (and its
    ``label``) that never gets it, the using step's ``send`` and the ``guard_columns``."""
    if runtime_params is None:
        from core.config import runtime_params as configured
        runtime_params = list(configured())
    rows_by_col: dict[str, list[dict]] = {}
    for sheet in (ciq_sheets or {}).values():
        if isinstance(sheet, dict):
            rows = [r for r in sheet.get("sample_rows") or [] if isinstance(r, dict)]
            for col in sheet.get("columns") or sheet.get("headers") or []:
                rows_by_col.setdefault(str(col).strip(), rows)
    steps = [s for _, s in _iter_steps(parsed.get("phases") or {})]
    guards = [str(s.get("when") or "") for s in steps]
    guard_cols = {m.group(1).strip() for g in guards for t in g.split("&&") if (m := _WHEN_TERM.match(t))}
    sheets = {id(rows_by_col[c]): rows_by_col[c] for c in guard_cols if c in rows_by_col}
    if not sheets:
        return []
    defined_always = set(_JAR_BUILTINS) | set(runtime_params)
    defined_always |= set(((parsed.get("globals") or {}).get("vars") or {}).keys())

    def runs(guard: str, row: dict) -> bool:
        for term in guard.split("&&"):
            m = _WHEN_TERM.match(term)
            if not m:
                continue
            value = str(row.get(m.group(1).strip()) or "").strip()
            if (value == m.group(3)) != (m.group(2) == "=="):
                return False
        return True

    problems: dict[str, dict] = {}
    for rows in sheets.values():
        for row in rows:
            label = str(next((v for v in row.values() if str(v or "").strip()), "row"))
            registered: set[str] = set()
            for step, guard in zip(steps, guards):
                if not runs(guard, row):
                    continue
                for name in {m.group(1).strip() for m in _PLACEHOLDER.finditer(str(step.get("send") or ""))}:
                    if "." in name or name in defined_always or name in registered or name in problems:
                        continue
                    problems[name] = {"variable": name, "row": row, "label": label,
                                      "send": str(step.get("send")), "guard_columns": sorted(guard_cols)}
                for reg in step.get("register") or []:
                    rx = reg.get("regex") if isinstance(reg, dict) else None
                    if isinstance(rx, str):
                        registered |= set(_NAMED_GROUP.findall(rx))
    return list(problems.values())


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
        captured = _expr_fails_on_real_output(step, outputs)
        if captured:
            problems.append(
                f"Step '{send[:80]}' ({phase_name}): on the real output the step registers {captured}, so its "
                "expr fails on a healthy node — the register regex captures the wrong field. Real output "
                f"(relevant lines):\n{_excerpt(matching[0]['output'], _success_regex(step) or '')}\n"
                "Count the columns in this output and capture the right one.")
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


def _prompts_after(send: str, samples: list[dict]) -> list[str]:
    """Prompts the real node showed right after ``send`` (the next sample's prompt)."""
    seen = [str(samples[i + 1].get("prompt") or "") for i in range(len(samples) - 1)
            if isinstance(samples[i], dict) and isinstance(samples[i + 1], dict)
            and _command_matches(send, str(samples[i].get("command", "")))]
    return [p for p in dict.fromkeys(seen) if p]


def repair_prompt_regexes(parsed: dict, command_samples: list[dict]) -> int:
    """A su/exit ``prompt_regex`` that misses the prompt the real node showed next becomes
    that prompt, escaped (``rdshyd201$`` with an unescaped end anchor never matched
    ``rdshyd201$``; DEP-34). Only prompts recorded in transcripts are used."""
    changed = 0
    for _, step in _iter_steps(parsed.get("phases") or {}):
        regex = str(step.get("prompt_regex") or "")
        send = str(step.get("send") or "")
        if not regex or not send:
            continue
        seen = _prompts_after(send, command_samples)
        if not seen or _regex_matches_any(regex, seen + [p + " " for p in seen]) is True:
            continue
        literals = [re.escape(p.strip()) for p in seen if p.strip()]
        if literals:
            step["prompt_regex"] = (literals[0] if len(literals) == 1
                                    else "(?:" + "|".join(literals) + ")") + r"\s*$"
            changed += 1
    return changed


def _check_prompt_change(step: dict, send: str, phase_name: str, samples: list[dict]) -> str:
    """A su/exit step's prompt_regex must match the prompt the real node showed next.

    In a transcript the prompt after ``su - x`` is the prompt of the following command.
    """
    regex = str(step.get("prompt_regex") or "")
    if not regex:
        return ""
    seen = _prompts_after(send, samples)
    if not seen:
        return ""
    if _regex_matches_any(regex, seen + [p + " " for p in seen]) is True:
        return ""
    return (f"Step '{send}' ({phase_name}): prompt_regex /{regex}/ does not match the prompt the "
            f"real node shows after this command: {', '.join(repr(p) for p in dict.fromkeys(seen))}. "
            "Write the prompt_regex for that prompt.")


_PHASE_KINDS = ("pre", "activity", "post")


def _phase_kind(name: str) -> str:
    """preNodeHealthCheck / pre_checks -> pre, postNodeHealthCheck -> post, anything else -> activity."""
    text = re.sub(r"[^a-z]", "", str(name).lower())
    if text.startswith("pre"):
        return "pre"
    return "post" if text.startswith("post") else "activity"


def check_completeness(parsed: dict, expected: dict | None) -> list[str]:
    """The workflow covers what the requirements describe (G-6): a step in every phase kind
    the requirements list, every node that has commands, and a plausible share of the
    commands — an LLM answer cut short (76 lines, RDS pre-checks only) passed every other
    check because nothing in it was wrong."""
    if not expected:
        return []
    steps_by_kind: dict[str, int] = {}
    used_nodes: set[str] = set()
    total = 0
    for name, step in _iter_steps(parsed.get("phases") or {}):
        kind = _phase_kind(name)
        steps_by_kind[kind] = steps_by_kind.get(kind, 0) + 1
        used_nodes.add(str(step.get("node") or "").upper())
        total += 1
    problems = []
    labels = {"pre": "pre-check", "activity": "activity", "post": "post-check"}
    for kind in sorted({_phase_kind(p) for p in expected.get("phases") or []}, key=_PHASE_KINDS.index):
        if not steps_by_kind.get(kind):
            problems.append(f"The requirements have {'an' if kind == 'activity' else 'a'} {labels[kind]} phase but the workflow has no "
                            f"{labels[kind]} steps — generate the COMPLETE workflow (all phases of the MOP).")
    node_cmds: dict[str, int] = expected.get("node_commands") or {}
    for node, count in sorted(node_cmds.items()):
        if count >= 2 and node.upper() not in used_nodes:
            problems.append(f"The requirements list {count} commands on node {node} but no workflow step "
                            f"runs on {node} — include its steps.")
    wanted = sum(node_cmds.values())
    # MOP command lists repeat commands per phase/node: a complete workflow had 27 steps for
    # 116 listed commands, the cut-short one 5 of 106
    if wanted >= 8 and total < wanted * 0.12:
        problems.append(f"The workflow has {total} steps for {wanted} MOP commands in the requirements — it "
                        "looks cut short; output the complete workflow.")
    return problems


_EXPR_CMP = re.compile(r"^\s*([A-Za-z_]\w*)\s*(>=|<=|==|!=|>|<)\s*(-?\d+(?:\.\d+)?)\s*$")


def check_expr_vars(parsed: dict, runtime_params: list[str] | None = None) -> list[str]:
    """``criteria.expr`` may only use registered variables (DEP-24): named groups of a
    validation regex are not visible to expr; a ``register`` on the step (or an earlier
    step) is (JAR probes 2026-11-02)."""
    from .autofix import EXPR_IDENT, EXPR_KEYWORDS, _criteria_blocks
    if runtime_params is None:
        from core.config import runtime_params as configured
        runtime_params = list(configured())
    defined = set(_JAR_BUILTINS) | set(runtime_params)
    defined |= set(((parsed.get("globals") or {}).get("vars") or {}).keys())
    problems: list[str] = []
    for phase_name, step in _iter_steps(parsed.get("phases") or {}):
        for reg in step.get("register") or []:
            if isinstance(reg, dict) and isinstance(reg.get("regex"), str):
                defined |= set(_NAMED_GROUP.findall(reg["regex"]))
        for criteria in _criteria_blocks(step):
            expr = criteria.get("expr")
            if not isinstance(expr, str):
                continue
            unknown = sorted({m.group(1) for m in EXPR_IDENT.finditer(expr)} - EXPR_KEYWORDS - defined)
            if unknown:
                problems.append(
                    f"Step '{str(step.get('send') or step.get('command_description'))[:80]}' ({phase_name}): expr "
                    f"'{expr}' uses {', '.join(unknown)}, which no step registers. Register the value on this "
                    "step (register: [{regex: '…(?<name>…)…'}]) and use exactly that name; named groups of "
                    "the validation regex are not visible to expr.")
    return problems


def _expr_on_real_output(step: dict, outputs: list[str]) -> tuple[str, str]:
    """Evaluate a simple `var OP number` success expr on the values the step's register
    regex captures from real output: ("pass" | "fail" | "n/a", "var=value, …")."""
    validation = step.get("validation") if isinstance(step.get("validation"), dict) else {}
    success = validation.get("success") if isinstance(validation.get("success"), dict) else {}
    criteria = success.get("criteria") if isinstance(success.get("criteria"), dict) else {}
    m = _EXPR_CMP.match(str(criteria.get("expr") or ""))
    if not m:
        return "n/a", ""
    var, op, limit = m.group(1), m.group(2), float(m.group(3))
    seen = []
    for reg in step.get("register") or []:
        rx = reg.get("regex") if isinstance(reg, dict) else None
        if not isinstance(rx, str) or f"<{var}>" not in rx:
            continue
        try:
            compiled = re.compile(re.sub(r"\(\?<([A-Za-z][A-Za-z0-9]*)>", r"(?P<\1>", rx), re.MULTILINE)
        except re.error:
            return "n/a", ""
        for out in outputs:
            hit = compiled.search(out.replace("\r\n", "\n"))
            # Like the JAR: the FIRST group's value is stored under the name
            raw = hit.group(1) if hit and hit.re.groups else None
            if raw is None:
                continue
            try:
                value = float(raw.strip())
            except ValueError:
                return "n/a", ""
            ok = {">=": value >= limit, "<=": value <= limit, ">": value > limit, "<": value < limit,
                  "==": value == limit, "!=": value != limit}[op]
            if ok:
                return "pass", f"{var}={raw.strip()}"
            seen.append(f"{var}={raw.strip()}")
    return ("fail", ", ".join(seen)) if seen else ("n/a", "")


def _expr_fails_on_real_output(step: dict, outputs: list[str]) -> str:
    """'var=value' when a simple `var OP number` expr fails for every real output."""
    status, detail = _expr_on_real_output(step, outputs)
    return detail if status == "fail" else ""


_LINE_JOIN = re.compile(r"(?:\\s[*+])?\\n(?:\\s[*+])?")


def relax_line_joins(parsed: dict, command_samples: list[dict]) -> int:
    """Make multi-line validation regexes tolerate lines in between (DEP-22).

    LLMs write ``A\\n\\s+B`` for output where A and B are not adjacent (log lines between
    them). When such a regex fails every real output recorded for its command, its line
    joins become ``[\\s\\S]*?``; the change is kept only if the regex then matches. Returns
    how many regexes changed (``parsed`` is changed in place).
    """
    samples = [s for s in command_samples or [] if isinstance(s, dict) and s.get("command")]
    changed = 0
    for _, step in _iter_steps(parsed.get("phases") or {}):
        send = str(step.get("send") or "").strip()
        validation = step.get("validation") if isinstance(step.get("validation"), dict) else {}
        success = validation.get("success") if isinstance(validation.get("success"), dict) else {}
        criteria = success.get("criteria") if isinstance(success.get("criteria"), dict) else {}
        regex = criteria.get("regex")
        if not send or not isinstance(regex, str) or "${" in regex:
            continue
        outputs = [s["output"] for s in samples if _command_matches(send, s["command"])]
        if not outputs or _regex_matches_any(regex, outputs) is True:
            continue
        relaxed = _relax_joins(_LINE_JOIN.sub(lambda _: r"[\s\S]*?", regex), outputs)
        if relaxed and relaxed != regex:
            criteria["regex"] = relaxed
            changed += 1
    return changed


def repair_capture_columns(parsed: dict, command_samples: list[dict]) -> int:
    """Shift a register capture right until its `var OP n` expr holds on real output (DEP-26).

    LLMs miscount table columns (sar: 5 numbers before %idle, they write 4), so the
    captured value is another column and the check fails on a healthy node. Variants skip
    1-4 more fields before the group; the first whose captured value satisfies the expr on
    the recorded real output wins. Returns how many register regexes changed.
    """
    samples = [s for s in command_samples or [] if isinstance(s, dict) and s.get("command")]
    changed = 0
    for _, step in _iter_steps(parsed.get("phases") or {}):
        send = str(step.get("send") or "").strip()
        outputs = [s["output"] for s in samples if send and _command_matches(send, s["command"])]
        if not outputs or not _expr_fails_on_real_output(step, outputs):
            continue
        validation = step.get("validation") if isinstance(step.get("validation"), dict) else {}
        criteria = (validation.get("success") or {}).get("criteria") if isinstance(validation.get("success"), dict) else {}
        var = _EXPR_CMP.match(str((criteria or {}).get("expr") or "")).group(1)
        for reg in step.get("register") or []:
            rx = reg.get("regex") if isinstance(reg, dict) else None
            if not isinstance(rx, str) or f"(?<{var}>" not in rx:
                continue
            for k in range(1, 5):
                candidate = rx.replace(f"(?<{var}>", "(?:\\S+\\s+){%d}(?<%s>" % (k, var), 1)
                trial = dict(step, register=[dict(reg, regex=candidate)])
                if _expr_on_real_output(trial, outputs)[0] == "pass":  # must capture and hold
                    reg["regex"] = candidate
                    changed += 1
                    break
    return changed


# Joins that may have to span lines: \s* / \s+ and an unescaped .* / .+ (with or without ?),
# which never crosses a line break ('apache … running.*instance1… running'; DEP-43)
_WS_JOIN = re.compile(r"\\s[*+]|(?<!\\)\.[*+]\??")


def _relax_joins(regex: str, outputs: list[str]) -> str:
    """Fewest ``\\s+``/``\\s*`` joins widened to ``[\\s\\S]*?`` so the regex matches real output.

    All joins are widened first; then each one is tightened back if the regex still
    matches without it — so only the joins that really span other lines change.
    """
    if _regex_matches_any(regex, outputs) is True:
        return regex
    spots = [m.span() for m in _WS_JOIN.finditer(regex)]

    def build(wide: set[int]) -> str:
        out, last = [], 0
        for k, (a, b) in enumerate(spots):
            out.append(regex[last:a])
            out.append(r"[\s\S]*?" if k in wide else regex[a:b])
            last = b
        return "".join(out) + regex[last:]

    wide = set(range(len(spots)))
    if not spots or _regex_matches_any(build(wide), outputs) is not True:
        return ""
    for k in range(len(spots)):
        if _regex_matches_any(build(wide - {k}), outputs) is True:
            wide.discard(k)
    return build(wide)


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
    (re.compile(r"\$\{rec_row\."), "uses 'rec_row…' — CIQ values are "
                                    "'${configSequences.0.records.0.data.<Column>}'"),
    (re.compile(r"\$\{node_row\."),"uses 'node_row…' — NIAM IDs are '${<NODE>.niamID-<n>}'; "
                                     "other node fields are not in the CIQ JSON"),
    (re.compile(r"\$\{(register|env)\."), "uses 'register.'/'env.' — registered values and "
                                           "--var values are plain '${name}'"),
    (re.compile(r"\$\{configSequences\.[^}]*\.data\.['\"]"), "quotes the column name, which resolves "
                                                            "to empty — write it bare (spaces are fine)"),
    (re.compile(r"\$\{configSequences\.\d+\.records\.\d+\.data\}"),
     "is the whole CIQ record, which the JAR passes as '[object Object]' — pass the columns the "
     "script needs one by one ('${configSequences.0.records.0.data.<Column>}')"),
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

    # sftp / rest / email / nested-steps steps carry no 'send'
    if not cmd and not any(k in step for k in ("sftp", "rest", "email", "steps")):
        errors.append(f"{step_id}: missing 'send' (command to execute)")
    sftp = step.get("sftp")
    if isinstance(sftp, dict):
        if sftp.get("operation") not in ("upload", "download"):
            errors.append(f"{step_id}: sftp.operation must be upload or download")
        if not sftp.get("local_path") or not sftp.get("remote_path"):
            errors.append(f"{step_id}: sftp needs local_path and remote_path")

    # Node reference check (skip placeholders)
    if node and not is_placeholder(node) and node not in known_nodes:
        errors.append(f"{step_id}: references unknown node '{node}'")

    return errors
