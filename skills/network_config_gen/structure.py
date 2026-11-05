"""Workflow structure step 6 adds after the LLM — deterministic and idempotent.

1. Every CIQ record (DEP-9): steps that use record columns run in a ``type: loop`` over
   ``${configSequences.0.records}`` (item ``rec``, columns ``${rec.data.<Col>}``). With
   ``records.0`` only, a node group with 3 announcement files loaded one of them. The JAR
   runs a step as a loop only with ``type: loop``; ``reverse_order`` must be set (it NPEs
   on null) — both verified against cli-automation execute/generate 2026-11-03.
2. Backup "via repo server" (spec 010 O-D2 / O-D7, otherwise backups stay in the node's /tmp):
   each node copies the ``tar -c`` archive of a backup phase to the repo server
   (``execution.repo_server``, workflow node ``REPO``) with ``scp`` / ``sftp`` (node type's
   ``file_transfer``, repo password by ``expect_reply``) and back before a rollback extracts it;
   every file the workflow moves between the automation server and a node is staged on the repo
   server (framework sftp) and copied by the node the same way.
3. Checksums: every ``sftp`` transfer is followed by ``sha256sum`` on both ends and a
   ``criteria.expr`` comparison of the two registered sums (string ``==`` verified).
4. Execution contract (spec 011, ``contract.py``): failure flags, phase conditions, abort and
   rollback rules.
0. Before all: the run's phases (spec 010, ``select_phases``): the model's nine phases in order,
   the ones the run switched off dropped.

The LLM writes the flat form (``${configSequences.0.records.0.data.<Col>}``); ``flatten``
turns a structured workflow back into it (loops unwrapped, added steps removed), so the
validator and the regenerate prompt work on what the LLM wrote, and ``apply`` is safe to
run again. Added steps carry an ``id`` starting with ``nc_``.
"""
from __future__ import annotations

import copy
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

AUTO_ID = "nc_"
ITEM_VAR = "rec"
RECORDS = "${configSequences.0.records}"
_REC0 = "configSequences.0.records.0.data."
_RECORD_REF = re.compile(r"configSequences\.\d+\.records\.\d+\.data\.")
_BACKUP_PHASE = re.compile(r"backup", re.I)
_ROLLBACK_PHASE = re.compile(r"rollback|fallback|restore|revert", re.I)
_TAR_CREATE = re.compile(r"\btar\s+(-?[A-Za-z]*c[A-Za-z]*f[A-Za-z]*|-?[A-Za-z]*f[A-Za-z]*c[A-Za-z]*)\s+([^;&|]+)")
_SHELL_SUBST = re.compile(r"_?\$\([^)]*\)|_?`[^`]*`")
_SUM = "[0-9a-f]{64}"


# --------------------------------------------------------------------------- helpers

def _phase_bodies(parsed: dict):
    phases = parsed.get("phases") or {}
    for name, body in (phases.items() if isinstance(phases, dict) else []):
        if isinstance(body, dict) and isinstance(body.get("steps"), list):
            yield str(name), body


def is_record_loop(step: Any) -> bool:
    return (isinstance(step, dict) and isinstance(step.get("steps"), list)
            and "configSequences.0.records" in str(step.get("for_each") or "")
            and _RECORD_REF.search(str(step.get("for_each"))) is None)


def _rewrite(value: Any, pattern: re.Pattern, repl: str) -> Any:
    if isinstance(value, str):
        return pattern.sub(repl, value)
    if isinstance(value, list):
        return [_rewrite(v, pattern, repl) for v in value]
    if isinstance(value, dict):
        return {k: _rewrite(v, pattern, repl) for k, v in value.items()}
    return value


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)


def _uses_record(step: dict) -> bool:
    return any(_RECORD_REF.search(s) for s in _strings(step))


def _registers(step: dict) -> set[str]:
    names: set[str] = set()
    for reg in step.get("register") or []:
        regex = reg.get("regex") if isinstance(reg, dict) else reg
        names |= set(re.findall(r"\(\?<([A-Za-z][A-Za-z0-9]*)>", str(regex or "")))
    return names


def _uses_vars(step: dict, names: set[str]) -> bool:
    if not names:
        return False
    text = " ".join(_strings({k: v for k, v in step.items() if k != "register"}))
    return any(re.search(rf"\$\{{{re.escape(n)}\}}|(?<![\w.]){re.escape(n)}(?![\w.])", text) for n in names)


def _moved_files(step: dict) -> set[str]:
    sftp = step.get("sftp") if isinstance(step.get("sftp"), dict) else {}
    files = {str(sftp[k]) for k in ("remote_path", "local_path") if sftp.get(k) and "/" in str(sftp[k])}
    if _is_auto(step) and str(step.get("id")).endswith("_copy"):  # the node's scp / sftp with the repo
        files |= {re.sub(r"^\S*@(?:\[[^\]]*\]|[^\s:@]+):", "", tok) for tok in str(step.get("send") or "").split()[1:]
                  if "/" in tok and not tok.startswith("'")}
    return files


def _is_auto(step: Any) -> bool:
    return isinstance(step, dict) and str(step.get("id") or "").startswith(AUTO_ID)


def _local_node(parsed: dict) -> str:
    for name, node in (parsed.get("nodes") or {}).items():
        if isinstance(node, dict) and "local" in (node.get("type"), node.get("plugin")):
            return str(name)
    parsed.setdefault("nodes", {})["LOCAL"] = {"type": "local", "plugin": "local"}
    return "LOCAL"


