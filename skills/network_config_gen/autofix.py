"""Auto-fix utilities for LLM-generated workflow YAML.

Fixes common LLM mistakes while preserving ${...} runtime placeholders:
- Remove unknown fields (field whitelist per section)
- Auto-generate validation.description when missing
- Fix unsupported Java functions in criteria.expr
- Ensure referenced nodes exist in nodeGroups
- Repair YAML syntax (markdown fencing, trailing garbage)
- Rewrite placeholders to the syntax the cli-automation JAR resolves
- su/exit steps complete on the step prompt_regex, not on output validation
"""
from __future__ import annotations

import logging
import re
from io import StringIO
from typing import Any

logger = logging.getLogger(__name__)

# Fields allowed in each workflow section
_VALID_NODE_FIELDS = {
    "name", "host", "port", "user", "password", "prompt_regex", "type",
    "ssh", "escalation", "ne_id", "role", "kex_algorithms", "connect_timeout",
}

_VALID_COMMAND_FIELDS = {
    "cmd", "command", "node", "description", "command_description",
    "validation", "register", "on_failure", "timeout", "timeout_sec",
    "target_user", "delay", "expect", "send", "escalation", "exit_after",
    # StepDefinition fields from schemas/cli-automation-workflow-schema_v1.2.yaml
    "id", "prompt_regex", "when", "skip_when", "use_exit_code",
    "retries", "retry_delay_sec", "retry_on", "expect_reply",
    # were stripped before S8-16: SFTP transfers, loops, REST/email steps, nested steps
    "type", "execution_criteria", "var_meta", "for_each", "item_var", "max_iterations",
    "continue_when", "break_when", "rest", "sftp", "email", "steps",
}

# Shell "put"/"get" are sftp sub-commands, not shell commands: the JAR's sftp step does it
_SHELL_TRANSFER = re.compile(r"^(put|get)\s+(\S+)\s+(\S+)\s*$")
# A success regex that expects the command to fail (file absent, …)
_EXPECTS_ABSENCE = re.compile(r"No such file|cannot access|does not exist|not found", re.I)

# Step fields the JAR only accepts as strings (a497018: LLM map-valued `when` broke deserialization)
_STRING_ONLY_FIELDS = {"when", "skip_when", "prompt_regex"}

# Placeholder rewrites to the context the cli-automation JAR builds from the CIQ JSON
# (verified with `generate`/`execute` probes, spec 004 DEP-1/DEP-3/DEP-6):
#   record values  ${configSequences.0.records.0.data.<Column>}   (dot-numeric index; spaces ok)
#   NIAM IDs       ${<NODE>.niamID-<n>}
#   node group     ${node} ${nodeGroup} ${crGroup} ${email} ${NODE_TYPE} ${SUB_ACTIVITY_NAME}
# DEP-12: ${configSequences.0.records.0.data.'Test IMSI'} → ${configSequences.0.records.0.data.Test IMSI}.
# Also applied to the raw text: inside a single-quoted YAML scalar the quotes break parsing.
_QUOTED_COLUMN = re.compile(r"\$\{(configSequences\.\d+\.records\.\d+\.data\.)(?:'+([^'}]*)'+|\"([^\"}]*)\")\}")


# DEP-48: subscript form ${configSequences.0.records.0.data['Test IMSI']} (also in bare `when`
# paths) → the JAR's dot form data.Test IMSI. Quotes may be doubled inside a single-quoted scalar.
_SUBSCRIPT_COLUMN = re.compile(r"(configSequences\.\d+\.records\.\d+\.data)\[\s*(?:'+([^'\]\n]*)'+|\"([^\"\]\n]*)\")\s*\]")


def _dot_column(m: re.Match) -> str:
    return m.group(1) + "." + (m.group(2) if m.group(2) is not None else m.group(3)).strip()


_REC_ROW = re.compile(r"\$\{rec_row\.data\.(?:'+([^'}]*)'+|\"([^\"}]*)\"|([^}]+))\}")


def _rec_row_column(m: re.Match) -> str:
    return "${configSequences.0.records.0.data." + (m.group(1) or m.group(2) or m.group(3)).strip() + "}"


def _unquote_column(m: re.Match) -> str:
    return "${" + m.group(1) + (m.group(2) if m.group(2) is not None else m.group(3)).strip() + "}"


_PLACEHOLDER_REWRITES = [
    # DEP-3: ${{x}} → ${x}
    (re.compile(r"\$\{\{\s*(.+?)\s*\}\}"), r"${\1}"),
    # DEP-6: ${configData.tables.<Sheet>.<Col>} → first record of the node group's config sequence.
    # Sheet/column may be bare, "quoted", 'quoted' or ''quoted'' (inside a single-quoted shell arg).
    (re.compile(r"\$\{configData\.tables\.(?:'+[^']*'+|\"[^\"]*\"|[^.}]+)\."
                r"(?:'+([^']*)'+|\"([^\"]*)\"|([^}]+))\}"),
     lambda m: "${configSequences.0.records.0.data." + (m.group(1) or m.group(2) or m.group(3)).strip() + "}"),
    # DEP-17: the production framework's record syntax ${rec_row.data.<Col>} (also seen in
    # MOPs and developer notes) → the JAR's first record of the node group
    (_REC_ROW, _rec_row_column),
    (_SUBSCRIPT_COLUMN, _dot_column),
    # DEP-12: a quoted column resolves to empty; the JAR wants it bare (spaces ok)
    (_QUOTED_COLUMN, _unquote_column),
    # DEP-18: same in bare `when` paths — a quoted column makes the condition false, so
    # e.g. an identifier lookup guarded by it never runs (JAR-verified 2026-11-02)
    (re.compile(r"(?<!\{)(configSequences\.\d+\.records\.\d+\.data\.)(?:'([^'\n]*)'|\"([^\"\n]*)\")"),
     lambda m: m.group(1) + (m.group(2) if m.group(2) is not None else m.group(3)).strip()),
    # DEP-1: ${node_row.<NODE>.NIAM_NAME<n>} → ${<NODE>.niamID-<n>}
    (re.compile(r"\$\{node_row\.([A-Za-z0-9_]+)\.NIAM_NAME(\d+)\}"), r"${\1.niamID-\2}"),
    # DEP-7: registered outputs are plain context variables: ${register.x} → ${x}
    (re.compile(r"\$\{register\.([A-Za-z_][A-Za-z0-9_]*)\}"), r"${\1}"),
    # DEP-8: ${env.x} never resolves (OS env is ${ENV.X}); run-time values come from
    # `execute --var X=…` or a registered output, both referenced as ${x}
    (re.compile(r"\$\{env\.([A-Za-z_][A-Za-z0-9_]*)\}"), r"${\1}"),
]

_SU_OR_EXIT = re.compile(r"^(su(\s+-)?\s+\S+|exit)$")

_VALID_VALIDATION_FIELDS = {
    "criteria", "success", "failure", "description", "timeout",
}


