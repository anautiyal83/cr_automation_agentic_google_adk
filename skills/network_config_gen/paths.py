"""Paths as variables (user 2026-11-18, as the production workflows write them): every folder the workflow reads or
writes is declared once at the top, in ``globals.vars``, and the steps use its name — production changes one line.

- automation server: helper scripts run from ``ACTIVITY_PYTHON_SCRIPT_PATH`` (execution.script_path, production's
  CLICR script folder). The folder is shared by every UC, so each script carries its UC's prefix
  (``pgw_rds_1058_create_xml.py``). The files the workflow makes and reads there (a script's output file, an sftp
  transfer's local side, the record values of ``nc_records/``) live in ``LOCAL_PATH`` (execution.local_path,
  ``/mnt/shared_data/${CHILD_REQ_ID}/${NODE_NAME}``: one folder per order and node), created by ``nc_localdir``
- nodes: each folder a node step names (``/srv/sftp/smc-sftp-DEFAULT/outbox``) is ``<TYPE>_<FOLDER>_PATH``
  (``PGW_OUTBOX_PATH``); regexes and messages keep the text (a regex is not interpolated)

The last pass of ``production.apply``; ``undo`` (``production.plain`` → ``structure.flatten``) gives the plain form
back. ``expand_nodes`` is the workflow as the nodes see it (steps 8 and 9: the simulator answers on real paths),
``local_view`` as the helper scripts' checks run it (bare script and file names, as before).
"""
from __future__ import annotations

import re
from typing import Any

SCRIPT_VAR = "ACTIVITY_PYTHON_SCRIPT_PATH"
LOCAL_VAR = "LOCAL_PATH"
LOCAL_DIR_ID = "nc_localdir"
_SUFFIX = "_PATH"
_LOCAL_NODES = ("local", "LOCAL")
# folders that are the system's, not the activity's
_SYSTEM = (("dev",), ("proc",), ("sys",), ("bin",), ("sbin",), ("usr", "bin"), ("usr", "sbin"), ("usr", "local", "bin"))
# an absolute path as a whole word: /a/b, /a/${X}/c.txt (not a URL, host:/path, a regex or a sed expression)
_PATH = re.compile(r"(?<![\w.\-}/$:~#@\\])(/(?:[\w.\-]+|\$\{[^}]+\})(?:/(?:[\w.\-]+|\$\{[^}]+\}))*)")
_SCRIPT_CALL = re.compile(r"\b(python[\d.]*\s+)([\w.\-]+\.py)\b")
# a file name the workflow makes or reads (a host name like eir01.example.com is none)
_FILE_LIKE = re.compile(r"^(?:[\w.\-]|\$\{[^}]+\})+\.(?:xml|res|txt|csv|tsv|json|ya?ml|html?|log|out|xlsx|xlsm|xls|"
                        r"tar|t?gz|tgz|bz2|zip|dat|cfg|conf|ini|sql|pdf|properties|lic|pem|crt|key)$", re.I)
_ARG_VALUE = re.compile(r"--?[\w-]+[= ]\s*(['\"]?)([^\s'\"]+)\1")
_REDIRECT = re.compile(r"(?<![\d&>])>>?\s*(['\"]?)([^\s'\";|&]+)\1")
_CIQ_REF = re.compile(r"\$\{(?:configSequences\.\d+\.records\.\d+|[A-Za-z_]\w*)\.data\.")
_KEEP_DIR = "nc_records"


# --------------------------------------------------------------------------- helpers

def script_prefix(uc_name: str) -> str:
    """``PGW_RDS_1058_IFC_CAPABILITY_MODIFICATION`` → ``pgw_rds_1058``; a UC without a number: its whole name."""
    uc = re.sub(r"[^A-Za-z0-9_]+", "_", str(uc_name or "")).strip("_")
    m = re.match(r"^([A-Za-z_]+?)_(\d+)(?:_|$)", uc)
    return (f"{m.group(1)}_{m.group(2)}" if m else uc).lower()


def _uc_of(parsed: dict) -> str:
    return str(parsed.get("name") or "").replace("-", "_").upper()  # production._file: name = kebab(uc)


def _phase_steps(parsed: dict):
    for body in (parsed.get("phases") or {}).values():
        if isinstance(body, dict) and isinstance(body.get("steps"), list):
            yield body


def _all(steps: list):
    for s in steps or []:
        if isinstance(s, dict):
            yield s
            yield from _all(s.get("steps"))


