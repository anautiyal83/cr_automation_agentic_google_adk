"""LLM prompt templates for CIQ validation rules and JSON output generation."""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Validation Rules Generation
# ---------------------------------------------------------------------------

VALIDATION_RULES_SYSTEM = """\
You are a CIQ validation rules expert. You generate YAML validation-rules files
that conform to a specific schema and accurately validate CIQ Excel workbooks
for telecom network activities.

Your output must be valid YAML — no markdown fencing, no explanations, just YAML.
"""

VALIDATION_RULES_USER = """\
Generate a validation-rules.yaml for the CIQ workbook described below.

=== UC NAME ===
{uc_name}

=== CIQ STRUCTURE ===
{ciq_summary}

=== REQUIREMENTS ===
{requirements_summary}

=== SCHEMA REFERENCE ===
{schema_summary}

=== INSTRUCTIONS ===
1. Define validation rules for EVERY sheet that has data columns.
2. Use EXACT column names from the CIQ structure above — do NOT rename or invent columns.
3. Set appropriate types (string, enum, integer, decimal, boolean, ip, etc.) based on sample values.
4. Mark columns as required where CIQ samples show consistent data.
5. Use allowedValues for columns with small, discrete value sets visible in samples.
6. Add cross-sheet workbook_rules where relationships exist (e.g., Index → data sheets).
7. Configure node_id_config if a Node_ID or Node_Details sheet exists.
8. Include outputs section for aggregates needed by downstream processing.
9. Set report formats to [JSON, HTML, MSEXCEL].

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
that define how CIQ Excel data is extracted into structured JSON for telecom
network activity automation.

Your output must be valid YAML — no markdown fencing, no explanations, just YAML.
"""

JSON_OUTPUT_USER = """\
Generate a json-output.yaml for the CIQ workbook described below.

=== UC NAME ===
{uc_name}

=== CIQ STRUCTURE ===
{ciq_summary}

=== REQUIREMENTS ===
{requirements_summary}

=== SCHEMA REFERENCE ===
{schema_summary}

=== INSTRUCTIONS ===
1. Set output_mode to "individual" if CIQ has per-node/per-subscriber rows, or "single" for one output.
2. Configure segregate_by with the appropriate sheet and grouping column from requirements.
3. Use EXACT column names from the CIQ structure — "SheetName.ColumnName" for cross-sheet refs.
4. Use _each for array iterations with DISTINCT when iterating unique values.
5. Use _join for comma-separated aggregations.
6. Map CIQ columns to the JSON structure needed by the network activity commands.
7. Reference the requirements for node groups, commands, and XML template mappings.
8. Include configData tables for lookup data needed during execution.

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
