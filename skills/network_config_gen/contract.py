"""Execution contract (spec 011) — the default failure, abort and rollback rules of every workflow.

Applied deterministically after the LLM (``structure.apply``) and removed again by ``strip``
(``structure.flatten``), so the validator and the regenerate prompt see what the LLM wrote.

Rules (user, 2026-11-04 — unless the UC's documents say otherwise):

1. PreNodeHealthCheck / PostNodeHealthCheck run completely even if a command fails (a step later
   steps depend on — a user switch, a value they use — stops the phase).
2. A failed PreNodeHealthCheck command aborts every next phase (``PRECHECK_FAILED``).
3. A failed Backup aborts every next phase (``BACKUP_FAILED``).
4. A failed Activity command stops the activity; only Rollback starts, and only when the node was
   changed (``CHANGES_MADE`` → ``ROLLBACK_REQUIRED``).
5. Rollback runs on ``ROLLBACK_REQUIRED == 'true'`` or the runtime parameter ``ROLLBACK_ONLY``.

JAR-verified (research.md): flags in ``globals.vars``, set by validation branch ``vars`` (also on
exit-code failures, ``${var}`` values resolved), read by phase ``when``; after a stop the JAR runs only
phases whose ``name`` contains "rollback".

A change on the node (E-D3) = writing outside a temporary directory or changing configuration on a
remote node; staging in ``/tmp`` is not a change, an unknown destination is.
"""
from __future__ import annotations

import re
import shlex
from typing import Any

FLAGS = ("ROLLBACK_ONLY", "PRECHECK_FAILED", "BACKUP_FAILED", "CHANGES_MADE", "ROLLBACK_REQUIRED")
DESCRIPTION = "Execution contract (spec 011)"

PRE, BACKUP, ACTIVITY, POST, ROLLBACK = "pre_health", "backup", "activity", "post_health", "rollback"
_NOT_RB = "ROLLBACK_ONLY != 'true'"
_OK_BEFORE = "PRECHECK_FAILED != 'true' && BACKUP_FAILED != 'true'"
PHASE_WHEN = {
    PRE: _NOT_RB,
    BACKUP: f"{_NOT_RB} && PRECHECK_FAILED != 'true'",
    ACTIVITY: f"{_NOT_RB} && {_OK_BEFORE}",
    POST: f"{_NOT_RB} && {_OK_BEFORE} && ROLLBACK_REQUIRED != 'true'",
    ROLLBACK: "ROLLBACK_REQUIRED == 'true' || ROLLBACK_ONLY == 'true'",
}

_ROLLBACK_NAME = re.compile(r"rollback|fallback|restore|revert", re.I)
_TEMP_DIRS = ("/tmp", "/var/tmp", "/dev/shm")
_CONFIG_CMDS = re.compile(r"^(ldapmodify|ldapadd|ldapdelete|crontab|useradd|usermod|userdel|passwd|reboot|"
                          r"shutdown|kill|pkill)$")
_SERVICE = re.compile(r"\b(systemctl|service)\b.*\b(start|stop|restart|reload|enable|disable)\b")
_PATH_CMDS = re.compile(r"^(cp|mv|rm|rmdir|chmod|chown|chgrp|mkdir|ln|touch|truncate|install|unzip|gunzip|dd)$")
_REDIRECT = re.compile(r"(?<![0-9&])>>?\s*([^\s;&|]+)")
_SEGMENTS = re.compile(r"\s*(?:;|&&|\|\||\||\n)\s*")


# --------------------------------------------------------------------------- classification

def phase_kind(key: str, body: dict) -> str:
    """pre_health | backup | activity | post_health | rollback (key and name together)."""
    text = f"{key} {body.get('name') or ''}"
    if _ROLLBACK_NAME.search(text):
        return ROLLBACK
    if re.search(r"backup", text, re.I):
        return BACKUP
    # a health check, or a pre/post check that is not the activity's own (ActivityPrecheck)
    if re.search(r"health", text, re.I) or (re.search(r"check", text, re.I) and not re.search(r"activity", text, re.I)):
        if re.search(r"post", text, re.I):
            return POST
        if re.search(r"pre", text, re.I):
            return PRE
    return ACTIVITY


def _is_temp(path: str) -> bool:
    p = path.strip("'\"")
    return any(p == d or p.startswith(d + "/") for d in _TEMP_DIRS)


def _words(segment: str) -> list[str]:
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return segment.split()


