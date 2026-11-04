"""LLM prompt templates for helper script generation."""
from __future__ import annotations

# ---------------------------------------------------------------------------
# create_xml.py — SPML XML builder from CIQ data
# ---------------------------------------------------------------------------

CREATE_XML_SYSTEM = """\
You are a Python developer. Generate a standalone Python script that creates
SPML XML modification files from command-line arguments (CIQ data values).

RULES:
- Use argparse with EXACTLY the flags listed in the CLI CONTRACT section
- Build the XML as TEXT from the sample request XMLs: copy the sample's wrapper and blocks
  verbatim into string templates and substitute only the values (escape them with
  xml.sax.saxutils.escape). Do NOT build or re-parse it with ElementTree/minidom: namespace
  re-prefixing, duplicate xmlns attributes ("ExpatError: duplicate attribute") and
  tostring() encoding arguments broke every such script (G-8)
- The script must produce ONE XML file per invocation
- The XML structure must match the sample request XMLs provided
- Use the EXACT XML namespaces, element names, and structure from the samples
- Args that equal "UNCHANGED" should be SKIPPED (omit that modification block)
- Write to the --output_file path
- Include the SPML batchRequest wrapper with all namespaces
- On success print a message matching the OUTPUT CONTRACT (if given)
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
1. Define every flag from the CLI CONTRACT with argparse, spelled exactly as listed.
2. For EACH modification type in the sample XMLs, create a <modification> block.
3. If an arg value is "UNCHANGED", "unchanged", empty, or None — SKIP that modification entirely.
4. Write the XML to the output-file flag of the contract.
5. Map each flag to the XML element/attribute the sample XMLs use for that value
   (subscriber identifiers, attribute values, …) — derive the mapping from the samples.
6. Combine ALL non-UNCHANGED modifications into a SINGLE request XML.
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
- Use argparse with EXACTLY the flags listed in the CLI CONTRACT section
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
1. Parse the XML file given by the response-file flag of the contract.
2. Check the root batchResponse element's result attribute.
3. Check each child response element's result attribute.
4. Print a clear success message (it MUST match the OUTPUT CONTRACT, if given) or a
   failure message with details about which modification failed
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
- Use argparse with EXACTLY the flags listed in the CLI CONTRACT section
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
1. The contract's pre/post flags carry command outputs captured before and after the
   activity (typically "key: value" lines); the CIQ flag(s) carry the expected values.
2. Parse pre and post outputs into key-value dicts.
3. Build an HTML table: Field | CIQ Expected | Pre-Activity | Post-Activity | Status
4. Status: MATCH (green) if post matches CIQ, CHANGED (blue) if different from pre,
   UNCHANGED (grey) if CIQ value is "UNCHANGED", MISMATCH (red) if post != CIQ expected.
5. Write to the output-file flag of the contract.

Output ONLY the Python script. No markdown, no explanations.
"""

# ---------------------------------------------------------------------------
# Generic fallback
# ---------------------------------------------------------------------------

GENERIC_SCRIPT_SYSTEM = """\
You are a Python developer. Generate a standalone Python script based on the
description provided. Use argparse with EXACTLY the flags listed in the CLI CONTRACT
section, handle errors gracefully, and exit 0 on success, 1 on error. Output ONLY
Python code.
"""

# ---------------------------------------------------------------------------
# CLI contract — appended to every script prompt (the workflow is the source of truth)
# ---------------------------------------------------------------------------

CLI_CONTRACT_SECTION = """

=== CLI CONTRACT (MUST MATCH EXACTLY) ===
The workflow runs this script as:
{invocations}

Define EVERY one of these flags with argparse, spelled EXACTLY like this (no renaming,
no snake_case/camelCase conversion, no extra required flags):
{flags}
Use parser.parse_known_args() so an additional flag added later never breaks a run.
"""

OUTPUT_CONTRACT_SECTION = """
=== OUTPUT CONTRACT (MUST MATCH) ===
The workflow validates this script's output with the regex(es) below. On success, print a
message that matches every one of them (use the literal text the regex expects):
{regexes}
"""

CLI_CONTRACT_RETRY = """

=== YOUR PREVIOUS SCRIPT WAS REJECTED ===
{problems}

=== YOUR PREVIOUS SCRIPT ===
```python
{previous_code}
```
Fix exactly these problems in this script (read the traceback: the failing line and the
exception say what is wrong) and keep everything that works. Return the complete corrected script.
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
