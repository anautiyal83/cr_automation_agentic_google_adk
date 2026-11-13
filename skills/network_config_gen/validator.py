"""Structural validation for workflow YAML.

Checks structure against cli-automation framework schema.
Treats ${...} placeholders as valid — they resolve at runtime.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from .autofix import _walk_all_steps, parse_yaml, is_placeholder

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
    # Checks run on what the LLM wrote: record loops unwrapped, steps added by the pipeline
    # (repo backups, transfer checksums — structure.py) left out
    from .structure import flatten
    flatten(parsed)

    # Must have nodes (or nodeGroups for backwards compat)
    nodes = parsed.get("nodes", parsed.get("nodeGroups", {}))
    if not nodes:
        errors.append("Missing 'nodes' section")
    elif not isinstance(nodes, dict):
        errors.append("'nodes' must be a mapping")

    # Collect known node names
    known_nodes = {"local", "LOCAL"}  # the framework provides the automation server (production declares none)
    if isinstance(nodes, dict):
        known_nodes |= set(nodes.keys())

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

    rc = (expected or {}).get("record_columns") or {}
    errors.extend(check_placeholders(parsed, node_fields=rc.get("node_fields"), node_types=rc.get("node_types")))
    errors.extend(check_file_flows(parsed))
    errors.extend(check_expr_vars(parsed))
    errors.extend(check_empty_registers(parsed))
    errors.extend(check_file_listing_regexes(parsed))
    errors.extend(check_script_arguments(parsed))
    errors.extend(check_shell_in_regexes(parsed))
    errors.extend(check_listing_counts(parsed))
    errors.extend(check_checked_before_load(parsed))
    errors.extend(check_wildcard_removals(parsed))
    errors.extend(check_grep_filter_checks(parsed))
    errors.extend(check_restore_sources(parsed))
    errors.extend(check_helper_script_nodes(parsed, list((expected or {}).get("mop_commands") or [])
                                            + [str(c.get("command") or "") for c in command_samples or []
                                               if isinstance(c, dict)]))
    errors.extend(check_iteration(parsed))
    errors.extend(check_unbalanced_quotes(parsed))
    errors.extend(check_condition_syntax(parsed))
    errors.extend(check_record_columns(parsed, (expected or {}).get("record_columns")))
    errors.extend(check_scope_every(parsed))
    from .autofix import untar_check_issues  # option A: verify an extracted archive's members
    errors.extend(untar_check_issues(parsed))
    errors.extend(check_unused_record_columns(parsed, (expected or {}).get("record_columns")))
    errors.extend(check_completeness(parsed, expected))
    if command_samples:
        errors.extend(check_against_samples(parsed, command_samples))
    if ciq_sheets:
        errors.extend(check_ciq_coverage(parsed, ciq_sheets))
        errors.extend(check_alternative_columns(parsed, ciq_sheets))
        errors.extend(check_request_inputs(parsed, ciq_sheets, (expected or {}).get("request_columns") or {}))
        errors.extend(check_row_variables(parsed, ciq_sheets))
    return errors


_PY_CALL = re.compile(r"^\s*(?:sudo\s+)?python[\d.]*\s+(\S*?)([\w.-]+\.py)\b")
_PY_INLINE = re.compile(r"^\s*(?:sudo\s+)?python[\d.]*\s+-c\s")


_SHELL_IN_REGEX = re.compile(r"\$\(")  # not backticks: GNU tar prints ``Removing leading `/'``


def check_shell_in_regexes(parsed: dict) -> list[str]:
    """A validation / register regex holding shell syntax (``^(-rwxrwxrwx)\\s+.*$(basename .+?)``, OB-73): a regex
    sees only the command's output — ``$`` is the end of a line, so it never matches and a count stays 0."""
    from .autofix import _criteria_blocks
    errors = []
    for phase, step in _iter_steps(parsed.get("phases") or {}):
        regexes = [str(c.get("regex")) for c in _criteria_blocks(step) if isinstance(c.get("regex"), str)]
        regexes += [str(r.get("regex")) for r in step.get("register") or []
                    if isinstance(r, dict) and isinstance(r.get("regex"), str)]
        for rx in dict.fromkeys(regexes):
            if _SHELL_IN_REGEX.search(rx):
                errors.append(f"Phase '{phase}': the regex {rx!r} of `{str(step.get('send') or '')[:80]}` contains "
                              "shell syntax (`$(…)`) — a regex only sees the command's output text and `$` "
                              "is the end of a line, so it never matches. Match what the output line shows (e.g. the "
                              "file name as `ls` prints it).")
    return errors


_LS_LONG_CMD = re.compile(r"\bls\s+(?:-\S+\s+)*-[a-zA-Z]*l[a-zA-Z]*\b")
_KEEPS_LINES = {"grep", "egrep", "fgrep", "head", "tail", "sort"}
# `^` then group openers, then a number or a space first — where an `ls -l` line has its mode (`-rwxrwxrwx.`)
_STARTS_WITH_NUMBER = re.compile(r"^\^(?:\((?:\?<[A-Za-z][A-Za-z0-9]*>|\?:)?)*(?:\\d|\[0-9|\[\[:digit:|\d|\\s| )")


def check_listing_counts(parsed: dict) -> list[str]:
    """A count over an `ls -l` listing whose regex wants the line to start with a number (OB-74, run c89f1b67:
    `^(?<TOTAL>\\d+)\\s+.*$`, `^(?<OKN>\\d+)\\s+-rwxrwxrwx.*$`): a long listing line starts with the mode
    (`-rwxrwxrwx. 1 user group 65 Nov 13 16:04 name`), so the count stays 0 and the check fails on every node. Only
    when the listing reaches the output as it is (piped to grep / head / sort at most)."""
    errors = []
    for phase, step in _iter_steps(parsed.get("phases") or {}):
        send = str(step.get("send") or "")
        m = _LS_LONG_CMD.search(send)
        if not m:
            continue
        rest = re.split(r"\s*(?:&&|;|\|\||\bfi\b|\belse\b)", send[m.end():])[0]
        rest = re.sub(r"\$\([^()]*\)", "", rest)  # the pipes of `$(tar -tf … | sed …)` are not the listing's
        if any(seg.split()[0] not in _KEEPS_LINES for seg in rest.split("|")[1:] if seg.split()):
            continue  # `ls -l … | wc -l` / `| awk …`: the output is no longer the listing
        for reg in step.get("register") or []:
            if isinstance(reg, dict) and reg.get("loop") and reg.get("count_var") \
                    and _STARTS_WITH_NUMBER.match(str(reg.get("regex") or "")):
                errors.append(f"Phase '{phase}': `{send[:90]}` counts {reg['count_var']} with {reg['regex']!r}, which "
                              "wants each line to start with a number — an `ls -l` line starts with the mode "
                              "(`-rwxrwxrwx. 1 user group 65 Nov 13 16:04 name`), so the count stays 0. Count the "
                              "lines that show the file (`^[-d]\\S{9}`) and those with the wanted mode (`^-rwxrwxrwx`).")
    return errors


