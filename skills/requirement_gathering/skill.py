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
        if self._vendor_profile:
            analysis_notes += "\n\n" + _vendor_profile_summary(self._vendor_profile)
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
        # Drop SSH/PAM questions covered by vendor profile
        logger.info("VENDOR_PROFILE_DEBUG: vendor_profile=%s, questions_before_filter=%d",
                     bool(self._vendor_profile), len(questions) if questions else 0)
        if self._vendor_profile:
            logger.info("VENDOR_PROFILE_DEBUG: gateway_enabled=%s, ssh_user=%s, nodes=%d",
                         self._vendor_profile.get("vendor", {}).get("access_gateway", {}).get("enabled"),
                         self._vendor_profile.get("vendor", {}).get("ssh_defaults", {}).get("user"),
                         len(self._vendor_profile.get("category_1_node_activity", {}).get("nodes", [])))
        if questions and self._vendor_profile:
            questions = questionnaire.filter_vendor_profile_questions(questions, self._vendor_profile)
            logger.info("VENDOR_PROFILE_DEBUG: questions_after_filter=%d", len(questions))

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

        # Re-analyze with new answers
        combined_notes = self._developer_notes
        if self._vendor_profile:
            combined_notes += "\n\n" + _vendor_profile_summary(self._vendor_profile)
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

        # Persist all data to the canonical UC document
        _save_requirements(self._uc_name, requirements)
        # Also save analysis and vendor profile snapshot
        doc = _load_uc_doc(self._uc_name) or {}
        doc["analysis"] = self._analysis
        if self._vendor_profile:
            doc["vendor_profile"] = self._vendor_profile
        _save_uc_doc(self._uc_name, doc)

        # Generate HTML report
        html = generate_summary_html(
            self._uc_name,
            self._ciq_sheets,
            self._analysis,
            requirements,
            self._developer_notes,
            self._all_answers,
            self._doc_extracted,
            question_texts=self._question_texts,
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
