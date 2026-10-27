"""LLM prompt templates for network execution workflow YAML generation."""
from __future__ import annotations

WORKFLOW_GEN_SYSTEM = """\
You are a network automation workflow expert. You generate execution workflow \
YAML files that the cli-automation JAR uses to connect to network nodes, \
run commands, and validate outputs for telecom activities.

Your output must be valid YAML — no markdown fencing, no explanations, just YAML.
"""

WORKFLOW_GEN_USER = """\
Generate an execution workflow YAML for the network activity described below.

=== UC NAME ===
{uc_name}

=== REQUIREMENTS ===
Nodes:
{nodes_summary}

Commands (by phase):
{commands_summary}

Orchestration:
{orchestration_summary}

=== CIQ STRUCTURE ===
{ciq_summary}

=== RAW MOP DOCUMENT ===
{mop_text}

=== SSH SESSION LOGS (prompts and credentials) ===
{ssh_summary}

=== XML SAMPLES ===
{xml_summary}

=== DEVELOPER ANSWERS (AUTHORITATIVE — override all other sources) ===
{developer_answers}

=== SCHEMA REFERENCE ===
{schema_summary}

=== VARIABLE PATTERNS ===
Use these runtime variable patterns in the workflow YAML:

  ${{nodeGroups.<NodeName>.<field>}}    — node fields from JSON output (e.g. niamID-1, host, port)
  ${{configData.tables.<Sheet>.<Col>}}  — CIQ data values from JSON output tables
  ${{register.<varName>}}              — values registered from previous command outputs
  ${{env.<key>}}                        — runtime environment variables (e.g. ORDERNO)
  $<segregateVar>                      — segregate_by variable from JSON output (e.g. $unit)

Do NOT hardcode values that come from CIQ — use ${{configData.tables...}} placeholders.
Do NOT hardcode node IPs/hostnames — use ${{nodeGroups...}} placeholders.
Register command outputs that later commands need with register: [varName].

=== INSTRUCTIONS ===
1. Define nodeGroups with all network nodes from requirements. Use SSH configs and prompt patterns provided.
2. Create phases matching the orchestration phases: {phases}
3. For each phase, list commands in execution order from the MOP document.
4. Each command needs: cmd, node, description, and validation block.
5. Validation must have: criteria (with expr), success.message, failure.message, and description.
6. Use register for commands whose output is needed by later commands.
7. Use ${{configData.tables...}} for CIQ values and ${{nodeGroups...}} for node properties.
8. Handle privilege escalation (su, ctrl+p+e) with the escalation config from requirements.
9. Include on_failure: continue for non-critical commands where appropriate.

Output ONLY valid YAML. No markdown, no code fences, no explanations.
"""

WORKFLOW_REGENERATE = """\
The execution workflow YAML below was tested and has failures. Fix the issues.

=== CURRENT WORKFLOW YAML ===
{current_yaml}

=== TEST FAILURES ===
{test_failures}

=== REVIEWER ANALYSIS ===
{reviewer_analysis}

=== REQUIREMENTS (key fields) ===
Nodes: {nodes_summary}
Phases: {phases}

=== DEVELOPER ANSWERS (AUTHORITATIVE) ===
{developer_answers}

=== VARIABLE PATTERNS ===
  ${{nodeGroups.<NodeName>.<field>}}    — node fields from JSON output
  ${{configData.tables.<Sheet>.<Col>}}  — CIQ data values
  ${{register.<varName>}}              — registered command outputs
  ${{env.<key>}}                        — runtime environment variables

=== INSTRUCTIONS ===
1. Fix ONLY the issues identified in the test failures and reviewer analysis.
2. Preserve all ${{...}} variable placeholders — do NOT replace them with hardcoded values.
3. Keep all commands and phases that worked unchanged.
4. Ensure every validation block has: criteria.expr, success.message, failure.message, description.
5. Output the COMPLETE corrected YAML — not a diff, the full file.

Output ONLY valid YAML. No markdown, no code fences, no explanations.
"""
