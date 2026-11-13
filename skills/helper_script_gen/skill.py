"""Helper Script Generation — generates Python scripts for LOCAL node steps.

Reads sample request/response XMLs and CIQ structure from requirements,
then uses LLM to generate Python scripts that the workflow references.

Scripts generated:
- create_xml.py: builds SPML modification XMLs from CIQ data
- parse_response.py: parses SPML batchResponse XML for success/failure
- create_html_report.py: compares pre/post command outputs against CIQ
"""
from __future__ import annotations

import json
import logging
import re
import shlex
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from . import prompts

logger = logging.getLogger(__name__)

LlmFn = Callable[[str, str], Awaitable[str]]

CONTRACT_ATTEMPTS = 3  # generate, then repair the previous script with its problems (G-7)
_PY_SCRIPT = re.compile(r"^\s*python3?\s+(\S+\.py)\b")
_FLAG = re.compile(r"^(--[A-Za-z][\w-]*)")
_ADD_ARGUMENT = re.compile(r"add_argument\s*\((.*?)\)\s*$", re.S | re.M)
_QUOTED_FLAG = re.compile(r"""['"](--[A-Za-z][\w-]*)['"]""")


def contract_flags(command_lines: list[str]) -> list[str]:
    """Flags the workflow passes to a script, across all its invocations (in order).

    The workflow is the source of truth for the script's CLI (spec 004 DEP-11).
    """
    flags: list[str] = []
    for line in command_lines:
        try:
            tokens = shlex.split(line, posix=True)
        except ValueError:
            tokens = line.split()
        for tok in tokens:
            m = _FLAG.match(tok)
            if m and m.group(1) not in flags:
                flags.append(m.group(1))
    return flags


def check_script_contract(code: str, flags: list[str]) -> list[str]:
    """Problems with a generated script: syntax errors, or contract flags argparse lacks."""
    try:
        compile(code, "<generated script>", "exec")
    except SyntaxError as e:
        return [f"script does not compile: {e.msg} (line {e.lineno})"]
    defined = {f for args in _ADD_ARGUMENT.findall(code) for f in _QUOTED_FLAG.findall(args)}
    missing = [f for f in flags if f not in defined]
    if missing:
        return [f"argparse does not define {', '.join(missing)} — the workflow passes these flags "
                f"(defined: {', '.join(sorted(defined)) or 'none'})"]
    return []


_CELL_WORDS = re.compile(r"\b(?:unchanged|blank|empty|null|none|n/?a)\b", re.IGNORECASE)


def cell_semantics(requirements: dict) -> list[str]:
    """What the developer said special CIQ cells mean (``UNCHANGED`` keeps the value, a blank cell deletes the
    attribute, …): the sentences of the answers and notes that name such a cell. The answers reach the prompt cut at
    3000 characters; run 6570694d's report script compared a blank cell as a value and failed (OB-92)."""
    texts: list[str] = []

    def walk(value):
        if isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
        elif isinstance(value, str):
            texts.append(value)
    walk((requirements or {}).get("developer_answers") or {})
    walk((requirements or {}).get("developer_notes") or "")
    out: list[str] = []
    for text in texts:
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
            sentence = sentence.strip()
            if 12 <= len(sentence) <= 400 and _CELL_WORDS.search(sentence) and sentence not in out:
                out.append(sentence)
    return out[:8]


_UNCHANGED_LITERAL = re.compile(r"""['"]UNCHANGED['"]""")
_BLANK_BRANCH = re.compile(r"""==\s*(?:""|'')|is\s+None|in\s*[\(\[{]\s*(?:""|''|None)|\bnot\s+[A-Za-z_][\w.\[\]'"]*\s*:|"""
                           r"""\.strip\(\)\s*==\s*(?:""|'')|len\([^)]*\)\s*==\s*0""")


