"""LLM prompt templates for network activity requirement gathering."""

SYSTEM_PROMPT = """You are a Nokia telecom UC automation expert and cr-automation framework specialist.

Your job: Analyze input documents and identify ALL information needed to generate
production-quality workflow YAML, validation rules, and json-output templates.

DOMAIN EXPERTISE:
- Nokia node types: PGW (Packet Gateway), RDS (Remote Data Store), SBC (Session
  Border Controller), DSR (Diameter Signaling Router)
- NIAM login: SSH via M2M port, niamID-based node identification, failover across
  niamID-1/2/3 when multiple NIAM columns exist in CIQ
- LDAP provisioning: ldapsearch/ldapmodify on RDS nodes for subscriber management
- CIQ conventions: Index sheet (CRGroup, Zone/NodeGroup, Email), IP sheet
  (Node_Name, NIAM_NAME1/2/3), data sheets (UC-specific configuration)
- MOP patterns: pre-node-health-check, backup, activity, post-check, rollback

FRAMEWORK EXPERTISE:
- cli-automation YAML model: phases, steps, register (named capture groups),
  validation (criteria.expr), expect_reply, for_each loops
- ciq-processor json-output: tableKeys, _each directives, _row, WHERE clauses
- Node definitions: SSH config (host, port, user, auth, ne_id, kex_algorithms,
  connect_timeout_sec, elevate_to_root), prompt_regex
- Variable patterns: ${{nodeGroups}}, ${{node_row.PGW.niamID-1}}, ${{configData}}
"""

ANALYZE_PROMPT = """Analyze this UC's documents and identify what's available and what's missing.

IMPORTANT: Mark items as KNOWN (not gaps) if the information is visible in ANY of the provided data:
- CIQ column names and counts are KNOWN — do not list them as gaps
- NIAM column count is KNOWN from IP sheet headers
- LDAP credentials visible in SSH logs are KNOWN
- Shell prompts visible in SSH logs are KNOWN
- Command syntax in MOP is KNOWN
- XML structure in sample files is KNOWN
- Only report GENUINE gaps — information that truly cannot be found anywhere in the provided data

UC Name: {uc_name}

=== CIQ STRUCTURE ===
{ciq_summary}

=== MOP CONTENT ===
{mop_summary}

=== COMMAND OUTPUTS ===
{cmd_summary}

=== DEVELOPER NOTES ===
{developer_notes}

Respond with a JSON object covering all 3 categories.
For each category, only list items in "gaps" that are GENUINELY missing — not items visible in the data above.

{{
  "uc_type": "capability_modification | license_loading | software_upgrade | config_change | other",
  "activity_type": "per_subscriber | per_node | bulk",

  "category_1_node_activity": {{
    "nodes_detected": [{{"name": "...", "type": "PGW|RDS|SBC|DSR", "role": "..."}}],
    "login_approach": "niam | direct_ssh | unknown",
    "niam_columns_count": 0,
    "root_escalation_detected": true/false,
    "root_method": "su_root | ctrl_p_e | sudo | unknown",
    "gaps": ["list of missing items"]
  }},

  "category_2_commands": {{
    "commands_found": [{{"command": "ldapsearch", "node": "RDS", "purpose": "...", "params_from_ciq": ["col1", "col2"]}}],
    "ldap_detected": true/false,
    "ldap_details_known": true/false,
    "gaps": ["list of missing items"]
  }},

  "category_3_orchestration": {{
    "grouping_column": "Zone | NodeGroup | ...",
    "niam_failover_needed": true/false,
    "backup_mentioned": true/false,
    "ssh_details_known": true/false,
    "gaps": ["list of missing items"]
  }},

  "overall_readiness": "COMPLETE | PARTIAL | INSUFFICIENT"
}}"""

QUESTIONS_PROMPT = """Based on this analysis, generate targeted questions for the developer.

Analysis:
{analysis_json}

Developer already provided:
{developer_notes}

=== DATA ALREADY AVAILABLE (DO NOT ask about these) ===
{available_data}

=== CRITICAL RULES — WHAT NOT TO ASK ===
- Do NOT ask about anything visible in the CIQ columns (sheet names, column names, column count)
- Do NOT ask about NIAM column count — it's visible in the CIQ IP sheet headers
- Do NOT ask about which NIAM to try first — always try NIAM_NAME1, then 2, then 3 in order
- Do NOT ask about XML structure/template — sample XML files are provided as input artifacts
- Do NOT ask about LDAP credentials if they're visible in the SSH session logs
- Do NOT ask about shell prompts if they're visible in the SSH session logs
- Do NOT ask about command syntax if it's in the MOP document
- Do NOT re-ask questions that have been answered in "Developer already provided" above
- Only ask about things that are GENUINELY UNKNOWN and not inferable from the input documents

=== QUESTION STYLE ===
- ALWAYS provide a default_value for every question — extract it from the documents if possible
- Write questions for a LAYMAN USER, not a developer
- Use simple everyday language, avoid technical jargon
- Keep questions short (1 sentence) — put technical details in "context"
- Use "context" field to explain WHY this is needed
- Use "helper_text" field to show an example answer
- For select/boolean, make the options self-explanatory
- MAXIMUM 10-12 questions per round. Focus on the MOST IMPORTANT gaps only.

Respond with a JSON array:
[
  {{
    "id": "na_q1",
    "category": "node_activity | commands | orchestration",
    "question": "Simple, clear question in plain language",
    "importance": "critical | recommended | optional",
    "input_type": "text | boolean | select | password",
    "options": ["Option A — explanation", "Option B — explanation"],
    "default_value": "sensible default extracted from documents",
    "context": "Technical context: why this matters",
    "helper_text": "Example answer"
  }}
]

Use STABLE question IDs with category prefix: na_q1, na_q2 for node_activity; cmd_q1, cmd_q2 for commands; orch_q1, orch_q2 for orchestration.
Only include questions for GENUINE gaps that cannot be answered from the provided data."""

