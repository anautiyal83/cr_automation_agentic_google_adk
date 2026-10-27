"""CIQ Validation & JSON Output Generation — main skill orchestrator.

Two entry points:
- ``generate(input)`` — first run: generate both YAMLs via LLM + autofix
- ``regenerate(feedback)`` — after test failures: correct the broken YAML(s) using feedback

CIQ structure comes from ``requirements.doc_extracted.ciq_sheets`` — already
parsed by the upstream requirement_gathering skill. No redundant Excel reads.

The skill is stateful: after ``generate()``, it retains the CIQ structure,
requirements, schemas, and generated YAMLs so ``regenerate()`` only needs
the failure feedback.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .validation_gen import generate_validation_rules, regenerate_validation_rules
from .json_output_gen import generate_json_output, regenerate_json_output

logger = logging.getLogger(__name__)

LlmFn = Callable[[str, str], Awaitable[str]]


@dataclass
class SkillInput:
    """Input for CIQ validation & JSON output generation."""
    uc_name: str
    requirements: dict = field(default_factory=dict)
    generation_hints: dict = field(default_factory=dict)
    schema_dir: str | None = None


@dataclass
class RegenerateFeedback:
    """Feedback from testing skill for regeneration."""
    test_results: list[dict] = field(default_factory=list)
    reviewer_analysis: str = ""
    attempt_number: int = 1
    target: str = ""  # "validation_rules" | "json_output" | "" (both)


@dataclass
class SkillOutput:
    """Output from CIQ validation & JSON output generation."""
    validation_rules_yaml: str = ""
    json_output_yaml: str = ""
    validation_errors: list[str] = field(default_factory=list)
    json_output_errors: list[str] = field(default_factory=list)
    status: str = "pending"  # "passed" | "failed" | "pending"
    error: str = ""


class CiqValidationJsonGenSkill:
    """Generates validation-rules.yaml and json-output.yaml from requirements.

    CIQ structure is extracted from ``requirements.doc_extracted.ciq_sheets``
    (already parsed by the requirement_gathering skill).

    Stateful: retains context between generate() and regenerate() calls.
    LLM is injected via ``llm_fn(system_prompt, user_prompt) -> str``.
    """

    def __init__(self, llm_fn: LlmFn):
        self._llm_fn = llm_fn
        self._uc_name = ""
        self._ciq_sheets: dict = {}
        self._requirements: dict = {}
        self._validation_schema_summary = ""
        self._json_output_schema_summary = ""
        self._last_validation_yaml = ""
        self._last_json_output_yaml = ""

    async def generate(self, inp: SkillInput) -> SkillOutput:
        """Generate both YAML artifacts from requirements.

        CIQ structure is read from ``inp.requirements["doc_extracted"]["ciq_sheets"]``.
        """
        self._uc_name = inp.uc_name
        self._requirements = inp.requirements

        # Extract CIQ structure from requirements (already parsed upstream)
        doc_extracted = inp.requirements.get("doc_extracted", {})
        self._ciq_sheets = doc_extracted.get("ciq_sheets", {})
        if not self._ciq_sheets:
            return SkillOutput(
                status="failed",
                error="No CIQ structure in requirements.doc_extracted.ciq_sheets",
            )

        # Load schema summaries from generation_hints (injected by requirement_gathering)
        self._validation_schema_summary = inp.generation_hints.get("step_3", "")
        self._json_output_schema_summary = inp.generation_hints.get("step_4", "")

        if not self._validation_schema_summary or not self._json_output_schema_summary:
            self._discover_schemas(inp.schema_dir)

        # Step 1: Generate validation-rules.yaml
        try:
            val_yaml, val_errors = await generate_validation_rules(
                self._llm_fn,
                self._uc_name,
                self._ciq_sheets,
                self._requirements,
                self._validation_schema_summary,
            )
            self._last_validation_yaml = val_yaml
        except Exception as e:
            logger.error("Validation rules generation failed: %s", e, exc_info=True)
            return SkillOutput(status="failed", error=f"Validation rules generation failed: {e}")

        # Step 2: Generate json-output.yaml
        try:
            json_yaml, json_errors = await generate_json_output(
                self._llm_fn,
                self._uc_name,
                self._ciq_sheets,
                self._requirements,
                self._json_output_schema_summary,
            )
            self._last_json_output_yaml = json_yaml
        except Exception as e:
            logger.error("JSON output generation failed: %s", e, exc_info=True)
            return SkillOutput(
                validation_rules_yaml=val_yaml,
                validation_errors=val_errors,
                status="failed",
                error=f"JSON output generation failed: {e}",
            )

        all_errors = val_errors + json_errors
        status = "failed" if all_errors else "passed"

        return SkillOutput(
            validation_rules_yaml=val_yaml,
            json_output_yaml=json_yaml,
            validation_errors=val_errors,
            json_output_errors=json_errors,
            status=status,
        )

    async def regenerate(self, feedback: RegenerateFeedback) -> SkillOutput:
        """Regenerate YAML(s) using test failure feedback.

        Uses retained state from the previous generate() call.
        Only regenerates the broken artifact(s), keeps passing ones unchanged.
        """
        if not self._ciq_sheets:
            return SkillOutput(status="failed", error="No CIQ context — call generate() first")

        test_failures_text = json.dumps(feedback.test_results, indent=2, default=str)[:5000]

        val_yaml = self._last_validation_yaml
        val_errors: list[str] = []
        json_yaml = self._last_json_output_yaml
        json_errors: list[str] = []

        regen_validation = feedback.target in ("validation_rules", "")
        regen_json = feedback.target in ("json_output", "")

        if regen_validation and self._last_validation_yaml:
            try:
                val_yaml, val_errors = await regenerate_validation_rules(
                    self._llm_fn,
                    self._last_validation_yaml,
                    self._ciq_sheets,
                    test_failures_text,
                    feedback.reviewer_analysis,
                    self._validation_schema_summary,
                )
                self._last_validation_yaml = val_yaml
            except Exception as e:
                logger.error("Validation rules regeneration failed: %s", e, exc_info=True)
                val_errors = [f"Regeneration failed: {e}"]

        if regen_json and self._last_json_output_yaml:
            try:
                json_yaml, json_errors = await regenerate_json_output(
                    self._llm_fn,
                    self._last_json_output_yaml,
                    self._ciq_sheets,
                    test_failures_text,
                    feedback.reviewer_analysis,
                    self._json_output_schema_summary,
                )
                self._last_json_output_yaml = json_yaml
            except Exception as e:
                logger.error("JSON output regeneration failed: %s", e, exc_info=True)
                json_errors = [f"Regeneration failed: {e}"]

        all_errors = val_errors + json_errors
        status = "failed" if all_errors else "passed"

        return SkillOutput(
            validation_rules_yaml=val_yaml,
            json_output_yaml=json_yaml,
            validation_errors=val_errors,
            json_output_errors=json_errors,
            status=status,
        )

    def _discover_schemas(self, schema_dir: str | None = None):
        """Discover and build schema summaries if not provided via generation_hints."""
        from src.skills.requirement_gathering.schema_discovery import (
            discover_schemas,
            build_validation_rules_summary,
            build_json_output_summary,
        )
        schemas = discover_schemas(schema_dir)
        if not self._validation_schema_summary and schemas.validation_rules.found:
            self._validation_schema_summary = build_validation_rules_summary(schemas.validation_rules)
        if not self._json_output_schema_summary and schemas.json_output.found:
            self._json_output_schema_summary = build_json_output_summary(schemas.json_output)