_WRITE_DEST = re.compile(r"\b(?:cp|mv|install)\s+(?:-\S+\s+)*(\S+)\s+(\S+)|\btar\s+-?\w*x\w*f?\s+(\S+).*?\s-C\s+(\S+)")
_PLACEHOLDER = re.compile(r"\$\{[^{}]+\}")


def check_checked_before_load(parsed: dict) -> list[str]:
    """A check before the activity that requires what the activity loads (OB-73, run c89f1b67): `ls -ltrh
    <destination>/ | grep <INPUT_FILE>` requiring the announcements ran in the pre-check, found nothing (nothing is
    loaded yet), set PRECHECK_FAILED and the backup and the load never ran. A pre-check / backup step that reads where
    the activity copies or extracts to, names the file it loads, and has a success condition is reported."""
    from .contract import ACTIVITY, BACKUP, PRE, phase_kind
    phases = parsed.get("phases") or {}
    if not isinstance(phases, dict):
        return []
    loads: list[tuple[str, set[str]]] = []  # (destination, placeholders of the file loaded there)
    for key, body in phases.items():
        if not isinstance(body, dict) or phase_kind(str(key), body) != ACTIVITY:
            continue
        for _, step in _iter_steps({key: body}):
            for m in _WRITE_DEST.finditer(str(step.get("send") or "")):
                src, dest = (m.group(1), m.group(2)) if m.group(2) else (m.group(3), m.group(4))
                dest = dest.strip("'\";").rstrip("/")
                names = set(_PLACEHOLDER.findall(src or ""))
                if dest and names and not dest.startswith("/tmp"):
                    loads.append((dest, names))
    errors = []
    for key, body in phases.items():
        if not isinstance(body, dict) or phase_kind(str(key), body) not in (PRE, BACKUP):
            continue
        for _, step in _iter_steps({key: body}):
            send = str(step.get("send") or "")
            loaded = next((d for d, names in loads if d in send and any(n in send for n in names)), "")
            listed = next((d for d, _ in loads if _lists_contents(send, d)), "")
            if not (loaded or listed):
                continue
            criteria = ((step.get("validation") or {}).get("success") or {}).get("criteria") or {}
            if not criteria.get("expr") and str(criteria.get("regex") or ".*") in (".*", ""):
                continue  # records what is there, requires nothing
            if loaded:
                errors.append(f"Phase '{key}': `{send.strip()[:100]}` requires the file the activity loads into "
                              f"{loaded} before the activity runs — nothing is loaded yet, so it fails and the backup "
                              "and the load never run. Before the activity, list what is there without requiring it "
                              "(no success condition on the loaded files); check them after the load.")
            else:
                # user, 2026-11-13: what the folder holds before the load is recorded, never a condition (MRF:
                # `BADPERM_COUNT == 0` over the existing announcements blocked the load)
                errors.append(f"Phase '{key}': `{send.strip()[:100]}` puts a condition on what {listed} holds before "
                              "the activity — the files already there are not the activity's: before the load, the "
                              "folder's contents are only recorded (no success condition, no PRECHECK_FAILED). Check "
                              "the loaded files after the load.")
    return errors


def _lists_contents(send: str, folder: str) -> bool:
    """`ls [-l…] <folder>[/]` (not `ls -d`, the folder itself) or `find <folder>` — the files in the folder."""
    for m in re.finditer(r"\b(ls|find)\s+((?:-\S+\s+)*)(\S+)", send):
        arg = m.group(3).strip("'\"").rstrip("/")
        if arg == folder and not (m.group(1) == "ls" and "d" in m.group(2).replace("-", "")):
            return True
    return False


_GREP_FILTER = re.compile(r"\|\s*e?grep\s+((?:-[a-zA-Z]+\s+)*)(\"[^\"]*\"|'[^']*'|\S+)\s*\|\|\s*true\b")


def _grep_sample(pattern: str) -> str:
    """A name the grep pattern finds: quotes off, `$(basename A .ext)` → A's file name without .ext, `${…}` → X."""
    text = pattern.strip("'\"")

    def basename(m: re.Match) -> str:
        args = m.group(1).split()
        name = args[0].rsplit("/", 1)[-1] if args else ""
        return name[: -len(args[1])] if len(args) > 1 and name.endswith(args[1]) else name
    text = re.sub(r"\$\(basename\s+([^()]*)\)", basename, text)
    return re.sub(r"\$\{[^{}]+\}|\$\([^()]*\)", "X5003", text).replace("\\", "")


