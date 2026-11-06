"""RequirementGatheringSkill — main orchestrator for telecom activity requirement gathering.

Usage::

    skill = RequirementGatheringSkill(llm_fn=my_async_llm_function)

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
from .parsers import parse_ciq, parse_command_outputs, parse_mop, parse_ssh_logs, parse_xml_samples
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
    # Questions a later step could not settle (shared.clarifications): asked on every path
    extra_questions: list = field(default_factory=list)
    ciq_data: dict | None = None
    mop_file_path: str = ""
    ssh_log_path: str = ""
    xml_sample_paths: list[str] = field(default_factory=list)
    # Other uploaded text documents (session logs, command outputs) — scanned only for
    # real command → output samples, not sent to the LLM
    transcript_paths: list[str] = field(default_factory=list)
    developer_notes: str = ""
    previous_answers: dict | None = None
    schema_dir: str | None = None
    vendor_profile: dict | None = None  # Pre-filled from vendor profile selection


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


class RequirementGatheringSkill:
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
        self._question_texts: dict[str, str] = {}  # q_id -> question text
        self._round_num = 0
        self._parsed_data: dict[str, Any] = {}
        self._schemas: DiscoveredSchemas | None = None
        self._schema_dir: str | None = None
        self._vendor_profile: dict | None = None
        self._previous_requirements: dict = {}
        self._edits: list[dict] = []  # "Edit requirements" at the step 2 review, in order
        self._ciq_file_path = ""
        self._ciq_replaced = False                      # a corrected CIQ was uploaded at step 2
        self._all_ciq_sheets: dict[str, dict] = {}      # every visible sheet of the workbook
        self._excluded_sheets: set[str] = set()         # sheets the activity does not use (per UC)

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
        self._vendor_profile = inp.vendor_profile

        # Discover project schemas early (for downstream hints)
        self._schemas = discover_schemas(inp.schema_dir)
        if self._schemas.missing:
            logger.warning("Missing schemas: %s", self._schemas.missing)

        # Load previous data (accumulated across re-runs)
        if inp.previous_answers:
            self._all_answers = dict(inp.previous_answers)
        else:
            self._all_answers = dict(_load_answers(inp.uc_name))
        self._question_texts = dict(_load_question_texts(inp.uc_name))
        self._previous_requirements = _load_requirements(inp.uc_name)

        # --- Deterministic parsing ---
        self._raw_artifacts = self._read_raw_artifacts(inp)
        mop_parsed = parse_mop(self._raw_artifacts.get("mop_text", ""))
        ssh_parsed = parse_ssh_logs(self._raw_artifacts.get("ssh_logs", ""))
        xml_parsed = parse_xml_samples(self._raw_artifacts.get("xml_samples", ""))
        command_samples = parse_command_outputs(
            "\n\n".join([self._raw_artifacts.get("ssh_logs", "")] + _read_texts(inp.transcript_paths)))
        # Keep them for later runs too: steps 6/8 reuse real outputs nobody re-uploads (DEP-20)
        from tools.command_samples import add as _remember_samples
        _remember_samples(command_samples, source=inp.uc_name)
        self._ciq_file_path = inp.ciq_file_path or ""
        self._all_ciq_sheets = parse_ciq(
            ciq_file_path=inp.ciq_file_path,
            ciq_data=inp.ciq_data,
        )
        # Sheets the developer said this activity does not use (Edit requirements, kept per UC)
        self._excluded_sheets = {s for s in _load_uc_doc(inp.uc_name).get("excluded_ciq_sheets") or []
                                 if s in self._all_ciq_sheets}
        self._ciq_sheets = {k: v for k, v in self._all_ciq_sheets.items() if k not in self._excluded_sheets}

        self._doc_extracted = {
            "mop_commands": mop_parsed["commands"],
            "mop_phases": mop_parsed["phases"],
            "ssh_prompts": ssh_parsed["prompts"],
            "ssh_credentials": ssh_parsed["credentials"],
            "ldap_port": ssh_parsed["ldap_port"],
            "xml_files": xml_parsed,
            "ciq_sheets": self._ciq_sheets,
            # Real command outputs from uploaded transcripts: step 6 checks validation
            # regexes against them, step 8 can reuse them as simulator responses
            "command_samples": command_samples,
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
        if self._vendor_profile:
            analysis_notes += "\n\n" + _vendor_profile_summary(self._vendor_profile)
        if self._all_answers:
            analysis_notes += "\n\n=== Previously Answered Questions ===\n" + self._answers_text()

        # --- LLM analysis ---
        self._analysis = await analyzer.analyze_uc(
            self._llm_fn,
            self._uc_name,
            ciq_summary,
            mop_summary,
            cmd_summary,
            analysis_notes,
        )
        self._drop_answered_gaps()

        readiness = self._analysis.get("overall_readiness", "INSUFFICIENT")

        # If previous run already gathered substantial answers OR compiled requirements,
        # skip the questionnaire and go straight to compilation.
        # The user already answered everything — just use accumulated data.
        has_previous_reqs = bool(self._previous_requirements.get("category_1_node_activity"))
        has_substantial_answers = len(self._all_answers) >= 10

        # Deterministic CIQ questions (either-or columns) are asked even when the LLM finds no
        # gaps: the documents cannot say how one column's value is derived from the other
        ciq_questions = [q for q in questionnaire.ciq_alternative_questions(self._ciq_sheets)
                         if q["id"] not in self._all_answers]
        # MOP conditions whose other branch the MOP never describes (G-16): a question, not a guess
        from skills.ciq_validation_json_gen.rules_baseline import read_ciq_rows
        ciq_questions = [q for q in (inp.extra_questions or []) if isinstance(q, dict) and q.get("id")
                         and q["id"] not in self._all_answers] + ciq_questions
        ciq_questions += [q for q in questionnaire.mop_branch_questions(
                              (self._doc_extracted or {}).get("mop_commands") or [], self._ciq_sheets,
                              read_ciq_rows(getattr(inp, "ciq_file_path", "") or "", self._ciq_sheets))
                          if q["id"] not in self._all_answers]
        # A backup is stored on the repo server: which one (it becomes a node of the workflow)
        ciq_questions += [q for q in questionnaire.repo_server_question(
                              (self._doc_extracted or {}).get("mop_commands") or [])
                          if q["id"] not in self._all_answers]
        self._store_question_texts(ciq_questions)
        # Only the CIQ questions are asked on the paths below: their answers need no new round
        self._ciq_only_round = bool(ciq_questions)
        if ciq_questions:
            self._round_num = 1

        if has_previous_reqs and has_substantial_answers:
            logger.info(
                "Skipping questionnaire — %d previous answers + compiled requirements exist "
                "(%d CIQ question(s) still open)", len(self._all_answers), len(ciq_questions),
            )
            return SkillOutput(
                analysis=self._analysis,
                readiness="COMPLETE",
                doc_extracted=self._doc_extracted,
                all_answers=self._all_answers,
                questions=ciq_questions,
                round_num=1,
            )

        if self._all_answers and readiness == "COMPLETE":
            return SkillOutput(
                analysis=self._analysis,
                readiness=readiness,
                doc_extracted=self._doc_extracted,
                all_answers=self._all_answers,
                questions=ciq_questions,
                round_num=1,
            )

        # Generate questions for gaps (first run or insufficient previous data)
        available_data = questionnaire.build_available_data_summary(
            self._ciq_sheets, self._raw_artifacts, ssh_parsed,
            vendor_profile=self._vendor_profile,
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
        # No pattern / secret questions, no repeats in other words (spec 011)
        questions = questionnaire.clean_questions(questions, self._question_texts, self._all_answers)
        # Drop SSH/PAM questions covered by vendor profile
        if questions and self._vendor_profile:
            questions = questionnaire.filter_vendor_profile_questions(questions, self._vendor_profile)
            logger.info("Vendor profile filter: %d questions remain after filtering", len(questions))

        questions = ciq_questions + [q for q in questions if q.get("id") not in {c["id"] for c in ciq_questions}]
        self._ciq_only_round = False

        # Store question texts for the summary report
        self._store_question_texts(questions)

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
        if getattr(self, "_ciq_only_round", False):
            # The requirements were complete; only the CIQ either-or question was open
            self._ciq_only_round = False
            return SkillOutput(
                analysis=self._analysis,
                readiness="COMPLETE",
                doc_extracted=self._doc_extracted,
                all_answers=self._all_answers,
                questions=[],
                round_num=self._round_num,
            )

        # Re-analyze with new answers
        combined_notes = self._developer_notes
        if self._vendor_profile:
            combined_notes += "\n\n" + _vendor_profile_summary(self._vendor_profile)
        if self._all_answers:
            combined_notes += "\n\n=== Developer Answers ===\n" + self._answers_text()

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
        self._drop_answered_gaps()

        readiness = self._analysis.get("overall_readiness", "INSUFFICIENT")

        # Stop asking after enough data is collected
        if readiness == "COMPLETE" or len(self._all_answers) >= 50:
            if len(self._all_answers) >= 50:
                logger.info("Stopping questionnaire — %d answers is sufficient", len(self._all_answers))
            return SkillOutput(
                analysis=self._analysis,
                readiness="COMPLETE" if len(self._all_answers) >= 50 else readiness,
                doc_extracted=self._doc_extracted,
                all_answers=self._all_answers,
                questions=[],
                round_num=self._round_num,
            )

        # Generate next round
        ssh_parsed = parse_ssh_logs(self._raw_artifacts.get("ssh_logs", ""))
        available_data = questionnaire.build_available_data_summary(
            self._ciq_sheets, self._raw_artifacts, ssh_parsed,
            vendor_profile=self._vendor_profile,
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
        # No pattern / secret questions, no repeats in other words (spec 011)
        questions = questionnaire.clean_questions(questions, self._question_texts, self._all_answers)
        # Drop SSH/PAM questions covered by vendor profile
        if questions and self._vendor_profile:
            questions = questionnaire.filter_vendor_profile_questions(questions, self._vendor_profile)

        # Store question texts for the summary report
        self._store_question_texts(questions)

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

    def apply_edit(self, answers: dict[str, Any], note: str, by: str = "", at: str = "",
                   ciq_file_path: str = "", excluded_sheets: list[str] | None = None) -> dict[str, Any]:
        """The developer's edit of the compiled requirements (step 2 review): changed answers
        replace the old ones, also in the UC's saved answers, so the next run does not bring them
        back; the note joins the developer notes as authoritative; a corrected CIQ workbook is read
        again; ``excluded_sheets`` (all sheets the activity does not use) are left out, also in
        later runs of the UC. Returns the changed answers.
        Call ``incorporate_answers({})`` and ``compile_final()`` afterwards."""
        from shared.requirement_edits import note_block
        changed = {k: {"from": self._all_answers.get(k, ""), "to": v} for k, v in (answers or {}).items()
                   if str(self._all_answers.get(k, "")) != str(v)}
        self._all_answers.update({k: c["to"] for k, c in changed.items()})
        _save_answers(self._uc_name, self._all_answers)
        if note and note.strip():
            self._developer_notes = "\n\n".join(
                x for x in (self._developer_notes.strip(), note_block(note, by, at)) if x)
        record: dict[str, Any] = {"at": at, "by": by, "note": (note or "").strip(), "answers": changed}
        if ciq_file_path:
            self._all_ciq_sheets = parse_ciq(ciq_file_path=ciq_file_path)
            self._ciq_file_path, self._ciq_replaced = ciq_file_path, True
            record["ciq_file"] = Path(ciq_file_path).name
        if ciq_file_path or excluded_sheets is not None:
            if excluded_sheets is not None:
                self._excluded_sheets = set(excluded_sheets)
            self._excluded_sheets &= set(self._all_ciq_sheets)
            doc = _load_uc_doc(self._uc_name) or {}
            doc["excluded_ciq_sheets"] = sorted(self._excluded_sheets)
            _save_uc_doc(self._uc_name, doc)
            self._ciq_sheets = {k: v for k, v in self._all_ciq_sheets.items() if k not in self._excluded_sheets}
            self._doc_extracted["ciq_sheets"] = self._ciq_sheets
            record["excluded_sheets"] = sorted(self._excluded_sheets)
        self._ciq_only_round = False  # the edit is analysed again in full
        self._edits.append(record)
        return changed

    async def compile_final(self) -> SkillOutput:
        """Compile final requirements package and generate HTML report.

        Merges with previously saved requirements so each re-run
        accumulates data rather than starting from scratch.

        Returns ``SkillOutput`` with ``requirements``, ``requirements_json``,
        and ``requirements_html`` populated.
        """
        _save_answers(self._uc_name, self._all_answers)
        _save_question_texts(self._uc_name, self._question_texts)

        requirements = await analyzer.compile_requirements(
            self._llm_fn,
            self._uc_name,
            self._analysis,
            self._developer_notes,
            self._all_answers,
            raw_artifacts=self._raw_artifacts,
        )

        # Merge with previous run's requirements (fill gaps, keep existing data)
        if self._previous_requirements:
            requirements = compiler.merge_previous_requirements(
                requirements, self._previous_requirements,
            )

        # Merge vendor profile data (nodes, SSH, escalation, CIQ mappings)
        if self._vendor_profile:
            requirements = compiler.merge_vendor_profile(requirements, self._vendor_profile)

        # Merge deterministic extractions (override LLM gaps)
        requirements = compiler.merge_doc_extracted(requirements, self._doc_extracted)
        # Developer notes verbatim: step 6 follows them as authoritative guidance
        if self._developer_notes:
            requirements["developer_notes"] = self._developer_notes

        # Enforce user answers as hard overrides
        requirements = compiler.enforce_answers(
            requirements,
            self._all_answers,
            self._raw_artifacts,
            self._doc_extracted.get("ssh_prompts", []),
        )

        if self._all_answers.get("orch_repo_server"):
            repo = questionnaire.parse_repo_server(self._all_answers["orch_repo_server"])
            if repo.get("host"):
                requirements["repo_server"] = repo

        # Inject schema summaries into generation_hints for Steps 3 & 4
        requirements = compiler.inject_schema_hints(
            requirements,
            schemas=self._schemas,
            schema_dir=self._schema_dir,
        )

        # Persist all data to the canonical UC document
        _save_requirements(self._uc_name, requirements)
        # Also save analysis and vendor profile snapshot
        doc = _load_uc_doc(self._uc_name) or {}
        doc["analysis"] = self._analysis
        if self._vendor_profile:
            doc["vendor_profile"] = self._vendor_profile
        _save_uc_doc(self._uc_name, doc)
        # This run's edits (not saved with the UC: the next run starts from their result)
        if self._edits:
            requirements["requirement_updates"] = list(self._edits)
        if self._excluded_sheets:
            requirements["excluded_ciq_sheets"] = sorted(self._excluded_sheets)
        if self._ciq_replaced:  # later steps read this workbook (agents.requirements_agent.ciq_file_path)
            requirements["ciq_file_path"] = self._ciq_file_path

        # Override readiness for report when we have sufficient data
        report_analysis = dict(self._analysis)
        if len(self._all_answers) >= 10 and self._previous_requirements:
            report_analysis["overall_readiness"] = "COMPLETE"

        # Generate HTML report
        html = generate_summary_html(
            self._uc_name,
            self._ciq_sheets,
            report_analysis,
            requirements,
            self._developer_notes,
            self._all_answers,
            self._doc_extracted,
            question_texts=self._question_texts,
        )

        # Step 6 reads the answers with their questions ({id: answer} alone loses the meaning)
        requirements["developer_questions"] = {
            k: v for k, v in self._question_texts.items() if k in (requirements.get("developer_answers") or {})}
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

    def _answers_text(self) -> str:
        """Developer answers as Q/A pairs for the LLM: ``{"orch_q2": "ls -ltrh …"}`` alone does not
        say what was answered, and the re-analysis raised the same gaps again (OB-21)."""
        parts = []
        for qid, answer in self._all_answers.items():
            question = self._question_texts.get(qid)
            parts.append(f"Q: {question}\nA: {answer}" if question else f"{qid}: {answer}")
        return "\n\n".join(parts)

    def _drop_answered_gaps(self) -> None:
        """Remove gaps that an answered question already covers (most of the gap's words are in
        the question); with no gap left the requirements are COMPLETE."""
        answered = [_gap_words(self._question_texts[qid]) for qid, a in self._all_answers.items()
                    if qid in self._question_texts and str(a).strip()]
        if not answered or not isinstance(self._analysis, dict):
            return
        dropped, left = [], 0
        for cat in ("category_1_node_activity", "category_2_commands", "category_3_orchestration"):
            section = self._analysis.get(cat)
            if not isinstance(section, dict) or not isinstance(section.get("gaps"), list):
                continue
            keep = []
            for gap in section["gaps"]:
                words = _gap_words(gap if isinstance(gap, str) else json.dumps(gap))
                if words and any(len(words & q) / len(words) >= 0.6 for q in answered):
                    dropped.append(gap)
                else:
                    keep.append(gap)
            section["gaps"] = keep
            left += len(keep)
        if dropped:
            logger.info("Dropped %d gap(s) already answered by the developer: %s", len(dropped),
                        "; ".join(str(g)[:80] for g in dropped))
            if not left:
                self._analysis["overall_readiness"] = "COMPLETE"

    def _store_question_texts(self, questions: list[dict] | None):
        """Store question ID → question text mapping for the summary report."""
        if not questions:
            return
        for q in questions:
            if isinstance(q, dict) and q.get("id") and q.get("question"):
                self._question_texts[q["id"]] = q["question"]

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
# Vendor profile → developer notes summary
# ------------------------------------------------------------------

def _vendor_profile_summary(vp: dict) -> str:
    """Convert vendor profile dict to a text block for LLM context.

    This tells the analyzer that SSH, PAM, escalation, and prompt details
    are ALREADY KNOWN so it should NOT list them as gaps.
    """
    lines = [
        "=== VENDOR PROFILE (pre-configured — treat ALL of these as KNOWN, not gaps) ===",
    ]
    vendor = vp.get("vendor", {})
    if vendor.get("name"):
        lines.append(f"Vendor: {vendor['name']}")

    gw = vendor.get("access_gateway", {})
    if gw.get("enabled"):
        lines.append(f"Access Gateway: {gw.get('name', 'PAM')} — ENABLED")
        lines.append(f"  Host: {gw.get('host', '?')}:{gw.get('port', 22)}")
        lines.append(f"  Username: {gw.get('username', '?')}")
        lines.append(f"  Auth method: {gw.get('auth_method', 'key')}")
        if gw.get("prompt_pattern"):
            lines.append(f"  Gateway prompt: {gw['prompt_pattern']}")
        lines.append("  >> SSH goes through this gateway — direct SSH details are NOT needed")

    ssh = vendor.get("ssh_defaults", {})
    if ssh:
        lines.append(f"SSH defaults: user={ssh.get('user', '?')}, port={ssh.get('port', 22)}")
        if ssh.get("kex_algorithms"):
            lines.append(f"  KEX: {ssh['kex_algorithms']}")
        if ssh.get("host_key_algorithms"):
            lines.append(f"  Host key: {ssh['host_key_algorithms']}")
        lines.append("  >> SSH config is KNOWN — do not ask about SSH user, port, password, or KEX")

    nodes = vp.get("category_1_node_activity", {}).get("nodes", [])
    for n in nodes:
        name = n.get("name", "?")
        node_ssh = n.get("ssh", {})
        node_esc = n.get("escalation", {})
        via_pam = node_ssh.get("login_via_pam", False)
        lines.append(f"Node {name}: login={'via gateway' if via_pam else 'direct SSH'}")
        if node_ssh.get("prompt_pattern"):
            lines.append(f"  Prompt: {node_ssh['prompt_pattern']} — KNOWN")
        if node_esc.get("method") and node_esc["method"] != "none":
            lines.append(f"  Escalation: {node_esc['method']} → {node_esc.get('target_user', '?')} — KNOWN")
            if node_esc.get("prompt_pattern"):
                lines.append(f"  Escalated prompt: {node_esc['prompt_pattern']} — KNOWN")
        if via_pam:
            lines.append(f"  >> {name} connects via gateway — do NOT ask SSH host/port/user/password for {name}")

    if vp.get("node_id_config"):
        nc = vp["node_id_config"]
        lines.append(f"Node ID column: {nc.get('node_column', '?')} — KNOWN")
        if nc.get("gateway_id_column"):
            lines.append(f"Gateway ID column: {nc['gateway_id_column']} — KNOWN")

    if vp.get("ciq_field_mappings"):
        lines.append(f"CIQ field mappings: {len(vp['ciq_field_mappings'])} fields pre-mapped — KNOWN")

    lines.append(">> ALL items above are KNOWN — do NOT report them as gaps or ask questions about them")
    return "\n".join(lines)


# ------------------------------------------------------------------
# UC Knowledge Document — single canonical file per UC
# ------------------------------------------------------------------
#
# Location: data/knowledge/{uc_name}/requirements.json
#
# Structure:
#   {
#     "uc_name": "...",
#     "version": 3,                    // incremented each save
#     "last_updated": "...",
#     "developer_answers": { "q_id": "answer", ... },
#     "question_texts":    { "q_id": "question text", ... },
#     "compiled_requirements": { ... },  // category_1, category_2, category_3, xml_templates
#     "vendor_profile": { ... },         // last-used vendor profile snapshot
#     "analysis": { ... },               // last LLM analysis
#   }

_uc_doc_cache: dict[str, dict] = {}


def _uc_doc_path(uc_name: str) -> Path:
    return _ANSWERS_DIR / uc_name / "requirements.json"


def _load_uc_doc(uc_name: str) -> dict:
    """Load the canonical UC knowledge document. Returns empty dict if none."""
    if uc_name in _uc_doc_cache:
        return _uc_doc_cache[uc_name]

    fpath = _uc_doc_path(uc_name)
    if fpath.exists():
        try:
            doc = json.loads(fpath.read_text(encoding="utf-8"))
            _uc_doc_cache[uc_name] = doc
            logger.info("Loaded UC doc v%d for %s (%d answers)",
                        doc.get("version", 0), uc_name,
                        len(doc.get("developer_answers", {})))
            return doc
        except Exception as e:
            logger.debug("Could not load UC doc for %s: %s", uc_name, e)

    # Migrate from old flat files if they exist
    doc = _migrate_old_files(uc_name)
    if doc:
        _uc_doc_cache[uc_name] = doc
        _save_uc_doc(uc_name, doc)
        return doc

    return {}


def _save_uc_doc(uc_name: str, doc: dict):
    """Save the canonical UC knowledge document."""
    from datetime import datetime, timezone
    doc["uc_name"] = uc_name
    doc["version"] = doc.get("version", 0) + 1
    doc["last_updated"] = datetime.now(timezone.utc).isoformat()
    _uc_doc_cache[uc_name] = doc
    try:
        fpath = _uc_doc_path(uc_name)
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
        logger.info("Saved UC doc v%d for %s", doc["version"], uc_name)
    except Exception as e:
        logger.error("Could not save UC doc for %s: %s", uc_name, e)


def _migrate_old_files(uc_name: str) -> dict | None:
    """Migrate old scattered files (_answers.json, _questions.json, _requirements.json)
    into the new canonical format. Returns None if no old files found."""
    doc: dict = {}
    found = False

    answers_file = _ANSWERS_DIR / f"{uc_name}_answers.json"
    if answers_file.exists():
        try:
            doc["developer_answers"] = json.loads(answers_file.read_text(encoding="utf-8"))
            found = True
        except Exception:
            pass

    questions_file = _ANSWERS_DIR / f"{uc_name}_questions.json"
    if questions_file.exists():
        try:
            doc["question_texts"] = json.loads(questions_file.read_text(encoding="utf-8"))
            found = True
        except Exception:
            pass

    reqs_file = _ANSWERS_DIR / f"{uc_name}_requirements.json"
    if reqs_file.exists():
        try:
            doc["compiled_requirements"] = json.loads(reqs_file.read_text(encoding="utf-8"))
            found = True
        except Exception:
            pass

    if found:
        logger.info("Migrated old files to UC doc for %s", uc_name)
        return doc
    return None


# Convenience accessors — used by the skill
_GAP_STOPWORDS = {
    "the", "and", "for", "with", "from", "what", "how", "which", "are", "should", "can", "does", "any",
    "there", "this", "that", "its", "into", "exact", "specific", "command", "commands", "syntax",
    "method", "way", "detail", "details", "need", "needed", "use", "used", "using", "mop", "mention",
    "mentions", "when", "where", "who", "will", "would", "must", "not", "also", "each", "all",
}


def _gap_words(text: str) -> set[str]:
    """Content words of a gap/question, lightly stemmed (transferring/transferred → transferr)."""
    import re
    words = set()
    for w in re.findall(r"[a-z0-9_]+(?:\.[a-z0-9_]+)*", str(text).lower()):
        if w in _GAP_STOPWORDS or (len(w) < 3 and not w.isdigit()):
            continue
        for suffix in ("ing", "ed", "s"):
            if w.endswith(suffix) and len(w) - len(suffix) >= 3:
                w = w[: -len(suffix)]
                break
        words.add(w)
    return words


def _load_answers(uc_name: str) -> dict:
    return _load_uc_doc(uc_name).get("developer_answers", {})


def _load_question_texts(uc_name: str) -> dict[str, str]:
    return _load_uc_doc(uc_name).get("question_texts", {})


def _load_requirements(uc_name: str) -> dict:
    return _load_uc_doc(uc_name).get("compiled_requirements", {})


def _save_answers(uc_name: str, answers: dict):
    doc = _load_uc_doc(uc_name) or {}
    doc["developer_answers"] = answers
    _save_uc_doc(uc_name, doc)


def _save_question_texts(uc_name: str, texts: dict[str, str]):
    doc = _load_uc_doc(uc_name) or {}
    existing = doc.get("question_texts", {})
    existing.update(texts)
    doc["question_texts"] = existing
    _save_uc_doc(uc_name, doc)


def _save_requirements(uc_name: str, requirements: dict):
    doc = _load_uc_doc(uc_name) or {}
    # Strip transient fields before persisting
    to_save = {k: v for k, v in requirements.items()
               if k not in ("doc_extracted", "generation_hints")}
    doc["compiled_requirements"] = to_save
    _save_uc_doc(uc_name, doc)


def _read_texts(paths: list[str]) -> list[str]:
    """Contents of the given text files, each prefixed with a file separator line."""
    texts = []
    for path in paths or []:
        try:
            texts.append(f"=== {Path(path).name} ===\n" + Path(path).read_text(encoding="utf-8", errors="replace"))
        except Exception as e:
            logger.debug("Could not read transcript %s: %s", path, e)
    return texts
