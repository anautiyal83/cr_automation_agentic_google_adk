"""Schema discovery — locate and parse project YAML schemas at runtime.

Reads the authoritative schema files from the project's ``schemas/`` directory
and extracts structured summaries that the LLM can use to generate
schema-compliant validation-rules.yaml and json-output.yaml.

No schema definitions are embedded in code — everything is read from disk.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default schema directory relative to project root
_DEFAULT_SCHEMA_DIR = Path(__file__).resolve().parent.parent.parent / "schemas"

_VALIDATION_RULES_FILENAME = "validation_rules_schema.yaml"
_JSON_OUTPUT_FILENAME = "json-output-schema.yaml"


@dataclass
class SchemaInfo:
    """Parsed schema information for a single schema file."""
    name: str
    path: str = ""
    raw_text: str = ""
    parsed: dict[str, Any] = field(default_factory=dict)
    found: bool = False
    error: str = ""


@dataclass
class DiscoveredSchemas:
    """All discovered project schemas."""
    validation_rules: SchemaInfo = field(default_factory=lambda: SchemaInfo(name="validation_rules"))
    json_output: SchemaInfo = field(default_factory=lambda: SchemaInfo(name="json_output"))
    schema_dir: str = ""

    @property
    def all_found(self) -> bool:
        return self.validation_rules.found and self.json_output.found

    @property
    def missing(self) -> list[str]:
        result = []
        if not self.validation_rules.found:
            result.append(f"validation_rules_schema.yaml (looked in {self.schema_dir})")
        if not self.json_output.found:
            result.append(f"json-output-schema.yaml (looked in {self.schema_dir})")
        return result


def discover_schemas(schema_dir: str | Path | None = None) -> DiscoveredSchemas:
    """Discover and read project schema files.

    Args:
        schema_dir: Path to schemas directory. If None, uses the project's
                    ``schemas/`` directory (auto-detected from this file's location).

    Returns:
        DiscoveredSchemas with raw text and parsed dicts for each schema.
        Check ``.all_found`` and ``.missing`` to verify completeness.
    """
    sdir = Path(schema_dir) if schema_dir else _DEFAULT_SCHEMA_DIR
    result = DiscoveredSchemas(schema_dir=str(sdir))

    if not sdir.is_dir():
        logger.warning("Schema directory not found: %s", sdir)
        result.validation_rules.error = f"Directory not found: {sdir}"
        result.json_output.error = f"Directory not found: {sdir}"
        return result

    # Discover validation rules schema
    result.validation_rules = _read_schema(sdir / _VALIDATION_RULES_FILENAME, "validation_rules")

    # Discover json-output schema
    result.json_output = _read_schema(sdir / _JSON_OUTPUT_FILENAME, "json_output")

    found = [s.name for s in (result.validation_rules, result.json_output) if s.found]
    logger.info("Schema discovery: found %d/2 schemas in %s: %s", len(found), sdir, found)

    return result


def _read_schema(path: Path, name: str) -> SchemaInfo:
    """Read and parse a single schema YAML file."""
    info = SchemaInfo(name=name, path=str(path))

    if not path.exists():
        info.error = f"File not found: {path}"
        return info

    try:
        raw = path.read_text(encoding="utf-8")
        info.raw_text = raw
        info.found = True
    except Exception as e:
        info.error = f"Could not read {path}: {e}"
        return info

    try:
        from ruamel.yaml import YAML
        from io import StringIO
        parsed = YAML().load(StringIO(raw))
        info.parsed = dict(parsed) if parsed else {}
    except Exception as e:
        # Raw text is still available even if parse fails
        info.error = f"YAML parse warning: {e}"
        logger.debug("Schema %s parsed with warning: %s", name, e)

    return info


# ---------------------------------------------------------------------------
# Schema summary builders — produce concise text for LLM prompt injection
# ---------------------------------------------------------------------------

def build_validation_rules_summary(schema: SchemaInfo) -> str:
    """Build a concise summary of the validation-rules schema for LLM consumption.

    Extracts: supported column types, rule types, workbook rules, settings,
    and outputs. This is injected into generation_hints.step_3.
    """
    if not schema.found:
        return f"[SCHEMA NOT FOUND: {schema.error}]"

    p = schema.parsed
    lines = ["=== VALIDATION RULES SCHEMA REFERENCE ==="]

    # Settings
    settings = p.get("settings", {})
    if settings:
        lines.append(f"\nGlobal settings: {_dict_summary(settings)}")

    # Supported column types (extract from sheets.<SheetName>.columns)
    lines.append("\n--- Supported column types ---")
    lines.append("string (default), enum, integer, decimal, boolean, date, time, datetime,")
    lines.append("email, phone, ip, mac, cidr, hostname, fqdn, protocol, urlScheme, custom")

    # Column-level keys
    lines.append("\n--- Column-level keys ---")
    lines.append("required, type, aliases, unique, uniqueWhen, ignoreCase, description,")
    lines.append("minLength, maxLength, pattern, patternMessage, allowedValues,")
    lines.append("dropdownDisabled, allowedValuesWhen, crossRef, validator,")
    lines.append("minOnePerGroup, consolidate, consolidate_separator,")
    lines.append("conditionalPattern, messages, sheetRef, sheetRefIgnoreCase, multi")
    lines.append("\ninteger extra: minValue, maxValue, allowedRanges")
    lines.append("decimal extra: minDecimal, maxDecimal, precision")
    lines.append("boolean: accept ('true/false' | 'yes/no' | '1/0')")
    lines.append("date/time/datetime: format (SimpleDateFormat pattern)")
    lines.append("ip: accepts (ipv4 | ipv6 | both)")

    # allowedValuesWhen operators
    lines.append("\n--- allowedValuesWhen operators ---")
    lines.append("equals (default), notEquals, contains, blank, notBlank,")
    lines.append("greaterThan, greaterThanOrEquals, lessThan, lessThanOrEquals")
    lines.append("Symbol aliases: ==, !=, >, >=, <, <=")
    lines.append("Compound: when: \"COL_A == 'X' && COL_B == 'Y'\" (supports && / ||)")

    # Row rules
    lines.append("\n--- Row rules (per sheet) ---")
    lines.append("require (with when condition), forbid (with when condition),")
    lines.append("compare (cross-column), one_of, only_one, all_or_none,")
    lines.append("unique_key (composite), pooled_unique, sum...equals")
    lines.append("\nwhen operators: equals, notEquals, blank, notBlank, contains,")
    lines.append("greaterThan, greaterThanOrEquals, lessThan, lessThanOrEquals")
    lines.append("Compound: all: [...] / any: [...] nesting")
    lines.append("Cross-sheet existence: sheet + column + exists/notExists")

    # Workbook rules
    lines.append("\n--- Workbook-level rules ---")
    lines.append("subset, superset, unique, match, subset_any, count_per,")
    lines.append("constant_within, set_match, contiguous_sequence, set,")
    lines.append("cross_check, cross_sheet_compare")

    # Global flags
    lines.append("\n--- Global flags ---")
    lines.append("validateIndexSheets, validateNodeIds, skipValidationValues")
    lines.append("node_id_config: sheet, nodeColumn, niamColumn")

    # Outputs
    lines.append("\n--- Post-validation outputs (outputs:) ---")
    lines.append("Aggregates: distinct, count, sum, group")
    lines.append("Each: sheet, column, aggregate, separator, groupBy, name")

    # Report output
    lines.append("\n--- Report output ---")
    lines.append("formats: [JSON, HTML, MSEXCEL], filename with {nodeType}/{activity} placeholders")

    # Sheet-level settings override
    lines.append("\n--- Sheet-level ---")
    lines.append("required, required_if_listed_in, aliases, settings (headerRow, dataStartRow, etc.)")
    lines.append("columns: {ColumnName: {type, required, ...}}")
    lines.append("rules: [{require/forbid/compare/one_of/only_one/all_or_none/unique_key/pooled_unique/sum}]")

    lines.append("\n=== END VALIDATION RULES SCHEMA ===")
    return "\n".join(lines)


def build_json_output_summary(schema: SchemaInfo) -> str:
    """Build a concise summary of the json-output schema for LLM consumption.

    Extracts: output_mode, segregate_by, data directives, examples.
    Injected into generation_hints.step_4.
    """
    if not schema.found:
        return f"[SCHEMA NOT FOUND: {schema.error}]"

    lines = ["=== JSON OUTPUT SCHEMA REFERENCE ==="]

    # Top-level keys
    lines.append("\n--- Top-level keys ---")
    lines.append("output_mode: individual | single (required)")
    lines.append("segregate_by: {sheet, column, as: $varname} (required for individual)")
    lines.append("key_dot_replacement: string (optional, replaces dots in column-name keys)")
    lines.append("data: {...} (required, the JSON template tree)")

    # Value resolution
    lines.append("\n--- Value resolution rules ---")
    lines.append("Plain string (no $, no .) inside _each block: column name in current row")
    lines.append("Plain string outside _each: static string literal")
    lines.append("\"SheetName.ColumnName\": first non-blank value from sheet+column")
    lines.append("'\"Column.With.Dots\"': literal column name (bypasses Sheet.Column parsing)")
    lines.append("\"Sheet.Col WHERE [Sheet.]FilterCol = value\": relational lookup")
    lines.append("  Multiple WHERE conditions joined with AND")
    lines.append("$varname: variable from segregate_by or DISTINCT iteration")

    # Array directives
    lines.append("\n--- Array directives (_each) ---")
    lines.append("_each: \"DISTINCT [FROM] Sheet.Column [WHERE cond] [AS $var]\" (RECOMMENDED)")
    lines.append("  Sets $var to each distinct value, scopes sheet rows automatically")
    lines.append("_each: SheetName (iterate every row)")
    lines.append("_each: \"SheetName WHERE [Sheet.]FilterCol = value\" (filtered rows)")
    lines.append("_each: \"SheetName WHERE Col IN Sheet.Col\" (multi-value membership)")

    # Other directives
    lines.append("\n--- Other directives ---")
    lines.append("_join: \"Sheet.Column [WHERE ...]\" + separator (string joining)")
    lines.append("_ref: \"dotted.path.to.template.value\" (template self-reference)")
    lines.append("_col: $var | {_ref: ...} + separator (indirect column reference)")
    lines.append("_row: \"*\" | \"exclude [COL, ...]\" | {exclude: [...]} | [COL, ...] (emit all columns as map)")
    lines.append("_row_join: {exclude, format, separator} (serialise row to string)")
    lines.append("Nested objects: any map without _each/_join/_ref/_col/_row/_row_join")

    lines.append("\n=== END JSON OUTPUT SCHEMA ===")
    return "\n".join(lines)


def _dict_summary(d: dict, max_keys: int = 10) -> str:
    """Brief string representation of a dict."""
    items = list(d.items())[:max_keys]
    parts = [f"{k}={v}" for k, v in items]
    if len(d) > max_keys:
        parts.append(f"... +{len(d) - max_keys} more")
    return "{" + ", ".join(parts) + "}"
