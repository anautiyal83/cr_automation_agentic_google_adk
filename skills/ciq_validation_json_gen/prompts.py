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
IMPORTANT: Use "DISTINCT Sheet.Column AS $var" for iterating unique values from a CIQ sheet.
Template fields are SIBLING keys of _each, NOT list items.

CORRECT — iterate distinct node names from CIQ:
  nodes:
    _each: "DISTINCT IP.Node_Name WHERE IP.ZONE = $Zone AS $node"
    name: $node
    niam: "IP.NIAM_NAME1 WHERE IP.Node_Name = $node AND IP.ZONE = $Zone"

CORRECT — iterate rows from a sheet with filter:
  subscribers:
    _each: "Profile Modification WHERE Zone = $Zone"
    Test_IMSI: Profile Modification.Test IMSI

WRONG — static list _each does NOT support WHERE lookups on siblings:
  nodes:
    _each:
    - PGW
    - RDS
    name: $value

WRONG — list items mixed with mapping keys:
  nodes:
    _each:
    - PGW
    - name: $value

=== TARGET JSON STRUCTURE (the JSON the ciq-processor must produce) ===
The MOP generator and execution engine expect this EXACT structure:
{{
  "nodeGroups": [{{
    "nodeGroup": "<Zone>",
    "crGroup": "<CRGroup>",
    "email": "<email>",
    "nodes": [{{
      "node": "<Zone>",
      "<NodeType1>": {{ "niamID-1": "...", "niamID-2": "...", "niamID-3": "..." }},
      "<NodeType2>": {{ "niamID-1": "...", "niamID-2": "...", "niamID-3": "..." }}
    }}],
    "configSequences": [{{
      "configSeq": "Step1",
      "records": [{{
        "data": {{ ... all CIQ columns per subscriber row ... }}
      }}]
    }}]
  }}]
}}

=== EXAMPLE json-output.yaml (produces the target structure) ===
output_mode: individual
segregate_by:
  sheet: Index
  column: ZONE
  as: $Zone
data:
  nodeGroups:
    _each: "DISTINCT Index.ZONE WHERE Index.ZONE = $Zone AS $zoneNode"
    nodeGroup: $zoneNode
    crGroup: "Index.CRGroup WHERE Index.ZONE = $zoneNode"
    email: "Index.Email WHERE Index.ZONE = $zoneNode"
    nodes:
      _each: "DISTINCT Index.ZONE WHERE Index.ZONE = $zoneNode AS $n"
      node: $n
      PGW:
        niamID-1: "IP.NIAM_NAME1 WHERE IP.Node_Name = PGW AND IP.ZONE = $zoneNode"
        niamID-2: "IP.NIAM_NAME2 WHERE IP.Node_Name = PGW AND IP.ZONE = $zoneNode"
        niamID-3: "IP.NIAM_NAME3 WHERE IP.Node_Name = PGW AND IP.ZONE = $zoneNode"
      RDS:
        niamID-1: "IP.NIAM_NAME1 WHERE IP.Node_Name = RDS AND IP.ZONE = $zoneNode"
        niamID-2: "IP.NIAM_NAME2 WHERE IP.Node_Name = RDS AND IP.ZONE = $zoneNode"
        niamID-3: "IP.NIAM_NAME3 WHERE IP.Node_Name = RDS AND IP.ZONE = $zoneNode"
    configSequences:
      _each: "DISTINCT Index.ZONE WHERE Index.ZONE = $zoneNode AS $seq"
      configSeq: Step1
      records:
        _each: "Profile Modification WHERE Zone = $zoneNode"
        data:
          Zone: Zone
          Test IMSI: Test IMSI
          Test MSISDN: Test MSISDN
          Mandatory Cap: Mandatory Cap
          Optional Cap: Optional Cap
          IFC: IFC
          VNI: VNI
          refinmocName: refinmocName
          refinmtcName: refinmtcName
          APN: APN
          Packet Data Network Type: Packet Data Network Type

=== INSTRUCTIONS ===
1. Your json-output.yaml must produce JSON matching the TARGET STRUCTURE above.
2. Use output_mode: individual with segregate_by on the grouping column (usually Index.ZONE).
3. Use "DISTINCT Sheet.Column AS $var" for iterating unique values.
4. Use "Sheet WHERE Col = $var" for row iteration with filter.
5. Inside a row iteration (_each: Sheet), use plain column names (no Sheet. prefix).
6. Map ALL CIQ data columns into records[].data — the execution engine needs them.
7. Node niamID-1/2/3 come from the IP sheet filtered by Node_Name and ZONE.
8. Use EXACT column names from the CIQ structure.

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