def _step(sid: str, node: str, description: str, when: str = "", **body) -> dict:
    step = {"id": sid, "node": node}
    if when:
        step["when"] = when
    step.update(body)
    step["command_description"] = description
    return step


# --------------------------------------------------------------------------- flatten

def flatten(parsed: dict) -> int:
    """Undo ``apply``: unwrap record loops (``${rec.data.X}`` → ``records.0``) and drop the
    added ``nc_`` steps. Returns the number of changes."""
    from . import contract
    changed = contract.strip(parsed)
    changed += _unstage(parsed)

    def walk(steps: list) -> list:
        nonlocal changed
        out = []
        for step in steps:
            if is_record_loop(step):
                item = str(step.get("item_var") or ITEM_VAR)
                pattern = re.compile(rf"(?<![\w.]){re.escape(item)}\.data\.")
                for child in walk(step["steps"]):
                    out.append(_rewrite(child, pattern, _REC0))
                changed += 1
                continue
            if _is_auto(step):
                changed += 1
                continue
            if isinstance(step, dict) and isinstance(step.get("steps"), list):
                step["steps"] = walk(step["steps"])
            out.append(step)
        return out

    for _, body in _phase_bodies(parsed):
        body["steps"] = walk(body["steps"])
    nodes = parsed.get("nodes")
    repo = _repo_config()["node"]
    if changed and isinstance(nodes, dict) and repo in nodes:
        used = {str(s.get("node")) for _, body in _phase_bodies(parsed) for s in _all_steps(body["steps"])}
        if repo not in used:  # added by backups_by_convention
            del nodes[repo]
    return changed


def _all_steps(steps: list):
    for step in steps:
        if isinstance(step, dict):
            yield step
            if isinstance(step.get("steps"), list):
                yield from _all_steps(step["steps"])


def flattened(parsed: dict) -> dict:
    copy_ = copy.deepcopy(parsed)
    flatten(copy_)
    return copy_


# --------------------------------------------------------------------------- record loops

def loop_record_steps(parsed: dict) -> int:
    """Wrap each run of steps that use record columns in a loop over all records.

    A run starts at a step that uses a record column and goes on while the next step uses
    one, reads a variable registered inside the run, uses a file moved inside the run (the
    restored backup is extracted in the same pass), or was added for a step of the run."""
    wrapped = 0
    for name, body in _phase_bodies(parsed):
        steps, out, i = body["steps"], [], 0
        while i < len(steps):
            step = steps[i]
            if not isinstance(step, dict) or is_record_loop(step) or not _uses_record(step):
                out.append(step)
                i += 1
                continue
            run, regs, files = [step], _registers(step), _moved_files(step)
            j = i + 1
            while j < len(steps) and isinstance(steps[j], dict) and not is_record_loop(steps[j]) and (
                    _uses_record(steps[j]) or _uses_vars(steps[j], regs) or _is_auto(steps[j])
                    or any(f in s for f in files for s in _strings(steps[j]))):
                run.append(steps[j])
                regs |= _registers(steps[j])
                files |= _moved_files(steps[j])
                j += 1
            # an added step right before the run whose value the run uses (the hostname of the
            # repo path) belongs to every pass too
            while out and _is_auto(out[-1]) and not is_record_loop(out[-1]) \
                    and any(_uses_vars(s, _registers(out[-1])) for s in run):
                run.insert(0, out.pop())
            pattern = _RECORD_REF
            out.append({
                "id": f"{AUTO_ID}records_{re.sub(r'[^A-Za-z0-9]', '', name)}_{wrapped + 1}",
                "type": "loop",
                "node": _local_node(parsed),
                "command_description": "For each CIQ record of the node group",
                "for_each": RECORDS,
                "item_var": ITEM_VAR,
                "reverse_order": False,
                "steps": [_rewrite(s, pattern, f"{ITEM_VAR}.data.") for s in run],
            })
            wrapped += 1
            i = j
        body["steps"] = out
    if wrapped:
        logger.info("Structure: %d step runs loop over all CIQ records", wrapped)
    return wrapped


# --------------------------------------------------------------------------- checksums

def verify_transfers(parsed: dict) -> int:
    """sha256sum both ends of every sftp step; the receiving side compares the sums."""
    local = _local_node(parsed)
    count = 0

    def walk(steps: list) -> list:
        nonlocal count
        out = []
        for step in steps:
            if isinstance(step, dict) and isinstance(step.get("steps"), list):
                step["steps"] = walk(step["steps"])
            sftp = step.get("sftp") if isinstance(step, dict) else None
            if not isinstance(sftp, dict) or str(step.get("node", "")) == local:
                out.append(step)
                continue
            op = str(sftp.get("operation") or "").lower()
            lpath, rpath = str(sftp.get("local_path") or ""), str(sftp.get("remote_path") or "")
            if op not in ("upload", "download") or not lpath or not rpath:
                out.append(step)
                continue
            count += 1
            node, when = str(step.get("node")), str(step.get("when") or "")
            src_var, dst_var = f"xferSrc{count}", f"xferDst{count}"
            if op == "upload":
                src = (local, lpath, "local file")
                dst = (node, rpath.rstrip("/") + ("/" + lpath.rsplit("/", 1)[-1] if rpath.endswith("/") else ""),
                       f"file on {node}")
            else:
                src = (node, rpath, f"file on {node}")
                dst = (local, lpath, "local copy")
            out.append(_step(f"{AUTO_ID}sum{count}_src", src[0], f"Checksum of the {src[2]} before the transfer", when,
                             send=f"sha256sum {src[1]}",
                             register=[{"regex": f"(?<{src_var}>{_SUM})"}]))
            out.append(step)
            out.append(_step(f"{AUTO_ID}sum{count}_dst", dst[0], f"Verify the transferred file: checksum of the {dst[2]} equals the source",
                             when, send=f"sha256sum {dst[1]}",
                             register=[{"regex": f"(?<{dst_var}>{_SUM})"}],
                             validation={
                                 "description": "SHA-256 of the transferred file must equal the source file's",
                                 "success": {"criteria": {"expr": f"{dst_var} == {src_var}"},
                                             "message": "Checksums match: the complete file was transferred."},
                                 "failure": {"message": "Checksum mismatch: the file was not transferred completely."},
                             }))
        return out

    for _, body in _phase_bodies(parsed):
        body["steps"] = walk(body["steps"])
    if count:
        logger.info("Structure: %d transfers verified with sha256sum", count)
    return count