def _is_local(step: dict) -> bool:
    return str(step.get("node", "")) in _LOCAL_NODES


def _node_type(node: str) -> str:
    """``niam_pgw_1`` / ``niam_pgw_server`` / ``${TargetPGW}`` → ``PGW``; another node: its name."""
    m = re.fullmatch(r"\$\{Target([A-Za-z0-9]+)\}", node) or re.fullmatch(r"niam_([a-z0-9]+)_(?:\d+|server)", node)
    return (m.group(1) if m else re.sub(r"[^A-Za-z0-9]+", "_", node)).upper().strip("_") or "NODE"


def _vars(parsed: dict) -> dict:
    globals_ = parsed.setdefault("globals", {})
    if not isinstance(globals_.get("vars"), dict):
        globals_["vars"] = {}
    return globals_["vars"]


def node_path_vars(parsed: dict) -> dict[str, str]:
    """The node folders ``apply`` declared: ``<…>_PATH`` with a literal absolute value."""
    vars_ = (parsed.get("globals") or {}).get("vars") or {}
    return {k: str(v) for k, v in vars_.items()
            if isinstance(k, str) and k.endswith(_SUFFIX) and k not in (SCRIPT_VAR, LOCAL_VAR)
            and isinstance(v, str) and v.startswith("/") and "${" not in v}


def _folder(token: str) -> tuple[str, ...]:
    """The folder of a path word: up to the first ``${…}``, without a last part that is a file name."""
    parts = token.strip("/").split("/")
    literal = []
    for p in parts:
        if "${" in p:
            break
        literal.append(p)
    if len(literal) == len(parts) and len(literal) > 1 and "." in literal[-1]:
        literal = literal[:-1]
    if not literal or any(tuple(literal[:len(s)]) == s for s in _SYSTEM):
        return ()
    return tuple(literal)


def _word(text: str) -> re.Pattern:
    """``text`` as a whole path word (followed by ``/`` or the end of the word)."""
    return re.compile(r"(?<![\w.\-}/$:~#@\\])" + re.escape(text) + r"(?=/|$|[\s'\";|&)<>,])")


# --------------------------------------------------------------------------- apply

def apply(parsed: dict, uc_name: str = "") -> int:
    """Declare the paths and use their names; idempotent (``structure.apply`` flattens first)."""
    if not isinstance(parsed.get("phases"), dict):
        return 0
    changed = _node_paths(parsed)
    changed += _scripts(parsed, uc_name or _uc_of(parsed))
    changed += _local_files(parsed)
    if changed:
        vars_ = _vars(parsed)
        first = [k for k in (LOCAL_VAR, SCRIPT_VAR) if k in vars_] + sorted(node_path_vars(parsed))
        items = [(k, vars_[k]) for k in first] + [(k, v) for k, v in vars_.items() if k not in first]
        vars_.clear()
        vars_.update(items)  # the paths first, in the same order on every apply
    return changed


def _node_paths(parsed: dict) -> int:
    folders: dict[tuple[str, tuple[str, ...]], None] = {}
    steps = [s for body in _phase_steps(parsed) for s in _all(body["steps"])
             if s.get("type") != "loop" and not _is_local(s) and s.get("node")]

    def texts(s: dict) -> list[str]:
        out = [s["send"]] if isinstance(s.get("send"), str) else []
        sftp = s.get("sftp")
        if isinstance(sftp, dict) and isinstance(sftp.get("remote_path"), str):
            out.append(sftp["remote_path"])
        return out

    for s in steps:
        kind = _node_type(str(s["node"]))
        for t in texts(s):
            for m in _PATH.finditer(t):
                f = _folder(m.group(1))
                if f:
                    folders[(kind, f)] = None
    if not folders:
        return 0
    names: dict[tuple[str, tuple[str, ...]], str] = {}
    for kind, f in sorted(folders, key=lambda k: (k[0], k[1])):
        for depth in range(1, len(f) + 1):
            name = f"{kind}_{'_'.join(re.sub(r'[^A-Za-z0-9]+', '_', p) for p in f[-depth:]).upper()}{_SUFFIX}"
            name = re.sub(r"_+", "_", name)
            if name not in names.values():
                break
        names[(kind, f)] = name
    vars_ = _vars(parsed)
    changed = 0
    for (kind, f), name in names.items():
        value = "/" + "/".join(f)
        if vars_.get(name) != value:
            vars_[name] = value
            changed += 1
    by_kind: dict[str, list[tuple[str, str]]] = {}
    for (kind, f), name in names.items():
        by_kind.setdefault(kind, []).append(("/" + "/".join(f), name))
    for s in steps:  # the longest folder first: /a/b/c before /a/b
        pairs = sorted(by_kind.get(_node_type(str(s["node"])), []), key=lambda p: -len(p[0]))
        for key, holder in (("send", s), ("remote_path", s.get("sftp"))):
            if not isinstance(holder, dict) or not isinstance(holder.get(key), str):
                continue
            text = holder[key]
            for value, name in pairs:
                text = _word(value).sub("${" + name + "}", text)
            if text != holder[key]:
                holder[key] = text
                changed += 1
    return changed