def check_grep_filter_checks(parsed: dict) -> list[str]:
    """`… | grep <name> || true` prints the lines naming <name>, or nothing: a success regex matching neither can
    never pass (OB-78, run c89f1b67: the rollback's `ls -ltrh <dest>/ | grep "<file>" || true` wanted
    `No such file or directory|^total \\d+$` — failed with the restored original listed, and on the empty output too,
    where a `^` regex never matches)."""
    errors = []
    for phase, step in _iter_steps(parsed.get("phases") or {}):
        send = str(step.get("send") or "")
        greps = [m for m in _GREP_FILTER.finditer(send) if not re.search(r"[vcqlL]", m.group(1))]
        if not greps:
            continue
        criteria = ((step.get("validation") or {}).get("success") or {}).get("criteria") or {}
        rx = criteria.get("regex")
        if not isinstance(rx, str) or not rx or criteria.get("expr"):
            continue
        try:
            py = re.compile(re.sub(r"\(\?<([A-Za-z][A-Za-z0-9]*)>", r"(?P<\1>", rx), re.MULTILINE)
        except re.error:
            continue
        empty_ok = "^" not in rx and py.search("") is not None  # the JAR: a `^` regex never matches empty output
        lines = [f"-rw-r--r--. 1 user group 69 Nov 13 16:29 {_grep_sample(m.group(2))}" for m in greps]
        if empty_ok or any(py.search(line) for line in lines):
            continue
        errors.append(f"Phase '{phase}': `{send.strip()[:100]}` filters with `grep … || true`, so it prints the lines "
                      f"naming the file (like `{lines[0]}`) or nothing — its success regex {rx!r} matches neither, the "
                      "step can never pass. Check for the named line (present) or accept the empty output (absent).")
    return errors


_RM_WIDE = re.compile(r"\brm\s+((?:-\w+\s+)*)([^\s;|&()]+)")


def _temp_path(path: str) -> bool:
    return path.strip("'\"").startswith(("/tmp/", "/var/tmp/")) or "${ORDER_NO}" in path


def check_wildcard_removals(parsed: dict) -> list[str]:
    """`rm` of a whole node folder (`<dir>/*`, or `rm -r <dir>`) outside the staging area (OB-76, run c89f1b67: the
    rollback's fallback `|| rm -rf ${MRF_DESTINATION_PATH}/*` deleted every announcement): the user's rule is never
    to touch files the activity did not load — remove exactly the loaded files (structure.py's items list)."""
    errors = []
    for phase, step in _iter_steps(parsed.get("phases") or {}):
        send = str(step.get("send") or "")
        for m in _RM_WIDE.finditer(send):
            flags, target = m.group(1), m.group(2)
            if _temp_path(target) or target.startswith("$("):
                continue
            bare = target.strip("'\"")
            folder = bare.endswith("/") or bool(re.fullmatch(r"\$\{[^{}]+\}", bare))
            if bare.endswith("/*") or ("r" in flags.replace("-", "") and folder):
                errors.append(f"Phase '{phase}': `{send.strip()[:110]}` removes `{target}` — everything in that node "
                              "folder, not only what the activity loaded (never touch files unrelated to the activity). "
                              "Remove exactly the loaded files, e.g. from the list of what the activity put there.")
    return errors


_TAR_CREATE = re.compile(r"\btar\s+-?\w*c\w*f\s+(\S+)")
_TAR_EXTRACT = re.compile(r"\btar\s+-?\w*x\w*f\s+(\S+)")
_PRINTF_GET = re.compile(r"\b(?:get|put)\s+(\S+)\s+(\S+)")
_COPY_DEST = re.compile(r"\b(?:cp|mv|scp)\s+(?:-\S+\s+)*\S+\s+(\S+)")


def _path_pattern(path: str) -> str:
    """A path with its placeholders and shell variables as wildcards (`/tmp/${ORDER_NO}/x_$DATE.tar.gz` →
    `/tmp/*/x_*.tar.gz`)."""
    path = path.strip("'\";")
    path = re.sub(r"\$\{[^{}]+\}|\$\([^()]*\)|\$[A-Za-z_]\w*", "*", path)
    return re.sub(r"/+", "/", path)


def check_restore_sources(parsed: dict) -> list[str]:
    """A rollback that extracts an archive no step writes (OB-76, run c89f1b67: the backup wrote
    `/tmp/${ORDER_NO}/<folder>/announcement_backup_….tar.gz` and the repo copy, the rollback extracted
    `/tmp/announcement_backup_*.tar.gz` — nothing to restore, the node kept the loaded files)."""
    import fnmatch
    from .contract import ROLLBACK, phase_kind
    phases = parsed.get("phases") or {}
    if not isinstance(phases, dict):
        return []
    written: set[str] = set()
    for phase, step in _iter_steps(phases):
        send = str(step.get("send") or "")
        written |= {_path_pattern(m.group(1)) for m in _TAR_CREATE.finditer(send)}
        written |= {_path_pattern(m.group(2)) for m in _PRINTF_GET.finditer(send)}
        written |= {_path_pattern(m.group(1)) for m in _COPY_DEST.finditer(send)}
        sftp = step.get("sftp") if isinstance(step.get("sftp"), dict) else {}
        for key in ("remote_path", "local_path"):
            if sftp.get(key):
                written.add(_path_pattern(str(sftp[key])))
    written.discard("")
    errors = []
    for key, body in phases.items():
        if not isinstance(body, dict) or phase_kind(str(key), body) != ROLLBACK:
            continue
        for _, step in _iter_steps({key: body}):
            send = str(step.get("send") or "")
            for m in _TAR_EXTRACT.finditer(send):
                archive = _path_pattern(m.group(1))
                name = archive.rsplit("/", 1)[-1]
                if not archive.startswith("/") or archive in written or not name.replace("*", "").strip("._-"):
                    continue  # a registered name (`/tmp/${backupExists}`) cannot be told
                literal = [w for w in written if w.rsplit("/", 1)[-1].replace("*", "").strip("._-")]
                if any(fnmatch.fnmatch(w, archive) or fnmatch.fnmatch(archive, w) for w in literal):
                    continue
                backups = sorted(w for w in written if w.rsplit("/", 1)[-1].split("*")[0][:6]
                                 and name.split("*")[0][:6] == w.rsplit("/", 1)[-1].split("*")[0][:6])
                errors.append(f"Phase '{key}': `{send.strip()[:110]}` restores `{m.group(1)}`, which no step writes"
                              + (f" — the backup is written as {', '.join(f'`{b}`' for b in backups[:3])}" if backups
                                 else "") + ". Restore the archive the backup wrote (the same path, copied back to "
                              "the node first when it is on the repo server).")
    return errors