# --------------------------------------------------------------------------- repo backups

def _repo_config(requirements: dict | None = None) -> dict:
    from core.config import repo_server
    cfg = repo_server()
    override = (requirements or {}).get("repo_server") if isinstance(requirements, dict) else None
    if isinstance(override, dict):
        cfg.update({k: str(v) for k, v in override.items() if v not in (None, "")})
    return cfg


def _repo_node(parsed: dict, cfg: dict) -> str:
    nodes = parsed.setdefault("nodes", {})
    for name, node in nodes.items():  # the LLM declared one already
        if re.search(r"repo|backup", str(name), re.I) and isinstance(node, dict) \
                and "local" not in (node.get("type"), node.get("plugin")):
            return str(name)
    nodes[cfg["node"]] = {
        "type": "remote",
        "plugin": "ssh",
        "prompt_regex": r"(?m)[\$#>]\s*$",
        "ssh": {
            "host": cfg["host"],
            "port": str(cfg.get("port") or "22"),
            "user": cfg["user"],
            "auth": {"password": "${SECRET." + cfg.get("password_secret", "repo_password") + "}"},
            "connect_timeout_sec": "30",
        },
    }
    return cfg["node"]


def tar_archive(send: str) -> tuple[str, list[str]] | None:
    """(archive, sources) of a ``tar -c…f ARCHIVE SRC…`` command."""
    m = _TAR_CREATE.search(send)
    if not m:
        return None
    tokens = re.findall(r"(?:\$\([^)]*\)|`[^`]*`|\S)+", m.group(2))
    archive, sources, skip = "", [], False
    for tok in tokens:
        if skip:
            skip = False
            continue
        if tok == "-C":
            skip = True
            continue
        if tok.startswith("-"):
            continue
        if not archive:
            archive = tok
        else:
            sources.append(tok)
    return (archive, sources) if archive else None


def via_repo(requirements: dict | None) -> bool:
    """The run moves files through the repo server (spec 010 O-D2 Backup "via repo server"); without
    run options the default (backup kept on the node)."""
    from shared import run_options
    raw = requirements.get("run_options") if isinstance(requirements, dict) else None
    return bool(run_options.normalize(raw)["backup_via_repo"])


def node_login(parsed: dict, node: str) -> tuple[str, str, str]:
    """(user, address, password) of a workflow node's SSH login — for the repo server, what a node
    logs in to it with (O-D7: the node runs the copy, so a NIAM node needs no address of its own)."""
    spec = (parsed.get("nodes") or {}).get(node)
    ssh = spec.get("ssh") if isinstance(spec, dict) and isinstance(spec.get("ssh"), dict) else {}
    auth = ssh.get("auth") if isinstance(ssh.get("auth"), dict) else {}
    return str(ssh.get("user") or "root"), str(ssh.get("host") or node), str(auth.get("password") or ssh.get("password") or "")


def file_transfer(requirements: dict | None, node: str) -> str:
    """``sftp`` when the node type supports SFTP, else ``scp`` (vendor profile, O-D6)."""
    nodes = (((requirements or {}).get("category_1_node_activity") or {}).get("nodes") or []) \
        if isinstance(requirements, dict) else []
    for n in nodes:
        if isinstance(n, dict) and str(n.get("name", "")).upper() == node.upper():
            method = str((n.get("ssh") or {}).get("file_transfer") or "").lower()
            if method in ("sftp", "scp"):
                return method
    return "sftp"


def repo_copy(method: str, user: str, address: str, node_path: str, repo_path: str, to_repo: bool) -> str:
    """The command a node runs to copy a file to (``to_repo``) or from the repo server."""
    remote = f"{user}@{f'[{address}]' if ':' in address else address}:"  # brackets for IPv6 only
    if method == "scp":
        return f"scp {node_path} {remote}{repo_path}" if to_repo else f"scp {remote}{repo_path} {node_path}"
    if not to_repo:
        return f"sftp {remote}{repo_path} {node_path}"
    folder, _, name = repo_path.rpartition("/")
    return f"sftp {remote}{folder or '/'} <<< 'put {node_path} {name}'"


