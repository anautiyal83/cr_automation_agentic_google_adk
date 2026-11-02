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
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from . import prompts

logger = logging.getLogger(__name__)

LlmFn = Callable[[str, str], Awaitable[str]]


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

        for script_name, cmd_line in needed.items():
            try:
                code = await self._generate_script(
                    script_name, cmd_line, request_xmls, response_xmls,
                    ciq_summary, inp.workflow_yaml, answers,
                )
                scripts[script_name] = code
                logger.info("Generated %s: %d chars", script_name, len(code))
            except Exception as e:
                logger.error("Failed to generate %s: %s", script_name, e, exc_info=True)
                errors.append(f"{script_name}: {e}")

        status = "passed" if len(scripts) == len(needed) else "failed"
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
    ) -> str:
        """Generate a single Python script via LLM."""
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

        raw = await self._llm_fn(system, user)
        return self._clean_code_response(raw)

    @staticmethod
    def _find_needed_scripts(workflow_yaml: str) -> dict[str, str]:
        """Find python scripts referenced in LOCAL node steps.

        Returns {script_name: full_command_line}.
        """
        scripts = {}
        for m in re.finditer(r"send:\s*python\s+(\w+\.py)\s*(.*)", workflow_yaml):
            name = m.group(1)
            full_cmd = f"python {m.group(1)} {m.group(2)}".strip()
            if name not in scripts:
                scripts[name] = full_cmd
        return scripts

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