def check_helper_script_nodes(parsed: dict, known_commands: list[str]) -> list[str]:
    """A Python script the MOP / recorded sessions never run is one of ours: the pipeline generates it for the
    automation server (helper_script_gen), no node has it (OB-39: ``python3 /usr/local/bin/check_tac_counts.py`` on
    the EIR node, an invented path). It runs on ``local``, after the files it reads are copied there."""
    known = "\n".join(known_commands)
    errors = []
    for phase, step in _walk_all_steps(parsed.get("phases") or {}):
        node = str(step.get("node") or "")
        send = str(step.get("send") or "")
        if not node or node.upper() == "LOCAL" or node.startswith("${"):
            continue
        if _PY_INLINE.match(send) and send.strip() not in known:
            # OB-65 (run b81305ff): `python3 -c "import pandas … read_excel(DELTA_TAC_FILE) … open(dump)"` on the
            # bastion — the CIQ file is on the automation server, the dumps inside the pod
            errors.append(f"Phase '{phase}': an inline Python program (`python3 -c …`) runs on {node}, but the "
                          "nodes have neither the pipeline's input files nor its Python libraries — make it a helper "
                          "script the pipeline generates: `python3 <name>.py --<arg> …` on node local (bare name), "
                          "after copying every node file it reads to the automation server (out of a pod with "
                          "`oc cp`/`kubectl cp` to the node first, then an sftp download step)")
            continue
        m = _PY_CALL.match(send)
        if not m:
            continue
        if m.group(2) in known:
            continue  # the node's own tool
        errors.append(f"Phase '{phase}': `{m.group(1)}{m.group(2)}` runs on {node}, but no node has it — it is a "
                      f"helper script the pipeline generates for the automation server: run `python3 {m.group(2)} …` "
                      "on node local (bare name), after copying every node file it reads to the automation server "
                      "(out of a pod with `oc cp`/`kubectl cp` to the node first, then an sftp download step)")
    return errors


_PROMPT_STEP = re.compile(r"^(su(\s+-)?\s+\S+|exit|logout)$")
_SAMPLE_EXCERPT_LINES = 8


_CIQ_COLUMN = re.compile(r"\$\{configSequences\.\d+\.records\.\d+\.data\.([^}]+)\}")


def behaviour_issues(yaml_text: str, ciq_sheets: dict | None = None, expected: dict | None = None) -> list[str]:
    """The errors of ``validate_workflow`` about WHAT the activity does (requirements coverage, CIQ
    rows and columns the activity needs, request inputs) — a developer can answer those. All other
    errors are about HOW the framework is used (placeholders, regexes, quoting, conditions): step 6
    fixes them itself and they are never asked as questions (DEP-53)."""
    parsed = parse_yaml(yaml_text)
    if parsed is None:
        return []
    from .structure import flatten
    flatten(parsed)
    issues = list(check_unused_record_columns(parsed, (expected or {}).get("record_columns")))
    issues += check_completeness(parsed, expected)
    if ciq_sheets:
        issues += check_ciq_coverage(parsed, ciq_sheets)
        issues += check_alternative_columns(parsed, ciq_sheets)
        issues += check_request_inputs(parsed, ciq_sheets, (expected or {}).get("request_columns") or {})
    return issues


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
            # a shell guard on the column (`if [ "${…Test IMSI}" != "" ]; then …; else …; fi`, run 6570694d) runs the
            # branch without it for the rows that leave it empty (OB-88)
            missing = [r for r in rows if empty(r, col) and col in _CIQ_COLUMN.findall(_runs_for_row(send, r))]
            # Empty everywhere = an optional value passed through (scripts skip it); only a
            # column some rows fill and others leave empty breaks those node groups
            if not missing or len([r for r in rows if empty(r, col)]) == len(rows):
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


_RECORD_REF = re.compile(r"configSequences\.\d+\.records\.\d+\.data\.([^}=!<>&|'\"\n]+?)\s*(?=\}|==|!=|>=|<=|[<>]|&&|\|\||$)")


def check_record_columns(parsed: dict, record_columns: dict | None) -> list[str]:
    """``configSequences…records…data.<Col>`` must be a column of the CIQ JSON records (the
    data sheet). MRF_ANNOUNCEMENT_LOADING used Circle/VER from Node_Details: empty, so every
    path became ``…/audioclips//PREPAID/`` (G-15, JAR-verified)."""
    cols = list((record_columns or {}).get("columns") or [])
    if not cols:
        return []
    sheet = (record_columns or {}).get("sheet") or "the data sheet"
    bad: dict[str, str] = {}
    for phase_name, step in _iter_steps(parsed.get("phases") or {}):
        for key in ("send", "when", "skip_when"):
            for m in _RECORD_REF.finditer(str(step.get(key) or "")):
                col = m.group(1).strip()
                if col not in cols:
                    bad.setdefault(col, f"{phase_name}: {str(step.get('send') or step.get('command_description'))[:60]}")
    node_values = [f"${{{ntype}.{f}}}" for ntype, fields in ((record_columns or {}).get("node_fields") or {}).items()
                   for f in fields]
    other = (f"node values {', '.join(node_values)}, " if node_values else "") + \
        "a value derived in the command (e.g. $(basename ${configSequences.0.records.0.data.<Column>} .tar)) " \
        "or a value registered from the node"
    return [f"CIQ column '{c}' (step {where}) is not in the CIQ JSON records — they hold only {sheet} columns "
            f"{cols}; any other column resolves to empty. Use those columns, {other}."
            for c, where in bad.items()]


_CONDITION_CALL = re.compile(r"\.\w+\s*\(|(?<![!=<>])!(?!=)")


