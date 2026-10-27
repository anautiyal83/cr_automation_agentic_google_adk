"""LLM prompt templates for test case generation."""
from __future__ import annotations

TEST_CASE_GEN_SYSTEM = """\
You are a senior QA engineer specializing in CIQ (Customer Installation Questionnaire) \
validation for telecom network configurations.

You generate comprehensive test scenarios that verify:
1. Validation rules correctly accept valid CIQ data and reject invalid data.
2. JSON output templates correctly extract and map CIQ data to structured JSON.
3. Cross-sheet references and workbook-level rules work as expected.
4. Edge cases are handled (blank rows, special characters, boundary values).

Output valid JSON only — no markdown, no explanations.
"""

TEST_CASE_GEN_USER = """\
Generate professional test cases for the CIQ validation and JSON output configuration below.

=== UC NAME ===
{uc_name}

=== CIQ STRUCTURE ===
{ciq_summary}

=== VALIDATION RULES YAML ===
{validation_rules_yaml}

=== JSON OUTPUT YAML ===
{json_output_yaml}

=== INSTRUCTIONS ===
Generate a JSON array of test cases. Each test case must have:

{{
  "id": "TC_XXX",
  "category": "<one of: column_validation, cross_sheet, json_mapping, edge_case, negative, integration>",
  "severity": "<critical | major | minor>",
  "description": "<what this test verifies>",
  "preconditions": "<what must be true before test runs>",
  "test_type": "<positive | negative>",
  "target_sheet": "<sheet name being tested or 'workbook' for cross-sheet>",
  "target_columns": ["<column names involved>"],
  "scenario": "<detailed test scenario description>",
  "expected_outcome": "<PASS or FAIL with expected behavior>",
  "validation_points": ["<specific things to verify in the result>"]
}}

Generate at least:
- 3-5 positive tests per sheet (valid data accepted)
- 2-3 negative tests per sheet (invalid data caught)
- 2-3 cross-sheet reference tests
- 2-3 JSON output mapping verification tests
- 1-2 edge case tests (empty values, special characters, max length)

Prioritize critical business rules first, then edge cases.

Output ONLY a JSON array. No markdown, no code fences, no prose.
"""