def check_cell_handling(code: str, semantics: list[str]) -> list[str]:
    """A script that compares CIQ values and treats ``UNCHANGED`` specially must also handle a blank cell when the
    developer gave blank cells a meaning — otherwise every field the CIQ leaves blank reads as a mismatch (OB-92)."""
    blank = [s for s in semantics if re.search(r"\b(?:blank|empty)\b", s, re.IGNORECASE)]
    if not blank or not _UNCHANGED_LITERAL.search(code) or not re.search(r"mismatch", code, re.IGNORECASE):
        return []
    if _BLANK_BRANCH.search(code):
        return []
    return ["the script compares CIQ values and handles 'UNCHANGED' but not a blank / empty CIQ cell, which the "
            "developer gave a meaning: " + " ".join(f'"{s}"' for s in blank[:2]) + " — handle a blank expected value "
            "for EVERY field as that says (e.g. a blank cell that deletes an attribute: the attribute must be absent "
            "after the activity, and its absence is a MATCH)"]


def arg_values(cmd_lines: list[str]) -> list[str]:
    """The values the workflow passes the script (``--output_file '${ORDER_NO}.xml'`` → ``X1.xml``): what an
    f-string field printing an argument holds at run time."""
    import shlex
    out: list[str] = []
    for line in cmd_lines:
        try:
            argv = shlex.split(line, posix=True)
        except ValueError:
            argv = line.split()
        out += [re.sub(r"\$\{[^{}]+\}", "X1", v) for f, v in zip(argv, argv[1:])
                if f.startswith("--") and not v.startswith("--")]
    return list(dict.fromkeys(v for v in out if v))


def check_output_contract(code: str, regexes: list[str], values: list[str] | None = None) -> list[str]:
    """Problems if no message the script can print matches the workflow's success regex.

    Checked statically against the script's string literals (f-string fields → ``x``, and → each value the
    workflow passes the script: `generated successfully: {output_file}` prints `….xml`, OB-81);
    Java named groups are converted for Python. Unparseable regexes are skipped.
    """
    import ast
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []  # reported by check_script_contract
    texts: list[str] = []
    shapes: list[re.Pattern] = []  # f-strings with each field as any text: `for {args.kind}` prints `for white` (OB-39)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            texts.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            texts.append("".join(v.value if isinstance(v, ast.Constant) else "x" for v in node.values))
            texts += ["".join(v.value if isinstance(v, ast.Constant) else value for v in node.values)
                      for value in values or []]
            fixed_text = "".join(v.value for v in node.values if isinstance(v, ast.Constant))
            if len(fixed_text.strip()) >= 8:  # `f"{x}"` would match anything
                shapes.append(re.compile("".join(re.escape(v.value) if isinstance(v, ast.Constant) else ".+"
                                                 for v in node.values)))
    problems = []
    for rx in regexes:
        try:
            compiled = re.compile(re.sub(r"\(\?<([A-Za-z][A-Za-z0-9]*)>", r"(?P<\1>", rx), re.MULTILINE)
        except re.error:
            continue
        fixed = _longest_literal(rx)
        # f-string fields are unknown at check time: the regex's fixed text in a message is enough
        if not any(compiled.search(t) for t in texts) and not (
                len(fixed) >= 8 and (any(fixed in t for t in texts) or any(s.search(fixed) for s in shapes))):
            problems.append(f"no message the script prints matches the workflow's success regex /{rx}/ "
                            "— print exactly such a message on success")
    return problems


# A traceback means the script is broken — unless its exception only says the smoke run lacks
# the real environment (an input file a real run downloads first, a host, permissions)
_ENVIRONMENT_ERRORS = re.compile(r"^(FileNotFoundError|NotADirectoryError|IsADirectoryError|PermissionError|"
                                 r"ConnectionError|ConnectionRefusedError|TimeoutError|socket\.\w+|"
                                 r"urllib\.error\.\w+|OSError)\b")
_WF_PLACEHOLDER = re.compile(r"\$\{([^{}]+)\}")


def _test_values(requirements: dict) -> dict[str, str]:
    """Placeholder test values: framework runtime params + first non-empty CIQ sample value."""
    from core.config import runtime_params
    values: dict[str, str] = dict(runtime_params())
    for sheet in ((requirements.get("doc_extracted") or {}).get("ciq_sheets") or {}).values():
        for row in (sheet or {}).get("sample_rows") or []:
            if isinstance(row, dict):
                for k, v in row.items():
                    if str(v or "").strip():
                        values.setdefault(f"configSequences.0.records.0.data.{k}", str(v))
                        values.setdefault(f"rec.data.{k}", str(v))  # inside a record loop (spec 013)
    return values


