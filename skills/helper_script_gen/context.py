"""What a helper script must know beyond its command line (OB-58): the check of the step that runs it and the
format of every file it reads.

validate_tac_delta.py got the first 3000 characters of the workflow only: it failed when the delta TACs WERE in
the post dump (the step: "must be present") and compared whole dump records (``1,sb,eir,white,0169…``) with
TACs, so it could never fail.
"""
from __future__ import annotations

import re
import shlex
from pathlib import Path

_PY_SCRIPT = re.compile(r"^\s*python3?\s+(\S+\.py)\b")
_REDIRECT = re.compile(r"(?<![\d&>])>>?\s*(['\"]?)([^\s'\";|&]+)\1")
_CIQ_REF = re.compile(r"\$\{(?:configSequences\.\d+\.records\.\d+|[A-Za-z_]\w*)\.data\.([^}]+)\}")
_FILE_ARG = re.compile(r"\.[A-Za-z][A-Za-z0-9]{0,5}$|\$\{[^}]*\.data\.[^}]*\}$")


def _steps(workflow: dict):
    phases = workflow.get("phases") if isinstance(workflow, dict) else None

    def walk(steps):
        for s in steps or []:
            if isinstance(s, dict):
                yield s
                yield from walk(s.get("steps"))
    for body in (phases.values() if isinstance(phases, dict) else phases or []):
        yield from walk(body.get("steps") if isinstance(body, dict) else body)


def _name_pattern(path: str) -> re.Pattern:
    """A file name with its ``${…}`` parts as wildcards (``postdump_white_${DATE}.txt``)."""
    name = path.rstrip("/").rsplit("/", 1)[-1]
    return re.compile("".join(".+?" if p.startswith("${") else re.escape(p)
                              for p in re.split(r"(\$\{[^}]*\})", name) if p) + "$")


def file_args(command: str) -> list[tuple[str, str]]:
    """(flag, value) of the script's arguments that name files."""
    try:
        argv = shlex.split(command, posix=True)
    except ValueError:
        argv = command.split()
    return [(f, v) for f, v in zip(argv[2:], argv[3:]) if f.startswith("-") and not v.startswith("-")
            and _FILE_ARG.search(v)]


def _writer(workflow: dict, value: str) -> tuple[str, str]:
    """(node, command) of the step whose ``cmd >file`` writes the file ``value`` names (by its name: the file
    reaches the automation server by copies and downloads)."""
    if not re.sub(r"\$\{[^}]*\}", "", value.rsplit("/", 1)[-1]).strip("._-"):
        return "", ""  # a name that is all placeholder matches every file
    pattern = _name_pattern(value)
    for s in _steps(workflow):
        send = s.get("send")
        if not isinstance(send, str) or str(s.get("node", "")).lower() == "local":
            continue
        from skills.network_simulator_setup.vfs_commands import script_parts
        # `export …; source …; org -c X >file` (also inside `kubectl exec … -- bash -c '…'`): the part that writes it
        for part in script_parts(send):
            for _, target in _REDIRECT.findall(part):
                if pattern.match(target.rsplit("/", 1)[-1]):
                    return str(s.get("node")), part.strip()
    return "", ""


_WHOLE_VAR = re.compile(r"^\$\{([A-Za-z][A-Za-z0-9]*)\}$")


def _registers(workflow: dict) -> dict[str, tuple[str, str]]:
    """{registered name: (command, regex)}."""
    out: dict[str, tuple[str, str]] = {}
    for s in _steps(workflow):
        for reg in s.get("register") or []:
            rx = str(reg.get("regex") or "") if isinstance(reg, dict) else ""
            for name in re.findall(r"\(\?<([A-Za-z][A-Za-z0-9]*)>", rx):
                out.setdefault(name, (str(s.get("send") or "").strip(), rx))
    return out


def _value_args(command: str) -> list[tuple[str, str]]:
    """(flag, value) of the script's arguments that are one registered value (``--postdump_white "${DIFF}"``)."""
    try:
        argv = shlex.split(command, posix=True)
    except ValueError:
        argv = command.split()
    return [(f, v) for f, v in zip(argv[2:], argv[3:]) if f.startswith("-") and _WHOLE_VAR.match(v)
            and not _CIQ_REF.search(v)]


def _ciq_value(column: str, requirements: dict) -> str:
    for sheet in ((requirements.get("doc_extracted") or {}).get("ciq_sheets") or {}).values():
        for row in (sheet or {}).get("sample_rows") or []:
            if isinstance(row, dict) and str(row.get(column) or "").strip():
                return str(row[column]).strip()
    return ""