def clean_yaml_response(text: str) -> str:
    """Strip markdown code fences and leading/trailing noise."""
    text = re.sub(r"^```(?:ya?ml)?\s*\n?", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n?```\s*$", "", text, flags=re.MULTILINE)

    lines = text.split("\n")
    yaml_start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and (stripped.startswith(("#", "---", "description", "nodeGroups",
                                             "phases", "version", "name:"))
                         or ":" in stripped):
            yaml_start = i
            break

    cleaned = "\n".join(lines[yaml_start:]).rstrip()
    return sanitize_yaml(cleaned)


# `key: 'text \' more'` — YAML single quotes escape a quote as '' (a backslash is literal), so
# `regex: 'tar: Removing leading `/\''` broke the whole workflow (G-15)
_SQ_BACKSLASH_QUOTE = re.compile(r"(?m)^(\s*(?:- )?[\w.-]+:[ \t]*)'((?:[^'\n]|\\'|'')*\\'(?:[^'\n]|\\'|'')*)'[ \t]*$")


_PLAIN_KEY_LINE = re.compile(r"^(\s*(?:- )?)[\w.-]+\s*:\s+(?![\"'\[{|>&*!#])\S")


def _fold_plain_continuations(lines: list[str]) -> list[str]:
    """Join a plain scalar wrapped over several lines (``send: python x.py --a 'v'`` + deeper
    indented continuation lines) into its key line, so the colon check below sees the whole
    value: a continuation holding `` : `` (``${a != null ? a : b}``) made the YAML unparsable
    (DEP-49). A key with a plain inline value has no children, so every deeper line continues it."""
    out: list[str] = []
    key_col = None
    for line in lines:
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        if key_col is not None and stripped and not stripped.startswith("#") and indent > key_col:
            out[-1] = out[-1].rstrip() + " " + stripped
            continue
        m = _PLAIN_KEY_LINE.match(line)
        key_col = len(m.group(1)) if m else None
        out.append(line)
    return out


def sanitize_yaml(text: str) -> str:
    """Fix unquoted colons in YAML values generated by LLM.

    E.g., ``description: sar on RDS [Pre-health check: CPU idle]``
    becomes ``description: 'sar on RDS [Pre-health check: CPU idle]'``

    Also handles list items: ``- description: value: with colon``, and unquotes CIQ
    column names in placeholders (DEP-12), which break single-quoted scalars.
    """
    text = _REC_ROW.sub(_rec_row_column, text)
    text = _SUBSCRIPT_COLUMN.sub(_dot_column, text)
    text = _QUOTED_COLUMN.sub(_unquote_column, text)
    text = _SQ_BACKSLASH_QUOTE.sub(lambda m: m.group(1) + "'" + m.group(2).replace("\\'", "''") + "'", text)
    fixed_lines = []
    for line in _fold_plain_continuations(text.split("\n")):
        stripped = line.lstrip()
        if ":" in stripped and not stripped.startswith("#"):
            indent = len(line) - len(stripped)
            # Handle list items: "- key: value"
            list_prefix = ""
            content = stripped
            if content.startswith("- "):
                list_prefix = "- "
                content = content[2:]
            # Match key: value where value contains another colon
            match = re.match(r'^([\w_.-]+)\s*:\s*(.+)$', content)
            if match:
                key, val = match.group(1), match.group(2)
                if ":" in val and not val.startswith(('"', "'", "[", "{", "|", ">")):
                    val = val.replace("'", "''")
                    val = f"'{val}'"
                    line = " " * indent + f"{list_prefix}{key}: {val}"
        fixed_lines.append(line)
    return "\n".join(fixed_lines)


def parse_yaml(text: str) -> dict | None:
    """Parse YAML text, returning dict or None on failure.

    Tries raw parse first, then sanitized parse if raw fails.
    """
    from ruamel.yaml import YAML
    try:
        parsed = YAML().load(StringIO(text))
        return dict(parsed) if parsed else None
    except Exception:
        pass
    try:
        sanitized = sanitize_yaml(text)
        parsed = YAML().load(StringIO(sanitized))
        if parsed:
            logger.info("Workflow YAML parsed after sanitizing")
            return dict(parsed)
    except Exception:
        pass
    return None


def dump_yaml(data: dict) -> str:
    """Dump dict to YAML string."""
    from ruamel.yaml import YAML
    stream = StringIO()
    yaml = YAML()
    yaml.default_flow_style = False
    yaml.width = 200
    yaml.dump(data, stream)
    return stream.getvalue()


def fix_workflow(yaml_text: str) -> str:
    """Apply all auto-fix rules to workflow YAML.

    Normalizes LLM output to the cli-automation framework schema:
    - nodeGroups → nodes
    - cmd/command → send
    - description → command_description (on steps)
    - phases as list → phases as dict
    - Removes explicit NIAM login / Ctrl+P+E commands (framework handles these)
    - Fixes validation structure
    """
    yaml_text = clean_yaml_response(yaml_text)
    parsed = parse_yaml(yaml_text)
    if not parsed:
        yaml_text = sanitize_yaml(yaml_text)
        parsed = parse_yaml(yaml_text)
        if not parsed:
            return yaml_text

    # --- Normalize nodeGroups → nodes ---
    if "nodeGroups" in parsed and "nodes" not in parsed:
        parsed["nodes"] = parsed.pop("nodeGroups")
        logger.info("Renamed nodeGroups → nodes")

    nodes = parsed.get("nodes", {})
    if isinstance(nodes, dict):
        parsed["nodes"] = _fix_nodes(nodes)

    known_nodes = set(nodes.keys()) if isinstance(nodes, dict) else set()

    # --- Normalize phases ---
    phases = parsed.get("phases", {})

    # Convert list → dict if LLM generated a list
    if isinstance(phases, list):
        phases_dict = {}
        for p in phases:
            if isinstance(p, dict):
                name = p.pop("name", p.get("phase", f"phase_{len(phases_dict)}"))
                p.pop("phase", None)
                phases_dict[name] = p
        phases = phases_dict
        logger.info("Converted phases list → dict (%d phases)", len(phases))

    if isinstance(phases, dict):
        fixed_phases = {}
        for phase_name, phase_body in phases.items():
            if isinstance(phase_body, dict):
                fixed_phases[phase_name] = _fix_phase(phase_body, known_nodes)
            elif isinstance(phase_body, list):
                fixed_phases[phase_name] = _fix_phase({"steps": phase_body}, known_nodes)
        parsed["phases"] = fixed_phases

    # --- Regex named groups + placeholders the JAR cannot resolve ---
    renames = fix_named_groups(parsed.get("phases", {}))
    rename_expr_vars(parsed.get("phases", {}), renames)
    parsed = fix_placeholders(parsed, renames)
    fix_regex_placeholders(parsed.get("phases", {}))
    shell_guard_conditions(parsed)
    fix_expression_placeholders(parsed)
    fix_missing_downloads(parsed)
    drop_session_exits(parsed)
    fix_single_file_listings(parsed)
    unify_alternative_columns(parsed)
    order_absence_checks(parsed)
    single_token_condition_vars(parsed)
    generalize_prompt_cwd(parsed)

    # --- Drop nodes no step uses (e.g. an invented NIAM/jump-host node: NIAM access is
    # ssh.ne_id on the real nodes), then add missing nodes referenced in steps ---
    nodes = parsed.get("nodes", {})
    if isinstance(nodes, dict):
        used, dynamic = _step_nodes(parsed.get("phases", {}))
        if not dynamic:  # a ${…} node could be any of them: keep all
            for unused in [n for n in nodes if n not in used]:
                del nodes[unused]
                logger.info("Removed node '%s': no step uses it", unused)
        referenced = _collect_referenced_nodes(parsed.get("phases", {}))
        for node_name in referenced - set(nodes.keys()):
            if node_name.upper() == "LOCAL":
                nodes[node_name] = {"type": "local", "plugin": "local"}
            else:
                nodes[node_name] = {
                    "type": "remote", "plugin": "ssh",
                    "prompt_regex": f"{node_name.lower()}:~ #",
                    "ssh": {
                        "host": f"${{{node_name}.niamID-1}}",
                        "port": "22", "user": "oamsys",
                        "auth": {"password": "${SECRET.password}"},
                    },
                }
            logger.info("Added missing node '%s' referenced in workflow steps", node_name)
        parsed["nodes"] = nodes

    return dump_yaml(parsed)


_REGEX_PLACEHOLDER = re.compile(r"\$\{[^{}]*\}")

# `[user@host dir]#` prompts show the current directory: after a `cd` the prompt is
# `[root@node POSTPAID]#`, and a prompt_regex written for `~` never matches (MRF rollback-only,
# spec 011 E030). The directory part matches any directory.
_CWD_PROMPT = re.compile(r"(\\\[[A-Za-z0-9_.()|-]+@[A-Za-z0-9_.()|-]+ )(?:~|[A-Za-z0-9_./~-]+|\(\.\*\?\)|\.\*\??)(\\?\])")


def generalize_prompt_cwd(parsed: dict) -> int:
    r"""Node and step ``prompt_regex``: the directory of a ``[user@host dir]`` prompt → ``[^\]]*``."""
    changed = 0

    def fix(regex: Any) -> Any:
        nonlocal changed
        if not isinstance(regex, str):
            return regex
        new = _CWD_PROMPT.sub(lambda m: m.group(1) + r"[^\]]*" + m.group(2), regex)
        changed += new != regex
        return new

    for node in (parsed.get("nodes") or {}).values() if isinstance(parsed.get("nodes"), dict) else []:
        if isinstance(node, dict) and "prompt_regex" in node:
            node["prompt_regex"] = fix(node["prompt_regex"])

    def walk(steps):
        for step in steps or []:
            if isinstance(step, dict):
                if "prompt_regex" in step:
                    step["prompt_regex"] = fix(step["prompt_regex"])
                walk(step.get("steps"))
    for body in (parsed.get("phases") or {}).values() if isinstance(parsed.get("phases"), dict) else []:
        if isinstance(body, dict):
            walk(body.get("steps"))
    if changed:
        logger.info("Prompt regexes: the directory of %d [user@host dir] prompt(s) matches any directory", changed)
    return changed


def fix_regex_placeholders(phases: Any) -> int:
    """Replace ``${…}`` in validation/register regexes with ``.+?`` (spec 004 DEP-13).

    The JAR interpolates ``send`` but not regexes: ``uid=${configSequences…}`` reaches
    java.util.regex literally and fails with PatternSyntaxException (probed 2026-11-02).
    Returns the number of regexes changed.
    """
    changed = 0

    def fix(holder: Any, key: str = "regex") -> None:
        nonlocal changed
        if isinstance(holder, dict) and isinstance(holder.get(key), str) and "${" in holder[key]:
            holder[key] = _REGEX_PLACEHOLDER.sub(lambda _: ".+?", holder[key])
            changed += 1

    def walk(steps: Any) -> None:
        for step in steps or []:
            if not isinstance(step, dict):
                continue
            validation = step.get("validation")
            if isinstance(validation, dict):
                fix(validation.get("criteria"))
                for branch in ("success", "failure"):
                    b = validation.get(branch)
                    fix(b.get("criteria") if isinstance(b, dict) else None)
            for reg in step.get("register") or []:
                fix(reg)
            walk(step.get("steps"))

    bodies = phases.values() if isinstance(phases, dict) else (phases or [])
    for body in bodies:
        walk(body.get("steps") if isinstance(body, dict) else body)
    if changed:
        logger.info("Replaced placeholders in %d regexes (the JAR does not interpolate regexes)", changed)
    return changed


def _step_nodes(phases: Any) -> tuple[set[str], bool]:
    """Nodes used by any step (nested steps included) and whether a step picks it via ${…}."""
    used: set[str] = set()
    dynamic = False

    def walk(steps):
        nonlocal dynamic
        for step in steps or []:
            if not isinstance(step, dict):
                continue
            node = str(step.get("node") or "")
            if "${" in node:
                dynamic = True
            elif node:
                used.add(node)
            walk(step.get("steps"))

    bodies = phases.values() if isinstance(phases, dict) else (phases or [])
    for body in bodies:
        walk(body.get("steps") if isinstance(body, dict) else body)
    return used, dynamic


def _collect_referenced_nodes(phases: dict) -> set[str]:
    """Collect all node names referenced in phase steps."""
    refs = set()
    if not isinstance(phases, dict):
        return refs
    for phase_body in phases.values():
        if not isinstance(phase_body, dict):
            continue
        for step in phase_body.get("steps", []):
            if isinstance(step, dict):
                node = step.get("node", "")
                if node and not node.startswith("${"):
                    refs.add(node)
    return refs


def _fix_nodes(nodes: dict) -> dict:
    """Fix node definitions to match framework schema."""
    fixed = {}
    for name, config in nodes.items():
        if not isinstance(config, dict):
            fixed[name] = config
            continue

        # Ensure type and plugin
        config.setdefault("type", "remote")
        config.setdefault("plugin", "ssh")

        # Remove old-style "nodes" array (framework doesn't use it)
        config.pop("nodes", None)

        # Move prompt_regex from top level if in old format
        ssh = config.get("ssh", {})
        if isinstance(ssh, dict):
            # Ensure auth block
            if ssh.get("password") and "auth" not in ssh:
                ssh["auth"] = {"password": ssh.pop("password")}
            # Move kex/host_key from top level if needed
            for key in ("kex_algorithms", "host_key_algorithms"):
                if key in config and key not in ssh:
                    ssh[key] = config.pop(key)

        fixed[name] = config
    return fixed


# Commands that are handled by framework config, not explicit steps
_FRAMEWORK_HANDLED = {
    "niam login", "ssh $", "ctrl+p+e", "press ctrl+p+e",
    "elevate_to_root", "su - root",
}


def _fix_phase(phase: dict, known_nodes: set) -> dict:
    """Fix a single phase's steps/commands."""
    # Normalize commands → steps
    steps = phase.get("steps", phase.get("commands", []))
    phase.pop("commands", None)

    # Remove phase-level keys that don't belong in framework schema
    for bad_key in ("group_by", "niam_failover", "description"):
        if bad_key in phase and bad_key != "name":
            # Keep "name" but move "description" to "name" if name is missing
            if bad_key == "description" and "name" not in phase:
                phase["name"] = phase.pop("description")
            else:
                phase.pop(bad_key, None)

    if isinstance(steps, list):
        fixed_steps = []
        for step in steps:
            if isinstance(step, dict):
                # Skip commands that framework handles automatically
                cmd_text = (step.get("send") or step.get("cmd") or
                            step.get("command") or step.get("name", "")).lower()
                if any(handled in cmd_text for handled in _FRAMEWORK_HANDLED):
                    logger.debug("Removed framework-handled command: %s", cmd_text[:60])
                    continue
                step = _fix_step(step, known_nodes)
                fixed_steps.append(step)
        phase["steps"] = fixed_steps
    return phase


def _fix_step(step: dict, known_nodes: set) -> dict:
    """Normalize a single step to framework schema."""
    # cmd/command → send
    if "cmd" in step and "send" not in step:
        step["send"] = step.pop("cmd")
    if "command" in step and "send" not in step:
        step["send"] = step.pop("command")

    # description → command_description
    if "description" in step and "command_description" not in step:
        step["command_description"] = step.pop("description")

    # name → command_description (if no send)
    if "name" in step and "command_description" not in step:
        step["command_description"] = step.get("name", "")

    # Remove old-style keys
    for old_key in ("name", "user", "escalate", "success.message", "failure.message"):
        step.pop(old_key, None)

    # Fix register: convert list of strings or dicts with just names
    register = step.get("register", [])
    if isinstance(register, list):
        fixed_reg = []
        for r in register:
            if isinstance(r, str):
                # Bare string → regex that captures full output
                fixed_reg.append({"regex": "(?P<" + r + ">[\\s\\S]+)", "name": r})
            elif isinstance(r, dict):
                fixed_reg.append(r)
        if fixed_reg:
            step["register"] = fixed_reg

    # Fix validation block
    validation = step.get("validation", {})
    if isinstance(validation, dict):
        step["validation"] = _fix_validation(validation, step)

    # Fix criteria.expr — replace unsupported Java functions
    _fix_criteria_expr(step)

    _fix_prompt_change_step(step)
    _fix_transfer_step(step)
    _fix_absence_check(step)
    for key in ("when", "skip_when"):
        if isinstance(step.get(key), str):
            step[key] = fix_condition_operators(step[key])
    # Helper scripts (called by bare name) are generated for and live on the automation host;
    # a node's own tools have a path. `python parse_response.py` on PGW got an invented
    # answer from the simulator and no node has the script (DEP-40)
    if (isinstance(step.get("send"), str) and str(step.get("node", "")).upper() not in ("", "LOCAL")
            and _HELPER_CALL.match(step["send"])):
        logger.info("Moved helper script call %r from %s to LOCAL", step["send"][:60], step.get("node"))
        step["node"] = "LOCAL"
    if isinstance(step.get("send"), str):
        step["send"] = drop_stray_quote(step["send"])
    if str(step.get("node", "")).upper() == "LOCAL" and isinstance(step.get("send"), str):
        step["send"] = single_quote_arguments(split_dict_arguments(step["send"]))

    # Strip unknown fields that cause JAR deserialization errors
    for key in list(step.keys()):
        if key not in _VALID_COMMAND_FIELDS:
            step.pop(key)
        elif key in _STRING_ONLY_FIELDS and not isinstance(step[key], str):
            logger.info("Removed non-string %s on step %r", key, step.get("send", "")[:40])
            step.pop(key)

    return step


_HELPER_CALL = re.compile(r"^\s*python3?\s+[\w.-]+\.py(?:\s|$)")
_DOUBLE_QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')


_DICT_ARG = re.compile(r"(--[A-Za-z][\w-]*)\s+(['\"]?)\{(.*?)\}\2(?=\s+--|\s*$)", re.DOTALL)
_DICT_PAIR = re.compile(r"""['"]?([^'":,{}]+?)['"]?\s*:\s*(?:['"]?(\$\{[^}]*\}|[^,'"{}]*?)['"]?)\s*(?:,|$)""")


def split_dict_arguments(command: str) -> str:
    """``--ciq_data '{'Mandatory Cap': '${…}', 'VNI': '${…}'}'`` → ``--ciq_data_mandatory_cap
    '${…}' --ciq_data_vni '${…}'``: one flag per field. The JAR's LOCAL runner breaks on any
    double quote and nested single quotes are split by the shell, so a dict/JSON value can
    never arrive in one argument (probed; DEP-44). Helper scripts are generated afterwards
    from these flags."""
    def expand(m: re.Match) -> str:
        pairs = [(k.strip(), v.strip()) for k, v in _DICT_PAIR.findall(m.group(3)) if k.strip()]
        if not pairs:
            return m.group(0)
        flag = m.group(1)
        return " ".join(f"{flag}_{re.sub(r'[^a-z0-9]+', '_', k.lower()).strip('_')} '{v}'" for k, v in pairs)
    return _DICT_ARG.sub(expand, command)


def single_quote_arguments(command: str) -> str:
    """``--a "x y"`` → ``--a 'x y'`` in a LOCAL command. The JAR's LOCAL runner splits
    double-quoted arguments at spaces and drops the rest of the line (probed:
    ``--a "x y" --c "z"`` reached the program as ``['--a', 'x']``); single quotes arrive
    intact, multi-line values too. ``${…}`` is filled in by the JAR before the shell runs,
    so single quotes lose nothing (DEP-36)."""
    def quote(m: re.Match) -> str:
        value = re.sub(r'\\(["\\$`])', r"\1", m.group(1))
        return "'" + value.replace("'", "'\\''") + "'"
    return _DOUBLE_QUOTED.sub(quote, command)


def unbalanced_quotes(command: str) -> bool:
    """True when a shell would wait for a closing quote (the step never gets its prompt)."""
    import shlex
    try:
        shlex.split(command)
        return False
    except ValueError:
        return True


def drop_stray_quote(command: str) -> str:
    """``… -s sub objectclass=*"`` → ``… objectclass=*`` when the trailing quote is the only one
    left open: the shell waited for its closing quote and the step timed out (DEP-50). Other
    unbalanced commands are left for the validator."""
    s = command.rstrip()
    if s[-1:] in ("'", '"') and unbalanced_quotes(s) and not unbalanced_quotes(s[:-1]):
        return s[:-1].rstrip()
    return command


_WORD_OPERATOR = re.compile(r"('(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")|\s+(and|or)\s+", re.IGNORECASE)


def fix_condition_operators(condition: str) -> str:
    """``a == '' and b != ''`` → ``a == '' && b != ''`` (``or`` → ``||``) outside quoted values:
    the JAR joins conditions with &&/||; with ``and`` every step for EAST (only Test MSISDN
    given) was skipped, so the IMSI was never looked up (DEP-35)."""
    return _WORD_OPERATOR.sub(
        lambda m: m.group(1) if m.group(1) else (" && " if m.group(2).lower() == "and" else " || "), condition)


def _fix_transfer_step(step: dict) -> None:
    """``send: put <local> <remote>`` in a node shell → JAR sftp upload step (``get`` →
    download). A shell has no ``put``; the JAR's sftp step transfers over the node's SSH
    connection (verified against MockWire 2026-11-02, S8-16)."""
    if isinstance(step.get("sftp"), dict):
        # An sftp step prints nothing: an output regex can never match; its status decides
        for criteria in _criteria_blocks(step):
            criteria.pop("regex", None)
        _allow_overwrite(step["sftp"])
        return
    m = _SHELL_TRANSFER.match(str(step.get("send") or "").strip())
    if not m or str(step.get("node", "")).upper() == "LOCAL":
        return
    op, a, b = m.group(1), m.group(2).strip("'\""), m.group(3).strip("'\"")
    step["sftp"] = ({"operation": "upload", "local_path": a, "remote_path": b} if op == "put"
                    else {"operation": "download", "remote_path": a, "local_path": b})
    _allow_overwrite(step["sftp"])
    step.pop("send", None)
    validation = step.get("validation")
    if isinstance(validation, dict):  # transfer success is the step's own status
        for branch in ("success", "failure"):
            if isinstance(validation.get(branch), dict):
                validation[branch].pop("criteria", None)
    logger.info("Shell '%s' → sftp %s step", op, step["sftp"]["operation"])


def _allow_overwrite(sftp: dict) -> None:
    """A download whose local file exists fails with ``overwrite=false`` — and the file of
    the previous node group always exists when one run handles several (DEP-30)."""
    if sftp.get("operation") == "download" and "overwrite" not in sftp:
        sftp["overwrite"] = True


def _fix_absence_check(step: dict) -> None:
    """A check whose success means the command failed (``ls`` of a file that must not exist)
    exits non-zero, and the JAR fails any non-zero step before its regex: make it exit 0."""
    send = str(step.get("send") or "").strip()
    validation = step.get("validation") if isinstance(step.get("validation"), dict) else {}
    success = validation.get("success") if isinstance(validation.get("success"), dict) else {}
    criteria = success.get("criteria") if isinstance(success.get("criteria"), dict) else {}
    regex = criteria.get("regex")
    if (send and isinstance(regex, str) and _EXPECTS_ABSENCE.search(regex)
            and "|| true" not in send and "|| :" not in send):
        step["send"] = send + ("" if "2>&1" in send else " 2>&1") + " || true"


def _fix_prompt_change_step(step: dict) -> None:
    """``su``/``exit`` steps finish on the new prompt; they produce no output to validate.

    The JAR strips prompts from captured output, so a validation regex on the prompt
    can never match (spec 004 DEP-4) — and with ``on_failure: stop`` it aborts the
    phase. On a real node ``su - x; echo __CMD_DONE__:$?`` only echoes after the
    sub-shell exits, so the step can only complete by prompt detection. Move the
    prompt regex to the step-level ``prompt_regex`` and drop the output criterion
    (a branch without criteria always passes; a missing prompt still fails the step
    with a prompt timeout).
    """
    send = str(step.get("send", "")).strip()
    if not _SU_OR_EXIT.match(send):
        return
    validation = step.get("validation")
    if not isinstance(validation, dict):
        return
    success = validation.get("success")
    criteria = success.get("criteria") if isinstance(success, dict) else None
    regex = criteria.get("regex") if isinstance(criteria, dict) else None
    if not regex:
        return
    step.setdefault("prompt_regex", regex)
    criteria.pop("regex", None)
    if not criteria:
        success.pop("criteria", None)
    validation["description"] = (f"Prompt matches {step['prompt_regex']!r} after '{send}' "
                                 "(checked by step prompt_regex)")
    logger.info("Step %r: prompt validation moved to step prompt_regex %r", send, step["prompt_regex"])


def fix_placeholders(value: Any, renames: dict[str, str] | None = None) -> Any:
    """Rewrite placeholders (recursively) to syntax the cli-automation JAR resolves.

    ``renames`` maps registered variable names to their Java group names
    (see fix_named_groups); ``${register.x}`` and ``${x}`` both become ``${javaName}``.
    """
    if isinstance(value, str):
        for pattern, repl in _PLACEHOLDER_REWRITES:
            value = pattern.sub(repl, value)
        if renames:
            value = _VAR_REF.sub(lambda m: "${" + renames.get(m.group(1), m.group(1)) + "}", value)
        return value
    if isinstance(value, dict):
        return {k: fix_placeholders(v, renames) for k, v in value.items()}
    if isinstance(value, list):
        return [fix_placeholders(v, renames) for v in value]
    return value


_PY_NAMED_GROUP = re.compile(r"\(\?P<([A-Za-z_][A-Za-z0-9_]*)>")
_JAVA_NAMED_GROUP = re.compile(r"\(\?<([A-Za-z_][A-Za-z0-9_]*)>")
# "(" that opens a capturing group: not escaped, not "(?…", not inside a [...] class
_UNNAMED_GROUP = re.compile(r"(?<!\\)\((?!\?)(?![^\[]*\])")
_VAR_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def fix_named_groups(phases: Any) -> dict[str, str]:
    """Make register/validation regexes valid Java and return variable renames.

    The JAR compiles regexes with java.util.regex (spec 004 DEP-7):
    - Python ``(?P<name>…)`` is a PatternSyntaxException → ``(?<name>…)``
    - Java group names allow only letters and digits → ``pre_ldap_result`` → ``preLdapResult``
    - ``register: {regex: 'X=(\\d+)', name: v}`` without a named group registers nothing
      → the first capturing group becomes ``(?<v>…)``
    Registered values are referenced as ``${groupName}``.
    """
    renames: dict[str, str] = {}

    def java_name(name: str) -> str:
        parts = [p for p in name.split("_") if p]
        new = (parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:])) if parts else "v"
        if new != name:
            renames[name] = new
        return new

    def fix_regex(regex: str) -> str:
        regex = _PY_NAMED_GROUP.sub(lambda m: f"(?<{java_name(m.group(1))}>", regex)
        return _JAVA_NAMED_GROUP.sub(lambda m: f"(?<{java_name(m.group(1))}>", regex)

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        for reg in node.get("register") or []:
            if not isinstance(reg, dict) or not isinstance(reg.get("regex"), str):
                continue
            reg["regex"] = fix_regex(reg["regex"])
            if _JAVA_NAMED_GROUP.search(reg["regex"]):
                # The JAR stores the FIRST group's value under the name (probed 2026-11-02):
                # every other group must not capture
                reg["regex"] = _UNNAMED_GROUP.sub("(?:", reg["regex"])
            name = reg.get("name")
            if not (isinstance(name, str) and name):
                continue
            groups = _JAVA_NAMED_GROUP.findall(reg["regex"])
            target = java_name(name)
            if not groups:
                reg["regex"] = _name_first_group(reg["regex"], target)
            elif len(groups) == 1 and groups[0] != target:
                # `name:` is ignored when the regex has a named group — the LLM meant `name`
                reg["regex"] = reg["regex"].replace(f"(?<{groups[0]}>", f"(?<{target}>", 1)
                for old, new in list(renames.items()):
                    if new == groups[0]:
                        renames[old] = target
                renames[groups[0]] = target
        validation = node.get("validation")
        if isinstance(validation, dict):
            for branch in ("success", "failure", "warning"):
                criteria = (validation.get(branch) or {}).get("criteria") if isinstance(
                    validation.get(branch), dict) else None
                if isinstance(criteria, dict) and isinstance(criteria.get("regex"), str):
                    criteria["regex"] = fix_regex(criteria["regex"])
        for value in node.values():
            walk(value)

    walk(phases)
    if renames:
        logger.info("Renamed registered variables for Java regex: %s", renames)
    return renames