def check_unused_record_columns(parsed: dict, record_columns: dict | None) -> list[str]:
    """A record column every CIQ row fills is data the activity needs: MRF_ANNOUNCEMENT_LOADING
    ignored MRF_DESTINATION_PATH and wrote files to a path built from GROUP (G-15)."""
    filled = list((record_columns or {}).get("filled") or [])
    if not filled:
        return []
    text = "\n".join(_iter_strings(parsed.get("phases") or {}))
    unused = [c for c in filled if f"data.{c}" not in text]
    sheet = (record_columns or {}).get("sheet") or "the data sheet"
    samples = (record_columns or {}).get("samples") or {}
    problems = []
    for c in unused:
        msg = (f"CIQ column '{c}' ({sheet}) is filled for every CIQ row but the workflow never uses it — use "
               f"${{configSequences.0.records.0.data.{c}}} where the MOP needs that value instead of a fixed or "
               "derived one")
        vals = [v for v in samples.get(c, []) if v][:3]
        if vals:
            msg += f". CIQ values: {', '.join(vals)}"
            # A path column next to the paths the workflow builds itself (the MOP's example path)
            if all(v.startswith("/") for v in vals):
                prefix = "/".join(vals[0].split("/")[:4])
                built = sorted({p.rstrip("/") for p in re.findall(r"/[^\s'\";|&]+", text) if p.startswith(prefix)})
                if built:
                    msg += f" — the workflow builds {', '.join(built[:2])} instead"
        problems.append(msg)
    return problems


def check_condition_syntax(parsed: dict) -> list[str]:
    """``when``/``skip_when`` compare operands with ==, !=, <, >, <=, >= joined by && / ||.
    A method call (``x.endsWith('.tar')``) or ``!`` makes the condition false — with
    ``x.endsWith(…)`` and ``!x.endsWith(…)`` both branches were skipped (G-15, JAR-verified)."""
    problems = []
    for phase_name, step in _iter_steps(parsed.get("phases") or {}):
        for key in ("when", "skip_when"):
            cond = str(step.get(key) or "")
            if cond and _CONDITION_CALL.search(re.sub(r"'[^']*'|\"[^\"]*\"", "''", cond)):
                problems.append(
                    f"Step '{str(step.get('send') or step.get('command_description'))[:60]}' ({phase_name}): {key} "
                    f"'{cond[:90]}' uses a method call or '!' — the framework only compares values (==, !=, <, >) "
                    "joined by && / ||, so this is always false. Decide inside the command instead "
                    "(e.g. case \"$f\" in *.tar) tar -xf …;; *) cp …;; esac) or compare whole values.")
    return problems


def check_unbalanced_quotes(parsed: dict) -> list[str]:
    """A command whose quotes don't close makes the shell wait for more input: the step never
    gets its prompt back and times out (DEP-50)."""
    from .autofix import unbalanced_quotes
    return [f"Step '{str(step.get('send'))[:80]}' ({phase_name}): its quotes are not balanced — the shell "
            "would wait for the closing quote and the step hangs. Close or remove the stray quote."
            for phase_name, step in _iter_steps(parsed.get("phases") or {})
            if isinstance(step.get("send"), str) and unbalanced_quotes(step["send"])]


_SELF_COMPARE = re.compile(r"(?<![\w.])([A-Za-z_][\w.]*)\s*(?:==|!=|>=|<=)\s*\1(?![\w.])")


def check_iteration(parsed: dict) -> list[str]:
    """Per-item work the framework does not do the way the LLM wrote it (spec 013, EIR):
    - ``for_each`` on a step without ``type: loop`` is ignored: the command runs once;
    - a variable a looped register (``loop: true``) captures holds no value when a step reads it (probed: '');
    - an expression comparing a value with itself is always true (``A == A`` checked nothing).
    Production runs a command per item as one shell loop: ``for i in `<list>`; do <cmd with $i>; done``."""
    phases = parsed.get("phases") or {}
    looped, plain = set(), set()
    for _, step in _iter_steps(phases):
        for reg in step.get("register") or []:
            if isinstance(reg, dict):
                names = set(_NAMED_GROUP.findall(str(reg.get("regex") or "")))
                (looped if reg.get("loop") else plain).update(names)
    looped -= plain
    loop_fix = ("run the command for every item in ONE step as a shell loop over the list command: "
                "`for i in `<command that lists them>`; do <command using $i>; done`")
    problems, seen = [], set()
    for phase, step in _iter_steps(phases):
        send = str(step.get("send") or "")
        where = f"Step '{send[:70]}' ({phase})"
        if "for_each" in step and step.get("type") != "loop":
            problems.append(f"{where}: `for_each` only works on a `type: loop` step — here it is ignored and the "
                            f"command runs once; {loop_fix}.")
        for name in sorted(looped):
            if name not in seen and re.search(r"\$\{" + re.escape(name) + r"\}", send):
                seen.add(name)
                problems.append(f"{where}: ${{{name}}} comes from a register with `loop: true` (it only counts "
                                f"matches) and is empty here; {loop_fix}.")
        texts = [str(step.get(k) or "") for k in ("when", "skip_when")]
        v = step.get("validation")
        for branch in ("success", "failure", "warning"):
            crit = (v.get(branch) or {}).get("criteria") if isinstance(v, dict) and isinstance(v.get(branch), dict) else None
            if isinstance(crit, dict):
                texts.append(str(crit.get("expr") or ""))
        for t in texts:
            m = _SELF_COMPARE.search(t)
            if m:
                problems.append(f"{where}: `{m.group(0)}` compares {m.group(1)} with itself — always true, it checks "
                                "nothing. Compare the values that must agree (e.g. the counts of all items: "
                                "`… | sort -u | wc -l` must be 1).")
                break
    return problems


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
    problems += _long_listing_name_runs(parsed)
    return problems


_LS_LONG = re.compile(r"(?:^|[;&|]\s*)ls\s+-\w*l")
# `….txt\s+name…`: two file names next to each other (an extension, then whitespace, then a name)
_NAMES_JOINED = re.compile(r"\\?\.[A-Za-z0-9]{1,5}(?:\\s[+*]|\s+)[A-Za-z_]")


def _long_listing_name_runs(parsed: dict) -> list[str]:
    """``ls -l`` prints one file per line, its mode / owner / size / date before the name, in the listing's order
    (``-t``: by time): a regex wanting the names next to each other (``a_.+?.txt\\s+b_.+?.txt\\s+c_.+?.txt``) never
    matches — the EIR rollback's dump check failed on the files it had just written (OB-40)."""
    problems = []
    from .autofix import _criteria_blocks
    for phase_name, step in _iter_steps(parsed.get("phases") or {}):
        send = str(step.get("send") or "")
        if not _LS_LONG.search(send):
            continue
        for criteria in _criteria_blocks(step):
            regex = str(criteria.get("regex") or "")
            if _NAMES_JOINED.search(regex):
                problems.append(
                    f"Step '{send[:80]}' ({phase_name}): `ls -l` prints one file per line with its details before "
                    f"the name, in the listing's order — the regex {regex!r} wants the names next to each other "
                    "and never matches. Check each file: a loop count (register {regex: '<name pattern>', loop: "
                    "true, count_var: N} per file, expr 'N1 > 0 && N2 > 0 …') or one `ls -l <file>` step per file.")
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