COMPILE_PROMPT = """Compile the final requirements package for YAML generation.

UC: {uc_name}
Analysis: {analysis_json}
Developer Notes: {developer_notes}
Developer Answers: {answers_json}

Produce a structured requirements package as JSON:

{{
  "category_1_node_activity": {{
    "nodes": [
      {{"name": "niam_pgw_1", "type": "remote", "role": "PGW",
        "ne_id": "${{node_row.PGW.niamID-1}}", "ssh": {{...}} }}
    ],
    "niam_failover": true/false,
    "root_escalation": {{"method": "su - root", "password": "...", "prompt": "Password:"}},
    "activity_type": "per_subscriber",
    "data_sheet": "Profile Modification"
  }},
  "category_2_commands": {{
    "commands": [
      — LIST EVERY SINGLE COMMAND from the MOP document. Include ALL of these:
      — Health check commands (sar, ss, ndsLoadStats, provgw status, etc.)
      — LDAP commands (ldapsearch, ldapmodify)
      — File operations (copy, ls, sftp, scp)
      — XML generation (python script, echo, cat)
      — Monitoring commands (check response file, wait for output)
      — Login/escalation commands (su, CTRL+P+E)
      — Any other command mentioned in MOP

      {{"cmd": "sar", "node": "RDS", "purpose": "Check CPU utilization during health check",
        "syntax": "sar 1 10", "target_user": "sdfrun",
        "validation_criteria": "%idle should be greater than 90%% in at least 8 samples",
        "ciq_params": [], "register": ["idlePercent"]}},
      {{"cmd": "ss", "node": "RDS", "purpose": "Verify network link status",
        "syntax": "ss -tunap", "target_user": "sdfrun",
        "validation_criteria": "All REMOTE NODE STATUS entries should show Link UP",
        "ciq_params": [], "register": []}},
      {{"cmd": "ldapsearch", "node": "RDS", "purpose": "Query subscriber profile before modification",
        "syntax": "ldapsearch -x -h localhost -p 16611 -D 'cn=pgwAdminUser' -w ... -b 'imsi=${{TEST_IMSI}},dc=IMSI,dc=C-NTDB' ...",
        "target_user": "sdfrun",
        "validation_criteria": "LDAP search should return exactly 1 subscriber entry",
        "ciq_params": ["Test IMSI"], "register": ["profileName", "mandatoryCap", "optionalCap"]}}
    ],
    "ldap": {{"host": "localhost", "port": 16611, "bind_dn": "...", "password": "..."}}
  }},

  CRITICAL: The commands list must include EVERY command from the MOP — not just 3-4.
  A typical MOP has 10-20 commands. Extract ALL of them with:
  - cmd: the actual command string
  - node: which node it runs on (RDS, PGW, etc.)
  - purpose: plain English description of what this command does
  - syntax: the full command syntax with placeholders
  - target_user: which OS user runs this (oamsys, sdfrun, provgw, root)
  - validation_criteria: plain English description of what success looks like
  - ciq_params: which CIQ columns provide input to this command
  - register: what values to capture from output
  "category_3_orchestration": {{
    "grouping": {{"column": "Zone", "label": "Zone"}},
    "niam_failover": true,
    "ssh": {{"kex_algorithms": "...", "connect_timeout": 30}},
    "paths": {{"local": "/mnt/shared_data/${{CHILD_REQ_ID}}/${{NODE}}"}},
    "phases": ["preNodeHealthCheck", "backup", "activity", "postNodeHealthCheck", "rollback"]
  }},
  "generation_hints": {{
    "step_3": "specific instructions for validation rules generation",
    "step_4": "specific instructions for json-output generation",
    "step_6": "specific instructions for workflow generation",
    "step_8": "specific instructions for simulator setup"
  }},
  "xml_templates": {{
    "description": "Mapping of CIQ columns to XML modification templates",
    "ciq_to_xml_mapping": [
      {{"ciq_column": "CIQ column name", "xml_tag": "XML element/attribute to set",
        "xml_sample_file": "which sample XML this comes from",
        "spml_objectclass": "Subscriber type in SPML"}}
    ],
    "base_xml_structure": "The SPML batchRequest template with placeholders"
  }}
}}

CRITICAL for xml_templates:
- Analyze the sample XML files provided and map each CIQ column to its XML tag
- Use ACTUAL SPML format from the samples — do NOT invent dummy XML
- Replace hardcoded values with ${{configData.COLUMN_NAME}} references
- The IMSI field should always use ${{configData.TEST_IMSI}} or similar CIQ reference
- Include the full SPML namespace and version from the samples

Be concrete — use actual values from the answers and sample files, not placeholders."""
