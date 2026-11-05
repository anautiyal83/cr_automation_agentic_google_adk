"""Stateful file flows for the simulator, derived from the workflow (spec 004 S8-16).

Upload flows (order file → node inbox → response file appears in the outbox) cannot be
answered with static command responses: the same ``ls`` is expected to say "No such file"
before the upload and to list the file after it. MockWire models this with a sandbox file
system and triggers; this module derives both from the workflow itself:

- ``vfs.dirs``: the directories the workflow uploads to, lists or reads on the node
- ``vfs.triggers``: an upload ``…/inbox/${X}.xml`` followed by a check of a file named after
  it (``…/outbox/${X}.xml.res``) means the node produces that file → onCreate/create pair
- the produced file's content: the UC's sample response XML (sunny day)

File commands on those paths are then answered by MockWire's ``ls``/``cat`` builtins from
real file state, so static entries for them are dropped.
"""
from __future__ import annotations

import re
from typing import Any

_PLACEHOLDER = re.compile(r"\$\{([^{}]+)\}")
_ABS_PATH = re.compile(r"(?<![\w$}])/(?:[\w.${}-]+/)*[\w.${}-]+")
_FILE_CMD = re.compile(r"^\s*(ls|cat|test|stat|rm|head|tail|wc)\b")


def node_steps(workflow: dict, node: str) -> list[dict]:
    """The node's steps in execution order (nested steps included)."""
    out: list[dict] = []

    def walk(steps):
        for step in steps or []:
            if isinstance(step, dict):
                if str(step.get("node") or "") == node:
                    out.append(step)
                walk(step.get("steps"))

    phases = workflow.get("phases") or {}
    for body in (phases.values() if isinstance(phases, dict) else phases):
        walk(body.get("steps") if isinstance(body, dict) else body)
    return out


def derive_vfs(workflow: dict, node: str, response_lines: list[str],
               samples: list[dict] | None = None) -> dict | None:
    """``vfs`` section for one node, or None when the node has no file flows."""
    uploads: list[tuple[int, str]] = []
    checks: list[tuple[int, str]] = []
    for i, step in enumerate(node_steps(workflow, node)):
        sftp = step.get("sftp") if isinstance(step.get("sftp"), dict) else None
        if sftp and sftp.get("remote_path"):
            (uploads if sftp.get("operation") == "upload" else checks).append((i, str(sftp["remote_path"])))
            continue
        send = str(step.get("send") or "")
        if _FILE_CMD.match(send):
            checks += [(i, p) for p in _ABS_PATH.findall(send)]
    if not uploads and not checks:
        return None

    dirs = sorted({p.rsplit("/", 1)[0] for _, p in uploads + checks
                   if "/" in p.strip("/") and "${" not in p.rsplit("/", 1)[0]})
    triggers: list[dict] = []
    for ui, up in uploads:
        up_vars = set(_PLACEHOLDER.findall(up))
        for ci, chk in checks:
            if ci <= ui or chk == up:
                continue
            chk_vars = set(_PLACEHOLDER.findall(chk))
            if not chk_vars or not chk_vars <= up_vars:
                continue
            names = {v: _group_name(v, k) for k, v in enumerate(sorted(up_vars))}
            trigger = {"onCreate": _to_regex(up, names), "create": _to_template(chk, names),
                       "lines": list(response_lines) or ["OK"]}
            if trigger not in triggers:
                triggers.append(trigger)
    if not dirs and not triggers:
        return None
    vfs: dict[str, Any] = {"dirs": dirs}
    if triggers:
        vfs["triggers"] = triggers
    listing = derive_listing(node_steps(workflow, node), dirs, samples or [])
    if listing:
        vfs["listing"] = listing
    return vfs


# `ls -l` line of a real node: mode, links, owner, group
_LONG_LINE = re.compile(r"^([-dl][rwxsStT-]{9})[.+@]?\s+\d+\s+(\S+)\s+(\S+)\s", re.MULTILINE)
_MODE = re.compile(r"(?<![\w-])([-d][r-][w-][xsS-][r-][w-][xsS-][r-][w-][xtT-])(?![\w-])")


