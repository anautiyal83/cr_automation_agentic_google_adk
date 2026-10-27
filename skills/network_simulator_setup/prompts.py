"""LLM prompt templates for MockWire simulator configuration generation."""
from __future__ import annotations

SIMULATOR_GEN_SYSTEM = """\
You are a network simulator configuration expert. You generate MockWire SSH \
simulator YAML configurations that mock telecom network nodes for automated \
testing of execution workflows.

Each simulated node needs SSH server config, command-response mappings, \
prompt patterns, and file transfer stubs that match the workflow expectations.

Your output must be valid YAML — no markdown fencing, no explanations, just YAML.
"""

SIMULATOR_GEN_USER = """\
Generate MockWire simulator configuration YAML for the workflow below.

=== UC NAME ===
{uc_name}

=== WORKFLOW YAML (nodes and commands to simulate) ===
{workflow_summary}

=== NODE DEFINITIONS ===
{nodes_summary}

=== REQUIREMENTS ===
{requirements_summary}

=== SIMULATOR SCHEMA REFERENCE ===
{schema_summary}

=== INSTRUCTIONS ===
For each node referenced in the workflow:

1. Define an SSH server entry:
   - host: localhost (all nodes simulated locally)
   - port: unique per node (starting at 2222)
   - users: matching the workflow's SSH user + escalation users
   - auth: accept any password (simulator mode)
   - prompt: matching the workflow's prompt_regex patterns

2. Define command-response mappings for every command in the workflow:
   - Match the exact command string or a regex pattern
   - Return realistic output that satisfies the workflow's validation criteria
   - For health checks (sar, ss, ndsLoadStats): return output with passing values
   - For LDAP commands: return realistic search results
   - For file operations (sftp, scp): acknowledge success

3. Define escalation responses:
   - su/sudo: return the expected root/user prompt
   - ctrl+p+e: return privilege escalation prompt

4. Define file transfer stubs:
   - SFTP/SCP directories that the workflow expects
   - Response files for XML/SPML operations

Output ONLY valid YAML. No markdown, no code fences, no explanations.
"""

SIMULATOR_REGENERATE = """\
The simulator configuration below caused test failures. Fix the issues.

=== CURRENT SIMULATOR CONFIG YAML ===
{current_yaml}

=== TEST FAILURES ===
{test_failures}

=== REVIEWER ANALYSIS ===
{reviewer_analysis}

=== WORKFLOW NODES AND COMMANDS ===
{workflow_summary}

=== INSTRUCTIONS ===
1. Fix ONLY the issues identified in the test failures and reviewer analysis.
2. Ensure every command in the workflow has a matching simulator response.
3. Ensure prompt patterns match between workflow and simulator.
4. Ensure SSH ports and users are consistent.
5. Output the COMPLETE corrected YAML — not a diff, the full file.

Output ONLY valid YAML. No markdown, no code fences, no explanations.
"""