def _segment_changes(segment: str) -> bool:
    """One shell command: does it write outside a temporary directory or change configuration?"""
    seg = segment.strip()
    if not seg:
        return False
    for target in _REDIRECT.findall(seg):
        if target not in ("/dev/null",) and not _is_temp(target):
            return True
    if _SERVICE.search(seg):
        return True
    words = [w for w in _words(seg) if w not in ("sudo", "\\cp")] or _words(seg)
    if not words:
        return False
    cmd = words[0].lstrip("\\")
    args = [w for w in words[1:] if not w.startswith("-")]
    if _CONFIG_CMDS.match(cmd):
        return True
    if cmd == "sed" and any(w.startswith("-i") for w in words[1:]):
        return True
    if cmd == "tar":
        flags = "".join(w.lstrip("-") for w in words[1:2])
        if "x" not in flags:  # listing / creating an archive (a backup) changes nothing
            return False
        dest = next((words[i + 1] for i, w in enumerate(words[:-1]) if w in ("-C", "--directory")), None)
        return dest is None or not _is_temp(dest)
    if _PATH_CMDS.match(cmd):
        if cmd in ("cp", "mv", "install", "ln"):
            targets = args[-1:]  # the destination
        elif cmd == "unzip":
            dest = next((words[i + 1] for i, w in enumerate(words[:-1]) if w == "-d"), None)
            targets = [dest] if dest else []
        else:
            targets = args
        return not targets or any(not _is_temp(t) for t in targets)
    return False


_REPO_NODE = re.compile(r"repo|backup", re.I)
_TO_NODE = re.compile(r"^\s*(?:scp\b.*\s|sftp\s+)\S*@(?:\[[^\]]*\]|[^\s:@]+):(\S+)(?:\s+<<<\s*'put\s+\S+(?:\s+(\S+))?')?\s*$")


def _copy_to_node(send: str) -> str | None:
    """The node path a repo-server ``scp <src> user@[node]:<path>`` / ``sftp user@[node]:<dir> <<<
    'put <src> [<name>]'`` writes; None for anything else (a download, a command on the repo)."""
    m = _TO_NODE.match(send)
    if not m or (send.lstrip().startswith("sftp") and "<<<" not in send):
        return None
    return f"{m.group(1).rstrip('/')}/{m.group(2)}" if m.group(2) else m.group(1)


def changes_node(step: dict, local_nodes: set[str], repo_nodes: set[str] = frozenset()) -> bool:
    """A step on a remote node that changes how the node works (E-D3). On the repo server only a
    copy onto a node outside the temp directories does (spec 010 O-D7)."""
    if not isinstance(step, dict) or str(step.get("node", "")) in local_nodes:
        return False
    if str(step.get("node", "")) in repo_nodes:
        target = _copy_to_node(str(step.get("send") or ""))
        return target is not None and not _is_temp(target)
    sftp = step.get("sftp")
    if isinstance(sftp, dict):
        return str(sftp.get("operation", "")).lower() == "upload" and not _is_temp(str(sftp.get("remote_path", "")))
    send = str(step.get("send") or "")
    return any(_segment_changes(s) for s in _SEGMENTS.split(send))


def _is_user_switch(step: dict) -> bool:
    return bool(re.match(r"^\s*su(\s+-)?\s+\S+", str(step.get("send") or "")))


def _registered(step: dict) -> set[str]:
    names: set[str] = set()
    for reg in step.get("register") or []:
        rx = reg.get("regex") if isinstance(reg, dict) else reg
        names |= set(re.findall(r"\(\?<([A-Za-z][A-Za-z0-9]*)>", str(rx or "")))
    return names


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_text(v) for k, v in value.items() if k != "register")
    if isinstance(value, list):
        return " ".join(_text(v) for v in value)
    return ""


# --------------------------------------------------------------------------- apply / strip

def _leaves(steps: list):
    for step in steps:
        if not isinstance(step, dict):
            continue
        if isinstance(step.get("steps"), list):
            yield from _leaves(step["steps"])
        else:
            yield step


def _containers(steps: list):
    for step in steps:
        if isinstance(step, dict) and isinstance(step.get("steps"), list):
            yield step
            yield from _containers(step["steps"])


def combine(expr: str, own: str) -> str:
    """``(expr) && (own)`` without parentheses — the JAR does not parse them (a phase with them never
    runs); it does give ``&&`` precedence over ``||`` (probed): expanded into OR-of-AND terms."""
    own = (own or "").strip()
    if not own:
        return expr
    return " || ".join(f"{c.strip()} && {o.strip()}" for c in expr.split("||") for o in own.split("||"))


def split_own(when: str) -> tuple[str, str] | None:
    """(contract expression, the LLM's own part) of a ``combine`` result; None when ``when`` is not one."""
    when = (when or "").strip()
    for expr in sorted(PHASE_WHEN.values(), key=len, reverse=True):  # longest first: they share prefixes
        if when == expr:
            return expr, ""
        heads = [c.strip() + " && " for c in expr.split("||")]
        terms = [t.strip() for t in when.split("||")]
        rest = []
        for term in terms:
            head = next((h for h in heads if term.startswith(h)), None)
            if head is None:
                break
            if term[len(head):] not in rest:
                rest.append(term[len(head):])
        else:
            if rest and len(terms) == len(heads) * len(rest):
                return expr, " || ".join(rest)
    return None


def _put(d: dict, key: str, value: Any) -> None:
    """Set ``key`` as the last key: the same YAML whether or not it was there before (idempotent)."""
    d.pop(key, None)
    d[key] = value