def _repo_transfer(tag: str, repo: str, node: str, login: tuple[str, str, str], method: str,
                   node_path: str, repo_path: str, to_repo: bool, when: str, what: str) -> list[dict]:
    """Checksum at the source, the node's copy to / from the repo server (repo password by
    ``expect_reply``), checksum at the destination compared with the source's. Ids
    ``nc_<tag>_sum_node`` / ``_copy`` / ``_sum_repo`` (``flatten`` reads the node side back)."""
    user, address, password = login
    src_var, dst_var = f"{tag}Src", f"{tag}Dst"
    node_sum = (node, node_path, f"nc_{tag}_sum_node", f"file on {node}")
    repo_sum = (repo, repo_path, f"nc_{tag}_sum_repo", "file on the repo server")
    src, dst = (node_sum, repo_sum) if to_repo else (repo_sum, node_sum)
    copy_ = _step(f"nc_{tag}_copy", node, what, when,
                  send=repo_copy(method, user, address, node_path, repo_path, to_repo))
    if password:
        copy_["expect_reply"] = [{"expect": "assword:", "reply": f"{password}\n", "timeout_sec": 30}]
    return [
        _step(src[2], src[0], f"Checksum of the {src[3]} before the transfer", when,
              send=f"sha256sum {src[1]}", register=[{"regex": f"(?<{src_var}>{_SUM})"}]),
        copy_,
        _step(dst[2], dst[0], f"Verify the transferred file: checksum of the {dst[3]} equals the source", when,
              send=f"sha256sum {dst[1]}", register=[{"regex": f"(?<{dst_var}>{_SUM})"}],
              validation={
                  "description": "SHA-256 of the transferred file must equal the source file's",
                  "success": {"criteria": {"expr": f"{dst_var} == {src_var}"},
                              "message": "Checksums match: the complete file was transferred."},
                  "failure": {"message": "Checksum mismatch: the file was not transferred completely."},
              }),
    ]


def _order_ref() -> str:
    """The order number the framework passes at run time (``execution.runtime_params``, e.g.
    ``ORDERNO``): backups of an order are found by it — a rollback-only run of the order has no
    value an earlier execution registered (user, 2026-11-05)."""
    from core.config import runtime_params
    key = next((k for k in runtime_params() if re.search(r"(?i)order", k)), None)
    return "${" + key + "}" if key else "${crGroup}"


def node_name_ref(node: str, requirements: dict | None) -> str:
    """``${<NODE>.<field>}`` of the node's name in the CIQ node sheet (the field whose values are
    the INDEX sheet's node names, e.g. ``${MRF.NODE_NAME}``); '' when the CIQ has none."""
    if not isinstance(requirements, dict):
        return ""
    from .skill import _expected_coverage
    fields = (((_expected_coverage(requirements).get("record_columns") or {}).get("node_fields") or {})
              .get(node) or [])
    sheets = (requirements.get("doc_extracted") or {}).get("ciq_sheets") or {}

    def values(col: str, exclude: str = "") -> set[str]:
        return {str(r.get(col)).strip() for name, sh in sheets.items() if name != exclude
                for r in (sh or {}).get("sample_rows") or [] if isinstance(r, dict) and r.get(col)}
    index_nodes = {v for name, sh in sheets.items()
                   for col in ((sh or {}).get("columns") or list(((sh or {}).get("sample_rows") or [{}])[0]))
                   if re.fullmatch(r"(?i)node", str(col)) for v in values(col)}
    for f in fields:
        vals = values(f)
        if vals and index_nodes and vals <= index_nodes:
            return "${" + node + "." + f + "}"
    named = next((f for f in fields if re.fullmatch(r"(?i)node[ _]?name", f)), None)
    return "${" + node + "." + named + "}" if named else ""


def _rollback_bodies(parsed: dict) -> list[dict]:
    return [body for name, body in _phase_bodies(parsed)
            if _ROLLBACK_PHASE.search(name) or _ROLLBACK_PHASE.search(str(body.get("name") or ""))]


_GUARDED = re.compile(r"^(if (\[\[.+?\]\]); then\s*)(.+?)(\s*;\s*else echo SKIPPED_BY_CONDITION;\s*fi)$", re.S)
_EXTRACT = re.compile(r"^tar\s+-?[A-Za-z]*x[A-Za-z]*\s+(\S+)\s+-C\s+(\S+?)/?$")
_COPY = re.compile(r"^cp(?:\s+-\w+)*\s+(\S+)\s+(\S+?)/?$")
NOTHING_TO_BACK_UP = "NO_FILES_TO_BACK_UP"


def _loads(parsed: dict) -> list[dict]:
    """What the activity puts into a folder: ``cp <src> <dest>/`` (that file) and ``tar -xf
    <archive> -C <dest>`` (the archive's members), with the bash test guarding the step."""
    out = []
    for name, body in _phase_bodies(parsed):
        if _BACKUP_PHASE.search(name) or _ROLLBACK_PHASE.search(name) \
                or _ROLLBACK_PHASE.search(str(body.get("name") or "")):
            continue
        for step in _all_steps(body["steps"]):
            if _is_auto(step) or not isinstance(step.get("send"), str):
                continue
            send = step["send"].strip()
            g = _GUARDED.match(send)
            inner = g.group(3).strip() if g else send
            for kind, rx in (("extract", _EXTRACT), ("copy", _COPY)):
                m = rx.match(inner)
                if m:
                    out.append({"kind": kind, "src": m.group(1), "dest": m.group(2).rstrip("/"),
                                "test": g.group(2) if g else "", "node": str(step.get("node", ""))})
                    break
    return out