def _name_first_group(regex: str, name: str) -> str:
    """Turn the first plain capturing group into ``(?<name>…)``; wrap the regex if none."""
    i = 0
    while i < len(regex):
        if regex[i] == "\\":
            i += 2
            continue
        if regex[i] == "(" and not regex.startswith("(?", i):
            return f"{regex[:i]}(?<{name}>{regex[i + 1:]}"
        i += 1
    return f"(?<{name}>{regex})"


def _fix_validation(validation: dict, step: dict) -> dict:
    """Normalize validation to framework schema: description + success/failure branches."""
    # Auto-generate description if missing
    if "description" not in validation:
        cmd_text = step.get("send", step.get("cmd", "unknown"))
        validation["description"] = f"Validate output of: {str(cmd_text)[:80]}"

    # If old-style top-level criteria, move into success branch
    if "criteria" in validation and "success" not in validation:
        criteria = validation.pop("criteria")
        validation["success"] = {"criteria": criteria, "message": "Command completed successfully"}

    # Ensure success/failure branches exist
    if "success" not in validation:
        validation["success"] = {"message": "Command completed successfully"}
    if "failure" not in validation:
        validation["failure"] = {"message": "Command validation failed"}

    # Clean up old-style flat keys
    for old_key in ("success.message", "failure.message"):
        validation.pop(old_key, None)

    return validation


