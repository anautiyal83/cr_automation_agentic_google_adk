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

CONTRACT_ATTEMPTS = 2  # generate, then one retry with the contract problems listed
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


def check_output_contract(code: str, regexes: list[str]) -> list[str]:
    """Problems if no message the script can print matches the workflow's success regex.

    Checked statically against the script's string literals (f-string fields → ``x``);
    Java named groups are converted for Python. Unparseable regexes are skipped.
    """
    import ast
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []  # reported by check_script_contract
    texts: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            texts.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            texts.append("".join(v.value if isinstance(v, ast.Constant) else "x" for v in node.values))
    problems = []
    for rx in regexes:
        try:
            compiled = re.compile(re.sub(r"\(\?<([A-Za-z][A-Za-z0-9]*)>", r"(?P<\1>", rx), re.MULTILINE)
        except re.error:
            continue
        fixed = _longest_literal(rx)
        # f-string fields are unknown at check time: the regex's fixed text in a message is enough
        if not any(compiled.search(t) for t in texts) and not (
                len(fixed) >= 8 and any(fixed in t for t in texts)):
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
    return values


def smoke_run(script_name: str, code: str, command_lines: list[str], values: dict[str, str],
              timeout: int = 20) -> list[str]:
    """Run the script as the workflow does (placeholders → test values) in a temp folder.

    A traceback from a programming error is a problem (S9-06: NameError in create_xml.py);
    failures for missing inputs (a response file only a real run produces) are not.
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
            try:
                proc = subprocess.run(argv, cwd=tmp, capture_output=True, text=True, timeout=timeout)
            except subprocess.TimeoutExpired:
                problems.append(f"`{resolved}` did not finish within {timeout}s")
                continue
            err = proc.stderr or ""
            last = next((l.strip() for l in reversed(err.strip().splitlines()) if l.strip()), "")
            exc = last.split(":", 1)[0].strip()
            if "Traceback (most recent call last)" in err and not _ENVIRONMENT_ERRORS.match(exc):
                problems.append(f"running `{resolved}` crashed:\n" + "\n".join(err.strip().splitlines()[-6:]))
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


@dataclass
class SkillInput:
    """Input for helper script generation."""
    uc_name: str
    workflow_yaml: str = ""
    requirements: dict = field(default_factory=dict)
    run_dir: str = ""


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
        for script_name, cmd_lines in needed.items():
            flags = contract_flags(cmd_lines)
            regexes = output_regexes.get(script_name, [])
            contract = prompts.CLI_CONTRACT_SECTION.format(
                invocations="\n".join(f"  {c}" for c in cmd_lines),
                flags="\n".join(f"  {f}" for f in flags) or "  (no flags)")
            if regexes:
                contract += prompts.OUTPUT_CONTRACT_SECTION.format(
                    regexes="\n".join(f"  /{r}/" for r in regexes))
            try:
                code, problems = "", []
                for attempt in range(1, CONTRACT_ATTEMPTS + 1):
                    retry = prompts.CLI_CONTRACT_RETRY.format(
                        problems="\n".join(f"- {p}" for p in problems)) if problems else ""
                    code = await self._generate_script(
                        script_name, cmd_lines[0], request_xmls, response_xmls,
                        ciq_summary, inp.workflow_yaml, answers, extra=contract + retry,
                    )
                    problems = (check_script_contract(code, flags) or check_output_contract(code, regexes)
                                or smoke_run(script_name, code, cmd_lines, test_values))
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
                workflow_context=workflow_yaml[:3000],
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
        """{script: [success regexes]} of the workflow steps that run each script."""
        result: dict[str, list[str]] = {}
        for command, regex in _workflow_steps(workflow_yaml):
            m = _PY_SCRIPT.match(command)
            if m and regex:
                name = m.group(1).replace("\\", "/").rsplit("/", 1)[-1]
                if regex not in result.setdefault(name, []):
                    result[name].append(regex)
        return result

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