def loaded_members_only(parsed: dict) -> int:
    """``<dest>/*`` after ``tar -xf <archive> -C <dest>`` (same branch) means every file of the
    folder — ``chmod 777 <dest>/*`` changed files the activity never loaded. It becomes exactly the
    archive's members (user rule 2026-11-05: never touch anything else on the node)."""
    changed = 0
    for load in _loads(parsed):
        if load["kind"] != "extract":
            continue
        members = f"$(tar -tf {load['src']} | grep -v '/$' | sed 's#^#{load['dest']}/#')"
        glob = re.compile(re.escape(load["dest"]) + r"/\*(?=$|[\s;&|)])")
        for name, body in _phase_bodies(parsed):
            if _BACKUP_PHASE.search(name) or _ROLLBACK_PHASE.search(name):
                continue
            for step in _all_steps(body["steps"]):
                send = step.get("send") if isinstance(step, dict) else None
                if not isinstance(send, str) or _is_auto(step) or str(step.get("node", "")) != load["node"]:
                    continue
                g = _GUARDED.match(send.strip())
                if (g.group(2) if g else "") != load["test"] or not glob.search(send):
                    continue
                step["send"] = glob.sub(lambda m: members, send)
                changed += 1
    return changed


def _take_uploads(parsed: dict, body: dict, sources: set[str]) -> list[dict]:
    """The uploads of the files the activity loads, taken out of their (later) phase: the backup
    must know what the load will overwrite (option A, user 2026-11-05)."""
    moved = []
    for name, other in _phase_bodies(parsed):
        if other is body or _BACKUP_PHASE.search(name) or _ROLLBACK_PHASE.search(name):
            continue
        keep = []
        for step in other["steps"]:
            sftp = step.get("sftp") if isinstance(step, dict) else None
            if isinstance(sftp, dict) and str(sftp.get("operation", "")).lower() == "upload" \
                    and str(sftp.get("remote_path") or "") in sources and not _is_auto(step):
                moved.append(step)
            else:
                keep.append(step)
        other["steps"] = keep
    return moved


def _overwritten_steps(k: int, step: dict, loads: list[dict], dest: str, lst: str, path: str) -> list[dict]:
    """List what the load puts into ``dest`` and which of those exist already; the backup step then
    archives exactly those — nothing else on the node is touched (user rule, 2026-11-05)."""
    items, over = f"{lst}/items.list", f"{lst}/overwritten.list"
    node, when = str(step.get("node")), str(step.get("when") or "")
    steps = [_step(f"{AUTO_ID}bk{k}_items0", node, "Start the list of files the activity loads", when,
                   send=f"mkdir -p {lst} && rm -f {items} {over}")]
    for i, load in enumerate(loads, 1):
        name = load["src"].rsplit("/", 1)[-1]
        cmd = f"tar -tf {load['src']} >> {items}" if load["kind"] == "extract" else f"echo {name} >> {items}"
        steps.append(_step(f"{AUTO_ID}bk{k}_items{i}", node, f"Files the activity loads into {dest}", when,
                           send=f"if {load['test']}; then {cmd}; fi" if load["test"] else cmd))
    steps.append(_step(
        f"{AUTO_ID}bk{k}_existing", node, "Which of them exist already: only those are backed up", when,
        send=(f"if [[ -s {items} ]]; then ls -d $(cat {items} | grep -v '/$' | sed 's#^#{dest}/#') 2>/dev/null "
              f"| sed 's#^{dest}/##' > {over} || true; fi")))
    return steps


def _all_registered(parsed: dict) -> set[str]:
    return {v for _, body in _phase_bodies(parsed) for st in _all_steps(body["steps"]) for v in _registers(st)}