def _add_vars(step: dict, branch: str, values: dict) -> None:
    validation = step.get("validation")
    if not isinstance(validation, dict):
        validation = step["validation"] = {"description": DESCRIPTION}
    b = validation.get(branch)
    if not isinstance(b, dict):
        b = validation[branch] = {"message": f"{DESCRIPTION}: {branch}"}  # removed again by strip
    b.setdefault("vars", {}).update(values)


def apply(parsed: dict) -> int:
    """Add the contract. Idempotent (``strip`` first). Returns the number of phases covered."""
    strip(parsed)
    phases = parsed.get("phases")
    if not isinstance(phases, dict) or not phases:
        return 0
    nodes = parsed.get("nodes") or {}
    local_nodes = {str(n) for n, v in nodes.items()
                   if isinstance(v, dict) and "local" in (str(v.get("type", "")).lower(), str(v.get("plugin", "")).lower())}
    local_nodes |= {"LOCAL", "local"}
    repo_nodes = {str(n) for n in nodes if _REPO_NODE.search(str(n)) and str(n) not in local_nodes}
    flags = parsed.setdefault("globals", {}).setdefault("vars", {})
    if not isinstance(flags, dict):
        flags = parsed["globals"]["vars"] = {}
    for f in FLAGS:
        flags.setdefault(f, "false")

    covered = 0
    for key, body in phases.items():
        if not isinstance(body, dict) or not isinstance(body.get("steps"), list):
            continue
        kind = phase_kind(str(key), body)
        own = str(body.get("when") or "").strip()
        _put(body, "when", combine(PHASE_WHEN[kind], own))
        if kind == ROLLBACK and not re.search(r"rollback", str(body.get("name") or ""), re.I):
            body["name"] = f"Rollback - {body.get('name') or key}"  # the JAR runs only these after a stop
        steps = body["steps"]
        if kind in (PRE, POST):
            _put(body, "on_failure", "continue")
            leaves = list(_leaves(steps))
            for i, step in enumerate(leaves):
                later = " ".join(_text(s) for s in leaves[i + 1:])
                depended_on = _is_user_switch(step) or any(
                    re.search(rf"(?<![\w.]){re.escape(n)}(?![\w.])", later) for n in _registered(step))
                _put(step, "on_failure", "stop" if depended_on else "continue")
                if kind == PRE:
                    _add_vars(step, "failure", {"PRECHECK_FAILED": "true"})
            for c in _containers(steps):
                _put(c, "on_failure", "continue")
        elif kind == BACKUP:
            _put(body, "on_failure", "stop")
            for step in _leaves(steps):
                _put(step, "on_failure", "stop")
                _add_vars(step, "failure", {"BACKUP_FAILED": "true"})
            for c in _containers(steps):
                _put(c, "on_failure", "stop")
        elif kind == ACTIVITY:
            _put(body, "on_failure", "stop")
            for step in _leaves(steps):
                _put(step, "on_failure", "stop")
                if changes_node(step, local_nodes, repo_nodes):
                    _add_vars(step, "success", {"CHANGES_MADE": "true"})
                    _add_vars(step, "failure", {"ROLLBACK_REQUIRED": "true"})
                else:
                    _add_vars(step, "failure", {"ROLLBACK_REQUIRED": "${CHANGES_MADE}"})
            for c in _containers(steps):
                _put(c, "on_failure", "stop")
        covered += 1
    return covered


def strip(parsed: dict) -> int:
    """Remove what ``apply`` added (flags, contract ``when`` terms, branch ``vars``, contract-only
    validations). ``on_failure`` values and the rollback name prefix stay. Returns changes."""
    changed = 0
    globals_ = parsed.get("globals")
    vars_ = globals_.get("vars") if isinstance(globals_, dict) else None
    if isinstance(vars_, dict):
        for f in FLAGS:
            if vars_.get(f) == "false":
                del vars_[f]
                changed += 1
        if not vars_:
            del globals_["vars"]
        if not globals_:
            del parsed["globals"]
    phases = parsed.get("phases")
    for key, body in (phases.items() if isinstance(phases, dict) else []):
        if not isinstance(body, dict):
            continue
        parts = split_own(str(body.get("when") or ""))
        if parts is not None:
            if parts[1]:
                body["when"] = parts[1]
            else:
                del body["when"]
            changed += 1
        for step in _leaves(body.get("steps") or []):
            validation = step.get("validation")
            if not isinstance(validation, dict):
                continue
            if validation.get("description") == DESCRIPTION:
                del step["validation"]
                changed += 1
                continue
            for branch in ("success", "failure"):
                b = validation.get(branch)
                if isinstance(b, dict) and isinstance(b.get("vars"), dict):
                    for f in FLAGS:
                        if f in b["vars"]:
                            del b["vars"][f]
                            changed += 1
                    if not b["vars"]:
                        del b["vars"]
                if isinstance(b, dict) and set(b) == {"message"} and b["message"] == f"{DESCRIPTION}: {branch}":
                    del validation[branch]  # a branch the contract added to the LLM's validation
                    changed += 1
    return changed
