"""Schema-aware validation for generated YAML artifacts.

Validates that generated validation-rules.yaml and json-output.yaml conform
to the project schemas discovered at runtime. Does NOT modify the schemas.

Reports:
- YAML syntax errors
- Unsupported keys at each level
- Missing required keys
- Invalid values (e.g. unknown column types, unknown operators)
- Unresolved requirements (requirements present but not represented in YAML)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from io import StringIO
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Known-good values extracted from the authoritative schemas.
# These are read from schema_discovery, but we define the validation logic here.
# ---------------------------------------------------------------------------

_VALID_COLUMN_TYPES = {
    "string", "enum", "integer", "decimal", "boolean",
    "date", "time", "datetime", "email", "phone",
    "ip", "mac", "cidr", "hostname", "fqdn",
    "protocol", "urlScheme", "custom",
}

_VALID_COLUMN_KEYS = {
    "required", "type", "aliases", "unique", "uniqueWhen", "ignoreCase",
    "description", "minLength", "maxLength", "pattern", "patternMessage",
    "allowedValues", "dropdownDisabled", "allowedValuesWhen", "crossRef",
    "validator", "minOnePerGroup", "consolidate", "consolidate_separator",
    "conditionalPattern", "messages",
    # integer/decimal
    "minValue", "maxValue", "allowedRanges", "minDecimal", "maxDecimal", "precision",
    # boolean
    "accept",
    # date/time/datetime
    "format",
    # ip
    "accepts",
    # email/phone/mac/cidr/hostname/fqdn
    "multi",
    # enum/protocol/urlScheme
    "values",
    # sheetRef
    "sheetRef", "sheetRefIgnoreCase",
}

_VALID_RULE_KEYS = {
    "require", "forbid", "compare", "one_of", "only_one",
    "all_or_none", "unique_key", "pooled_unique", "sum", "equals", "when",
}

_VALID_WORKBOOK_RULE_TYPES = {
    "subset", "superset", "unique", "match", "subset_any",
    "count_per", "constant_within", "set_match", "contiguous_sequence",
    "set", "cross_check", "cross_sheet_compare",
}

_VALID_WHEN_OPERATORS = {
    "equals", "notEquals", "blank", "notBlank", "contains",
    "greaterThan", "greaterThanOrEquals", "lessThan", "lessThanOrEquals",
    "exists", "notExists",
    "==", "!=", ">", ">=", "<", "<=",
}

_VALID_OUTPUT_AGGREGATES = {"distinct", "count", "sum", "group"}

_VALID_OUTPUT_MODES = {"individual", "single"}

_JSON_OUTPUT_TOP_KEYS = {"output_mode", "segregate_by", "key_dot_replacement", "data"}

_JSON_OUTPUT_DIRECTIVES = {"_each", "_join", "_ref", "_col", "_row", "_row_join"}


@dataclass
class ValidationIssue:
    """A single schema validation issue."""
    severity: str  # "error" | "warning"
    path: str      # YAML path where the issue was found
    message: str

    def __str__(self):
        return f"[{self.severity.upper()}] {self.path}: {self.message}"


@dataclass
class ValidationResult:
    """Result of schema validation."""
    valid: bool = True
    issues: list[ValidationIssue] = field(default_factory=list)
    yaml_syntax_ok: bool = True
    yaml_syntax_error: str = ""

    def add(self, severity: str, path: str, message: str):
        self.issues.append(ValidationIssue(severity=severity, path=path, message=message))
        if severity == "error":
            self.valid = False

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    def summary(self) -> str:
        lines = []
        if not self.yaml_syntax_ok:
            lines.append(f"YAML syntax error: {self.yaml_syntax_error}")
        lines.append(f"Valid: {self.valid} | Errors: {len(self.errors)} | Warnings: {len(self.warnings)}")
        for issue in self.issues:
            lines.append(str(issue))
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# YAML syntax check
# ---------------------------------------------------------------------------

def _parse_yaml(yaml_text: str) -> tuple[dict | None, str]:
    """Parse YAML text, return (parsed_dict, error_message)."""
    try:
        from ruamel.yaml import YAML
        parsed = YAML().load(StringIO(yaml_text))
        if parsed is None:
            return {}, ""
        return dict(parsed) if isinstance(parsed, dict) else {"_root": parsed}, ""
    except Exception as e:
        return None, str(e)[:500]


# ---------------------------------------------------------------------------
# Validation Rules validator
# ---------------------------------------------------------------------------

def validate_validation_rules(yaml_text: str) -> ValidationResult:
    """Validate a validation-rules.yaml against the schema.

    Checks:
    1. Valid YAML syntax
    2. Known top-level keys
    3. Sheet definitions with valid column types and keys
    4. Row rules with valid operators
    5. Workbook rules with valid types
    6. Outputs with valid aggregates
    """
    result = ValidationResult()

    parsed, err = _parse_yaml(yaml_text)
    if parsed is None:
        result.yaml_syntax_ok = False
        result.yaml_syntax_error = err
        result.valid = False
        return result

    _VALID_TOP_KEYS = {
        "version", "settings", "validateIndexSheets", "validateNodeIds",
        "skipValidationValues", "node_id_config", "workbook_rules",
        "validators", "sheets", "outputs", "report_output",
    }

    # Check top-level keys
    for key in parsed:
        if key not in _VALID_TOP_KEYS:
            result.add("warning", f"/{key}", f"Unknown top-level key '{key}'")

    # Check sheets
    sheets = parsed.get("sheets", {})
    if not sheets:
        result.add("warning", "/sheets", "No sheet definitions found")
    elif isinstance(sheets, dict):
        for sheet_name, sheet_def in sheets.items():
            if not isinstance(sheet_def, dict):
                result.add("error", f"/sheets/{sheet_name}", "Sheet definition must be a mapping")
                continue
            _validate_sheet(result, sheet_name, sheet_def)

    # Check workbook_rules
    wb_rules = parsed.get("workbook_rules", [])
    if isinstance(wb_rules, list):
        for i, rule in enumerate(wb_rules):
            if isinstance(rule, dict):
                rule_types = set(rule.keys()) & _VALID_WORKBOOK_RULE_TYPES
                unknown = set(rule.keys()) - _VALID_WORKBOOK_RULE_TYPES - {"message", "on_missing", "missing"}
                if not rule_types:
                    result.add("error", f"/workbook_rules[{i}]",
                               f"No recognized rule type. Keys: {list(rule.keys())}. "
                               f"Supported: {sorted(_VALID_WORKBOOK_RULE_TYPES)}")
                for uk in unknown:
                    if uk not in ("from", "to", "columns", "relation", "values",
                                  "pairs", "filter", "where", "sheet", "group",
                                  "column", "count", "source", "target",
                                  "partitionBy", "bidirectional", "on",
                                  "driver", "left", "right", "active_values",
                                  "value_pairs"):
                        result.add("warning", f"/workbook_rules[{i}]/{uk}",
                                   f"Potentially unknown key '{uk}' in workbook rule")

    # Check outputs
    outputs = parsed.get("outputs", {})
    if isinstance(outputs, dict):
        for param_name, out_def in outputs.items():
            if isinstance(out_def, dict):
                agg = out_def.get("aggregate", "")
                if agg and agg not in _VALID_OUTPUT_AGGREGATES:
                    result.add("error", f"/outputs/{param_name}/aggregate",
                               f"Unknown aggregate '{agg}'. Supported: {sorted(_VALID_OUTPUT_AGGREGATES)}")

    return result


def _validate_sheet(result: ValidationResult, sheet_name: str, sheet_def: dict):
    """Validate a single sheet definition."""
    valid_sheet_keys = {
        "required", "required_if_listed_in", "aliases", "settings", "columns", "rules",
    }
    for key in sheet_def:
        if key not in valid_sheet_keys:
            result.add("warning", f"/sheets/{sheet_name}/{key}",
                       f"Unknown sheet-level key '{key}'")

    # Columns
    columns = sheet_def.get("columns", {})
    if isinstance(columns, dict):
        for col_name, col_def in columns.items():
            if not isinstance(col_def, dict):
                continue
            _validate_column(result, f"/sheets/{sheet_name}/columns/{col_name}", col_def)

    # Row rules
    rules = sheet_def.get("rules", [])
    if isinstance(rules, list):
        for i, rule in enumerate(rules):
            if isinstance(rule, dict):
                _validate_row_rule(result, f"/sheets/{sheet_name}/rules[{i}]", rule)


def _validate_column(result: ValidationResult, path: str, col_def: dict):
    """Validate a column definition."""
    col_type = col_def.get("type", "string")
    if col_type not in _VALID_COLUMN_TYPES:
        result.add("error", f"{path}/type",
                   f"Unknown column type '{col_type}'. Supported: {sorted(_VALID_COLUMN_TYPES)}")

    for key in col_def:
        if key not in _VALID_COLUMN_KEYS:
            result.add("warning", f"{path}/{key}", f"Unknown column key '{key}'")

    # Validate allowedValuesWhen operators
    avw = col_def.get("allowedValuesWhen", [])
    if isinstance(avw, list):
        for j, entry in enumerate(avw):
            if isinstance(entry, dict) and "operator" in entry:
                op = entry["operator"]
                if op not in _VALID_WHEN_OPERATORS:
                    result.add("error", f"{path}/allowedValuesWhen[{j}]/operator",
                               f"Unknown operator '{op}'. Supported: {sorted(_VALID_WHEN_OPERATORS)}")


def _validate_row_rule(result: ValidationResult, path: str, rule: dict):
    """Validate a row rule."""
    rule_keys = set(rule.keys())
    recognized = rule_keys & _VALID_RULE_KEYS
    if not recognized:
        result.add("warning", path,
                   f"No recognized rule type. Keys: {list(rule.keys())}")

    # Check when-clause operators
    when = rule.get("when", {})
    if isinstance(when, dict):
        op = when.get("operator", "")
        if op and op not in _VALID_WHEN_OPERATORS:
            result.add("error", f"{path}/when/operator",
                       f"Unknown operator '{op}'. Supported: {sorted(_VALID_WHEN_OPERATORS)}")
        # Recursive all/any
        for compound in ("all", "any"):
            if compound in when and isinstance(when[compound], list):
                for k, sub in enumerate(when[compound]):
                    if isinstance(sub, dict) and "operator" in sub:
                        sub_op = sub["operator"]
                        if sub_op not in _VALID_WHEN_OPERATORS:
                            result.add("error", f"{path}/when/{compound}[{k}]/operator",
                                       f"Unknown operator '{sub_op}'")


# ---------------------------------------------------------------------------
# JSON Output validator
# ---------------------------------------------------------------------------

def validate_json_output(yaml_text: str) -> ValidationResult:
    """Validate a json-output.yaml against the schema.

    Checks:
    1. Valid YAML syntax
    2. Required top-level keys (output_mode, data)
    3. Valid output_mode value
    4. segregate_by structure (when output_mode=individual)
    5. data tree uses only known directives
    """
    result = ValidationResult()

    parsed, err = _parse_yaml(yaml_text)
    if parsed is None:
        result.yaml_syntax_ok = False
        result.yaml_syntax_error = err
        result.valid = False
        return result

    # Check top-level keys
    for key in parsed:
        if key not in _JSON_OUTPUT_TOP_KEYS:
            result.add("warning", f"/{key}", f"Unknown top-level key '{key}'")

    # output_mode
    mode = parsed.get("output_mode", "")
    if not mode:
        result.add("error", "/output_mode", "Missing required key 'output_mode'")
    elif mode not in _VALID_OUTPUT_MODES:
        result.add("error", "/output_mode",
                   f"Invalid output_mode '{mode}'. Supported: {sorted(_VALID_OUTPUT_MODES)}")

    # segregate_by required for individual
    if mode == "individual":
        seg = parsed.get("segregate_by", {})
        if not seg:
            result.add("error", "/segregate_by",
                       "segregate_by is required when output_mode=individual")
        elif isinstance(seg, dict):
            for rk in ("sheet", "column"):
                if rk not in seg:
                    result.add("error", f"/segregate_by/{rk}",
                               f"Missing required key '{rk}' in segregate_by")

    # data
    data = parsed.get("data", {})
    if not data:
        result.add("error", "/data", "Missing required key 'data'")
    elif isinstance(data, dict):
        _validate_data_tree(result, "/data", data)

    return result


def _validate_data_tree(result: ValidationResult, path: str, node: Any):
    """Recursively validate the data template tree."""
    if not isinstance(node, dict):
        return

    # Check for directive keys
    directives_found = set(node.keys()) & _JSON_OUTPUT_DIRECTIVES
    # _each, _join etc. are valid — just validate known patterns
    if "_each" in node:
        each_val = node["_each"]
        if not isinstance(each_val, str):
            result.add("error", f"{path}/_each", "_each must be a string expression")

    if "_join" in node:
        join_val = node["_join"]
        if not isinstance(join_val, str):
            result.add("error", f"{path}/_join", "_join must be a string expression")

    if "_ref" in node:
        ref_val = node["_ref"]
        if not isinstance(ref_val, str):
            result.add("error", f"{path}/_ref", "_ref must be a dotted path string")

    if "_row" in node:
        row_val = node["_row"]
        if not isinstance(row_val, (str, dict, list)):
            result.add("error", f"{path}/_row", "_row must be string, list, or mapping")

    # Recurse into child dicts
    for key, value in node.items():
        if key in _JSON_OUTPUT_DIRECTIVES or key in ("separator", "format", "exclude"):
            continue
        if isinstance(value, dict):
            _validate_data_tree(result, f"{path}/{key}", value)
        elif isinstance(value, list):
            # Lists of dicts (array templates)
            for i, item in enumerate(value):
                if isinstance(item, dict):
                    _validate_data_tree(result, f"{path}/{key}[{i}]", item)


# ---------------------------------------------------------------------------
# Requirements coverage check
# ---------------------------------------------------------------------------

def check_requirements_coverage(
    requirements: dict,
    validation_rules_yaml: str,
    json_output_yaml: str,
) -> list[str]:
    """Check that activity requirements are represented in generated YAML.

    Returns a list of unresolved requirement descriptions.
    """
    unresolved: list[str] = []

    cat1 = requirements.get("category_1_node_activity", {})
    cat2 = requirements.get("category_2_commands", {})
    doc_extracted = requirements.get("doc_extracted", {})

    # Check that CIQ sheets referenced in requirements appear in validation rules
    ciq_sheets = doc_extracted.get("ciq_sheets", {})
    if ciq_sheets and validation_rules_yaml:
        for sheet_name in ciq_sheets:
            if sheet_name.lower() not in validation_rules_yaml.lower():
                unresolved.append(
                    f"CIQ sheet '{sheet_name}' not found in validation-rules.yaml"
                )

    # Check that data_sheet is represented in json-output
    data_sheet = cat1.get("data_sheet", "")
    if data_sheet and json_output_yaml:
        if data_sheet.lower() not in json_output_yaml.lower():
            unresolved.append(
                f"Data sheet '{data_sheet}' not found in json-output.yaml"
            )

    # Check CIQ columns with params are represented
    commands = cat2.get("commands", [])
    ciq_params = set()
    for cmd in commands:
        if isinstance(cmd, dict):
            for p in cmd.get("ciq_params", []):
                ciq_params.add(p)

    for param in ciq_params:
        if param and validation_rules_yaml and param.lower() not in validation_rules_yaml.lower():
            unresolved.append(
                f"CIQ param column '{param}' (used by commands) not found in validation-rules.yaml"
            )

    return unresolved