def backups_by_convention(parsed: dict, requirements: dict | None = None) -> int:
    """Backups by a naming convention, so a rollback — also a rollback-only run of the order —
    finds them without any value the execution registered (user, 2026-11-05; production M-9):

    - the archive on the node: ``/tmp/${ORDERNO}/<source folder>/<name>`` (the LLM's name without
      ``$(date …)``; the source folder keeps each record's backup apart)
    - via repo server (spec 010): ``<backup_dir>/${ORDERNO}/<node name in the CIQ>/<source
      folder>/<name>`` — the node copies it there and back (O-D7)
    - a folder the activity loads into (``cp`` / ``tar -x``): only the files the load will
      overwrite are backed up (``<list folder>/items.list`` → ``overwritten.list``; the uploads of
      the loaded files move before them), the rollback removes exactly what was loaded, then
      restores — never anything else on the node (user, 2026-11-05)
    - the rollback first lists the archive (``nc_rb<k>_check`` → ``ncBackup<k>``) and decides by it:
      references to what the backup step registered become that check."""
    order = _order_ref()
    repo_route = via_repo(requirements)
    cfg = _repo_config(requirements)
    local = _local_node(parsed)
    loads = _loads(parsed)
    registered = _all_registered(parsed)
    backups = []  # (node, archive path, repo path, dropped vars, absolute sources, load dest or '')
    for name, body in _phase_bodies(parsed):
        phase_is_backup = bool(_BACKUP_PHASE.search(name) or _BACKUP_PHASE.search(str(body.get("name") or "")))
        if _ROLLBACK_PHASE.search(name) or _ROLLBACK_PHASE.search(str(body.get("name") or "")):
            continue
        out = []
        for step in list(body["steps"]):
            if not isinstance(step, dict) or _is_auto(step) or str(step.get("node", "")) == local:
                out.append(step)
                continue
            send = str(step.get("send") or "")
            parsed_tar = tar_archive(send)
            if not parsed_tar or not (phase_is_backup or _BACKUP_PHASE.search(parsed_tar[0])
                                      or _BACKUP_PHASE.search(str(step.get("command_description") or ""))):
                out.append(step)
                continue
            archive, sources = parsed_tar
            basename = _SHELL_SUBST.sub("", archive).rsplit("/", 1)[-1]
            # `tar -c…f A -C <dir> <names>`: <dir> is what is backed up — our load-aware form, or the
            # LLM's own selection `-C <dest> $(ls <dest> | grep …)` (run 3f5d8990)
            # (the archive name may hold `$(date +%Y%m%d)` — a space inside, run 46e5fee1)
            done = re.search(r"\btar\s+-?[A-Za-z]*c[A-Za-z]*\s+(?:\$\([^()]*\)|\S)+\s+-C\s+(\S+)", send)
            src = done.group(1).rstrip("/") if done else (sources[0].rstrip("/") if sources else "")
            node = str(step.get("node"))
            # a source under a folder the activity loads into (`${DEST}/$(basename X .tar)`, a
            # guess at what the load creates — run 46e5fee1): the backup is of that folder
            under = [ld["dest"] for ld in loads if ld["node"] == node and src.startswith(ld["dest"].rstrip("/") + "/")]
            if under:
                src = under[0].rstrip("/")
            mirror = src if src.startswith("/") else (f"/{src}" if src.startswith("${") else "")
            node_dir = f"/tmp/{order}{mirror}"
            path = f"{node_dir}/{basename}"
            dropped = _registers(step) - {v for v in _registers(step) if v.startswith("ncArchived")}
            k =len(backups) + 1
            listed = "^" + r"\S+".join(re.escape(x) for x in re.split(r"\$\{[^}]*\}", path)) + "$"
            dest_loads = [ld for ld in loads if ld["dest"] == src.rstrip("/") and ld["node"] == node]
            if dest_loads:
                # only what the load overwrites; the LLM's "already there?" gate read a count taken
                # before the upload — the lists decide now
                if step.get("when"):
                    terms = [t for t in re.split(r"\s*&&\s*", str(step["when"])) if not any(
                        re.search(rf"(?<![\w.]){re.escape(v)}(?![\w.])", t) for v in registered)]
                    if terms:
                        step["when"] = " && ".join(terms)
                    else:
                        step.pop("when")
                out += _take_uploads(parsed, body, {ld["src"] for ld in dest_loads})
                out += _overwritten_steps(k, step, dest_loads, src.rstrip("/"), node_dir, path)
                step["send"] = (f"if [[ -s {node_dir}/overwritten.list ]]; then tar -czvf {path} -C {src.rstrip('/')} "
                                f"$(cat {node_dir}/overwritten.list) && ls -1 {path}; else echo {NOTHING_TO_BACK_UP}; fi")
                regs = [{"regex": f"(?<{v}>{re.escape(basename)})"} for v in sorted(dropped)]
                step["register"] = regs + [{"regex": f"(?<ncArchived{k}>/\\S+)"}]
                success = listed + "|" + NOTHING_TO_BACK_UP
            else:
                if archive != path:
                    stem = re.escape(archive.rsplit("/", 1)[-1].split("$(")[0].split("${")[0])
                    # listings the LLM or the DEP-58 autofix appended for the old name go with it
                    send = re.sub(r"\s*&&\s*ls\s+-1\s+\S*" + stem + r"\S*", "", send).replace(archive, path)
                if not send.lstrip().startswith(f"mkdir -p {node_dir}"):
                    send = f"mkdir -p {node_dir} && {send.strip()}"
                if f"ls -1 {path}" not in send:
                    send += f" && ls -1 {path}"
                step["send"] = send
                if dropped:
                    step["register"] = [{"regex": f"(?<{v}>{re.escape(basename)})"} for v in sorted(dropped)]
                success = listed
            for criteria in _success_criteria(step):
                if "regex" in criteria:
                    criteria["regex"] = success
            out.append(step)
            when = str(step.get("when") or "")
            repo_path = ""
            if repo_route:
                copy_when = " && ".join(x for x in (when, f"ncArchived{k} != ''" if dest_loads else "") if x)
                repo = _repo_node(parsed, cfg)
                who = node_name_ref(node, requirements)
                if who:
                    repo_dir = f"{cfg['backup_dir'].rstrip('/')}/{order}/{who}{mirror}"
                else:  # no node name column in the CIQ: the node's hostname
                    out.append(_hostname_step(f"{AUTO_ID}bk{k}_host", node, f"bkHost{k}", copy_when))
                    repo_dir = f"{cfg['backup_dir'].rstrip('/')}/{order}/${{bkHost{k}}}{mirror}"
                repo_path = f"{repo_dir}/{basename}"
                out += [
                    _step(f"{AUTO_ID}bk{k}_dir", repo, "Create the backup directory on the repo server", copy_when,
                          send=f"mkdir -p {repo_dir}"),
                    *_repo_transfer(f"bk{k}", repo, node, node_login(parsed, repo),
                                    file_transfer(requirements, node), path, repo_path, True, copy_when,
                                    f"{node} copies the backup {basename} to the repo server"),
                ]
            absolute = not dest_loads and bool(sources) and all(x.startswith(("/", "${")) for x in sources)
            # a rollback reads none of the backup step's values (the LLM's or ncArchived<k>): a
            # rollback-only run has none of them — they all become the file check
            backups.append((node, path, repo_path, _registers(step), absolute, src.rstrip("/") if dest_loads else ""))
        body["steps"] = out
    for k, b in enumerate(backups, 1):
        _rollback_by_convention(parsed, cfg, b, k, requirements)
    if backups:
        logger.info("Structure: %d backups by convention (%s)%s", len(backups), order,
                    " on the repo server" if repo_route else " on the node")
    return len(backups)


