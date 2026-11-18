"""Production format (spec 013) — the last pass of ``structure.apply``: the workflow is written the way the
production workflows bundled in the cli-automation JAR are (specs/013-production-workflow-format/production-style.md).

- file: ``version: 1.2``, ``name``, ``globals.defaults``; key order version, name, globals, nodes, phases
- phases: production keys (``activity_configuration`` …) and UPPER_SNAKE names; the generated name moves to
  ``description``
- expressions: ``when`` / ``skip_when`` / ``break_when`` / ``continue_when`` / ``criteria.expr`` / computed
  ``register.when`` as one ``${…}`` with double-quoted literals — the JAR evaluates them exactly like the plain
  form the rest of step 6 works with (probed 2026-11-11, spec 013); ``plain`` turns them back for ``flatten``
- steps: production field order; every validation ``enabled: true`` with a ``description``
- paths: every folder a variable in ``globals.vars`` (``paths``: scripts, the order's work folder, node folders)

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
    """Undo NIAM failover and the expression form (``structure.flatten``)."""
    from . import failover, paths
    changed = paths.undo(parsed) + failover.undo(parsed) + _expressions(parsed, unwrap)
    vars_ = (parsed.get("globals") or {}).get("vars")
    if isinstance(vars_, dict) and vars_.get("order_number") == "${ORDER_NO}":  # added by _file
        del vars_["order_number"]
        changed += 1
    return changed


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
    vars_ = globals_.get("vars") if isinstance(globals_.get("vars"), dict) else globals_.setdefault("vars", {})
    from core.config import runtime_params
    if "ORDER_NO" in runtime_params() and vars_.get("order_number") != "${ORDER_NO}":
        vars_["order_number"] = "${ORDER_NO}"
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
# A NIAM node logs in as production's do: the framework passes the NIAM gateway and its M2M account at run time
NIAM_LOGIN = {"host": "${NIAM_IP}", "port": "${M2MPORT}", "user": "${M2MUSER}"}
NIAM_SSH = {"connect_timeout_sec": 300, "read_timeout_sec": 300, "keepalive_sec": 30, "login_retries": 2,
            "login_retry_delay_sec": 15, "strict_host_key_checking": False}


def _niam_nodes(parsed: dict) -> int:
    """``niam_*`` nodes: login fields as production (``ne_id`` keeps the CIQ reference — spec 013 PF-D1); the
    node type's own settings (kex / host key algorithms, elevate_to_root) stay."""
    changed = 0
    for name, spec in (parsed.get("nodes") or {}).items():
        if not str(name).startswith("niam_") or not isinstance(spec, dict):
            continue
        if not isinstance(spec.get("ssh"), dict):
            spec["ssh"] = {}
        ssh = spec["ssh"]
        if not ssh.get("ne_id") and "niamID" in str(ssh.get("host")):
            ssh["ne_id"] = ssh["host"]
        want = {**NIAM_LOGIN, "auth": {"password": "${M2MPASSWORD}"}}
        for k, v in want.items():
            if ssh.get(k) != v:
                ssh[k] = v
                changed += 1
        for k, v in NIAM_SSH.items():
            if k not in ssh:
                ssh[k] = v
                changed += 1
        spec.setdefault("category", "internal")
        _reorder(spec, ("type", "plugin", "prompt_regex", "category", "ssh"))
        _reorder(ssh, ("host", "port", "user", "auth", "ne_id", "connect_timeout_sec", "read_timeout_sec",
                       "keepalive_sec", "login_retries", "login_retry_delay_sec", "strict_host_key_checking"))
    return changed


def _ssh_host_keys(parsed: dict) -> int:
    """Every ssh / sftp node accepts the node's host key (``strict_host_key_checking: false``, as production and
    the NIAM nodes): the automation server has no known_hosts entry for a node it reaches for the first time
    (user 2026-11-13, OB-64)."""
    changed = 0
    for spec in (parsed.get("nodes") or {}).values():
        for section in ("ssh", "sftp"):
            block = spec.get(section) if isinstance(spec, dict) else None
            if isinstance(block, dict) and "strict_host_key_checking" not in block and not block.get("known_hosts"):
                block["strict_host_key_checking"] = False
                changed += 1
    return changed


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


def apply(parsed: dict, uc_name: str = "", node_types: dict[str, int] | None = None) -> int:
    """The production form; idempotent. Returns the number of changes."""
    from . import failover
    changed = failover.apply(parsed, node_types)  # after the contract: the probes keep on_failure: warning
    changed += _local(parsed)
    changed += _niam_nodes(parsed)
    changed += _ssh_host_keys(parsed)
    changed += _file(parsed, uc_name)
    from . import paths
    changed += paths.apply(parsed, uc_name)  # every folder a variable at the top (user 2026-11-18)
    changed += _phases(parsed)
    changed += _steps(parsed)
    changed += _expressions(parsed, wrap)
    return changed
