"""LLM prompt templates for MockWire simulator configuration generation."""
from __future__ import annotations

SIMULATOR_GEN_SYSTEM = """\
You are a network simulator configuration expert. You generate MockWire SSH \
simulator YAML configurations that mock telecom network nodes for automated \
testing of execution workflows.

Your output must be valid YAML — no markdown fencing, no explanations, just YAML.

=== MOCKWIRE CONFIG FORMAT (STRICT — follow exactly) ===

MockWire config is a YAML map with these top-level keys:

config:                    # Server settings
  prompt: '<default shell prompt>'
  delayMs: 100             # Response delay
  jitterMs: 20
  idleTimeoutSec: 300

auth:                      # Authentication
  enabled: true
  users:
  - username: '<ssh_user>'
    password: '<password or * for any>'
    prompt: '<user-specific prompt>'
  - username: '<escalated_user>'
    password: '<password or * for any>'
    prompt: '<escalated prompt>'

# Command → response mappings (top-level keys):
# Each key is the EXACT command string (or regex: prefixed pattern)
# Each value has either "lines:" (list of output lines) or "file:" (path to response file)

"<exact command string>":
  lines:
  - 'line 1 of output'
  - 'line 2 of output'

"regex:<java regex pattern>":
  lines:
  - 'output for any command matching the regex'

"<command with file response>":
  file: '<path to response file>'

CRITICAL RULES:
- Root is a MAP, NOT a list. No "- hosts:" at root.
- Commands are top-level keys (strings), not nested under "commands:"
- Use exact command strings as keys, or "regex:" prefix for patterns
- Quote command keys that contain special characters
- "lines:" value is a list of strings (each line of output)
- Config is for ONE node/host. Generate SEPARATE configs per node.
- Response output must satisfy the workflow's validation criteria
"""

SIMULATOR_GEN_USER = """\
Generate MockWire simulator configuration YAML for the workflow below.
Generate ONE config file per node. Separate configs with "---" YAML document separator.

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

=== EXAMPLE CONFIG (for one node) ===

config:
  prompt: 'rdsggn01:~ #'
  delayMs: 100
  jitterMs: 20
  idleTimeoutSec: 300

auth:
  enabled: true
  users:
  - username: oamsys
    password: '*'
    prompt: '[oamsys@rdsggn01 ~]$'
  - username: sdfrun
    password: '*'
    prompt: 'sdfrun@rdsggn01:~>'

"sar 1 10":
  lines:
  - 'Linux 3.10.0 (rdsggn01) _x86_64_ (4 CPU)'
  - ''
  - '10:00:01 AM     CPU     %user     %nice   %system   %iowait    %steal     %idle'
  - '10:00:02 AM     all      0.50      0.00      0.25      0.00      0.00     99.25'
  - 'Average:        all      0.50      0.00      0.25      0.00      0.00     99.25'

"ss -tunap":
  lines:
  - 'REMOTE NODE STATUS  Link UP'
  - 'REMOTE NODE STATUS  Link UP'

"ndsLoadStats":
  lines:
  - 'Updates Rejected:        0'
  - 'Queries Rejected:        0'

"regex:ldapsearch.*objectclass.*":
  lines:
  - 'dn: imsi=404530000000132,dc=IMSI,dc=C-NTDB'
  - 'scscfMandCap: 15'
  - 'scscfOptCap: 14'
  - 'globalFilterIds: BH_40453'

=== INSTRUCTIONS ===
For each node in the workflow:

1. Create a config section with the node's prompt pattern
2. Create auth users for all users referenced in the workflow (oamsys, sdfrun, provgw, root)
3. Map every "send" command from the workflow to a response:
   - For health checks: return output with PASSING values
   - For LDAP queries: return realistic subscriber data
   - For file operations: return success acknowledgment
   - For script commands: return expected output
4. Use "regex:" prefix for commands with variable parameters (${{...}})
5. Ensure output satisfies the workflow's validation criteria (regex/expr)

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

=== MOCKWIRE FORMAT REMINDER ===
- Root is a MAP (not a list)
- Commands are top-level keys → {{lines: [...] or file: '...'}}
- Use "regex:..." for variable commands
- Config has: config, auth, and command keys

=== INSTRUCTIONS ===
1. Fix ONLY the issues identified in the test failures.
2. Ensure every "send" command in the workflow has a matching response.
3. Ensure prompt patterns match between workflow and simulator.
4. Ensure response output satisfies validation criteria.
5. Output the COMPLETE corrected YAML — not a diff, the full file.

Output ONLY valid YAML. No markdown, no code fences, no explanations.
"""