def _xml_tags_hint(document: str) -> str:
    """The tags as xml.etree.ElementTree reports them for the sample (namespaced root,
    unqualified children…): the LLM kept searching `spml:response` for an unqualified
    `<response>` through three repair rounds when only told to match the sample (G-11)."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(document.strip().encode("utf-8"))
    except ET.ParseError:
        return ""
    children = list(dict.fromkeys(c.tag for c in root))[:8]
    grand = list(dict.fromkeys(g.tag for c in root for g in c))[:8]
    return (f". ElementTree sees the sample as: root tag {root.tag!r} with attributes "
            f"{sorted(root.attrib)[:6]}; child tags {children}; grandchild tags {grand} — use exactly "
            "these tags (an unqualified tag has no namespace: root.findall('response'), not 'spml:response')")


_LOOKUP = re.compile(r"\.(?:findall|find|iterfind|findtext)\(\s*['\"]([^'\"]+)['\"]")


def _lookup_evidence(code: str, document: str) -> str:
    """What the script's own namespaced lookups find in the sample, and what the plain tag finds (OB-82, run
    61561981: told the tags, the LLM still wrote `root.findall('spml:response', ns)` and then
    `'{urn:…SPML:2:0}response'` for an unqualified `<response>`, three rounds — its comments said it must be
    namespaced). Counts run on the sample are evidence it cannot argue with."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(document.strip().encode("utf-8"))
    except ET.ParseError:
        return ""
    ns = dict(re.findall(r'xmlns:(\w+)="([^"]+)"', document))
    lines = []
    for path in dict.fromkeys(_LOOKUP.findall(code)):
        if ":" not in path and "{" not in path:
            continue
        plain = re.sub(r"\{[^}]*\}|\b\w+:(?=\w)", "", path)
        try:
            found, alt = len(root.findall(path, ns)), len(root.findall(plain))
        except (SyntaxError, KeyError, ValueError):
            continue
        if found == 0 and alt > 0:
            lines.append(f"`root.findall({path!r})` finds 0 elements in the sample, `root.findall({plain!r})` finds {alt}")
    return (" — run on the sample: " + "; ".join(lines) + ". Use the plain form.") if lines else ""


# A script argument that names a response file the node produced (--response_file X.xml.res)
_RESPONSE_ARG = re.compile(r"response|\.res\b", re.IGNORECASE)