_EXPR_CAST = re.compile(r"\b(?:Float|Int|float|int|Double|Number|parseFloat|parseInt)\s*\(\s*([A-Za-z_]\w*)\s*\)")
_EXPR_UNSUPPORTED = re.compile(
    r"all\s*\(|any\s*\(|re_search\s*\(|re\.match\s*\(|\.group\s*\(|json\.loads\s*\(|"
    r"for\s+\w+\s+in\s+|lambda\s+|output\.contains\s*\(")
_EXPR_LITERAL = re.compile(r"^\s*(true|false|1\s*==\s*1)\s*$", re.I)
EXPR_IDENT = re.compile(r"(?<![\w.'\"])([A-Za-z_]\w*)(?!\s*\()")
EXPR_KEYWORDS = {"and", "or", "not", "true", "false", "null", "None", "True", "False"}


def _criteria_blocks(step: dict) -> list[dict]:
    validation = step.get("validation")
    if not isinstance(validation, dict):
        return []
    blocks = [validation.get("criteria")]
    blocks += [b.get("criteria") for b in (validation.get(k) for k in ("success", "failure", "warning"))
               if isinstance(b, dict)]
    return [c for c in blocks if isinstance(c, dict)]


def _fix_criteria_expr(cmd: dict) -> None:
    """Make criteria.expr evaluable by the cli-automation JAR (probed 2026-11-02, DEP-24).

    The JAR compares registered context variables (``idle >= 90``). It has no Float()/Int()
    casts, a literal ``True`` always fails, and named groups of a validation regex are not
    visible to expr — only ``register:`` on the step makes a value available.
    """
    for criteria in _criteria_blocks(cmd):
        expr = criteria.get("expr")
        if not isinstance(expr, str):
            continue
        expr = _EXPR_CAST.sub(lambda m: m.group(1), expr).strip()
        if not expr or _EXPR_LITERAL.match(expr) or _EXPR_UNSUPPORTED.search(expr):
            criteria.pop("expr", None)  # the regex (if any) decides
            continue
        criteria["expr"] = expr
        regex = criteria.get("regex")
        groups = set(re.findall(r"\(\?P?<([A-Za-z_]\w*)>", regex)) if isinstance(regex, str) else set()
        registered = {g for r in (cmd.get("register") or []) if isinstance(r, dict)
                      for g in re.findall(r"\(\?P?<([A-Za-z_]\w*)>", str(r.get("regex", "")))}
        used = {m.group(1) for m in EXPR_IDENT.finditer(expr)} - EXPR_KEYWORDS
        if used & groups - registered:
            cmd.setdefault("register", []).append({"regex": regex})