def _success_criteria(step: dict) -> list[dict]:
    validation = step.get("validation") if isinstance(step.get("validation"), dict) else {}
    success = validation.get("success") if isinstance(validation.get("success"), dict) else {}
    return [c for c in (validation.get("criteria"), success.get("criteria")) if isinstance(c, dict)]


def _rollback_by_convention(parsed: dict, cfg: dict, backup: tuple, k: int, requirements: dict | None) -> None:
    node, path, repo_path, dropped, absolute, dest = backup
    flag = f"ncBackup{k}"
    basename = path.rsplit("/", 1)[-1]
    lst = path.rsplit("/", 1)[0]
    names = [re.escape(basename)] + [r"\$\{" + re.escape(v) + r"\}" for v in dropped]
    # a path may hold `$(basename X .tar)` — spaces inside (run 46e5fee1)
    word = r"(?:\$\([^()]*\)|\S)*"
    extract = re.compile(r"\btar\s+-?[A-Za-z]*x[A-Za-z]*\s+(" + word + r"(?:" + "|".join(names) + r"))(\s+-C\s+" + word + r")?")
    var_ref = re.compile(r"\$\{(" + "|".join(map(re.escape, dropped)) + r")\}|(?<![\w.$])(" +
                         "|".join(map(re.escape, dropped)) + r")(?![\w.])") if dropped else None
    stem = re.escape(re.sub(r"\.(tar(\.\w+)?|tgz)$", "", basename))
    # removals of the load folder's files (wildcards may hit files the activity never touched:
    # nc_rb<k>_remove removes exactly the loaded ones) and of the backup archive by a name glob
    owned = re.compile(r"^rm(?:\s+-\w+)*\s+(?:" + (re.escape(dest) + r"/.*" if dest else r"(?!)") +
                       r"|\S*/" + stem + r"\S*\*\S*)$")
    bodies = _rollback_bodies(parsed)
    if not bodies:
        return
    check_on = _repo_node(parsed, cfg) if repo_path else node
    check = _step(f"{AUTO_ID}rb{k}_check", check_on,
                  f"Is there a backup of this order to restore? ({basename})",
                  send=f"ls -1 {repo_path or path} 2>/dev/null || true",
                  register=[{"regex": f"(?<{flag}>\\S+)"}])
    # remove exactly what the activity loaded (its list), never the folder's other files
    remove = _step(f"{AUTO_ID}rb{k}_remove", node, f"Remove the files the activity loaded into {dest}",
                   send=(f"if [[ -f {lst}/items.list ]]; then rm -f $(cat {lst}/items.list | grep -v '/$' "
                         f"| sed 's#^#{dest}/#'); fi")) if dest else None
    placed = False
    for body in bodies:
        out = []
        for step in body["steps"]:
            if not isinstance(step, dict) or _is_auto(step):
                out.append(step)
                continue
            if str(step.get("node", "")) == node and owned.match(str(step.get("send") or "").strip()):
                continue  # `rm -rf <dest>/*` deleted files the activity never touched
            if var_ref is not None:
                # what the backup step registered is unknown in a rollback-only run: the file
                for key in ("when", "skip_when"):
                    if isinstance(step.get(key), str) and var_ref.search(step[key]):
                        cond = var_ref.sub(flag, step[key])
                        cond = re.sub(r"(" + flag + r")\s*(==|!=)\s*null\b", r"\1 \2 ''", cond)
                        if "||" not in cond:  # two backup values became the same check
                            terms = re.split(r"\s*&&\s*", cond)
                            cond = " && ".join(t for i, t in enumerate(terms) if t not in terms[:i])
                        step[key] = cond
                if isinstance(step.get("send"), str):
                    text = re.sub(r"/tmp/\$\{(?:" + "|".join(map(re.escape, dropped)) + r")\}", path, step["send"])
                    step["send"] = var_ref.sub(basename, text) if var_ref.search(text) else text
            text = str(step.get("send") or "")
            m = extract.search(text) if str(step.get("node", "")) == node else None
            if m and not placed:
                tail = f" -C {dest}" if dest else (" -C /" if absolute else (m.group(2) or ""))
                # exactly the restore: whether there is a backup is the step's `when` (the file check),
                # the loaded files go with nc_rb<k>_remove — the LLM's own wrappers tested another path
                # and had no `;` before `else` (run 3f5d8990)
                flags = "-xzvf" if path.endswith((".gz", ".tgz")) else "-xvf"
                step["send"] = f"tar {flags} {path}{tail}"
                when = str(step.get("when") or "")
                if f"{flag} != ''" not in when:
                    when = " && ".join(x for x in (when, f"{flag} != ''") if x)
                step["when"] = when
                if remove:
                    out.append(remove)
                out.append(check)
                if repo_path:
                    # a rollback-only run: nothing of this order exists on the node yet
                    out.append(_step(f"{AUTO_ID}rs{k}_dir", node, "Create the restore folder on the node", when,
                                     send=f"mkdir -p {lst}"))
                    out += _repo_transfer(f"rs{k}", check_on, node, node_login(parsed, check_on),
                                          file_transfer(requirements, node), path, repo_path, False, when,
                                          f"{node} copies the backup {basename} back from the repo server")
                placed = True
            out.append(step)
        body["steps"] = out
    if not placed:  # no restore step: remove what was loaded; the check still decides the rest
        bodies[0]["steps"][0:0] = [x for x in (remove, check) if x]


def _hostname_step(sid: str, node: str, var: str, when: str) -> dict:
    return _step(sid, node, f"Host name of {node} (its folder on the repo server)", when,
                 send="hostname", register=[{"regex": f"(?<{var}>[A-Za-z0-9][A-Za-z0-9._-]*)"}])