def smoke_run(script_name: str, code: str, command_lines: list[str], values: dict[str, str],
              timeout: int = 20, response_sample: str = "", output_regexes: list[str] | None = None,
              inputs: dict[str, bytes] | None = None) -> list[str]:
    """Run the script as the workflow does (placeholders → test values) in a temp folder.

    A traceback from a programming error is a problem (S9-06: NameError in create_xml.py);
    failures for missing inputs are not — except a response file the workflow passes the
    script: it gets the UC's sample response, and then the script must succeed and print what
    the step's regexes read (``output_regexes``). parse_response.py looked for a namespaced
    ``spml:response`` the sample does not have and failed on every node (G-11). ``inputs`` (name as the
    workflow writes it -> content): the files the script reads, in their real format (OB-58) - a crash
    parsing them is a problem.
    """
    import subprocess
    import sys
    import tempfile
    from pathlib import Path
    problems: list[str] = []
    with tempfile.TemporaryDirectory(prefix="helper-smoke-") as tmp:
        Path(tmp, script_name).write_text(code, encoding="utf-8")
        for line in command_lines[:3]:
            resolved = _WF_PLACEHOLDER.sub(lambda m: values.get(m.group(1).strip(), "1"), line)
            try:
                argv = shlex.split(resolved, posix=True)
            except ValueError:
                continue
            if not argv or not re.match(r"python3?$", argv[0]):
                continue
            argv[0] = sys.executable
            argv[1] = str(Path(tmp, Path(argv[1]).name))
            staged = {_WF_PLACEHOLDER.sub(lambda m: values.get(m.group(1).strip(), "1"), k): v
                      for k, v in (inputs or {}).items()}
            for i in range(2, len(argv)):
                name = argv[i].replace("\\", "/").rsplit("/", 1)[-1]
                if name in staged and argv[i - 1].startswith("-"):
                    Path(tmp, name).write_bytes(staged[name])
                    argv[i] = str(Path(tmp, name))
            fed = ""
            if response_sample:
                for flag, value in zip(argv[2:], argv[3:]):
                    if flag.startswith("--") and _RESPONSE_ARG.search(f"{flag} {value}") and "/" not in value:
                        Path(tmp, value).write_text(response_sample, encoding="utf-8")
                        fed = f"{flag} {value}"
            try:
                proc = subprocess.run(argv, cwd=tmp, capture_output=True, text=True, timeout=timeout)
            except subprocess.TimeoutExpired:
                problems.append(f"`{resolved}` did not finish within {timeout}s")
                continue
            if fed and "Traceback (most recent call last)" not in (proc.stderr or ""):
                out = (proc.stdout or "") + (proc.stderr or "")
                if proc.returncode != 0:
                    problems.append(f"`{resolved}` with the UC's sample response as {fed} (a successful "
                                    f"response) exited {proc.returncode}: {out.strip()[-300:]} — parse that "
                                    "document as it is (element names and namespaces exactly as in the sample)"
                                    + _xml_tags_hint(response_sample) + _lookup_evidence(code, response_sample))
                for rx in output_regexes or []:
                    try:
                        if not re.search(re.sub(r"\(\?<([A-Za-z][A-Za-z0-9]*)>", r"(?P<\1>", rx), out, re.M):
                            problems.append(f"`{resolved}` with the UC's sample response printed "
                                            f"{out.strip()[-200:]!r}, which the workflow regex /{rx}/ does not match")
                    except re.error:
                        continue
            err = proc.stderr or ""
            last = next((l.strip() for l in reversed(err.strip().splitlines()) if l.strip()), "")
            exc = last.split(":", 1)[0].strip()
            if "Traceback (most recent call last)" in err and not _ENVIRONMENT_ERRORS.match(exc):
                problems.append(f"running `{resolved}` crashed:\n" + "\n".join(err.strip().splitlines()[-6:]))
            elif proc.returncode == 2 and re.search(r"\berror: (the following arguments are required|"
                                                    r"unrecognized arguments|argument )", err):
                # argparse rejected the workflow's own command line: the script must accept
                # exactly these flags (a required --imsi the workflow never passes; G-10)
                problems.append(f"the workflow runs `{resolved}`, which the script rejects: {last} — "
                                "accept exactly the flags of that command line (make others optional, but never default "
                                "request data to values copied from the samples)")
    return problems


def _longest_literal(regex: str) -> str:
    """Longest run of literal text in a regex (escaped punctuation unescaped)."""
    runs, current, i = [], "", 0
    while i < len(regex):
        ch = regex[i]
        if ch == "\\" and i + 1 < len(regex):
            nxt = regex[i + 1]
            if nxt.isalnum():          # \d \s \w \b … end the run
                runs.append(current)
                current = ""
            else:
                current += nxt
            i += 2
            continue
        if ch in "?*{":                # the previous char is optional/repeated
            runs.append(current[:-1])
            current = ""
        elif ch in ".+^$|()[]}":
            runs.append(current)
            current = ""
        else:
            current += ch
        i += 1
    runs.append(current)
    return max(runs, key=len)


def _workflow_commands(workflow_yaml: str) -> list[str]:
    """All step commands (send/cmd/command) of a workflow, nested steps included."""
    return [cmd for cmd, _ in _workflow_steps(workflow_yaml)]