_SHELL_GUARD = re.compile(
    r"""if\s+\[\[?\s*(-n|-z)?\s*["']?\$\{configSequences\.\d+\.records\.\d+\.data\.([^}]+)\}["']?\s*"""
    r"""(?:(!=|==|=)\s*(?:""|''))?\s*\]\]?\s*;\s*then\s+(.*?)(?:\s*;\s*else\s+(.*?))?\s*;\s*fi\b""", re.S)


def _runs_for_row(send: str, row: dict) -> str:
    """The command text that runs for one CIQ row: a simple shell guard on a CIQ column being empty or not
    (``if [ "${…Test IMSI}" != "" ]; then echo "${…Test IMSI}"; else echo "${resolvedImsi}"; fi``) keeps only
    the branch the row takes (OB-88)."""
    def branch(m: re.Match) -> str:
        value = str(row.get(m.group(2).strip()) or "").strip()
        flag, op = m.group(1), m.group(3)
        filled = flag == "-n" or op == "!=" or (flag is None and op is None)
        return (m.group(4) or "") if (bool(value) == filled) else (m.group(5) or "")
    return _SHELL_GUARD.sub(branch, send)


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
        """The step may run for ``row``: some ``||`` alternative can hold. A term on a CIQ
        column is decided by the row; any other term (a registered value such as
        ``resolvedImsi != ''``) is known only at run time and counts as possibly true (DEP-56)."""
        for alternative in guard.split("||"):
            holds = True
            for term in alternative.split("&&"):
                m = _WHEN_TERM.match(term)
                if not m:
                    continue
                value = str(row.get(m.group(1).strip()) or "").strip()
                if (value == m.group(3)) != (m.group(2) == "=="):
                    holds = False
                    break
            if holds:
                return True
        return False

    problems: dict[str, dict] = {}
    for rows in sheets.values():
        for row in rows:
            label = str(next((v for v in row.values() if str(v or "").strip()), "row"))
            registered: set[str] = set()
            for step, guard in zip(steps, guards):
                if not runs(guard, row):
                    continue
                for name in {m.group(1).strip() for m in _PLACEHOLDER.finditer(
                        _runs_for_row(str(step.get("send") or ""), row))}:
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
        counted = _count_check_fails_on_real_output(step, outputs)
        if counted:
            problems.append(
                f"Step '{send[:80]}' ({phase_name}): on the real output of a healthy node the count check gives "
                f"{counted} — every item must show the expected text there. Fix the two count regexes (one line "
                f"per item / one line per item showing the text). Real output (relevant lines):\n"
                f"{_excerpt(matching[0]['output'], '')}")
        captured = _expr_fails_on_real_output(step, outputs)
        if captured:
            problems.append(
                f"Step '{send[:80]}' ({phase_name}): on the real output the step registers {captured}, so its "
                "expr fails on a healthy node — the register regex captures the wrong field. Real output "
                f"(relevant lines):\n{_excerpt(matching[0]['output'], _success_regex(step) or '')}\n"
                "Count the columns in this output and capture the right one.")
        if not counted and not captured:
            false = _expr_false_on_real_output(step, outputs)
            if false:
                problems.append(
                    f"Step '{send[:80]}' ({phase_name}): its success expr "
                    f"{((step.get('validation') or {}).get('success') or {}).get('criteria', {}).get('expr')!r} is "
                    f"false on the real output of a healthy node ({false}). Every line of the output counts — item "
                    "names, notices like 'Defaulted container …', blank lines. Check for the bad state instead: "
                    "register a capture of a line in a wrong state (e.g. (?<BADSTATE>… (?!ESTABLISHED\\b)\\S+)) and "
                    "expr 'BADSTATE == \"\"'. Real output (relevant lines):\n"
                    f"{_excerpt(matching[0]['output'], '')}")
        exprs = " ".join(str(((step.get("validation") or {}).get(b) or {}).get("criteria", {}).get("expr", ""))
                         for b in ("success", "failure", "warning") if isinstance(step.get("validation"), dict)
                         and isinstance((step["validation"].get(b) or {}), dict)
                         and isinstance((step["validation"].get(b) or {}).get("criteria"), dict))
        for reg in step.get("register") or []:
            rx = reg.get("regex") if isinstance(reg, dict) else None
            if not isinstance(rx, str):
                continue
            # production's "must be absent" captures find nothing on a healthy node: a bad state / non-zero
            # count the step requires empty, or an error capture (ERROR:.*|error:.*) — spec 013
            names = _NAMED_GROUP.findall(rx)
            count = str(reg.get("count_var") or "") if isinstance(reg, dict) else ""
            if any(re.search(rf"(?<![\w.]){re.escape(n)}\s*==\s*(?:''|\"\")", exprs) for n in names) \
                    or (count and re.search(rf"(?<![\w.]){re.escape(count)}\s*==\s*0(?![\d.])", exprs)) \
                    or re.fullmatch(r"\(\?<\w+>(?:[Ee]rror|ERROR)[^|)]*(?:\|(?:[Ee]rror|ERROR)[^|)]*)*\)", rx):
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
    # Hosts reached from a NIAM login run as nested sessions on its node (spec 013 B-R2): covered by it
    nested = any(n.startswith("NIAM_") for n in used_nodes)
    for node, count in sorted(node_cmds.items()):
        if count >= 2 and node.upper() not in used_nodes and not nested:
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
    from .autofix import EXPR_IDENT, EXPR_KEYWORDS, EXPR_UNSUPPORTED, _criteria_blocks
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
            if isinstance(reg, dict) and isinstance(reg.get("count_var"), str):
                defined.add(reg["count_var"])  # loop: true + count_var (spec 011 count checks)
        for criteria in _criteria_blocks(step):
            expr = criteria.get("expr")
            if not isinstance(expr, str):
                continue
            if EXPR_UNSUPPORTED.search(expr):
                problems.append(
                    f"Step '{str(step.get('send') or step.get('command_description'))[:80]}' ({phase_name}): expr "
                    f"'{expr}' is not something criteria.expr can evaluate: it only compares registered values "
                    "(a == b, n >= 90, joined with && or ||) and has no functions, lists, [i], .length or =>. "
                    "To check that every pod / line reports the same value, compute it in the command "
                    "(… | sort -u | wc -l), register that count (register: [{regex: '(?<distinctCount>\\d+)'}]) "
                    "and use expr 'distinctCount == 1'.")
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


