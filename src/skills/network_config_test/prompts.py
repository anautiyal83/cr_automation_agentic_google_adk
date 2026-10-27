"""LLM prompt templates for network configuration test case generation."""
from __future__ import annotations

TEST_CASE_GEN_SYSTEM = """\
You are a senior network automation QA engineer. You generate comprehensive \
test scenarios that verify execution workflow YAML configurations work correctly \
against simulated telecom network nodes.

You test both MOP (Method of Procedure) generation and actual command execution \
including validation criteria, variable substitution, and error handling.

Output valid JSON only — no markdown, no explanations.
"""

TEST_CASE_GEN_USER = """\
Generate professional test cases for the network execution workflow below.

=== UC NAME ===
{uc_name}

=== WORKFLOW YAML ===
{workflow_yaml}

=== SIMULATOR CONFIG ===
{simulator_summary}

=== REQUIREMENTS ===
Nodes: {nodes_summary}
Phases: {phases}
Commands: {commands_count} total

=== INSTRUCTIONS ===
Generate a JSON array of test cases. Each test case must have:

{{
  "id": "TC_XXX",
  "category": "<one of: mop_generation, command_execution, validation_criteria, variable_substitution, escalation, error_handling, phase_sequence, integration>",
  "severity": "<critical | major | minor>",
  "phase": "<workflow phase name this test targets>",
  "description": "<what this test verifies>",
  "test_type": "<mop_test | execution_test | both>",
  "target_node": "<node name>",
  "target_command": "<command being tested>",
  "scenario": "<detailed test scenario>",
  "expected_outcome": "<expected result>",
  "validation_points": ["<specific things to verify>"]
}}

Generate tests covering:

MOP Generation Tests:
- Each phase generates valid MOP scenarios
- Variable placeholders (${{...}}) appear correctly in MOP output
- Command ordering matches phase sequence
- Node references resolve correctly

Execution Tests:
- Each command executes on the correct node
- Validation criteria evaluate correctly against simulator output
- Register variables capture output for use by later commands
- Escalation (su, ctrl+p+e) works before privileged commands
- on_failure: continue allows non-critical commands to fail gracefully
- ${{configData}} and ${{nodeGroups}} placeholders resolve at runtime

Integration Tests:
- Full phase sequence completes in order
- Cross-phase register variable references work
- Pre/post health checks pass with simulator responses

Output ONLY a JSON array. No markdown, no code fences, no prose.
"""