def script_context(workflow: dict, script_name: str, requirements: dict, documents: list[str] | None = None,
                   samples: list[dict] | None = None) -> str:
    """Prompt section: what each step running ``script_name`` checks, and a sample of each file it reads."""
    from skills.network_simulator_setup.vfs_commands import redirect_contents
    from tools.input_documents import preview, sample_for
    checks, files = [], []
    seen: set[str] = set()
    for s in _steps(workflow):
        send = s.get("send")
        m = _PY_SCRIPT.match(send) if isinstance(send, str) else None
        if not m or m.group(1).replace("\\", "/").rsplit("/", 1)[-1] != script_name:
            continue
        val = s.get("validation") if isinstance(s.get("validation"), dict) else {}
        ok = (val.get("success") or {}) if isinstance(val.get("success"), dict) else {}
        bad = (val.get("failure") or {}) if isinstance(val.get("failure"), dict) else {}
        checks.append("\n".join(x for x in (
            f"- step: {s.get('command_description') or ''}",
            f"  the step checks: {val['description']}" if val.get("description") else "",
            f"  success means: {ok['message']}" if ok.get("message") else "",
            f"  failure means: {bad['message']}" if bad.get("message") else "") if x))
        for flag, value in file_args(send):
            if (flag, value) in seen:
                continue
            seen.add((flag, value))
            ref = _CIQ_REF.search(value)
            node, writer = ("", "") if ref else _writer(workflow, value)
            if writer:
                bare = re.split(r"(?<![\d&>])>", writer, maxsplit=1)[0].strip()
                text = (f"{flag} {value}: written on node {node} by `{writer}` — the file holds what `{bare}` "
                        "prints")
                recorded = (redirect_contents([{"cmd": writer}], samples or []).get(bare) or {}).get("lines") or []
                if recorded:
                    text += ". Real output of that command (recorded on the node; parse this format exactly):\n" \
                            + "\n".join(f"    {l}" for l in recorded[:12] if l.strip())
                files.append(text)
            elif ref:
                column = ref.group(1).strip()
                example = _ciq_value(column, requirements)
                text = f"{flag} {value}: the input file the CIQ column {column} names" + (
                    f" (e.g. {example})" if example else "")
                doc = sample_for(example, documents) if example else ""
                shown = preview(doc) if doc else ""
                if shown:
                    text += (f". The uploaded document of that kind ({Path(doc).name}; read every sheet that "
                             "applies, by its header, every cell as text — pandas dtype=str — so values like "
                             "01695000 keep their leading zeros):\n" + shown)
                files.append(text)
        registers = _registers(workflow)
        for flag, value in _value_args(send):
            name = _WHOLE_VAR.match(value).group(1)
            if (flag, value) in seen or name not in registers:
                continue
            seen.add((flag, value))
            source, rx = registers[name]
            multi = bool(re.search(r"\[\\s\\S\]|\[\\S\\s\]|^\(\?s", rx))
            text = (f"{flag} {value}: NOT a file name — the TEXT the workflow registered as {name} from the "
                    f"output of `{source}` (regex /{rx}/)"
                    + ("; it is that command's whole output, many lines" if multi else "")
                    + ". The script parses this text from the argument itself (in that command's output "
                      "format); it never opens it as a file.")
            # the files that command reads (`diff predump_white.txt postdump_white.txt`): their records' format
            for token in dict.fromkeys(t for t in source.split() if re.search(r"\.[A-Za-z]\w{0,5}$", t)):
                node, writer = _writer(workflow, token)
                bare = re.split(r"(?<![\d&>])>", writer, maxsplit=1)[0].strip() if writer else ""
                recorded = (redirect_contents([{"cmd": writer}], samples or []).get(bare) or {}).get(
                    "lines") or [] if writer else []
                if recorded:
                    text += (f"\n    `{source.split()[0]}` reads {token}, the output of `{bare}` on {node}; "
                             "its records look like (parse the field the check is about from each record):\n"
                             + "\n".join(f"      {l}" for l in recorded[:6] if l.strip()))
                    break
            files.append(text)
    if not checks:
        return ""
    out = ("\n\n=== WHAT THE WORKFLOW STEP RUNNING THIS SCRIPT CHECKS ===\n" + "\n".join(checks)
           + "\nThe script implements exactly this check: it prints its success output only when the check holds "
             "(never the opposite), and exits 1 with the reason otherwise.\n")
    if files:
        out += "\n=== FILES THE SCRIPT READS ===\n" + "\n".join(f"- {f}" for f in files) + (
            "\nCompare like with like: extract the field the check is about from each record of these formats "
            "(e.g. a column or the leading digits of a range), never whole lines against single values.\n")
    return out


def smoke_inputs(workflow: dict, script_name: str, requirements: dict, documents: list[str] | None = None,
                 samples: list[dict] | None = None) -> dict[str, bytes]:
    """{file name as the workflow writes it: content} for the smoke run's folder — the files the script reads: the uploaded
    document standing for a CIQ input file, a node file as its writer's recorded output."""
    from skills.network_simulator_setup.vfs_commands import redirect_contents
    from tools.input_documents import sample_for
    out: dict[str, bytes] = {}
    for s in _steps(workflow):
        send = s.get("send")
        m = _PY_SCRIPT.match(send) if isinstance(send, str) else None
        if not m or m.group(1).replace("\\", "/").rsplit("/", 1)[-1] != script_name:
            continue
        for _, value in file_args(send):
            ref = _CIQ_REF.search(value)
            if ref:
                example = _ciq_value(ref.group(1).strip(), requirements)
                doc = sample_for(example, documents) if example else ""
                if doc:
                    out[value.rsplit("/", 1)[-1]] = Path(doc).read_bytes()
                continue
            node, writer = _writer(workflow, value)
            if writer:
                bare = re.split(r"(?<![\d&>])>", writer, maxsplit=1)[0].strip()
                lines = (redirect_contents([{"cmd": writer}], samples or []).get(bare) or {}).get("lines") or []
                if lines:
                    out[value.rsplit("/", 1)[-1]] = ("\n".join(lines) + "\n").encode("utf-8")
    return out
