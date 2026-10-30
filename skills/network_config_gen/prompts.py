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
    on_failure: continue
    steps:
      - node: rds_node
        send: 'su - sdfrun'
        command_description: 'Switch to sdfrun user on RDS'
        validation:
          description: 'User switch to sdfrun'
          success:
            criteria:
              regex: '\\$'
            message: 'Switched to sdfrun user'
          failure:
            message: 'Failed to switch to sdfrun'
      - node: rds_node
        send: 'sar 1 10'
        command_description: 'sar on RDS [Pre-health check: CPU idle percentage]'
        on_failure: continue
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
      - node: rds_node
        send: 'ss'
        command_description: 'ss on RDS [Pre-health check: Remote node status]'
        on_failure: continue
        validation:
          description: 'All remote nodes should show Link UP'
          success:
            criteria:
              regex: 'Link UP'
            message: 'Remote nodes healthy'
          failure:
            message: 'Some remote nodes not UP'
      - node: rds_node
        send: 'ndsLoadStats'
        command_description: 'ndsLoadStats on RDS [Pre-health check: NDS load statistics]'
        on_failure: continue
        validation:
          description: 'Updates/Queries Rejected should be 0'
          success:
            criteria:
              regex: 'Updates Rejected:\\s*0.*Queries Rejected:\\s*0'
            message: 'NDS load healthy'
          failure:
            message: 'NDS load has rejected operations'

  activity_configuration:
    name: 'Main subscriber modification activity'
    steps:
      - node: rds_node
        send: "ldapsearch -x -h localhost -p 16611 -b \\"imsi=${{configData.tables.'Profile Modification'.'Test IMSI'}},dc=IMSI,dc=C-NTDB\\" objectclass=*"
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
    on_failure: continue
    steps:
      - node: rds_node
        send: 'sar 1 10'
        command_description: 'sar on RDS [Post-health check: CPU idle percentage]'
        on_failure: continue
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
2. Phase names MUST be exactly: preNodeHealthCheck, activity_configuration, postNodeHealthCheck
3. For each phase, list steps using "send" and "command_description".
4. Validation uses success/failure branches with criteria inside.
5. Register outputs with regex + name format.
6. Use ${{configData.tables...}} for CIQ values, ${{nodeGroups...}} for node properties.
7. Do NOT generate NIAM login commands or Ctrl+P+E commands — framework handles these via node config.
8. Do NOT generate "su" commands as inline wrappers (e.g. su - sdfrun -c "cmd").
   Instead: first step does "su - sdfrun" to switch user, subsequent steps run commands directly.
9. Quote all YAML values containing colons, brackets, or special characters.

=== PHASE BEHAVIOR RULES ===
- preNodeHealthCheck: any command failure is NON-BLOCKING — the step fails but execution continues
  to remaining health check commands. Phase status is "failed" if any step failed,
  but the next phase (activity_configuration) STILL executes.
- activity_configuration: command failures BLOCK — stop execution on first failure.
- postNodeHealthCheck: same as preNodeHealthCheck — non-blocking per command, but if any step
  fails the phase status is "failed" and no further phases execute.

=== LOCAL COMMANDS (Python scripts) ===
- The pipeline framework creates Python scripts (like create_xml.py) at runtime.
  Do NOT reference scripts that don't exist — the framework handles XML generation.
- add_xml_wrapper.py is NOT needed — the create_xml.py script handles wrapper/header/footer.
- For XML modification UCs: create_xml.py generates ONE XML per subscriber record using CIQ data.
  Multiple XMLs are created automatically when there are multiple records.
- Use node: LOCAL for local commands (Python scripts, file operations).

=== MOP HEADER VARIABLES ===
The MOP report needs these values. Set them in the workflow or they will be null:
  ${{env.NODE_TYPE}}          — e.g. "PGW_RDS" (from requirements)
  ${{env.ACTIVITY}}           — e.g. "1058_IFC_CAPABILITY_MODIFICATION"
  ${{env.CR_GROUP}}           — e.g. from ${{nodeGroups.crGroup}} or Index sheet
  ${{env.TARGET_NODES}}       — e.g. "PGW, RDS"

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