_EXPRESSION_PLACEHOLDER = re.compile(r"\$\{([^{}]*(?:\?|!=|==|\|\||&&)[^{}]*)\}")


_PATH_TOKEN = re.compile(r"/[^\s'\";|&<>()]+")


def use_path_columns(parsed: dict, record_columns: dict | None) -> int:
    """A CIQ record column that gives a directory for every row (MRF_DESTINATION_PATH) replaces
    the directory the workflow builds from the MOP's example (``…/audioclips/${MRF.Circle}/PREPAID``):
    paths sharing the CIQ values' leading directories have that many segments swapped for the
    column. The LLM kept the MOP path through every correction round (G-15)."""
    rc = record_columns or {}
    samples = rc.get("samples") or {}
    text = "\n".join(_all_strings(parsed.get("phases") or {}))
    changed = 0
    for col in rc.get("filled") or []:
        vals = [v.rstrip("/") for v in samples.get(col, []) if v]
        if not vals or not all(v.startswith("/") for v in vals) or f"data.{col}" in text:
            continue
        segs = [v.split("/")[1:] for v in vals]
        depth = min(len(s) for s in segs)
        shared = 0
        while shared < depth and len({s[shared] for s in segs}) == 1:
            shared += 1
        if shared < 4:
            continue
        lead = segs[0][:shared]
        ref = "${configSequences.0.records.0.data." + col + "}"

        def swap(m):
            nonlocal changed
            parts = m.group(0).split("/")[1:]
            k = 0
            while k < min(len(parts), shared) and parts[k] == lead[k]:
                k += 1
            # Same leading directories up to where the workflow puts its own placeholder
            # (RAJ ↔ ${MRF.Circle}) or all of them; never a different branch of the tree
            if len(parts) < depth or k < 4 or (k < shared and "${" not in parts[k]):
                return m.group(0)
            changed += 1
            rest = "/".join(parts[depth:])
            return ref + ("/" + rest if rest or m.group(0).endswith("/") else "")

        def walk(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    if k in ("send", "remote_path", "local_path") and isinstance(v, str):
                        node[k] = _PATH_TOKEN.sub(swap, v)
                    else:
                        walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
        walk(parsed.get("phases") or {})
    if changed:
        logger.info("Replaced %d MOP example path(s) with the CIQ's path column", changed)
    return changed


def _all_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _all_strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _all_strings(v)


SKIPPED_BY_CONDITION = "SKIPPED_BY_CONDITION"
_COND_OPERAND = r"\$\{[^{}]+\}|[A-Za-z_][\w.\-]*"
_COND_METHOD = re.compile(rf"({_COND_OPERAND})\.(endsWith|startsWith|contains|matches)\(\s*'([^']*)'\s*\)")
_COND_COMPARE = re.compile(rf"({_COND_OPERAND})\s*(==|!=)\s*('[^']*'|null)")
_COND_EXPR_PLACEHOLDER = re.compile(r"\$\{([^{}]*(?:==|!=|\.\w+\()[^{}]*)\}")


def _cond_operand(op: str) -> str:
    op = op.strip()
    return op if op.startswith("${") else "${" + op + "}"


def _glob(lit: str) -> str:
    """Bash pattern text: literal parts quoted, version-mask segments (13.X.X.X) as *."""
    parts = re.split(r"(?<![A-Za-z0-9])X(?![A-Za-z0-9])", lit)
    return "*".join(f"'{p}'" if p else "" for p in parts)


def condition_to_bash(cond: str) -> str:
    """``x.endsWith('.tar') && VER == '13.X.X.X'`` → ``[[ "${x}" == *".tar" && "${VER}" == "13."* ]]``;
    '' when the condition holds something this translation does not cover."""
    c = _COND_EXPR_PLACEHOLDER.sub(lambda m: m.group(1), cond.strip())
    # x.endsWith('.tar') == false → !x.endsWith('.tar'); == true / != false → the call itself
    call = rf"(?:{_COND_OPERAND})\.(?:endsWith|startsWith|contains|matches)\(\s*'[^']*'\s*\)"
    c = re.sub(rf"({call})\s*(==|!=)\s*(true|false)\b",
               lambda m: m.group(1) if (m.group(2) == "==") == (m.group(3) == "true") else "!" + m.group(1), c)

    def method(m):
        op, name, lit = _cond_operand(m.group(1)), m.group(2), m.group(3)
        if name == "matches":
            # bash ERE has no \d \w \s
            ere = lit.replace("\\d", "[0-9]").replace("\\w", "[A-Za-z0-9_]").replace("\\s", "[[:space:]]")
            return f"'{op}' =~ ^({ere})$"
        pat = {"endsWith": f"*'{lit}'", "startsWith": f"'{lit}'*", "contains": f"*'{lit}'*"}[name]
        return f"'{op}' == {pat}"

    def compare(m):
        op, oper, lit = _cond_operand(m.group(1)), m.group(2), m.group(3)
        lit = "" if lit == "null" else lit[1:-1]
        return f"'{op}' {oper} {_glob(lit) or chr(39) * 2}"

    c = _COND_METHOD.sub(method, c)
    c = _COND_COMPARE.sub(compare, c)
    c = re.sub(r"!(?!=)\s*", "! ", c)  # [[ ! 'a' == b ]] negates; !'a' is the string "!a"
    # Everything left must be bash test syntax: quoted operands, operators, ! ( ) && ||
    # Single quotes throughout: LOCAL commands break on any double quote (DEP-44)
    leftover = re.sub(r"'[^']*'|\$\{[^}]*\}|=~ \^\([^)]*\)\$|==|!=|&&|\|\||[!()*\s]", "", c)
    return f"[[ {c} ]]" if not leftover else ""


def shell_guard_conditions(parsed: dict) -> int:
    """A ``when`` / ``skip_when`` with a method call (``.endsWith('.tar')``), ``!`` or a version
    mask is always false in the framework — both branches of MRF_ANNOUNCEMENT_LOADING's
    copy-or-untar were skipped (G-15, JAR-verified). Such a condition becomes a bash test around
    the command; the not-taken branch prints SKIPPED_BY_CONDITION, which its success regex accepts."""
    from .validator import _CONDITION_CALL
    changed = 0

    def walk(steps):
        nonlocal changed
        for step in steps or []:
            if not isinstance(step, dict):
                continue
            walk(step.get("steps"))
            for key in ("when", "skip_when"):
                cond = str(step.get(key) or "")
                if not cond or not isinstance(step.get("send"), str):
                    continue
                plain = re.sub(r"'[^']*'", "''", cond)
                if not (_CONDITION_CALL.search(plain) or re.search(r"'[^']*(?<![A-Za-z0-9])X(?![A-Za-z0-9])", cond)):
                    continue
                test = condition_to_bash(cond)
                if not test:
                    continue
                if key == "skip_when":
                    test = f"! {test}"
                body = step["send"].strip()
                step["send"] = (f"if {test}; then\n{body}\nelse echo {SKIPPED_BY_CONDITION}; fi" if "\n" in body
                                else f"if {test}; then {body}; else echo {SKIPPED_BY_CONDITION}; fi")
                del step[key]
                for crit in _criteria_blocks(step):
                    if isinstance(crit.get("regex"), str) and crit["regex"]:
                        crit["regex"] = f"(?:{crit['regex']})|{SKIPPED_BY_CONDITION}"
                changed += 1

    for body in (parsed.get("phases") or {}).values():
        walk(body.get("steps") if isinstance(body, dict) else body)
    if changed:
        logger.info("Moved %d condition(s) with method calls / version masks into bash tests", changed)
    return changed


def fix_expression_placeholders(parsed: dict) -> int:
    """``${a != null ? a : b}`` → ``${a}`` (DEP-28): the JAR interpolates names only, an
    expression resolves to empty (`imsi=,`). The first identifier the workflow registers
    wins; placeholders without one are left for the validator."""
    registered: set[str] = set()

    def collect(steps):
        for step in steps or []:
            if isinstance(step, dict):
                for reg in step.get("register") or []:
                    if isinstance(reg, dict):
                        registered.update(_JAVA_NAMED_GROUP.findall(str(reg.get("regex", ""))))
                collect(step.get("steps"))

    phases = parsed.get("phases") or {}
    bodies = list(phases.values()) if isinstance(phases, dict) else list(phases or [])
    for body in bodies:
        collect(body.get("steps") if isinstance(body, dict) else body)
    changed = 0
    fallbacks: dict[str, str] = {}  # variable → CIQ column it falls back to

    def rewrite(m: re.Match) -> str:
        nonlocal changed
        expr = m.group(1)
        for ident in re.findall(r"[A-Za-z_][\w.]*", expr):
            if ident in registered:
                changed += 1
                column = re.search(r"configSequences\.\d+\.records\.\d+\.data\.([^?:|&!=]+?)\s*$", expr)
                if column:
                    fallbacks.setdefault(ident, column.group(1).strip().strip("'\""))
                return "${" + ident + "}"
        return m.group(0)

    def walk(value):
        if isinstance(value, str):
            return _EXPRESSION_PLACEHOLDER.sub(rewrite, value)
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v) for v in value]
        return value

    parsed["phases"] = walk(phases)
    for var, column in fallbacks.items():
        _insert_fallback_register(parsed, var, column)
    if changed:
        logger.info("Rewrote %d expression placeholders to registered variables", changed)
    return changed