_MARKED = re.compile(r"^[-dl][rwxsStT-]{9}([.+@]?)\s+\d+\s", re.MULTILINE)


def mode_suffix(samples: list[dict]) -> str:
    """The marker the real node prints after every ``ls -l`` mode (SELinux ``.``, ACL ``+``), when
    most recorded lines have one: a regex written from the real output (mode + SELinux dot) failed
    on the simulator's bare mode (run 3f5d8990)."""
    marks = [m.group(1) for s in samples for m in _MARKED.finditer(str(s.get("output") or ""))]
    if not marks:
        return ""
    top = max(set(marks), key=marks.count)
    return top if top and marks.count(top) * 2 > len(marks) else ""


def derive_listing(steps: list[dict], dirs: list[str], samples: list[dict]) -> list[dict]:
    """``vfs.listing``: the ``ls -l`` columns of files in each directory as the real node shows
    them (S8-23). MockWire prints ``-rw-r--r--`` by default; a workflow regex written from the
    real node (``^-rwx------\\s+…``) never matched, and a 30-retry poll hung the case.
    Recorded ``ls`` output of the directory wins, else the mode the workflow's regex spells out."""
    listing = []
    for d in dirs:
        entry: dict[str, str] = {}
        for s in samples:
            cmd = str(s.get("command") or "")
            if _FILE_CMD.match(cmd) and d in cmd:
                hit = next((m for m in _LONG_LINE.finditer(str(s.get("output") or "")) if m.group(1)[0] == "-"),
                           None)
                if hit:
                    entry = {"mode": hit.group(1), "owner": hit.group(2), "group": hit.group(3)}
                    break
        if not entry:
            for step in steps:
                send = str(step.get("send") or "")
                if not (_FILE_CMD.match(send) and d in send):
                    continue
                for regex in _success_regexes(step):
                    m = _MODE.search(regex.replace("\\-", "-"))
                    if m:
                        entry = {"mode": m.group(1)}
                        break
                if entry:
                    break
        if entry:
            listing.append({"path": re.escape(d) + "/[^/]+", **entry})
    return listing


def _success_regexes(step: dict) -> list[str]:
    found = []

    def walk(value):
        if isinstance(value, dict):
            for k, v in value.items():
                if k == "regex" and isinstance(v, str):
                    found.append(v)
                elif k != "failure":
                    walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(step.get("validation") or {})
    return found


def is_vfs_command(key: str, vfs: dict | None) -> bool:
    """A response key for a file command on a VFS path (answered by builtins instead)."""
    if not vfs:
        return False
    text = str(key)
    body = text[len("regex:"):] if text.startswith("regex:") else text
    body = body.replace("\\", "")  # regex keys escape the path
    return bool(_FILE_CMD.match(body.lstrip("^"))) and any(d in body for d in vfs.get("dirs", []))


def response_lines(requirements: dict) -> list[str]:
    """A sample response document of the UC (sunny day content of produced files)."""
    samples = (requirements.get("doc_extracted") or {}).get("xml_files") or []
    texts = [str(s.get("content", "") if isinstance(s, dict) else s) for s in samples]
    for text in texts:
        if re.search(r"<[\w:]*Response\b", text) or "result=\"success\"" in text:
            return text.splitlines()
    return []


def _group_name(var: str, index: int) -> str:
    name = re.sub(r"[^A-Za-z0-9]", "", var.split(".")[-1])
    return name if name and name[0].isalpha() else f"p{index}"


def _to_regex(path: str, names: dict[str, str]) -> str:
    parts = _PLACEHOLDER.split(path)  # literal, var, literal, var, ...
    out = []
    for k, part in enumerate(parts):
        out.append(f"(?<{names[part]}>[^/]+?)" if k % 2 else re.escape(part))
    return "".join(out)


def _to_template(path: str, names: dict[str, str]) -> str:
    return _PLACEHOLDER.sub(lambda m: "${" + names[m.group(1)] + "}", path)