_EXPR_TERM = re.compile(r"""^\s*([A-Za-z]\w*|-?\d+(?:\.\d+)?|'[^']*'|"[^"]*")\s*(==|!=|>=|<=|>|<)\s*"""
                        r"""([A-Za-z]\w*|-?\d+(?:\.\d+)?|'[^']*'|"[^"]*")\s*$""")


def _register_values(step: dict, output: str) -> dict[str, str | int] | None:
    """What the JAR registers from ``output``: a loop ``count_var`` → how many matches, a named group → the first
    match's FIRST group ("" when nothing matched). None when a regex does not compile."""
    values: dict[str, str | int] = {}
    text = output.replace("\r\n", "\n")
    for reg in step.get("register") or []:
        rx = reg.get("regex") if isinstance(reg, dict) else None
        if not isinstance(rx, str):
            continue
        compiled = _py(rx)
        if compiled is None:
            return None
        if reg.get("loop") and isinstance(reg.get("count_var"), str):
            values[reg["count_var"]] = sum(1 for _ in compiled.finditer(text))
        for name in _NAMED_GROUP.findall(rx):
            hit = compiled.search(text)
            values.setdefault(name, (hit.group(1) or "") if hit and hit.re.groups else "")
    return values


def _evaluate_expr(expr: str, values: dict) -> bool | None:
    """``A OP B`` terms joined by ``&&`` / ``||`` (``&&`` binds tighter, as in the JAR), operands registered names,
    numbers or quoted strings. None when it uses anything else (a name the step does not register, parentheses…)."""
    expr = expr.strip()
    if expr.startswith("${") and expr.endswith("}"):
        expr = expr[2:-1]
    if "(" in expr or not expr:
        return None

    def operand(tok: str):
        if tok[0] in "'\"":
            return tok[1:-1]
        if re.fullmatch(r"-?\d+(?:\.\d+)?", tok):
            return tok
        return values[tok] if tok in values else KeyError

    def term(text: str) -> bool | None:
        m = _EXPR_TERM.match(text)
        if not m:
            return None
        a, op, b = operand(m.group(1)), m.group(2), operand(m.group(3))
        if a is KeyError or b is KeyError:
            return None
        try:
            a, b = float(a), float(b)
        except (TypeError, ValueError):
            a, b = str(a), str(b)
            if op not in ("==", "!="):
                return None
        return {"==": a == b, "!=": a != b, ">=": a >= b, "<=": a <= b, ">": a > b, "<": a < b}[op]

    any_of = []
    for alternative in expr.split("||"):
        parts = [term(t) for t in alternative.split("&&")]
        if any(p is None for p in parts):
            return None
        any_of.append(all(parts))
    return any(any_of)


def _expr_false_on_real_output(step: dict, outputs: list[str]) -> str:
    """'NAME=value, …' when the step's success expr is false on every recorded (healthy) output — e.g. a count of
    lines WITHOUT "ESTABLISHED" that must be 0, while every pod also prints its name (OB-41). Empty otherwise."""
    validation = step.get("validation") if isinstance(step.get("validation"), dict) else {}
    success = validation.get("success") if isinstance(validation.get("success"), dict) else {}
    criteria = success.get("criteria") if isinstance(success.get("criteria"), dict) else {}
    expr = str(criteria.get("expr") or "")
    if not expr or not outputs:
        return ""
    seen = []
    for out in outputs:
        values = _register_values(step, out)
        if values is None:
            return ""
        verdict = _evaluate_expr(expr, values)
        if verdict is None or verdict:
            return ""
        used = [n for n in re.findall(r"[A-Za-z]\w*", expr) if n in values]
        seen.append(", ".join(f"{n}={values[n]!r}" for n in dict.fromkeys(used)))
    return "; ".join(seen[:2])


_COUNT_NAME = r"\$?\{?([A-Za-z]\w*)\}?"  # count_var names may hold `_` (only regex group names may not)
_COUNT_EQUAL = re.compile(rf"^\s*{_COUNT_NAME}\s*==\s*{_COUNT_NAME}\s*$")
_COUNT_POSITIVE = re.compile(rf"^\s*{_COUNT_NAME}\s*>\s*0\s*$")


def count_check(step: dict) -> tuple[str, str, str, str] | None:
    """(ok var, ok regex, total var, total regex) of a count check: ``register`` two loop counts and
    ``expr: OK == TOTAL && TOTAL > 0`` (spec 011 "every item") — the two terms in either order, the ``> 0`` on either
    count (``ESTABLISHED_COUNT > 0 && ESTABLISHED_COUNT == POD_COUNT``, OB-40); None otherwise."""
    validation = step.get("validation") if isinstance(step.get("validation"), dict) else {}
    success = validation.get("success") if isinstance(validation.get("success"), dict) else {}
    criteria = success.get("criteria") if isinstance(success.get("criteria"), dict) else {}
    expr = str(criteria.get("expr") or "").strip()
    if expr.startswith("${") and expr.endswith("}"):
        expr = expr[2:-1]
    terms = expr.split("&&")
    if len(terms) != 2:
        return None
    equal = next((m for m in map(_COUNT_EQUAL.match, terms) if m), None)
    positive = next((m for m in map(_COUNT_POSITIVE.match, terms) if m), None)
    if not equal or not positive or positive.group(1) not in equal.groups():
        return None
    counts = {r["count_var"]: str(r.get("regex") or "") for r in step.get("register") or []
              if isinstance(r, dict) and r.get("loop") and isinstance(r.get("count_var"), str)}
    total = positive.group(1)
    ok = equal.group(2) if equal.group(1) == total else equal.group(1)
    if ok not in counts or total not in counts:
        return None
    return ok, counts[ok], total, counts[total]


