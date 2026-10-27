"""TelecomReqSkill — main orchestrator for telecom activity requirement gathering.

Usage::

    skill = TelecomReqSkill(llm_fn=my_async_llm_function)

    output = await skill.parse_and_analyze(SkillInput(
        uc_name="PGW_RDS_1058_IFC_CAPABILITY_MODIFICATION",
        ciq_file_path="/path/to/ciq.xlsx",
        mop_file_path="/path/to/mop.txt",
        ssh_log_path="/path/to/logs/",
        xml_sample_paths=["/path/to/hss_pdn.xml"],
    ))

    while output.questions:
        answers = present_questions_to_user(output.questions)
        output = await skill.incorporate_answers(answers)

    output = await skill.compile_final()
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import analyzer, compiler, questionnaire
from .html_report import generate_questionnaire_html, generate_summary_html
from .parsers import parse_ciq, parse_mop, parse_ssh_logs, parse_xml_samples
from .schema_discovery import DiscoveredSchemas, discover_schemas

logger = logging.getLogger(__name__)

LlmFn = Callable[[str, str], Awaitable[str]]

# Answer persistence directory
_ANSWERS_DIR = Path("data/knowledge")


@dataclass
class SkillInput:
    """Input documents for requirement gathering."""
    uc_name: str
    ciq_file_path: str = ""
    ciq_data: dict | None = None
    mop_file_path: str = ""
    ssh_log_path: str = ""
    xml_sample_paths: list[str] = field(default_factory=list)
    developer_notes: str = ""
    previous_answers: dict | None = None
    schema_dir: str | None = None


@dataclass
class SkillOutput:
    """Output from a skill phase."""
    requirements: dict = field(default_factory=dict)
    requirements_json: str = ""
    requirements_html: str = ""
    questionnaire_html: str = ""
    questions: list[dict] = field(default_factory=list)
    analysis: dict = field(default_factory=dict)
    readiness: str = "INSUFFICIENT"
    doc_extracted: dict = field(default_factory=dict)
    all_answers: dict = field(default_factory=dict)
    round_num: int = 0


class TelecomReqSkill:
    """Stateful skill for gathering telecom UC activity requirements.

    Phases:
    1. ``parse_and_analyze`` — parse documents + LLM analysis
    2. ``incorporate_answers`` — add user answers and re-analyze (iterable)
    3. ``compile_final`` — compile final requirements + HTML report
    """

    def __init__(self, llm_fn: LlmFn):
        self._llm_fn = llm_fn
        self._uc_name = ""
        self._developer_notes = ""
        self._raw_artifacts: dict[str, str] = {}
        self._doc_extracted: dict[str, Any] = {}
        self._ciq_sheets: dict[str, dict] = {}
        self._analysis: dict[str, Any] = {}
        self._all_answers: dict[str, Any] = {}
        self._round_num = 0
        self._parsed_data: dict[str, Any] = {}
        self._schemas: DiscoveredSchemas | None = None
        self._schema_dir: str | None = None

    # ------------------------------------------------------------------
    # Phase 1: Parse + Analyze
    # ------------------------------------------------------------------

    async def parse_and_analyze(self, inp: SkillInput) -> SkillOutput:
        """Parse all input documents and run LLM analysis.

        Returns a ``SkillOutput`` with ``questions`` if gaps exist, or
        empty ``questions`` if readiness is COMPLETE.
        """
        self._uc_name = inp.uc_name
        self._developer_notes = inp.developer_notes
        self._schema_dir = inp.schema_dir

        # Discover project schemas early (for downstream hints)
        self._schemas = discover_schemas(inp.schema_dir)
        if self._schemas.missing:
            logger.warning("Missing schemas: %s", self._schemas.missing)

        # Load previous answers
        if inp.previous_answers:
            self._all_answers = dict(inp.previous_answers)
        else:
            self._all_answers = dict(_load_answers(inp.uc_name))

        # --- Deterministic parsing ---
        self._raw_artifacts = self._read_raw_artifacts(inp)
        mop_parsed = parse_mop(self._raw_artifacts.get("mop_text", ""))
        ssh_parsed = parse_ssh_logs(self._raw_artifacts.get("ssh_logs", ""))
        xml_parsed = parse_xml_samples(self._raw_artifacts.get("xml_samples", ""))
        self._ciq_sheets = parse_ciq(
            ciq_file_path=inp.ciq_file_path,
            ciq_data=inp.ciq_data,
        )

        self._doc_extracted = {
            "mop_commands": mop_parsed["commands"],
            "mop_phases": mop_parsed["phases"],
            "ssh_prompts": ssh_parsed["prompts"],
            "ssh_credentials": ssh_parsed["credentials"],
            "ldap_port": ssh_parsed["ldap_port"],
            "xml_files": xml_parsed,
            "ciq_sheets": self._ciq_sheets,
        }

        logger.info(
            "Document extraction: %d MOP commands, %d prompts, %d XML samples, %d CIQ sheets",
            len(mop_parsed["commands"]),
            len(ssh_parsed["prompts"]),
            len(xml_parsed),
            len(self._ciq_sheets),
        )

        # --- Build summaries for LLM ---
        ciq_summary = self._build_ciq_summary()
        mop_summary = self._build_mop_summary()
        cmd_summary = self._build_cmd_summary()

        analysis_notes = self._developer_notes
        if self._all_answers:
            analysis_notes += "\n\n=== Previously Answered Questions ===\n" + json.dumps(self._all_answers, indent=2)

        # --- LLM analysis ---
        self._analysis = await analyzer.analyze_uc(
            self._llm_fn,
            self._uc_name,
            ciq_summary,
            mop_summary,
            cmd_summary,
            analysis_notes,
        )

        readiness = self._analysis.get("overall_readiness", "INSUFFICIENT")

        # If COMPLETE with previous answers, skip questions
        if self._all_answers and readiness == "COMPLETE":
            return SkillOutput(
                analysis=self._analysis,
                readiness=readiness,
                doc_extracted=self._doc_extracted,
                all_answers=self._all_answers,
                questions=[],
            )

        # Generate questions for gaps
        available_data = questionnaire.build_available_data_summary(
            self._ciq_sheets, self._raw_artifacts, ssh_parsed,
        )
        questions = await questionnaire.generate_questions(
            self._llm_fn,
            self._analysis,
            self._developer_notes,
            self._all_answers,
            available_data,
        )

        # Filter already-answered
        if questions and self._all_answers:
            questions = questionnaire.filter_answered_questions(questions, self._all_answers)

        self._round_num = 1
        return SkillOutput(
            analysis=self._analysis,
            readiness=readiness,
            doc_extracted=self._doc_extracted,
            all_answers=self._all_answers,
            questions=questions,
            round_num=self._round_num,
        )

    # ------------------------------------------------------------------
    # Phase 2: Incorporate answers (iterable)
    # ------------------------------------------------------------------

    async def incorporate_answers(self, answers: dict[str, Any]) -> SkillOutput:
        """Add user answers, re-analyze, and generate next round of questions.

        Call repeatedly until ``output.questions`` is empty or caller decides
        to proceed with partial information.
        """
        self._all_answers.update(answers)
        _save_answers(self._uc_name, self._all_answers)

        # Re-analyze with new answers
        combined_notes = self._developer_notes
        if self._all_answers:
            combined_notes += "\n\n=== Developer Answers ===\n" + json.dumps(self._all_answers, indent=2)

        ciq_summary = self._build_ciq_summary()
        mop_summary = self._build_mop_summary()
        cmd_summary = self._build_cmd_summary()

        self._analysis = await analyzer.analyze_uc(
            self._llm_fn,
            self._uc_name,
            ciq_summary,
            mop_summary,
            cmd_summary,
            combined_notes,
        )

        readiness = self._analysis.get("overall_readiness", "INSUFFICIENT")

        if readiness == "COMPLETE":
            return SkillOutput(
                analysis=self._analysis,
                readiness=readiness,
                doc_extracted=self._doc_extracted,
                all_answers=self._all_answers,
                questions=[],
                round_num=self._round_num,
            )

        # Generate next round
        ssh_parsed = parse_ssh_logs(self._raw_artifacts.get("ssh_logs", ""))
        available_data = questionnaire.build_available_data_summary(
            self._ciq_sheets, self._raw_artifacts, ssh_parsed,
        )
        questions = await questionnaire.generate_questions(
            self._llm_fn,
            self._analysis,
            self._developer_notes,
            self._all_answers,
            available_data,
        )

        if questions and self._all_answers:
            questions = questionnaire.filter_answered_questions(questions, self._all_answers)

        self._round_num += 1
        return SkillOutput(
            analysis=self._analysis,
            readiness=readiness,
            doc_extracted=self._doc_extracted,
            all_answers=self._all_answers,
            questions=questions,
            round_num=self._round_num,
        )

    # ------------------------------------------------------------------
    # Phase 3: Compile final
    # ------------------------------------------------------------------

    async def compile_final(self) -> SkillOutput:
        """Compile final requirements package and generate HTML report.

        Returns ``SkillOutput`` with ``requirements``, ``requirements_json``,
        and ``requirements_html`` populated.
        """
        _save_answers(self._uc_name, self._all_answers)

        requirements = await analyzer.compile_requirements(
            self._llm_fn,
            self._uc_name,
            self._analysis,
            self._developer_notes,
            self._all_answers,
            raw_artifacts=self._raw_artifacts,
        )

        # Merge deterministic extractions (override LLM gaps)
        requirements = compiler.merge_doc_extracted(requirements, self._doc_extracted)

        # Enforce user answers as hard overrides
        requirements = compiler.enforce_answers(
            requirements,
            self._all_answers,
            self._raw_artifacts,
            self._doc_extracted.get("ssh_prompts", []),
        )

        # Inject schema summaries into generation_hints for Steps 3 & 4
        requirements = compiler.inject_schema_hints(
            requirements,
            schemas=self._schemas,
            schema_dir=self._schema_dir,
        )

        # Generate HTML report
        html = generate_summary_html(
            self._uc_name,
            self._ciq_sheets,
            self._analysis,
            requirements,
            self._developer_notes,
            self._all_answers,
            self._doc_extracted,
        )

        req_json = json.dumps(requirements, indent=2, default=str)

        return SkillOutput(
            requirements=requirements,
            requirements_json=req_json,
            requirements_html=html,
            analysis=self._analysis,
            readiness=self._analysis.get("overall_readiness", "INSUFFICIENT"),
            doc_extracted=self._doc_extracted,
            all_answers=self._all_answers,
            round_num=self._round_num,
        )

    # ------------------------------------------------------------------
    # Questionnaire HTML (for artifact display)
    # ------------------------------------------------------------------

    def generate_questionnaire_doc(
        self,
        questions: list[dict],
        round_num: int,
    ) -> str:
        """Generate questionnaire HTML for a round. Returns HTML string."""
        return generate_questionnaire_html(self._uc_name, questions, round_num)

    # ------------------------------------------------------------------
    # Remaining gaps (for continue/clarify dialog)
    # ------------------------------------------------------------------

    def get_remaining_gaps(self) -> list[str]:
        """Return list of remaining gap strings from latest analysis."""
        gaps = []
        for cat_key in ("category_1_node_activity", "category_2_commands", "category_3_orchestration"):
            gaps.extend(self._analysis.get(cat_key, {}).get("gaps", []))
        return gaps

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _read_raw_artifacts(inp: SkillInput) -> dict[str, str]:
        """Read raw file contents from input paths."""
        raw: dict[str, str] = {}

        if inp.mop_file_path:
            try:
                raw["mop_text"] = Path(inp.mop_file_path).read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                logger.debug("Could not read MOP file %s: %s", inp.mop_file_path, e)

        if inp.ssh_log_path:
            p = Path(inp.ssh_log_path)
            if p.is_file():
                try:
                    raw["ssh_logs"] = p.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    pass
            elif p.is_dir():
                parts = []
                for f in sorted(p.iterdir()):
                    if f.is_file() and f.suffix in (".txt", ".log", ""):
                        try:
                            content = f.read_text(encoding="utf-8", errors="replace")
                            parts.append(f"=== {f.name} ===\n{content}")
                        except Exception:
                            pass
                raw["ssh_logs"] = "\n\n".join(parts)

        if inp.xml_sample_paths:
            parts = []
            for sp in inp.xml_sample_paths:
                try:
                    content = Path(sp).read_text(encoding="utf-8", errors="replace")
                    parts.append(f"=== {Path(sp).name} ===\n{content}")
                except Exception:
                    pass
            raw["xml_samples"] = "\n\n".join(parts)

        return raw

    def _build_ciq_summary(self) -> str:
        if not self._ciq_sheets:
            return "No CIQ data"
        parts = []
        for sname, sdata in self._ciq_sheets.items():
            cols = sdata.get("columns", [])
            parts.append(f'Sheet "{sname}": columns={cols}')
            samples = sdata.get("sample_rows", [])
            if samples:
                parts.append(f"  Sample: {samples[:2]}")
        return "\n".join(parts)

    def _build_mop_summary(self) -> str:
        mop_text = self._raw_artifacts.get("mop_text", "")
        if mop_text:
            return "=== RAW MOP DOCUMENT ===\n" + mop_text[:8000]
        return "No MOP data"

    def _build_cmd_summary(self) -> str:
        ssh_logs = self._raw_artifacts.get("ssh_logs", "")
        if ssh_logs:
            return "=== RAW SSH SESSION LOGS ===\n" + ssh_logs[:12000]
        return "No command outputs"


# ------------------------------------------------------------------
# Answer persistence (module-level, shared across instances)
# ------------------------------------------------------------------

_answer_cache: dict[str, dict] = {}


def _load_answers(uc_name: str) -> dict:
    if uc_name in _answer_cache:
        return _answer_cache[uc_name]
    fpath = _ANSWERS_DIR / f"{uc_name}_answers.json"
    if fpath.exists():
        try:
            answers = json.loads(fpath.read_text(encoding="utf-8"))
            _answer_cache[uc_name] = answers
            logger.info("Loaded %d cached answers for UC %s", len(answers), uc_name)
            return answers
        except Exception as e:
            logger.debug("Could not load answers for %s: %s", uc_name, e)
    return {}


def _save_answers(uc_name: str, answers: dict):
    _answer_cache[uc_name] = answers
    try:
        _ANSWERS_DIR.mkdir(parents=True, exist_ok=True)
        fpath = _ANSWERS_DIR / f"{uc_name}_answers.json"
        fpath.write_text(json.dumps(answers, indent=2, default=str), encoding="utf-8")
    except Exception as e:
        logger.error("Could not save answers for %s: %s", uc_name, e)