def _scripts(parsed: dict, uc_name: str) -> int:
    from core.config import script_path
    folder = script_path()
    prefix = script_prefix(uc_name)
    if not folder:
        return 0
    changed = 0

    def call(m: re.Match) -> str:
        name = m.group(2)
        if prefix and not name.startswith(prefix + "_"):
            name = f"{prefix}_{name}"
        return f"{m.group(1)}${{{SCRIPT_VAR}}}/{name}"

    for body in _phase_steps(parsed):
        for s in _all(body["steps"]):
            if _is_local(s) and isinstance(s.get("send"), str) and _SCRIPT_CALL.search(s["send"]):
                s["send"] = _SCRIPT_CALL.sub(call, s["send"])
                changed += 1
    if changed:
        _vars(parsed)[SCRIPT_VAR] = folder
    return changed


def _local_files(parsed: dict) -> int:
    """The workflow's own files on the automation server under ``${LOCAL_PATH}`` (the CIQ's input files are
    ``structure.inputs_from_local_path``'s)."""
    from core.config import local_path
    folder = local_path()
    if not folder:
        return 0
    names: set[str] = set()
    for body in _phase_steps(parsed):
        for s in _all(body["steps"]):
            sftp = s.get("sftp")
            lp = str(sftp.get("local_path") or "") if isinstance(sftp, dict) else ""
            if lp and not lp.startswith(("/", "$")) and not _CIQ_REF.search(lp):
                names.add(lp)
            if _is_local(s) and isinstance(s.get("send"), str) and not str(s.get("id", "")).startswith("nc_in"):
                for m in list(_ARG_VALUE.finditer(s["send"])) + list(_REDIRECT.finditer(s["send"])):
                    v = m.group(2)
                    if "/" not in v and _FILE_LIKE.match(v) and not _CIQ_REF.search(v) and not v.endswith(".py"):
                        names.add(v)
    word = [(n, re.compile(r"(?<![\w.\-}/$])" + re.escape(n) + r"(?![\w.$\-])"))
            for n in sorted(names, key=len, reverse=True)]
    keep = re.compile(r"(?<![\w.\-}/$])" + _KEEP_DIR + r"(?=/|\s|$|['\"])")
    prefix = "${" + LOCAL_VAR + "}/"
    changed = 0
    used: set[int] = set()
    for i, body in enumerate(_phase_steps(parsed)):
        for s in _all(body["steps"]):
            sftp = s.get("sftp")
            if isinstance(sftp, dict) and str(sftp.get("local_path") or "") in names:
                sftp["local_path"] = prefix + str(sftp["local_path"])
                changed += 1
                used.add(i)
            if not (_is_local(s) and isinstance(s.get("send"), str)):
                continue
            text = keep.sub(prefix + _KEEP_DIR, s["send"])
            for n, rx in word:
                text = rx.sub(lambda m, n=n: prefix + n, text)
            if text != s["send"]:
                s["send"] = text
                changed += 1
                used.add(i)
    if not changed:
        return 0
    _vars(parsed)[LOCAL_VAR] = _vars(parsed).get(LOCAL_VAR) or folder
    for i, body in enumerate(_phase_steps(parsed)):  # the folder exists before a step writes into it
        if i in used and not any(isinstance(s, dict) and s.get("id") == LOCAL_DIR_ID for s in body["steps"]):
            body["steps"].insert(0, _local_dir_step())
            changed += 1
    return changed


