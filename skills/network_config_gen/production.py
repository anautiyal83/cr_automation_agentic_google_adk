"""Production format (spec 013) — the last pass of ``structure.apply``: the workflow is written the way the
production workflows bundled in the cli-automation JAR are (specs/013-production-workflow-format/production-style.md).

- file: ``version: 1.2``, ``name``, ``globals.defaults``; key order version, name, globals, nodes, phases
- phases: production keys (``activity_configuration`` …) and UPPER_SNAKE names; the generated name moves to
  ``description``
- expressions: ``when`` / ``skip_when`` / ``break_when`` / ``continue_when`` / ``criteria.expr`` / computed
  ``register.when`` as one ``${…}`` with double-quoted literals — the JAR evaluates them exactly like the plain
  form the rest of step 6 works with (probed 2026-11-11, spec 013); ``plain`` turns them back for ``flatten``
- steps: production field order; every validation ``enabled: true`` with a ``description``

Behaviour is unchanged: only the form differs.
"""
from __future__ import annotations

import re
from typing import Any

VERSION = 1.2
DEFAULTS = {"timeout_sec": 120, "interaction_timeout_sec": 180, "log_level": "detailed", "on_failure": "stop"}
_EXPR_KEYS = ("when", "skip_when", "break_when", "continue_when")
_STEP_ORDER = ("id", "type", "node", "command_description", "send", "sftp", "rest", "for_each", "item_var",
               "reverse_order", "when", "skip_when", "break_when", "continue_when", "hide_when_skipped",
               "prompt_regex", "on_failure", "use_exit_code", "timeout_sec", "retries", "retry_delay_sec",
               "retry_on", "on_failure_message", "expect_reply", "register", "validation", "steps")
_LITERAL_SQ = re.compile(r"'([^'\"]*)'")
_LITERAL_DQ = re.compile(r'"([^"\']*)"')


# --------------------------------------------------------------------------- expressions

def _whole(expr: str) -> bool:
    """``${…}`` around the whole expression (not ``${a} > ${b}``)."""
    if not (expr.startswith("${") and expr.endswith("}")):
        return False
    depth = 0
    for i, ch in enumerate(expr):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and i != len(expr) - 1:
                return False
    return True


def wrap(expr: Any) -> Any:
    """``A != '' && B == 'x'`` → ``${A != "" && B == "x"}``."""
    if not isinstance(expr, str) or not expr.strip():
        return expr
    e = expr.strip()
    if _whole(e) or "${" in e:  # already production form, or a placeholder the JAR fills before evaluating
        return e
    return "${" + _LITERAL_SQ.sub(r'"\1"', e) + "}"


def unwrap(expr: Any) -> Any:
    """``${A != ""}`` → ``A != ''`` (the form autofix, validator and contract read)."""
    if not isinstance(expr, str):
        return expr
    e = expr.strip()
    if not _whole(e) or re.fullmatch(r"\$\{[\w.-]+\}", e):  # a lone ${X} stays a placeholder
        return expr
    return _LITERAL_DQ.sub(r"'\1'", e[2:-1].strip())


def _expressions(parsed: dict, fn) -> int:
    changed = 0

    def put(d: dict, key: str) -> None:
        nonlocal changed
        if key in d:
            new = fn(d[key])
            if new != d[key]:
                d[key] = new
                changed += 1

    def walk(steps: Any) -> None:
        for step in steps or []:
            if not isinstance(step, dict):
                continue
            for k in _EXPR_KEYS:
                put(step, k)
            v = step.get("validation")
            for branch in ("success", "failure", "warning"):
                crit = (v.get(branch) or {}).get("criteria") if isinstance(v, dict) and isinstance(v.get(branch), dict) else None
                if isinstance(crit, dict):
                    put(crit, "expr")
            for reg in step.get("register") or []:
                if isinstance(reg, dict) and "name" in reg:
                    put(reg, "when")
            walk(step.get("steps"))

    for body in (parsed.get("phases") or {}).values():
        if isinstance(body, dict):
            for k in _EXPR_KEYS:
                put(body, k)
            walk(body.get("steps"))
    return changed


def plain(parsed: dict) -> int:
    """Undo the expression form (``structure.flatten``)."""
    return _expressions(parsed, unwrap)


# --------------------------------------------------------------------------- file / phases / steps