_ABS_PATH = re.compile(r"(?<![\w$}])/(?:[\w.${}-]+/)+[\w.${}-]+")


def remote_file_reads(parsed: dict) -> list[dict]:
    """LOCAL steps that read a file which only exists on a node (listed/read there, or an
    sftp upload target) without an earlier sftp download of it — by absolute path or by its
    bare name (``parse_response.py --response_file '${ORDERNO}.xml.res'`` after
    ``ls -l /…/outbox/${ORDERNO}.xml.res`` on PGW; DEP-29).

    Each hit: ``steps`` (the list holding the LOCAL step), ``index``, ``phase``, ``send``,
    ``node``/``remote_path`` (where the file is) and ``local_name`` (what the step reads).
    """
    phases = parsed.get("phases") or {}
    items = list(phases.items()) if isinstance(phases, dict) else []
    # Files that exist locally anyway: anything uploaded from or downloaded to the host
    local: set[str] = set()

    def collect(steps):
        for step in steps or []:
            if isinstance(step, dict):
                sftp = step.get("sftp") if isinstance(step.get("sftp"), dict) else None
                if sftp and sftp.get("operation") == "upload" and sftp.get("local_path"):
                    local.add(str(sftp["local_path"]).rsplit("/", 1)[-1])
                collect(step.get("steps"))

    for _, body in items:
        collect(body.get("steps") if isinstance(body, dict) else body)

    remote: dict[str, tuple[str, str]] = {}  # full path and basename -> (node, full path)
    downloaded: set[str] = set()
    hits: list[dict] = []

    def walk(phase, steps):
        for i, step in enumerate(list(steps or [])):
            if not isinstance(step, dict):
                continue
            node = str(step.get("node") or "")
            sftp = step.get("sftp") if isinstance(step.get("sftp"), dict) else None
            if sftp:
                if sftp.get("operation") == "download":
                    downloaded.add(str(sftp.get("local_path") or ""))
                    downloaded.add(str(sftp.get("local_path") or "").rsplit("/", 1)[-1])
                elif sftp.get("remote_path"):
                    path = str(sftp["remote_path"])
                    remote[path] = remote[path.rsplit("/", 1)[-1]] = (node, path)
            elif node.upper() != "LOCAL":
                for path in _ABS_PATH.findall(str(step.get("send") or "")):
                    remote[path] = remote[path.rsplit("/", 1)[-1]] = (node, path)
            else:
                send = str(step.get("send") or "")
                tokens = {t.strip("'\"") for t in re.split(r"[\s=]+", send)}
                for token in sorted(tokens):
                    name = token.rsplit("/", 1)[-1]
                    # The node's path, its bare name, or the name under a made-up local
                    # directory ('/tmp/${ORDERNO}.xml.res' — never downloaded there either)
                    key = token if token in remote else name if "/" in token and "." in name else ""
                    if (key in remote and token not in downloaded and name not in downloaded
                            and name not in local and ("/" in token or "." in token)):
                        hits.append({"steps": steps, "index": i, "phase": phase, "send": send,
                                     "node": remote[key][0], "remote_path": remote[key][1],
                                     "local_name": name, "token": token})
                        downloaded.add(name)
            walk(phase, step.get("steps"))

    for phase, body in items:
        walk(phase, body.get("steps") if isinstance(body, dict) else body)
    return hits