def _local_dir_step() -> dict:
    return {"id": LOCAL_DIR_ID, "node": "local",
            "command_description": "Create the work folder of this order on the automation server",
            "send": f"mkdir -p ${{{LOCAL_VAR}}} && ls -ld ${{{LOCAL_VAR}}}", "on_failure": "stop",
            "validation": {"enabled": True, "description": "The work folder exists",
                           "success": {"criteria": {"regex": r"^d"}, "message": "Work folder ready."},
                           "failure": {"message": "The work folder could not be created on the automation server."}}}


# --------------------------------------------------------------------------- undo / views

def undo(parsed: dict) -> int:
    """The plain form again: node folders written out, bare script names (without the UC prefix), the workflow's
    own files without ``${LOCAL_PATH}/`` (the CIQ inputs keep theirs: ``structure._local_path_undo``)."""
    changed = expand_nodes(parsed)
    vars_ = (parsed.get("globals") or {}).get("vars")
    for k in node_path_vars(parsed):
        del vars_[k]
        changed += 1
    prefix = script_prefix(_uc_of(parsed))
    call = re.compile(r"\b(python[\d.]*\s+)\$\{" + SCRIPT_VAR + r"\}/(?:" + re.escape(prefix) + r"_)?([\w.\-]+\.py)\b") \
        if prefix else re.compile(r"\b(python[\d.]*\s+)\$\{" + SCRIPT_VAR + r"\}/([\w.\-]+\.py)\b")
    own = re.compile(re.escape("${" + LOCAL_VAR + "}/") + r"(?!\$\{(?:configSequences\.\d+\.records\.\d+|[A-Za-z_]\w*)\.data\.)")
    for body in _phase_steps(parsed):
        body["steps"][:] = [s for s in body["steps"] if not (isinstance(s, dict) and s.get("id") == LOCAL_DIR_ID)]
        for s in _all(body["steps"]):
            if isinstance(s.get("send"), str):
                text = call.sub(r"\1\2", s["send"])
                if _is_local(s):
                    text = own.sub("", text)
                if text != s["send"]:
                    s["send"] = text
                    changed += 1
            sftp = s.get("sftp")
            if isinstance(sftp, dict) and isinstance(sftp.get("local_path"), str) and own.match(sftp["local_path"]):
                sftp["local_path"] = own.sub("", sftp["local_path"], count=1)
                changed += 1
    if isinstance(vars_, dict):
        text = repr(parsed.get("phases"))
        for k in (SCRIPT_VAR, LOCAL_VAR):
            if k in vars_ and "${" + k + "}" not in text:
                del vars_[k]
                changed += 1
    return changed


def expand_nodes(parsed: dict) -> int:
    """``${PGW_OUTBOX_PATH}`` → ``/srv/sftp/smc-sftp-DEFAULT/outbox`` in every step (the declarations stay)."""
    found = node_path_vars(parsed)
    if not found:
        return 0
    changed = 0

    def walk(o: Any) -> Any:
        nonlocal changed
        if isinstance(o, str):
            new = o
            for k, v in found.items():
                new = new.replace("${" + k + "}", v)
            changed += new != o
            return new
        if isinstance(o, list):
            return [walk(x) for x in o]
        if isinstance(o, dict):
            return {k: walk(v) for k, v in o.items()}
        return o

    parsed["phases"] = walk(parsed.get("phases"))
    return changed


def expand_nodes_text(yaml_text: str) -> str:
    """``expand_nodes`` on the text (the rest as written): what steps 8 and 9 derive the simulator from."""
    vars_ = _declared(yaml_text)
    for k, v in node_path_vars({"globals": {"vars": vars_}}).items():
        yaml_text = yaml_text.replace("${" + k + "}", v)
    return yaml_text


def local_view(yaml_text: str) -> str:
    """The workflow as the helper scripts' checks run it: real node folders, bare script names (with the UC
    prefix: the file's name) and the workflow's own files without ``${LOCAL_PATH}/`` (they run in one folder)."""
    text = expand_nodes_text(yaml_text).replace("${" + SCRIPT_VAR + "}/", "")
    return re.sub(re.escape("${" + LOCAL_VAR + "}/") + r"(?!\$\{(?:configSequences\.\d+\.records\.\d+|[A-Za-z_]\w*)"
                  r"\.data\.)", "", text)


def _declared(yaml_text: str) -> dict:
    try:
        import yaml
        doc = yaml.safe_load(yaml_text) or {}
    except Exception:  # noqa: BLE001
        return {}
    vars_ = (doc.get("globals") or {}).get("vars") if isinstance(doc, dict) else None
    return vars_ if isinstance(vars_, dict) else {}