def _workflow_steps(workflow_yaml: str) -> list[tuple[str, str]]:
    """(command, success regex) of every workflow step, nested steps included."""
    from io import StringIO
    from ruamel.yaml import YAML
    try:
        workflow = YAML(typ="safe").load(StringIO(workflow_yaml)) or {}
    except Exception:
        return []
    phases = workflow.get("phases") if isinstance(workflow, dict) else None
    bodies = phases.values() if isinstance(phases, dict) else (phases or [])
    commands: list[tuple[str, str]] = []

    def walk(steps):
        for step in steps or []:
            if not isinstance(step, dict):
                continue
            cmd = step.get("send", step.get("cmd", step.get("command")))
            if isinstance(cmd, str):
                validation = step.get("validation") if isinstance(step.get("validation"), dict) else {}
                success = validation.get("success") if isinstance(validation.get("success"), dict) else {}
                criteria = success.get("criteria") if isinstance(success.get("criteria"), dict) else {}
                regex = criteria.get("regex")
                commands.append((cmd.strip(), regex.strip() if isinstance(regex, str) else ""))
            walk(step.get("steps"))

    for body in bodies:
        walk(body.get("steps") if isinstance(body, dict) else body)
    return commands


def _workflow_registers(workflow_yaml: str) -> list[tuple[str, list[str]]]:
    """(command, register regexes) of every workflow step that registers values."""
    from io import StringIO
    from ruamel.yaml import YAML
    try:
        workflow = YAML(typ="safe").load(StringIO(workflow_yaml)) or {}
    except Exception:
        return []
    phases = workflow.get("phases") if isinstance(workflow, dict) else None
    out: list[tuple[str, list[str]]] = []

    def walk(steps):
        for step in steps or []:
            if isinstance(step, dict):
                cmd = step.get("send")
                regs = [str(r.get("regex")) for r in step.get("register") or []
                        if isinstance(r, dict) and r.get("regex")]
                if isinstance(cmd, str) and regs:
                    out.append((cmd.strip(), regs))
                walk(step.get("steps"))

    for body in (phases.values() if isinstance(phases, dict) else (phases or [])):
        walk(body.get("steps") if isinstance(body, dict) else body)
    return out


@dataclass
class SkillInput:
    """Input for helper script generation."""
    uc_name: str
    workflow_yaml: str = ""
    requirements: dict = field(default_factory=dict)
    run_dir: str = ""
    sample_inputs: list[str] = field(default_factory=list)  # the run's uploaded documents (OB-58)


@dataclass
class SkillOutput:
    """Output from helper script generation."""
    scripts: dict[str, str] = field(default_factory=dict)  # filename → content
    status: str = "pending"
    error: str = ""


