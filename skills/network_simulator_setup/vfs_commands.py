"""Commands the simulated node answers from its sandbox file system (DEP-59).

MockWire runs ``cp mv chmod chown tar -c/-x/-t ls cat rm …``, ``if [[ … ]]; then …; fi`` and
``$(…)`` itself on its VFS (MockWire f5928df). A command whose paths all lie where the workflow
itself puts files — ``/tmp``, upload folders, node-side copy destinations, backup sources (seeded
by ``guards.backup_seed_files``), archives and copy / extract targets — gets no response key:
a canned answer would hide what the activity really did (MRF: the guarded ``cp`` copied
nothing and the post-check ``ls … | grep`` passed or failed with whatever the LLM wrote).
Commands on other paths (health checks reading real node files) keep their responses.
"""
from __future__ import annotations

import re
import shlex

from .guards import _GUARD, _resolve, targets

FILE_COMMANDS = {"ls", "cat", "test", "stat", "rm", "head", "tail", "wc", "cp", "mv", "chmod", "chown",
                 "mkdir", "touch", "tar", "sha256sum", "md5sum"}
# answered by MockWire without a path (pipeline filters, pure builtins)
PURE = {"echo", "true", "false", ":", "basename", "dirname", "grep", "egrep", "awk", "sed", "sort", "uniq",
        "tr", "paste", "head", "tail", "wc"}
_SPLIT = re.compile(r"\s*(?:\|\||&&|;|\|)\s*")
_FD = re.compile(r"\s*\d?>&\d|\s*2>\s*/dev/null")
_PEER_SIDE = re.compile(r"^\S*@(?:\[[^\]]*\]|[^\s:@/]+):")


_IF = re.compile(r"^if \[\[.+?\]\]; then\s*(.+?)(?:\s*;\s*else\s+(.+?))?\s*;\s*fi$", re.S)


def _simple_commands(cmd: str) -> list[str]:
    """Split at | && || ; outside quotes and $(…); ``if [[ … ]]; then A; [else B;] fi`` yields the
    commands of both branches (MockWire evaluates the test itself; run 9de5fcb8's member check
    `if …; then ls -ld $(tar -tf …); else ls … | grep …; fi` got a canned key)."""
    m = _GUARD.match(cmd.strip())
    if m:
        cmd = m.group(2)
    else:
        m = _IF.match(cmd.strip())
        if m:
            return _simple_commands(m.group(1)) + (_simple_commands(m.group(2)) if m.group(2) else [])
    parts, depth, quote, start = [], 0, "", 0
    i = 0
    while i < len(cmd):
        ch = cmd[i]
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif cmd.startswith("$(", i):
            depth += 1
            i += 1
        elif ch == ")" and depth:
            depth -= 1
        elif not depth and (ch in ";|" or cmd.startswith("&&", i)):
            parts.append(cmd[start:i])
            i += 2 if cmd.startswith(("&&", "||"), i) else 1
            start = i
            continue
        i += 1
    parts.append(cmd[start:])
    out = []
    for p in parts:
        p = _FD.sub("", p).strip()
        if not p:
            continue
        outer, inners = _lift_substitutions(p)
        out.append(outer)
        for inner in inners:  # `$(cat L | grep -v '/$' | sed …)`: judged as commands of their own
            out += _simple_commands(inner)
    return out


SUBST = "__SUBST__"


def _lift_substitutions(cmd: str) -> tuple[str, list[str]]:
    """``cmd`` with every top-level ``$(…)`` replaced by a token, and the inner commands."""
    out, inners, i, quote = [], [], 0, ""
    while i < len(cmd):
        ch = cmd[i]
        if quote == "'":
            quote = "" if ch == "'" else quote
        elif ch == "'":
            quote = "'"
        elif cmd.startswith("$(", i):
            depth, j, q = 1, i + 2, ""
            while j < len(cmd) and depth:
                c = cmd[j]
                if q:
                    q = "" if c == q else q
                elif c in "'\"":
                    q = c
                elif cmd.startswith("$(", j):
                    depth += 1
                    j += 1
                elif c == ")":
                    depth -= 1
                j += 1
            inners.append(cmd[i + 2:j - 1])
            out.append(SUBST)
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out), inners


def _words(simple: str) -> list[str]:
    try:
        return shlex.split(simple, posix=True)
    except ValueError:
        return simple.split()


def _norm(path: str) -> str:
    """A path template without quotes, glob tail and trailing slash."""
    p = path.strip("'\"")
    last = p.rsplit("/", 1)[-1]
    if any(c in last for c in "*?["):
        p = p.rsplit("/", 1)[0] if "/" in p else ""
    return p.rstrip("/") or "/"


def _is_path(word: str) -> bool:
    return word.startswith("/") or word.startswith("${")


def _under(path: str, prefixes: set[str]) -> bool:
    p = _norm(path)
    return any(p == d or p.startswith(d.rstrip("/") + "/") for d in prefixes)


def _tar_paths(words: list[str]) -> tuple[str, str, list[str], str]:
    """(mode c/x/t, archive, other path args, -C dir) of a tar command."""
    mode, archive, base, rest, want_f = "", "", "", [], False
    i = 1
    while i < len(words):
        w = words[i]
        if w == "-C" and i + 1 < len(words):
            base = words[i + 1]
            i += 2
            continue
        if w.startswith("--file="):
            archive = w[7:]
        elif w.startswith("-") or (i == 1 and re.fullmatch(r"[A-Za-z]+", w)):
            opts = w.lstrip("-")
            mode = mode or next((m for m in "cxt" if m in opts), "")
            want_f = want_f or "f" in opts
        elif want_f and not archive:
            archive = w
        else:
            rest.append(w)
        i += 1
    return mode, archive, rest, base