_ABSENT = re.compile(r"no such file", re.IGNORECASE)
_VARS = re.compile(r"\$\{([^}]+)\}")


def order_absence_checks(parsed: dict) -> int:
    """A "file must not exist yet" check (``ls … || true`` expecting "No such file") on a
    file named after an upload (``outbox/${ORDERNO}.xml.res`` after ``inbox/${ORDERNO}.xml``)
    runs before that upload: placed after it, it raced the node that produces the file and
    failed on every node (DEP-45)."""
    moved = 0
    for body in (parsed.get("phases") or {}).values():
        steps = body.get("steps") if isinstance(body, dict) else None
        if not isinstance(steps, list):
            continue
        for check in [s for s in steps if isinstance(s, dict)]:
            send = str(check.get("send") or "")
            if not send.lstrip().startswith("ls") or not any(
                    _ABSENT.search(str(c.get("regex") or "")) for c in _criteria_blocks(check)):
                continue
            check_vars = set(_VARS.findall(send))
            uploads = [s for s in steps[:steps.index(check)] if isinstance(s, dict)
                       and isinstance(s.get("sftp"), dict) and s["sftp"].get("operation") == "upload"
                       and s.get("node") == check.get("node") and check_vars
                       and check_vars <= set(_VARS.findall(str(s["sftp"].get("remote_path") or "")))]
            if uploads:
                steps.remove(check)
                steps.insert(steps.index(uploads[0]), check)
                moved += 1
                logger.info("Moved absence check %r before the upload that produces the file", send[:60])
    return moved


_COND_TERM = re.compile(r"^\s*configSequences\.\d+\.records\.\d+\.data\.(.+?)\s*(==|!=)\s*''\s*$")


def _guard_terms(when: str) -> list[tuple[str, str]]:
    """[(column, '==' | '!=')] of a guard made only of ``<col> ==/!= ''`` terms joined by &&."""
    terms = []
    for part in str(when or "").split("&&"):
        m = _COND_TERM.match(part)
        if not m:
            return []
        terms.append((m.group(1).strip(), m.group(2)))
    return terms


def unify_alternative_columns(parsed: dict) -> int:
    """One value for every CIQ row when rows give column A or an alternative (DEP-41).

    The LLM looked the IMSI up via the MSISDN into ``resolvedImsi`` for rows without a Test
    IMSI, but kept every IMSI step on ``${…data.Test IMSI}`` guarded by ``Test IMSI != ''``:
    rows like EAST never ran them. When a step registers exactly one variable V only for
    rows where A is empty, steps guarded by ``A != ''`` that use ``${…data.A}`` use ``${V}``
    unguarded, V is registered from A for rows that give A, and the lookup runs first.
    """
    nodes = parsed.get("nodes") or {}
    local = next((n for n in nodes if str(n).upper() == "LOCAL"), None)
    changed = 0
    for body in (parsed.get("phases") or {}).values():
        steps = body.get("steps") if isinstance(body, dict) else None
        if not isinstance(steps, list):
            continue
        for lookup in [s for s in steps if isinstance(s, dict)]:
            terms = _guard_terms(lookup.get("when"))
            empty = [c for c, op in terms if op == "=="]
            names = [n for r in lookup.get("register") or [] if isinstance(r, dict)
                     for n in re.findall(r"\(\?P?<([A-Za-z][A-Za-z0-9]*)>", str(r.get("regex") or ""))]
            # A lookup derives V from an alternative column B the row gives (guard B != '',
            # command uses ${…data.B}); steps of the workflow's own branch use ${V} instead
            given = [c for c, op in terms if op == "!=" and re.search(
                r"\$\{configSequences\.\d+\.records\.\d+\.data\." + re.escape(c) + r"\s*\}",
                str(lookup.get("send") or ""))]
            if len(empty) != 1 or len(set(names)) != 1 or not given:
                continue
            column, var = empty[0], names[0]
            ref = re.compile(r"\$\{configSequences\.(\d+)\.records\.(\d+)\.data\." + re.escape(column) + r"\s*\}")
            # (a step that registers V from A itself — the one inserted below — is not a user)
            users = [s for s in steps if isinstance(s, dict) and s is not lookup
                     and ref.search(str(s.get("send") or "")) and (column, "!=") in _guard_terms(s.get("when"))
                     and f"<{var}>" not in str(s.get("register") or "")]
            # The workflow already has its own branch for the rows without A (steps guarded
            # A == '' that use ${V}): leave it as written
            own_branch = any(s is not lookup and isinstance(s, dict) and (column, "==") in _guard_terms(s.get("when"))
                             and "${" + var + "}" in str(s.get("send") or "") for s in steps)
            if not users or own_branch:
                continue
            for s in users:
                s["send"] = ref.sub("${" + var + "}", str(s["send"]))
                rest = [t for t in _guard_terms(s["when"]) if t != (column, "!=")]
                if rest:
                    s["when"] = " && ".join(f"configSequences.0.records.0.data.{c} {op} ''" for c, op in rest)
                else:
                    s.pop("when", None)
            first = min(steps.index(s) for s in users)
            if steps.index(lookup) > first:
                steps.remove(lookup)
                steps.insert(first, lookup)
            path = f"configSequences.0.records.0.data.{column}"
            steps.insert(steps.index(lookup), {
                "node": local or lookup.get("node"),
                "send": "echo ${" + path + "}",
                "command_description": f"Use {column} as {var} when the CIQ row gives it",
                "when": f"{path} != ''",
                "register": [{"regex": f"(?<{var}>\\S+)"}],
                "validation": {"description": f"{column} is given",
                               "success": {"message": f"{var} set from {column}"},
                               "failure": {"message": f"{column} missing"}},
            })
            changed += len(users)
            logger.info("Rows without %s get %s from a lookup: %d steps now use ${%s}", column, var, len(users), var)
    return changed


_LS_ONE_FILE = re.compile(r"^\s*ls\s+-\w*l\w*\s+['\"]?(/\S*?/([^/\s'\"]*\.[^/\s'\"]+))['\"]?(?:\s+2>&1)?(?:\s*\|\|\s*true)?\s*$")


def fix_single_file_listings(parsed: dict) -> int:
    """``ls -l <one file>`` prints just the file's line, never ``total N``; the LLM kept
    writing ``total\\s+\\d+…`` even after the validator's feedback, and the poll failed on
    every node (DEP-39). Such a regex becomes the file's long-listing line (non-empty when
    the step talks about an empty file)."""
    changed = 0
    for _, step in _walk_all_steps(parsed.get("phases") or {}):
        m = _LS_ONE_FILE.match(str(step.get("send") or ""))
        if not m:
            continue
        name = re.sub(r"\\\$\\\{[^}]*\\\}", ".+?", re.escape(m.group(2)))
        about = json_text(step.get("validation")).lower()
        size = r"[1-9]\d*" if "empty" in about else r"\d+"
        for criteria in _criteria_blocks(step):
            regex = str(criteria.get("regex") or "")
            if re.search(r"(?<![A-Za-z])total\b", regex):
                criteria["regex"] = rf"^[-l][rwxsStT-]{{9}}\S*\s+\d+\s+\S+\s+\S+\s+{size}\s+.+{name}\s*$"
                changed += 1
    return changed