def files_via_repo(parsed: dict, requirements: dict | None = None) -> int:
    """Every file the workflow moves between the automation server and a node goes through the
    repo server (O-D2 "via repo server" moves everything, O-D7): an upload becomes automation
    server → repo (framework sftp, the LLM's step on node REPO, id ``nc_xr<k>_stage``) → node (the
    node's copy from the repo); a download node → repo (the node's copy) → automation server."""
    if not via_repo(requirements):
        return 0
    cfg = _repo_config(requirements)
    local = _local_node(parsed)
    stage_dir = f"{cfg['backup_dir'].rstrip('/')}/${{crGroup}}/files"
    count = 0

    def walk(steps: list) -> list:
        nonlocal count
        out = []
        for step in steps:
            if isinstance(step, dict) and isinstance(step.get("steps"), list):
                step["steps"] = walk(step["steps"])
            sftp = step.get("sftp") if isinstance(step, dict) else None
            node = str(step.get("node", "")) if isinstance(step, dict) else ""
            op = str((sftp or {}).get("operation") or "").lower() if isinstance(sftp, dict) else ""
            lpath, rpath = (str(sftp.get("local_path") or ""), str(sftp.get("remote_path") or "")) \
                if isinstance(sftp, dict) else ("", "")
            if _is_auto(step) or op not in ("upload", "download") or not lpath or not rpath or node == local \
                    or re.search(r"repo|backup", node, re.I):
                out.append(step)
                continue
            count += 1
            repo = _repo_node(parsed, cfg)
            when = str(step.get("when") or "")
            name = (lpath if op == "upload" else rpath).rstrip("/").rsplit("/", 1)[-1]
            node_path = rpath.rstrip("/") + "/" + name if op == "upload" and rpath.endswith("/") else rpath
            staged = f"{stage_dir}/{name}"
            step["id"] = f"{AUTO_ID}xr{count}_stage"
            step["node"] = repo
            sftp["remote_path"] = staged
            mkdir = _step(f"{AUTO_ID}xr{count}_dir", repo, "Create the file directory on the repo server", when,
                          send=f"mkdir -p {stage_dir}")
            copy_ = _repo_transfer(f"xr{count}", repo, node, node_login(parsed, repo),
                                   file_transfer(requirements, node), node_path, staged, op == "download", when,
                                   f"{node} copies {name} {'to' if op == 'download' else 'from'} the repo server")
            out += [mkdir, step, *copy_] if op == "upload" else [mkdir, *copy_, step]
        return out

    for _, body in _phase_bodies(parsed):
        body["steps"] = walk(body["steps"])
    if count:
        logger.info("Structure: %d file transfers go through the repo server", count)
    return count


def _unstage(parsed: dict) -> int:
    """``flatten`` for ``files_via_repo``: each staged step back on its node and path (read from the
    node-side checksum of the same transfer; a record loop may hold one and not the other)."""
    steps = [s for _, body in _phase_bodies(parsed) for s in _all_steps(body["steps"])]
    origin = {}
    for s in steps:
        m = re.fullmatch(rf"{AUTO_ID}(xr\d+)_sum_node", str(s.get("id") or ""))
        if m:
            path = str(s.get("send", ""))[len("sha256sum "):]
            origin[m.group(1)] = (str(s.get("node")), path.replace("${" + ITEM_VAR + ".data.", "${" + _REC0))
    restored = 0
    for s in steps:
        m = re.fullmatch(rf"{AUTO_ID}(xr\d+)_stage", str(s.get("id") or ""))
        if m and m.group(1) in origin and isinstance(s.get("sftp"), dict):
            s.pop("id")
            s["node"], s["sftp"]["remote_path"] = origin[m.group(1)]
            restored += 1
    return restored


# --------------------------------------------------------------------------- entry point

def select_phases(parsed: dict, requirements: dict | None = None) -> int:
    """The run's phases (spec 010): each generated phase is put under its model phase
    (``run_options.canonical_phase`` — two that map to the same one are merged), in model order;
    phases the run's options switch off are dropped. Without options every phase is kept."""
    from shared import run_options
    phases = parsed.get("phases")
    if not isinstance(phases, dict) or not phases:
        return 0
    raw = (requirements or {}).get("run_options") if isinstance(requirements, dict) else None
    keep = set(run_options.selected(raw))
    grouped: dict[str, dict] = {}
    for key, body in phases.items():
        if not isinstance(body, dict) or not isinstance(body.get("steps"), list):
            continue
        canon = run_options.canonical_phase(str(key), body)
        if canon in grouped:
            grouped[canon]["steps"] = list(grouped[canon]["steps"]) + list(body["steps"])
        else:
            grouped[canon] = body
    new = {k: grouped[k] for k in run_options.KEYS if k in grouped and k in keep}
    dropped = [k for k in grouped if k not in keep]
    if dropped:
        logger.info("Phases switched off for this run: %s", ", ".join(dropped))
    changed = int(list(new) != list(phases))
    parsed["phases"] = new
    return changed


def apply(parsed: dict, requirements: dict | None = None) -> int:
    """flatten → repo backups → record loops → transfer checksums → execution contract
    (spec 011; idempotent)."""
    from . import contract
    flatten(parsed)
    changes = select_phases(parsed, requirements)
    changes += loaded_members_only(parsed)
    changes += backups_by_convention(parsed, requirements)
    changes += files_via_repo(parsed, requirements)
    changes += loop_record_steps(parsed)
    changes += verify_transfers(parsed)
    changes += contract.apply(parsed)
    return changes
