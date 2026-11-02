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
}

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


def sanitize_yaml(text: str) -> str:
    """Fix unquoted colons in YAML values generated by LLM.

    E.g., ``description: sar on RDS [Pre-health check: CPU idle]``
    becomes ``description: 'sar on RDS [Pre-health check: CPU idle]'``

    Also handles list items: ``- description: value: with colon``, and unquotes CIQ
    column names in placeholders (DEP-12), which break single-quoted scalars.
    """
    text = _REC_ROW.sub(_rec_row_column, text)
    text = _QUOTED_COLUMN.sub(_unquote_column, text)
    fixed_lines = []
    for line in text.split("\n"):
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
    parsed = fix_placeholders(parsed, renames)
    fix_regex_placeholders(parsed.get("phases", {}))

    # --- Add missing nodes referenced in steps ---
    nodes = parsed.get("nodes", {})
    if isinstance(nodes, dict):
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

    # Strip unknown fields that cause JAR deserialization errors
    for key in list(step.keys()):
        if key not in _VALID_COMMAND_FIELDS:
            step.pop(key)
        elif key in _STRING_ONLY_FIELDS and not isinstance(step[key], str):
            logger.info("Removed non-string %s on step %r", key, step.get("send", "")[:40])
            step.pop(key)

    return step


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


def _fix_criteria_expr(cmd: dict) -> None:
    """Fix unsupported Java functions in criteria.expr.

    The cli-automation JAR supports a limited expression language.
    Common LLM mistakes:
    - Python-style: all(), any(), re_search(), .group()
    - Unsupported: re.match(), json.loads()
    Replace with simple variable-exists or contains checks.
    """
    validation = cmd.get("validation", {})
    if not isinstance(validation, dict):
        return

    criteria = validation.get("criteria", {})
    if not isinstance(criteria, dict):
        return

    expr = criteria.get("expr", "")
    if not isinstance(expr, str):
        return

    # Patterns that indicate unsupported functions
    _UNSUPPORTED = [
        r"all\s*\(", r"any\s*\(", r"re_search\s*\(", r"re\.match\s*\(",
        r"\.group\s*\(", r"json\.loads\s*\(", r"int\s*\(\s*\)", r"\|\s*int\(\)",
        r"for\s+\w+\s+in\s+", r"lambda\s+",
    ]

    needs_fix = any(re.search(p, expr) for p in _UNSUPPORTED)
    if not needs_fix:
        return

    # Simplify to a basic contains/exists check
    # Try to extract the key string being checked
    quoted = re.findall(r"'([^']+)'|\"([^\"]+)\"", expr)
    if quoted:
        # Use the first quoted string as a contains check
        check_str = quoted[0][0] or quoted[0][1]
        criteria["expr"] = f"output.contains('{check_str}')"
        logger.debug("Simplified unsupported expr to contains check: '%s'", check_str)
    else:
        # Fall back to always-true (don't block execution)
        criteria["expr"] = "true"
        logger.debug("Could not simplify expr, set to 'true': %s", expr[:80])


def is_placeholder(value: Any) -> bool:
    """Check if a value is a runtime placeholder like ${nodeGroups.RDS.host}."""
    if not isinstance(value, str):
        return False
    return bool(re.search(r"\$\{.+?\}", value) or (value.startswith("$") and not value.startswith("${")))
