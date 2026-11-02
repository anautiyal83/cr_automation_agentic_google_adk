"""LLM prompt templates for helper script generation."""
from __future__ import annotations

# ---------------------------------------------------------------------------
# create_xml.py — SPML XML builder from CIQ data
# ---------------------------------------------------------------------------

CREATE_XML_SYSTEM = """\
You are a Python developer. Generate a standalone Python script that creates
SPML XML modification files from command-line arguments (CIQ data values).

RULES:
- Use argparse for CLI args
- Use xml.etree.ElementTree to build XML
- The script must produce ONE XML file per invocation
- The XML structure must match the sample request XMLs provided
- Use the EXACT XML namespaces, element names, and structure from the samples
- Args that equal "UNCHANGED" should be SKIPPED (omit that modification block)
- Write to the --output_file path
- Include the SPML batchRequest wrapper with all namespaces
- Print "XML created: <filename>" on success
- Exit 0 on success, 1 on error
- Output ONLY Python code, no explanations
"""

CREATE_XML_USER = """\
Generate {script_name} that builds SPML modification XMLs.

=== COMMAND LINE (how the workflow calls this script) ===
{cmd_line}

=== SAMPLE REQUEST XMLs (use these as templates for XML structure) ===
{request_xmls}

=== SAMPLE RESPONSE XML (for reference — what success looks like) ===
{response_xml}

=== CIQ STRUCTURE (column names and sample values) ===
{ciq_summary}

=== DEVELOPER ANSWERS ===
{answers}

=== INSTRUCTIONS ===
1. Parse ALL args from the command line shown above using argparse.
2. For EACH modification type in the sample XMLs, create a <modification> block.
3. If an arg value is "UNCHANGED", "unchanged", empty, or None — SKIP that modification entirely.
4. The --output_file arg specifies where to write the XML.
5. The IMSI comes from --imsi arg and goes into <identifier alias="IMSI">.
6. Combine ALL non-UNCHANGED modifications into a SINGLE batchRequest XML.
7. Match the EXACT namespace URIs, element names, and nesting from the samples.

Output ONLY the Python script. No markdown, no explanations.
"""

# ---------------------------------------------------------------------------
# parse_response.py — SPML response parser
# ---------------------------------------------------------------------------

PARSE_RESPONSE_SYSTEM = """\
You are a Python developer. Generate a standalone Python script that parses
an SPML batchResponse XML file and reports success or failure.

RULES:
- Use argparse for --response_file arg
- Use xml.etree.ElementTree to parse
- Check batchResponse result="success" AND each response result="success"
- Print clear SUCCESS or FAILURE message with details
- Exit 0 on success, 1 on failure, 2 on parse error
- Output ONLY Python code, no explanations
"""

PARSE_RESPONSE_USER = """\
Generate {script_name} that parses SPML response XML.

=== COMMAND LINE ===
{cmd_line}

=== SAMPLE RESPONSE XML ===
{response_xml}

=== INSTRUCTIONS ===
1. Parse the XML file at --response_file path.
2. Check the root batchResponse element's result attribute.
3. Check each child response element's result attribute.
4. Print "SUCCESS: All modifications applied successfully" or
   "FAILURE: <details about which modification failed>"
5. Handle missing file, malformed XML gracefully.

Output ONLY the Python script. No markdown, no explanations.
"""

# ---------------------------------------------------------------------------
# create_html_report.py — pre/post comparison report
# ---------------------------------------------------------------------------

HTML_REPORT_SYSTEM = """\
You are a Python developer. Generate a standalone Python script that creates
an HTML comparison report between pre-activity and post-activity command outputs.

RULES:
- Use argparse for --pre_data, --post_data, --ciq_data, --output_file args
- Generate a clean HTML report with a comparison table
- Highlight differences between pre and post values
- Show CIQ expected values alongside actual values
- Color: green for matching, red for mismatching, grey for UNCHANGED
- Exit 0 on success, 1 on error
- Output ONLY Python code, no explanations
"""

HTML_REPORT_USER = """\
Generate {script_name} that creates a pre/post comparison HTML report.

=== COMMAND LINE ===
{cmd_line}

=== CIQ STRUCTURE ===
{ciq_summary}

=== INSTRUCTIONS ===
1. --pre_data and --post_data are LDAP output strings (key: value format).
2. --ciq_data is JSON string with CIQ column values for this subscriber.
3. Parse both LDAP outputs into key-value dicts.
4. Build an HTML table: Field | CIQ Expected | Pre-Activity | Post-Activity | Status
5. Status: MATCH (green) if post matches CIQ, CHANGED (blue) if different from pre,
   UNCHANGED (grey) if CIQ value is "UNCHANGED", MISMATCH (red) if post != CIQ expected.
6. Write to --output_file.

Output ONLY the Python script. No markdown, no explanations.
"""

# ---------------------------------------------------------------------------
# Generic fallback
# ---------------------------------------------------------------------------

GENERIC_SCRIPT_SYSTEM = """\
You are a Python developer. Generate a standalone Python script based on the
description provided. Use argparse for arguments, handle errors gracefully,
and exit 0 on success, 1 on error. Output ONLY Python code.
"""

GENERIC_SCRIPT_USER = """\
Generate {script_name}.

=== COMMAND LINE ===
{cmd_line}

=== CIQ STRUCTURE ===
{ciq_summary}

=== WORKFLOW CONTEXT ===
{workflow_context}

Output ONLY the Python script. No markdown, no explanations.
"""
