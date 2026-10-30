"""LLM prompt templates for CIQ validation rules and JSON output generation."""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Validation Rules Generation
# ---------------------------------------------------------------------------

VALIDATION_RULES_SYSTEM = """\
You are a CIQ validation rules expert. You generate YAML validation-rules files
that define per-sheet column types for CIQ Excel workbooks.

CRITICAL RULES:
- Output ONLY the "sheets:" section — nothing else (no workbook_rules, no outputs, no node_id_config, no report)
- Use ONLY "type: string" with "required: true/false" for most columns
- Use "type: enum" with "values: [...]" ONLY for columns where the sample data shows a SMALL CLOSED set (< 10 values)
- Do NOT use "allowedValues" on string columns — the values come from the CIQ, not from you
- Use EXACT column names from the CIQ — do NOT rename or invent columns
- Your output must be valid YAML — no markdown fencing, no explanations, just YAML
"""

VALIDATION_RULES_USER = """\
Generate a validation-rules.yaml for this CIQ workbook.

=== UC NAME ===
{uc_name}

=== CIQ STRUCTURE ===
{ciq_summary}

=== REQUIREMENTS ===
{requirements_summary}

=== SCHEMA REFERENCE ===
{schema_summary}

=== EXAMPLE OUTPUT FORMAT ===
sheets:
  IP:
    columns:
      ZONE:
        type: string
        required: true
      Node_Name:
        type: enum
        required: true
        values:
        - PGW
        - RDS
      NIAM_NAME1:
        type: string
        required: true
  Index:
    columns:
      CRGroup:
        type: string
        required: true

=== INSTRUCTIONS ===
1. Output ONLY the "sheets:" section with column definitions.
2. Use EXACT column names from the CIQ structure — do NOT invent columns.
3. Use "type: string" for most columns. Use "type: enum" with "values:" ONLY when
   sample data shows a small closed set (e.g. Node_Name with 2-3 values).
4. Do NOT generate workbook_rules, outputs, node_id_config, report, or any other section.
5. Do NOT use allowedValues on string columns — those are free-form data fields.
6. Mark columns as required: true where samples show consistent non-empty data.

Output ONLY valid YAML. No markdown, no code fences, no explanations.
"""

VALIDATION_RULES_REGENERATE = """\
The validation-rules.yaml below was tested and has failures. Fix the issues.

=== CURRENT VALIDATION RULES YAML ===
{current_yaml}

=== TEST FAILURES ===
{test_failures}

=== REVIEWER ANALYSIS ===
{reviewer_analysis}

=== CIQ STRUCTURE ===
{ciq_summary}

=== SCHEMA REFERENCE ===
{schema_summary}

=== INSTRUCTIONS ===
1. Fix ONLY the issues identified in the test failures and reviewer analysis.
2. Use EXACT column names from the CIQ structure — do NOT rename columns.
3. Keep all rules that passed unchanged.
4. Output the COMPLETE corrected YAML — not a diff, the full file.

Output ONLY valid YAML. No markdown, no code fences, no explanations.
"""

# ---------------------------------------------------------------------------
# JSON Output Generation
# ---------------------------------------------------------------------------

JSON_OUTPUT_SYSTEM = """\
You are a CIQ JSON output template expert. You generate YAML json-output files
that define how CIQ Excel data is extracted into structured JSON for the
ciq-processor JAR.

CRITICAL RULES:
- Output must be valid YAML — no markdown fencing, no explanations
- Use EXACT column names from the CIQ — "Sheet Name.Column Name" for cross-sheet refs
- Every _each block must be a YAML mapping (not a sequence mixed with a mapping)
- The _each value is a list of iteration values; child keys are at the same mapping level
"""

JSON_OUTPUT_USER = """\
Generate a json-output.yaml for this CIQ workbook.

=== UC NAME ===
{uc_name}

=== CIQ STRUCTURE ===
{ciq_summary}

=== REQUIREMENTS ===
{requirements_summary}

=== SCHEMA REFERENCE ===
{schema_summary}

=== CORRECT _each SYNTAX ===
IMPORTANT: _each creates an array by iterating. The template fields are SIBLING keys, not list items.

CORRECT (fields are sibling keys of _each):
  nodes:
    _each:
    - PGW
    - RDS
    name: $value
    niam: IP.NIAM_NAME1 WHERE IP.Node_Name = $value

WRONG (fields as list items under _each — NEVER do this):
  nodes:
    _each:
    - PGW
    - RDS
    - name: $value

=== EXAMPLE OUTPUT ===
output_mode: individual
segregate_by:
  sheet: Index
  column: ZONE
  as: $Zone
data:
  Zone: $Zone
  CRGroup: Index.CRGroup WHERE Index.ZONE = $Zone
  nodes:
    _each:
    - PGW
    - RDS
    name: $value
    niam_name: IP.NIAM_NAME1 WHERE IP.Node_Name = $value AND IP.ZONE = $Zone
  subscribers:
    _each: "Profile Modification WHERE Zone = $Zone"
    Test_IMSI: Profile Modification.Test IMSI
    Test_MSISDN: Profile Modification.Test MSISDN
  configData:
    tables:
      Profile Modification:
        Mandatory Cap: Profile Modification.Mandatory Cap

=== INSTRUCTIONS ===
1. Set output_mode to "individual" (per-zone/group output) or "single".
2. Configure segregate_by with grouping sheet/column from requirements.
3. Use EXACT "Sheet Name.Column Name" syntax for all column references.
4. _each value is a YAML list; template fields are SIBLING KEYS (not list items).
5. Use WHERE clauses to filter cross-sheet lookups.
6. Include configData.tables for CIQ columns needed during command execution.

Output ONLY valid YAML. No markdown, no code fences, no explanations.
"""

JSON_OUTPUT_REGENERATE = """\
The json-output.yaml below was tested and has failures. Fix the issues.

=== CURRENT JSON OUTPUT YAML ===
{current_yaml}

=== TEST FAILURES ===
{test_failures}

=== REVIEWER ANALYSIS ===
{reviewer_analysis}

=== CIQ STRUCTURE ===
{ciq_summary}

=== SCHEMA REFERENCE ===
{schema_summary}

=== INSTRUCTIONS ===
1. Fix ONLY the issues identified in the test failures and reviewer analysis.
2. Use EXACT column names from the CIQ structure — do NOT rename columns.
3. Verify all "Sheet.Column" references point to existing sheets and columns.
4. Keep all mappings that worked unchanged.
5. Output the COMPLETE corrected YAML — not a diff, the full file.

Output ONLY valid YAML. No markdown, no code fences, no explanations.
"""
