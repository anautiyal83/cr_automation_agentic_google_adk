"""LLM prompt templates for MockWire command → response generation.

The LLM only writes command responses for one node at a time. Prompts, users,
NIAM, escalation (`su`/`exit`) and runtime entries are built deterministically
(see topology.py).
"""
from __future__ import annotations

NODE_RESPONSES_SYSTEM = """\
You write realistic command outputs for a MockWire SSH simulator that stands in for \
a telecom network node during automated workflow testing.

Output ONLY a YAML map — no markdown fences, no explanations. Each key is a command \
the workflow sends; each value has a "lines:" list with the exact output lines.

FORMAT:
"<exact command string>":
  lines:
  - '<output line 1>'
  - '<output line 2>'
"regex:<java regex matching the command>":
  lines:
  - '<output line>'

RULES:
- One entry per command listed. Use the exact command text as the key when it has no \
${...} placeholders.
- When the command contains ${...} placeholders, use a "regex:" key: escape regex \
metacharacters in the literal parts and replace each placeholder with \\S+ (or .* \
inside quotes). A shell substitution such as $(date +%Y%m%d) contains spaces: replace it \
with .+? (never \\S+).
- Output must look like the real tool's output (e.g. real `sar` prints %user %nice \
%system %iowait %steal %idle) AND satisfy the step's success validation.
- Commands that print nothing on success get `lines: []`.
- Do NOT emit config, auth, niam, su, exit or stty entries.
- Use SINGLE quotes for every key and every output line (never double quotes: \
regex backslashes like \\S are invalid escapes in double-quoted YAML). Double any \
single quote inside a value.
- Keep outputs short and realistic (at most ~20 lines per command); never pad \
lines with whitespace.
"""

NODE_RESPONSES_USER = """\
UC: {uc_name}
Node: {node} (prompt after login: {prompt!r})

=== COMMANDS SENT TO THIS NODE (phase | command | success validation) ===
{commands}

=== SIMULATOR HINTS FROM REQUIREMENTS ===
{hints}
{feedback}
Write the YAML map of command responses for node {node}.
"""

FEEDBACK_SECTION = """
=== PREVIOUS EXECUTION TEST FAILURES (fix these responses) ===
{feedback}
"""

INVALID_YAML_RETRY = """
Your previous answer was not valid YAML. Answer again with ONLY the YAML map, single-quoted keys and lines, no prose.
"""

CONFORM_USER = """\
UC: {uc_name}
Node: {node}

These simulator responses are for a HEALTHY node on a normal day, but the workflow's own
success check rejects them. For each key below, return the output a healthy node prints that
the regex matches (realistic text, same command; for an existence pre-check this usually means
the "not found" output).

{listing}

Answer with ONLY the YAML map: the same keys, each with a 'lines' list.
"""