def _paths(simple: str) -> tuple[list[str], list[str]]:
    """(paths the command reads or needs, destinations it creates) of one file command."""
    words = _words(simple)
    if not words:
        return [], []
    name, args = words[0], [w for w in words[1:] if not w.startswith("-") or w == "-"]
    if name == "tar":
        mode, archive, rest, base = _tar_paths(words)
        if mode == "c":
            return [p for p in rest if _is_path(p)], [archive] if archive else []
        return ([archive] if archive else []) + [p for p in rest if _is_path(p)], [base] if base and mode == "x" else []
    if name in ("chmod", "chown"):
        args = args[1:]
    if name in ("cp", "mv"):
        return [a for a in args[:-1] if _is_path(a)], args[-1:] if args and _is_path(args[-1]) else []
    if name in ("mkdir", "touch"):
        return [], [a for a in args if _is_path(a)]
    return [a for a in args if _is_path(a)], []


def _dir_of(dest: str) -> str:
    """The folder a destination creates files in (``…/`` and extension-less names are folders)."""
    d = dest.strip("'\"")
    last = d.rstrip("/").rsplit("/", 1)[-1]
    return d.rstrip("/") if d.endswith("/") or "." not in last else d.rsplit("/", 1)[0]


def workflow_prefixes(commands: list[dict], steps: list[dict], vfs: dict | None) -> set[str]:
    """Folders the workflow itself fills on this node (templates with ``${…}``)."""
    from skills.network_config_gen.structure import tar_archive
    prefixes = {"/tmp"} | {_norm(d) for d in (vfs or {}).get("dirs") or []}
    for step in steps:
        sftp = step.get("sftp") if isinstance(step.get("sftp"), dict) else None
        if sftp and sftp.get("remote_path"):
            prefixes.add(_norm(_dir_of(str(sftp["remote_path"]))))
    sends = [str(c.get("cmd") or "") for c in commands]
    for send in sends:
        for simple in _simple_commands(send):
            words = _words(simple)
            if not words:
                continue
            if words[0] in ("scp", "sftp"):          # a node-side copy from the repo server
                local = [w for w in words[1:] if not w.startswith("-") and not _PEER_SIDE.match(w)
                         and w not in ("<<<",) and _is_path(w)]
                prefixes |= {_norm(_dir_of(w)) for w in local}
            parsed = tar_archive(simple) if words[0] == "tar" else None
            if parsed:                                # backup sources are seeded, the archive is made
                prefixes |= {_norm(s) for s in parsed[1] if _is_path(s)}
                prefixes.add(_norm(parsed[0].rsplit("/", 1)[0]) if "/" in parsed[0] else "/tmp")
            if words[0] == "mkdir":
                prefixes |= {_norm(a) for a in words[1:] if _is_path(a)}
    # copy / move / extract targets count once what they copy is known (to a fixpoint)
    changed = True
    while changed:
        changed = False
        for send in sends:
            for simple in _simple_commands(send):
                words = _words(simple)
                if not words or words[0] not in ("cp", "mv", "tar", "touch"):
                    continue
                reads, dests = _paths(simple)
                if reads and not all(_under(r, prefixes) for r in reads):
                    continue
                for d in dests:
                    folder = _norm(_dir_of(d))
                    if folder not in prefixes and folder != "/":  # tar -x -C / restores known paths
                        prefixes.add(folder)
                        changed = True
    prefixes.discard("/")  # never "every path"
    return prefixes


def vfs_handled(cmd: str, prefixes: set[str]) -> bool:
    """MockWire answers the whole command from its VFS: every part is a pure builtin / filter or
    a file command whose paths lie under ``prefixes`` — and at least one is a file command."""
    simple = _simple_commands(cmd)
    files = 0
    for s in simple:
        words = _words(s)
        if not words:
            continue
        name = words[0]
        if name in FILE_COMMANDS:
            reads, dests = _paths(s)
            # tar -x … -C /: a known archive restores its own (absolute) members
            paths = reads + [d for d in dests if not (name == "tar" and _norm(d) == "/")]
            if name == "ls" and not paths and SUBST not in s:
                return False                        # the working directory: not modelled
            if not all(_under(p, prefixes) for p in paths):
                return False
            files += 1
        elif name not in PURE:
            return False
        else:  # `echo x >> /tmp/…/items.list`: a redirect into the VFS is a file command
            targets = [words[i + 1] for i, w in enumerate(words[:-1]) if w in (">", ">>")]
            if targets:
                if not all(_under(t, prefixes) for t in targets):
                    return False
                files += 1
    return files > 0


def resolved_dirs(prefixes: set[str], ciq_json_files: list[str]) -> list[str]:
    """The prefixes as concrete folders per CIQ record (``vfs.dirs`` MockWire creates at start)."""
    out: set[str] = set()
    tgts = targets(ciq_json_files) or [{"record": {}, "fields": {}, "node": "", "nodeGroup": "", "crGroup": ""}]
    for p in prefixes:
        for t in tgts:
            d = _resolve(p, t).rstrip("/")
            if d.startswith("/") and "${" not in d and "$(" not in d and d != "":
                out.add(d)
    return sorted(out)