def _kebab(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _reorder(d: dict, order: tuple) -> bool:
    keys = list(d.keys())
    want = [k for k in order if k in d] + [k for k in keys if k not in order]
    if keys == want:
        return False
    items = [(k, d[k]) for k in want]
    d.clear()
    d.update(items)
    return True


def _file(parsed: dict, uc_name: str) -> int:
    changed = 0
    if parsed.get("version") != VERSION:
        parsed["version"] = VERSION
        changed += 1
    if uc_name and not parsed.get("name"):
        parsed["name"] = _kebab(uc_name)
        changed += 1
    globals_ = parsed.get("globals")
    if not isinstance(globals_, dict):
        globals_ = parsed["globals"] = {}
    defaults = globals_.get("defaults")
    if not isinstance(defaults, dict):
        defaults = globals_["defaults"] = {}
    for k, v in DEFAULTS.items():
        if k not in defaults:
            defaults[k] = v
            changed += 1
    _reorder(globals_, ("vars", "defaults"))
    return changed + _reorder(parsed, ("version", "name", "globals", "nodes", "phases"))


def _phases(parsed: dict) -> int:
    from shared import run_options
    phases = parsed.get("phases")
    if not isinstance(phases, dict):
        return 0
    changed, new = 0, {}
    for key, body in phases.items():
        if not isinstance(body, dict):
            new[key] = body
            continue
        model = run_options.canonical_phase(str(key), body)
        wkey, wname = run_options.WORKFLOW_KEYS[model], run_options.WORKFLOW_NAMES[model]
        if body.get("name") != wname:
            if body.get("name") and not body.get("description"):
                body["description"] = body["name"]
            body["name"] = wname
            changed += 1
        body.setdefault("description", run_options.PURPOSE[model])
        _reorder(body, ("name", "description", "when", "on_failure", "steps"))
        if wkey in new:  # two generated phases of one model phase (select_phases merges them first)
            new[wkey]["steps"] = list(new[wkey].get("steps") or []) + list(body.get("steps") or [])
        else:
            new[wkey] = body
        changed += wkey != key
    parsed["phases"] = new
    return changed


def _steps(parsed: dict) -> int:
    changed = 0

    def walk(steps: Any) -> None:
        nonlocal changed
        for step in steps or []:
            if not isinstance(step, dict):
                continue
            # Explicit, as production writes it: what the JAR does without one is stop (probed 2026-11-11;
            # the MOP's "conditions" text wrongly said "continue")
            if "on_failure" not in step:
                step["on_failure"] = "stop"
                changed += 1
            v = step.get("validation")
            if isinstance(v, dict):
                if v.get("enabled") is not True:
                    v["enabled"] = True
                    changed += 1
                if not v.get("description") and step.get("command_description"):
                    v["description"] = step["command_description"]
                    changed += 1
                _reorder(v, ("enabled", "description", "success", "failure", "warning"))
            changed += _reorder(step, _STEP_ORDER)
            walk(step.get("steps"))

    for body in (parsed.get("phases") or {}).values():
        if isinstance(body, dict):
            walk(body.get("steps"))
    return changed


LOCAL = "local"


def _local(parsed: dict) -> int:
    """Automation-server steps on ``node: local`` — the framework provides it, production declares none;
    loops carry no node."""
    nodes = parsed.get("nodes") if isinstance(parsed.get("nodes"), dict) else {}
    names = {str(n) for n, v in nodes.items()
             if isinstance(v, dict) and LOCAL in (str(v.get("type", "")).lower(), str(v.get("plugin", "")).lower())}
    names |= {"LOCAL"}
    changed = 0

    def walk(steps: Any) -> None:
        nonlocal changed
        for step in steps or []:
            if not isinstance(step, dict):
                continue
            if step.get("type") == "loop" and "node" in step:
                del step["node"]
                changed += 1
            elif str(step.get("node", "")) in names:
                step["node"] = LOCAL
                changed += 1
            walk(step.get("steps"))

    for body in (parsed.get("phases") or {}).values():
        if isinstance(body, dict):
            walk(body.get("steps"))
    for n in names:
        if n in nodes:
            del nodes[n]
            changed += 1
    return changed


def apply(parsed: dict, uc_name: str = "") -> int:
    """The production form; idempotent. Returns the number of changes."""
    changed = _local(parsed)
    changed += _file(parsed, uc_name)
    changed += _phases(parsed)
    changed += _steps(parsed)
    changed += _expressions(parsed, wrap)
    return changed
