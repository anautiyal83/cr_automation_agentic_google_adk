"""YAML helpers and auto-fixes for MockWire simulator configuration.

- Parse LLM output robustly (code fences, `---` documents, truncation)
- Read nodes and commands from workflow YAML
"""
from __future__ import annotations

import logging
import re
from io import StringIO

logger = logging.getLogger(__name__)


def clean_yaml_response(text: str) -> str:
    """Strip markdown code fences, document separators and leading prose.

    ``---`` separators are removed so several documents merge into one map
    (an LLM sometimes splits its answer; dropping documents would lose entries).
    """
    fenced = re.search(r"```(?:ya?ml)?[ \t]*\n(.*?)```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)  # ignore prose around a fenced block
    text = re.sub(r"^```(?:ya?ml)?\s*\n?", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n?```\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^(---|\.\.\.)\s*$", "", text, flags=re.MULTILINE)

    lines = text.split("\n")
    yaml_start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and (stripped.startswith("#") or ":" in stripped):
            yaml_start = i
            break

    return "\n".join(lines[yaml_start:]).rstrip()


def parse_llm_yaml(text: str) -> dict | list | None:
    """Clean, parse, and if needed repair LLM-generated YAML."""
    cleaned = clean_yaml_response(text)
    parsed = parse_yaml(cleaned)
    if parsed is None:
        for label, repaired in (
            ("double-quote escapes", _requote_backslash_scalars(cleaned)),
            ("truncation", _repair_truncated_yaml(_requote_backslash_scalars(cleaned))),
        ):
            parsed = parse_yaml(repaired)
            if parsed is not None:
                logger.info("Parsed LLM YAML after %s repair", label)
                break
    return parsed


def salvage_yaml_entries(text: str) -> dict:
    """Parse a top-level YAML map entry by entry, keeping every entry that parses.

    Used when the whole document is invalid because of one bad entry.
    """
    cleaned = _requote_backslash_scalars(clean_yaml_response(text))
    blocks: list[list[str]] = []
    for line in cleaned.split("\n"):
        if line and not line[0].isspace() and not line.startswith(("-", "#")):
            blocks.append([line])
        elif blocks:
            blocks[-1].append(line)
    result: dict = {}
    for block in blocks:
        parsed = parse_yaml("\n".join(block))
        if isinstance(parsed, dict):
            result.update(parsed)
    if result:
        logger.info("Salvaged %d/%d YAML entries from invalid LLM output", len(result), len(blocks))
    return result


_DQ_SCALAR = re.compile(r'^(\s*(?:-\s+)?)"((?:[^"\\]|\\.)*)"(\s*:.*|\s*)$')


def _requote_backslash_scalars(text: str) -> str:
    """Re-quote double-quoted keys/items containing backslashes with single quotes.

    LLMs write regex keys like ``"regex:ls \\S+\\.xml"``: ``\\S`` is an invalid
    escape in a YAML double-quoted scalar, so the whole document fails to parse.
    Single-quoted scalars take backslashes literally, which is what the regex means.
    """
    out = []
    for line in text.split("\n"):
        m = _DQ_SCALAR.match(line)
        if m and "\\" in m.group(2):
            inner = m.group(2).replace('\\"', '"').replace("\\\\", "\\").replace("'", "''")
            line = f"{m.group(1)}'{inner}'{m.group(3)}"
        out.append(line)
    return "\n".join(out)


def parse_yaml(text: str) -> dict | list | None:
    """Parse YAML text, returning dict/list or None on failure.

    Allows duplicate keys (common in MockWire configs where command strings repeat).
    """
    try:
        from ruamel.yaml import YAML
        yaml = YAML()
        yaml.allow_duplicate_keys = True
        parsed = yaml.load(StringIO(text))
        if isinstance(parsed, dict):
            return dict(parsed)
        if isinstance(parsed, list):
            return list(parsed)
        return None
    except Exception:
        return None


def dump_yaml(data: dict) -> str:
    """Dump dict to YAML string."""
    from ruamel.yaml import YAML
    stream = StringIO()
    yaml = YAML()
    yaml.default_flow_style = False
    yaml.width = 1_000_000  # never fold: a folded long regex KEY is invalid YAML (MockWire exits)
    yaml.dump(yaml11_safe(data), stream)
    return stream.getvalue()


# Plain scalars YAML 1.1 (SnakeYAML in MockWire and cli-automation) reads as something else than
# the string: "10:00:00" is the base-60 int 36000 (`time` answered 36000, run c89f65c2), yes/no/on/
# off are booleans, "~" is null, "0755" octal, "1_000" an int
_YAML11_NOT_STR = __import__("re").compile(
    r"^(?:[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+(?:\.[0-9_]*)?"
    r"|y|Y|yes|Yes|YES|n|N|no|No|NO|true|True|TRUE|false|False|FALSE|on|On|ON|off|Off|OFF"
    r"|~|null|Null|NULL|[-+]?0[0-7_]+|[-+]?[0-9][0-9_]*_[0-9_]*(?:\.[0-9_]*)?)$")


def yaml11_safe(data):
    """``data`` with every plain string YAML 1.1 would not read back as that string quoted."""
    from ruamel.yaml.scalarstring import DoubleQuotedScalarString, ScalarString
    if isinstance(data, dict):
        items = []
        for key, value in list(data.items()):
            if isinstance(key, str) and not isinstance(key, ScalarString) and _YAML11_NOT_STR.match(key):
                key = DoubleQuotedScalarString(key)
            items.append((key, yaml11_safe(value)))
        data.clear()  # rebuilt in order: MockWire tries regex keys in file order
        for key, value in items:
            data[key] = value
        return data
    if isinstance(data, list):
        for i, item in enumerate(data):
            data[i] = yaml11_safe(item)
        return data
    if isinstance(data, str) and not isinstance(data, ScalarString) and _YAML11_NOT_STR.match(data):
        return DoubleQuotedScalarString(data)
    return data


def _repair_truncated_yaml(yaml_text: str) -> str:
    """Attempt to repair YAML truncated by LLM token limits.

    Fixes:
    - Unclosed quotes (single or double)
    - Incomplete trailing lines
    """
    lines = yaml_text.split("\n")

    # Remove trailing incomplete lines (no colon, not a valid list item)
    while lines:
        last = lines[-1].rstrip()
        if not last:
            lines.pop()
            continue
        # Check for unclosed quotes on the last line
        for q in ("'", '"'):
            count = last.count(q)
            if count % 2 != 0:
                # Try closing the quote
                lines[-1] = last + q
                logger.info("Repaired unclosed %s quote on last line", q)
                return "\n".join(lines)
        # If last line looks incomplete (no value after colon, or bare text)
        stripped = last.strip()
        if stripped and not stripped.endswith(":") and ":" not in stripped and not stripped.startswith("-"):
            # Likely a truncated value — remove the line
            logger.info("Removed truncated line: %s", stripped[:60])
            lines.pop()
            continue
        break

    return "\n".join(lines)


def extract_workflow_nodes(workflow_yaml: str) -> list[str]:
    """Extract the remote node names a workflow connects to.

    Local nodes (``type: local`` / ``plugin: local``) run commands natively and
    are not simulated.
    """
    parsed = parse_yaml(workflow_yaml)
    if not parsed:
        return []

    nodes = set()
    local_nodes = set()
    # Support both "nodes" (new) and "nodeGroups" (old)
    node_groups = parsed.get("nodes", parsed.get("nodeGroups", {}))
    if isinstance(node_groups, dict):
        for ng_name, ng_val in node_groups.items():
            if isinstance(ng_val, dict) and "local" in (ng_val.get("type"), ng_val.get("plugin")):
                local_nodes.add(ng_name)
                continue
            nodes.add(ng_name)
            if isinstance(ng_val, dict):
                for node in ng_val.get("nodes", []):
                    if isinstance(node, dict):
                        nodes.add(node.get("name", ""))

    # Also check commands for node references
    for _phase_name, steps in _iter_phase_steps(parsed):
        for step in steps:
            node = step.get("node", "")
            if node and not re.search(r"\$\{", node) and node not in local_nodes:
                nodes.add(node)

    nodes.discard("")
    return sorted(nodes)


def extract_workflow_commands(workflow_yaml: str) -> list[dict]:
    """Extract commands from a workflow YAML for simulator response mapping."""
    parsed = parse_yaml(workflow_yaml)
    if not parsed:
        return []

    commands = []
    for phase_name, steps in _iter_phase_steps(parsed):
        for step in steps:
            commands.append({
                "cmd": step.get("send", step.get("cmd", step.get("command", ""))),
                "node": step.get("node", ""),
                "phase": phase_name,
                "validation": step.get("validation", {}),
                "register": step.get("register") or [],
                "prompt_regex": step.get("prompt_regex", ""),
            })
    return commands


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _iter_phase_steps(parsed: dict):
    """Yield (phase_name, [step dicts]) for phases given as a map or a list."""
    phases = parsed.get("phases", [])
    if isinstance(phases, dict):
        items = list(phases.items())
    elif isinstance(phases, list):
        items = [(p.get("name", p.get("phase", "")), p) for p in phases if isinstance(p, dict)]
    else:
        items = []
    for key, phase in items:
        if not isinstance(phase, dict):
            continue
        steps = phase.get("steps", phase.get("commands", []))
        yield key, list(_leaf_steps(steps))


def _leaf_steps(steps):
    """Steps in order; a container (``type: loop`` over CIQ records) stands for its steps."""
    for s in steps or []:
        if not isinstance(s, dict):
            continue
        if isinstance(s.get("steps"), list):
            yield from _leaf_steps(s["steps"])
        else:
            yield s


_UNRELATED_COMMAND = "zz_netconfig_unrelated_probe_command --x"


def safe_regex_key(key: str) -> str | None:
    """A response key the LLM wrote as ``regex:<shell command>``: a ``|`` outside a group is the
    shell's pipe, never an alternation — ``ls x | grep y || true`` as a regex has an EMPTY
    alternative and answered every command of the node (E030: the backup ``tar`` got the
    ``ls`` output, no archive was created). Such pipes are escaped; a key that still matches an
    unrelated command is dropped (None). Exact keys are returned unchanged."""
    if not key.startswith("regex:"):
        return key
    body, out, depth, i = key[6:], [], 0, 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            out.append(body[i:i + 2])
            i += 2
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == "|" and depth == 0:
            ch = "\\|"
        out.append(ch)
        i += 1
    fixed = "".join(out)
    try:
        pattern = re.compile(re.sub(r"\(\?<([A-Za-z][A-Za-z0-9]*)>", r"(?P<\1>", fixed))
    except re.error:
        return None
    if pattern.search(_UNRELATED_COMMAND):
        return None  # matches anything: it would answer every command of the node
    return "regex:" + fixed