def json_text(value) -> str:
    import json
    return json.dumps(value, default=str) if value is not None else ""


def _walk_all_steps(phases):
    bodies = phases.items() if isinstance(phases, dict) else enumerate(phases or [])
    for name, body in bodies:
        stack = list((body.get("steps") if isinstance(body, dict) else body) or [])
        while stack:
            step = stack.pop(0)
            if isinstance(step, dict):
                yield name, step
                stack[0:0] = list(step.get("steps") or [])


_SU = re.compile(r"^\s*(?:sudo\s+)?su\b|^\s*sudo\s+-[is]\b")
_SESSION_CLOSED = re.compile(r"(?i)connection\b.*\bclosed|logout|closed\.")


def drop_session_exits(parsed: dict) -> int:
    """Drop ``exit`` steps that leave no ``su`` on their node: they close the login session
    the JAR opened and manages. An ``exit`` that leaves an ``su`` but waits for
    "Connection to .* closed." (printed by a local ssh client, never by the node) waits for
    the node's prompt instead (DEP-33)."""
    phases = parsed.get("phases") or {}
    bodies = list(phases.values()) if isinstance(phases, dict) else list(phases)
    nodes = parsed.get("nodes") or {}
    depth: dict[str, int] = {}
    dropped = 0

    def walk(steps):
        nonlocal dropped
        for step in list(steps or []):
            if not isinstance(step, dict):
                continue
            node = str(step.get("node") or "")
            send = str(step.get("send") or "").strip()
            if node and node.upper() != "LOCAL":
                if _SU.match(send):
                    depth[node] = depth.get(node, 0) + 1
                elif send in ("exit", "logout"):
                    if depth.get(node, 0) > 0:
                        depth[node] -= 1
                        # Leaving su returns to the node's own prompt, not "Connection … closed"
                        if _SESSION_CLOSED.search(str(step.get("prompt_regex") or "")):
                            node_regex = (nodes.get(node) or {}).get("prompt_regex") if isinstance(nodes, dict) else None
                            if node_regex:
                                step["prompt_regex"] = node_regex
                            else:
                                step.pop("prompt_regex", None)
                            for criteria in _criteria_blocks(step):
                                if _SESSION_CLOSED.search(str(criteria.get("regex") or "")):
                                    criteria.pop("regex", None)
                    else:
                        steps.remove(step)
                        dropped += 1
                        logger.info("Dropped '%s' on %s: no su to leave, it would close the session", send, node)
                        continue
            walk(step.get("steps"))

    for body in bodies:
        walk(body.get("steps") if isinstance(body, dict) else body)
    return dropped


def fix_missing_downloads(parsed: dict) -> int:
    """Insert the sftp download a LOCAL step needs before it (the node and path are known
    from the step that lists the file there); absolute remote paths in the LOCAL command
    are pointed at the local copy."""
    hits = remote_file_reads(parsed)
    for hit in reversed(hits):  # later inserts first: indices stay valid
        steps, i = hit["steps"], hit["index"]
        step = steps[i]
        if hit["token"].startswith("/"):
            step["send"] = str(step["send"]).replace(hit["token"], hit["local_name"])
        download = {
            "node": hit["node"],
            "sftp": {"operation": "download", "remote_path": hit["remote_path"], "local_path": hit["local_name"],
                     "overwrite": True},
            "command_description": f"Download {hit['local_name']} from {hit['node']} for the local step",
            "validation": {"description": "File transfer successful",
                           "success": {"criteria": {}, "message": f"{hit['local_name']} downloaded."},
                           "failure": {"message": f"Failed to download {hit['local_name']}."}},
        }
        if step.get("when"):
            download["when"] = step["when"]
        steps.insert(i, download)
        logger.info("Inserted sftp download of %s from %s before LOCAL step", hit["remote_path"], hit["node"])
    return len(hits)


def _insert_fallback_register(parsed: dict, var: str, column: str) -> None:
    """Before the first use of ``${var}``: when the CIQ column is filled, register var from
    it (the expression's "else" branch, as when-guarded steps the JAR supports)."""
    path = f"configSequences.0.records.0.data.{column}"
    nodes = parsed.get("nodes") or {}
    local = next((n for n in nodes if str(n).upper() == "LOCAL"), None)
    for body in (parsed.get("phases") or {}).values():
        steps = body.get("steps") if isinstance(body, dict) else None
        if not isinstance(steps, list):
            continue
        for i, step in enumerate(steps):
            if isinstance(step, dict) and "${" + var + "}" in str(step.get("send") or ""):
                steps.insert(i, {
                    "node": local or step.get("node"),
                    "send": "echo ${" + path + "}",
                    "command_description": f"Use {column} as {var} when the CIQ row gives it",
                    "when": f"{path} != ''",
                    "register": [{"regex": f"(?<{var}>\\S+)"}],
                })
                return


def rename_expr_vars(phases: Any, renames: dict[str, str]) -> None:
    """Apply register group renames (fix_named_groups) to expr identifiers too."""
    if not renames:
        return

    def walk(steps):
        for step in steps or []:
            if not isinstance(step, dict):
                continue
            for criteria in _criteria_blocks(step):
                if isinstance(criteria.get("expr"), str):
                    criteria["expr"] = EXPR_IDENT.sub(lambda m: renames.get(m.group(1), m.group(1)),
                                                      criteria["expr"])
            walk(step.get("steps"))

    bodies = phases.values() if isinstance(phases, dict) else (phases or [])
    for body in bodies:
        walk(body.get("steps") if isinstance(body, dict) else body)


def is_placeholder(value: Any) -> bool:
    """Check if a value is a runtime placeholder like ${nodeGroups.RDS.host}."""
    if not isinstance(value, str):
        return False
    return bool(re.search(r"\$\{.+?\}", value) or (value.startswith("$") and not value.startswith("${")))


def _group_body(regex: str, name: str) -> tuple[int, int] | None:
    """(start, end) of the body of the named group ``(?<name>…)`` in ``regex``."""
    m = re.search(r"\(\?P?<" + re.escape(name) + r">", regex)
    if not m:
        return None
    depth, i, in_class = 1, m.end(), False
    while i < len(regex):
        ch = regex[i]
        if ch == "\\":
            i += 2
            continue
        if in_class:
            in_class = ch != "]"
        elif ch == "[":
            in_class = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return m.end(), i
        i += 1
    return None


# a group body that can capture a space: unescaped ".", \s, [\s\S], a negated class, a space
_SPACE_CAPABLE = re.compile(r"(?<!\\)\.|\\s|\[\^[^\]]*\]| ")


def single_token_condition_vars(parsed: dict) -> int:
    r"""A registered value holding a space makes every `when` that reads it false (cli-automation,
    probed 2026-11-03: `spaced != ''` and `'${spaced}' != ''` both skip) — MRF's backup gate
    `announcementFiles` captured `(.*)` ("total 16K") and the backup never ran. A variable a
    condition reads registers one token: its group body becomes ``\S+``."""
    phases = parsed.get("phases") or {}
    steps = [s for _, s in _walk_all_steps(phases)]
    in_when = set()
    for s in steps:
        if isinstance(s.get("when"), str):
            in_when |= set(re.findall(r"(?<![\w.$'{])([A-Za-z][A-Za-z0-9]*)(?![\w.(])", s["when"]))
            in_when |= set(re.findall(r"\$\{([A-Za-z][A-Za-z0-9]*)\}", s["when"]))
    changed = 0
    for s in steps:
        for reg in s.get("register") or []:
            if not isinstance(reg, dict) or not isinstance(reg.get("regex"), str):
                continue
            for name in in_when:
                span = _group_body(reg["regex"], name)
                if span and _SPACE_CAPABLE.search(reg["regex"][span[0]:span[1]]):
                    reg["regex"] = reg["regex"][:span[0]] + r"\S+" + reg["regex"][span[1]:]
                    changed += 1
                    logger.info("Register %s read by a condition: one token (\\S+)", name)
    return changed
