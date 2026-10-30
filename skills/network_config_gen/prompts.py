"""LLM prompt templates for network execution workflow YAML generation."""
from __future__ import annotations

WORKFLOW_GEN_SYSTEM = """\
You are a network automation workflow expert generating execution YAML for the \
cli-automation framework. You produce YAML that the framework JAR uses to SSH \
into network nodes, run commands, and validate outputs.

Your output must be valid YAML — no markdown fencing, no explanations, just YAML.

=== FRAMEWORK YAML MODEL (STRICT — follow exactly) ===

ROOT KEYS:
  nodes:   Map<String, NodeDefinition>   — named connection endpoints
  phases:  Map<String, PhaseBlock>       — ordered map of named phases with steps

NODE DEFINITION (nodes.<name>):
  type: remote              # "remote" for SSH nodes, "local" for local commands
  plugin: ssh               # "ssh" for remote, "local" for local
  prompt_regex: '<regex>'   # regex matching the shell prompt after login
  ssh:
    host: '<host or ${var}>'
    port: '22'
    user: '<username>'
    auth:
      password: '${SECRET.password}'
    ne_id: '<NIAM ID or ${var}>'          # Network element ID for NIAM gateway login
    elevate_to_root: true/false           # auto Ctrl+P+E after login
    kex_algorithms: '+diffie-hellman-group14-sha1'
    host_key_algorithms: '+ssh-rsa'
    connect_timeout_sec: '30'

PHASE DEFINITION (phases.<phaseName>):
  name: 'Human-readable phase description'
  steps:
    - node: <nodeName>
      send: '<shell command>'
      command_description: 'What this command does'
      register:
        - regex: '(?P<varName>pattern)'
          name: varName
      validation:
        description: 'What success looks like'
        success:
          criteria:
            regex: '<pattern>'        # or expr: '<expression>'
          message: 'Success message'
        failure:
          message: 'Failure message'
      on_failure: continue            # optional: continue, stop, warning
      timeout_sec: 60                 # optional

CRITICAL RULES:
- Use "send" NOT "cmd" or "command" for the shell command
- Use "command_description" NOT "description" for step description
- phases is a MAP (dict keyed by phase name), NOT a list
- nodes is a MAP (dict keyed by node name), NOT nodeGroups
- validation has success/failure branches with criteria inside, NOT top-level criteria
- register uses {regex: '...', name: '...'}, NOT just a list of strings
- For NIAM gateway access: set ssh.ne_id on the node (framework handles NIAM login)
- For root escalation: set ssh.elevate_to_root: true (framework handles Ctrl+P+E)
- Do NOT generate explicit NIAM login or Ctrl+P+E commands — framework handles these
- Do NOT generate "su - sdfrun" as a separate step — use expect_reply on the next command or set user context
- Quote ALL values containing colons, brackets, or special chars in YAML
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

  ${{nodeGroups.<NodeName>.<field>}}    — node fields from JSON output (e.g. niamID-1, host)
  ${{configData.tables.<Sheet>.<Col>}}  — CIQ data values from JSON output tables
  ${{register.<varName>}}              — values registered from previous command outputs
  ${{env.<key>}}                        — runtime environment variables (e.g. ORDERNO)
  $<segregateVar>                      — segregate_by variable from JSON output

=== EXAMPLE WORKFLOW STRUCTURE ===

nodes:
  rds_node:
    type: remote
    plugin: ssh
    prompt_regex: '(?m)\\$\\s*$'
    ssh:
      host: '${{nodeGroups.RDS.niamID-1}}'
      port: '22'
      user: 'oamsys'
      auth:
        password: '${{SECRET.m2m_password}}'
      ne_id: '${{nodeGroups.RDS.niamID-1}}'
      elevate_to_root: true
      kex_algorithms: '+diffie-hellman-group14-sha1'
      host_key_algorithms: '+ssh-rsa'
      connect_timeout_sec: '30'

  pgw_node:
    type: remote
    plugin: ssh
    prompt_regex: '(?m)\\$\\s*$'
    ssh:
      host: '${{nodeGroups.PGW.niamID-1}}'
      port: '22'
      user: 'oamsys'
      auth:
        password: '${{SECRET.m2m_password}}'
      ne_id: '${{nodeGroups.PGW.niamID-1}}'
      elevate_to_root: true
      kex_algorithms: '+diffie-hellman-group14-sha1'
      host_key_algorithms: '+ssh-rsa'
      connect_timeout_sec: '30'

  LOCAL:
    type: local
    plugin: local

phases:
  preNodeHealthCheck:
    name: 'Pre-activity health checks'
    steps:
      - node: rds_node
        send: 'sar 1 10'
        command_description: 'Check CPU utilization on RDS'
        register:
          - regex: 'Average:.*(?P<idle>\\d+\\.\\d+)\\s*$'
            name: rds_cpu_idle
        validation:
          description: 'CPU idle should be >= 90%'
          success:
            criteria:
              regex: 'Average:.*\\s9[0-9]\\.\\d+|Average:.*\\s100\\.00'
            message: 'CPU idle is healthy on RDS'
          failure:
            message: 'CPU idle is below 90% on RDS'

  activity:
    name: 'Main subscriber modification activity'
    steps:
      - node: rds_node
        send: "ldapsearch -x -h localhost -p 16611 -D 'cn=pgwAdminUser' -w 8123Admi -b \\"imsi=${{configData.tables.Profile Modification.Test IMSI}},dc=IMSI,dc=C-NTDB\\" -a always -s sub objectclass=*"
        command_description: 'Query subscriber profile before modification'
        register:
          - regex: '(?P<profile_data>[\\s\\S]+)'
            name: pre_ldap_result
        validation:
          description: 'LDAP search should return subscriber data'
          success:
            criteria:
              regex: 'dn: imsi='
            message: 'Subscriber profile retrieved'
          failure:
            message: 'LDAP search returned no data'

  postNodeHealthCheck:
    name: 'Post-activity health checks'
    steps:
      - node: rds_node
        send: 'sar 1 10'
        command_description: 'Check CPU utilization on RDS (post-activity)'
        validation:
          description: 'CPU idle should be >= 90%'
          success:
            criteria:
              regex: 'Average:.*\\s9[0-9]\\.\\d+|Average:.*\\s100\\.00'
            message: 'CPU idle is healthy on RDS (post-activity)'
          failure:
            message: 'CPU idle is below 90% on RDS (post-activity)'

=== INSTRUCTIONS ===
1. Define nodes for each network element. Use ne_id for NIAM access, elevate_to_root for Ctrl+P+E.
2. Create phases as a MAP: {phases}
3. For each phase, list steps using "send" and "command_description".
4. Validation uses success/failure branches with criteria inside.
5. Register outputs with regex + name format.
6. Use ${{configData.tables...}} for CIQ values, ${{nodeGroups...}} for node properties.
7. Do NOT generate NIAM login commands or Ctrl+P+E commands — framework handles these via node config.
8. Do NOT generate "su" commands — configure the target user on the node or use expect_reply.
9. Quote all YAML values containing colons, brackets, or special characters.

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

=== FRAMEWORK RULES (STRICT) ===
- Use "send" for commands, "command_description" for descriptions
- phases is a MAP (dict), NOT a list
- nodes is a MAP (dict), NOT nodeGroups
- validation has success/failure branches with criteria inside
- register uses {{regex: '...', name: '...'}}
- No NIAM login commands — use ne_id on nodes
- No Ctrl+P+E commands — use elevate_to_root: true on nodes

=== INSTRUCTIONS ===
1. Fix ONLY the issues identified in the test failures and reviewer analysis.
2. Preserve all ${{...}} variable placeholders — do NOT replace them with hardcoded values.
3. Keep all commands and phases that worked unchanged.
4. Output the COMPLETE corrected YAML — not a diff, the full file.

Output ONLY valid YAML. No markdown, no code fences, no explanations.
"""