def _py(regex: str) -> re.Pattern | None:
    try:
        return re.compile(re.sub(r"\(\?<([A-Za-z][A-Za-z0-9]*)>", r"(?P<\1>", regex), re.MULTILINE)
    except re.error:
        return None


def _count_check_fails_on_real_output(step: dict, outputs: list[str]) -> str:
    """'OK=a of TOTAL=b' when no real (healthy) output passes the step's count check."""
    check = count_check(step)
    if not check or not outputs:
        return ""
    ok_name, ok_rx, total_name, total_rx = check
    ok_p, total_p = _py(ok_rx), _py(total_rx)
    if ok_p is None or total_p is None:
        return ""
    seen = []
    for out in outputs:
        ok_n, total_n = len(ok_p.findall(out)), len(total_p.findall(out))
        if total_n > 0 and ok_n == total_n:
            return ""
        seen.append(f"{ok_name}={ok_n} of {total_name}={total_n}")
    return "; ".join(seen[:2])


_EVERY_SCOPE = re.compile(
    r"\b(every|all|each)\b[^.;]*?\b(lines?|entr(?:y|ies)|nodes?|items?|files?|samples?|rows?|cores?|instances?|"
    r"records?|links?|interfaces?|process(?:es)?|members?|subscribers?)\b", re.I)


def check_scope_every(parsed: dict) -> list[str]:
    """A check that must hold for EVERY item (every remote node "Link UP", all files 777) is
    a count, not "one line matches" — the regex passes when a single item shows the text
    (spec 011; MRF M-6, PGW G-6)."""
    problems: list[str] = []
    for phase_name, step in _iter_steps(parsed.get("phases") or {}):
        validation = step.get("validation") if isinstance(step.get("validation"), dict) else {}
        text = f"{validation.get('description') or ''}"
        if not _EVERY_SCOPE.search(text) or count_check(step):
            continue
        if not _success_regex(step):
            continue  # nothing checks the output (a script decides)
        problems.append(
            f"Step '{str(step.get('send') or step.get('command_description') or '')[:80]}' ({phase_name}): the check "
            f"says \"{text.strip()[:120]}\" — every item must show it, but the success regex passes when ONE line "
            "matches. Count instead: register: [{regex: '<line of one item>', loop: true, count_var: TOTAL}, "
            "{regex: '<line of one item showing the expected text>', loop: true, count_var: OKN}] and success "
            "criteria: {expr: 'OKN == TOTAL && TOTAL > 0'} (names: letters/digits; regexes from the real output).")
    return problems


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
    """``send`` is the recorded command: ``${…}`` stands for any value, and spacing around shell operators is free
    (``-n ${NS} | grep`` ran as ``-n nkasjock1ei1| grep`` — the per-pod count check was never held against its
    real output, OB-40)."""
    from tools.command_samples import literal_pattern
    send, recorded = _normalise(send), _normalise(recorded)
    if send == recorded:
        return True
    parts = re.split(r"\$\{[^}]*\}", send)
    pattern = ".+?".join(literal_pattern(p) for p in parts)
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
    # cli-automation generate drops every command of a node group with several nodes
    # when a step uses ${node} (MRF CR1: MRF1+MRF2 → empty MOP, 2026-11-03)
    (re.compile(r"\$\{node\}"), "empties the MOP of a node group with several nodes — register the "
                                "node's `hostname` and use that variable"),
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


def check_placeholders(parsed: dict, runtime_params: list[str] | None = None,
                       node_fields: dict[str, list[str]] | None = None,
                       node_types: dict[str, int] | None = None) -> list[str]:
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
    for _, step in _iter_steps(phases):  # computed registers: - name: X / value: … (production)
        defined |= {str(r["name"]) for r in step.get("register") or [] if isinstance(r, dict) and r.get("name")}
    defined |= set(_iter_count_vars(phases))

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
                # ${<TYPE>.niamID-n}: the CIQ node type's NIAM entry (spec 013); older workflows name the node so
                if root in _DOTTED_ROOTS or ((root in node_names or root in (node_types or {})) and re.match(
                        rf"^{re.escape(root)}\.niamID-\d+$", expr)):
                    continue
                if node_types and re.match(r"^[^.]+\.niamID-\d+$", expr):
                    problems.setdefault(m.group(0), f"'{root}' is not a node type of the CIQ — the NIAM login is "
                                        f"${{<TYPE>.niamID-<n>}} with TYPE one of {', '.join(sorted(node_types))}")
                    continue
                # Node values the CIQ JSON carries on the node entry (${MRF.VER})
                if expr.split(".", 1)[1] in ((node_fields or {}).get(root) or []):
                    continue
                problems.setdefault(m.group(0), "unknown placeholder root "
                                    f"'{root}' (not a node NIAM ID, configSequences, ENV, SECRET or vars)")
            elif expr not in defined:
                problems.setdefault(m.group(0), "variable is never registered by a step "
                                    "(add a register with a (?<name>…) group on the step that produces it)")
    return [f"Unresolvable placeholder {ref}: {why}"
            + (" — a bash array element: the framework fills every ${…} before the node sees it, so "
               "arrays cannot be used; compute it in one pipeline instead (e.g. `… | sort -u | wc -l`)"
               if "[" in ref else "")
            for ref, why in problems.items()]


def _iter_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _iter_strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _iter_strings(v)


def _iter_count_vars(value: Any):
    if isinstance(value, dict):
        for reg in value.get("register") or []:
            if isinstance(reg, dict) and isinstance(reg.get("count_var"), str):
                yield reg["count_var"]
        for v in value.values():
            yield from _iter_count_vars(v)
    elif isinstance(value, list):
        for v in value:
            yield from _iter_count_vars(v)


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