class HelperScriptGenSkill:
    """Generates Python helper scripts for workflow LOCAL node steps."""

    def __init__(self, llm_fn: LlmFn):
        self._llm_fn = llm_fn

    async def generate(self, inp: SkillInput) -> SkillOutput:
        """Generate all helper scripts referenced in the workflow."""
        # Find which scripts the workflow needs
        needed = self._find_needed_scripts(inp.workflow_yaml)
        if not needed:
            logger.info("No LOCAL python scripts referenced in workflow")
            return SkillOutput(scripts={}, status="passed")

        logger.info("Generating %d helper scripts: %s", len(needed), list(needed.keys()))

        # Extract context from requirements
        doc = inp.requirements.get("doc_extracted", {})
        xml_samples = doc.get("xml_files", doc.get("xml_samples", []))
        ciq_sheets = doc.get("ciq_sheets", {})
        answers = inp.requirements.get("developer_answers", {})
        # the confirmed CIQ cell rules (shared/ciq_cells.py, shown at the step 2 review) — the same the simulator uses
        from shared import ciq_cells
        cell_rules = ciq_cells.rules(inp.requirements)
        semantics = ciq_cells.describe(cell_rules)

        # Separate request vs response XMLs
        request_xmls = []
        response_xmls = []
        for x in xml_samples:
            content = x.get("content", "") if isinstance(x, dict) else str(x)
            if "batchResponse" in content:
                response_xmls.append(content)
            else:
                request_xmls.append(content)

        # Build CIQ column summary
        ciq_summary = self._build_ciq_summary(ciq_sheets)

        scripts: dict[str, str] = {}
        errors = []

        output_regexes = self._script_output_regexes(inp.workflow_yaml)
        test_values = _test_values(inp.requirements)
        from io import StringIO
        from ruamel.yaml import YAML
        from tools.command_samples import with_library
        from .context import script_context, smoke_inputs
        try:
            workflow = YAML(typ="safe").load(StringIO(inp.workflow_yaml)) or {}
        except Exception:
            workflow = {}
        samples = with_library(list(doc.get("command_samples") or []))
        for script_name, cmd_lines in needed.items():
            flags = contract_flags(cmd_lines)
            regexes = output_regexes.get(script_name, [])
            contract = prompts.CLI_CONTRACT_SECTION.format(
                invocations="\n".join(f"  {c}" for c in cmd_lines),
                flags="\n".join(f"  {f}" for f in flags) or "  (no flags)")
            if regexes:
                contract += prompts.OUTPUT_CONTRACT_SECTION.format(
                    regexes="\n".join(f"  /{r}/" for r in regexes))
            # the step's check and the real format of what the script reads (OB-58)
            contract = script_context(workflow, script_name, inp.requirements, inp.sample_inputs, samples) + contract
            # what UNCHANGED / blank CIQ cells mean, for every field (OB-92): the confirmed table
            blank_expect = ("the attribute is ABSENT after the activity (its absence is a MATCH)"
                            if cell_rules["blank"] == "delete" else "the value is unchanged")
            blank_send = ("send it so the attribute is deleted (an empty tag / value)"
                          if cell_rules["blank"] == "delete" else "leave the field out")
            contract += ("\n\n=== CIQ CELL RULES (confirmed at the requirements review — apply to EVERY field the "
                         "script reads from the CIQ) ===\n" + "\n".join(f"- {x}" for x in semantics)
                         + "\nA script that compares before / after with the CIQ: a cell 'UNCHANGED' expects the value "
                           f"unchanged; a blank cell (empty / None) expects that {blank_expect}; any other cell expects "
                           "that value. A script that builds a request: 'UNCHANGED' → leave the field out; blank → "
                           f"{blank_send}.")
            inputs = smoke_inputs(workflow, script_name, inp.requirements, inp.sample_inputs, samples)
            try:
                code, problems = "", []
                for attempt in range(1, CONTRACT_ATTEMPTS + 1):
                    # A retry repairs the rejected script: regenerating from scratch traded one
                    # crash for another in every attempt (G-7)
                    retry = prompts.CLI_CONTRACT_RETRY.format(
                        problems="\n".join(f"- {p}" for p in problems),
                        previous_code=code[:30000]) if problems else ""
                    code = await self._generate_script(
                        script_name, cmd_lines[0], request_xmls, response_xmls,
                        ciq_summary, inp.workflow_yaml, answers, extra=contract + retry,
                    )
                    problems = (check_script_contract(code, flags) or check_cell_handling(code, semantics)
                                or check_output_contract(code, regexes, arg_values(cmd_lines))
                                or smoke_run(script_name, code, cmd_lines, test_values,
                                             response_sample=response_xmls[0] if response_xmls else "",
                                             output_regexes=regexes, inputs=inputs))
                    if not problems:
                        break
                    logger.warning("%s attempt %d/%d violates the CLI contract: %s",
                                   script_name, attempt, CONTRACT_ATTEMPTS, problems)
                scripts[script_name] = code
                if problems:
                    errors.append(f"{script_name}: {'; '.join(problems)}")
                logger.info("Generated %s: %d chars, %d contract problems", script_name, len(code), len(problems))
            except Exception as e:
                logger.error("Failed to generate %s: %s", script_name, e, exc_info=True)
                errors.append(f"{script_name}: {e}")

        status = "passed" if len(scripts) == len(needed) and not errors else "failed"
        return SkillOutput(
            scripts=scripts,
            status=status,
            error="; ".join(errors) if errors else "",
        )

    async def _generate_script(
        self,
        script_name: str,
        cmd_line: str,
        request_xmls: list[str],
        response_xmls: list[str],
        ciq_summary: str,
        workflow_yaml: str,
        answers: dict,
        extra: str = "",
    ) -> str:
        """Generate a single Python script via LLM (``extra`` = CLI contract / retry notes)."""
        # Pick the right system prompt based on script type
        if "create_xml" in script_name:
            system = prompts.CREATE_XML_SYSTEM
            user = prompts.CREATE_XML_USER.format(
                script_name=script_name,
                cmd_line=cmd_line,
                request_xmls="\n---\n".join(request_xmls[:5]),
                response_xml=response_xmls[0] if response_xmls else "No response sample",
                ciq_summary=ciq_summary,
                answers=json.dumps(answers, indent=2, default=str)[:3000] if answers else "None",
            )
        elif "parse_response" in script_name:
            system = prompts.PARSE_RESPONSE_SYSTEM
            user = prompts.PARSE_RESPONSE_USER.format(
                script_name=script_name,
                cmd_line=cmd_line,
                response_xml=response_xmls[0] if response_xmls else "No response sample",
            )
        elif "html_report" in script_name or "report" in script_name:
            system = prompts.HTML_REPORT_SYSTEM
            user = prompts.HTML_REPORT_USER.format(
                script_name=script_name,
                cmd_line=cmd_line,
                ciq_summary=ciq_summary,
            )
        else:
            system = prompts.GENERIC_SCRIPT_SYSTEM
            user = prompts.GENERIC_SCRIPT_USER.format(
                script_name=script_name,
                cmd_line=cmd_line,
                ciq_summary=ciq_summary,
                workflow_context=workflow_yaml[:1500],
            )

        raw = await self._llm_fn(system, user + extra)
        return self._clean_code_response(raw)

    @staticmethod
    def _find_needed_scripts(workflow_yaml: str) -> dict[str, list[str]]:
        """Find python scripts the workflow runs and every command line that runs them.

        Returns {script_name: [command lines]} in workflow order.
        """
        scripts: dict[str, list[str]] = {}
        for command in _workflow_commands(workflow_yaml):
            m = _PY_SCRIPT.match(command)
            if m:
                name = m.group(1).replace("\\", "/").rsplit("/", 1)[-1]
                lines = scripts.setdefault(name, [])
                if command not in lines:
                    lines.append(command)
        return scripts

    @staticmethod
    def _script_output_regexes(workflow_yaml: str) -> dict[str, list[str]]:
        """{script: [regexes its output must match]} of the workflow steps that run each script:
        the success regex and the ``register`` regexes — a step that reads the script's output
        through registered values and an ``expr`` got a script printing something else (G-9)."""
        result: dict[str, list[str]] = {}
        registers = dict(_workflow_registers(workflow_yaml))
        for command, regex in _workflow_steps(workflow_yaml):
            m = _PY_SCRIPT.match(command)
            if not m:
                continue
            name = m.group(1).replace("\\", "/").rsplit("/", 1)[-1]
            for rx in ([regex] if regex else []) + registers.get(command, []):
                if rx not in result.setdefault(name, []):
                    result[name].append(rx)
        return {k: v for k, v in result.items() if v}

    @staticmethod
    def _build_ciq_summary(ciq_sheets: dict) -> str:
        """Build concise CIQ summary for prompts."""
        lines = []
        for sname, sdata in ciq_sheets.items():
            cols = sdata.get("columns", sdata.get("headers", []))
            samples = sdata.get("sample_rows", [])
            lines.append(f'Sheet "{sname}": columns=[{", ".join(cols)}]')
            if samples:
                for row in samples[:2]:
                    if isinstance(row, dict):
                        lines.append(f"  sample: {row}")
        return "\n".join(lines)

    @staticmethod
    def _clean_code_response(text: str) -> str:
        """Strip markdown code fences from LLM response."""
        text = re.sub(r"^```(?:python)?\s*\n?", "", text, flags=re.MULTILINE)
        text = re.sub(r"\n?```\s*$", "", text, flags=re.MULTILINE)
        # Find first line that looks like Python code
        lines = text.split("\n")
        start = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped and (stripped.startswith(("#", "import", "from", "def", "class", '"""', "'''"))
                            or "=" in stripped or stripped.startswith("if ")):
                start = i
                break
        return "\n".join(lines[start:]).rstrip() + "\n"
