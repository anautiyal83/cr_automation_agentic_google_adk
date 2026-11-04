"""Workflow structure step 6 adds after the LLM — deterministic and idempotent.

1. Every CIQ record (DEP-9): steps that use record columns run in a ``type: loop`` over
   ``${configSequences.0.records}`` (item ``rec``, columns ``${rec.data.<Col>}``). With
   ``records.0`` only, a node group with 3 announcement files loaded one of them. The JAR
   runs a step as a loop only with ``type: loop``; ``reverse_order`` must be set (it NPEs
   on null) — both verified against cli-automation execute/generate 2026-11-03.
2. Backup "via repo server" (spec 010 O-D2 / O-D7, otherwise backups stay in the node's /tmp):
   the repo server (``execution.repo_server``, workflow node ``REPO``) fetches each ``tar -c``
   archive of a backup phase from the node with ``scp`` / ``sftp`` (node type's ``file_transfer``,
   node password by ``expect_reply``) and copies it back before a rollback extracts it; every file
   the workflow moves between the automation server and a node is staged on the repo server too.
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
    if _is_auto(step) and str(step.get("id")).endswith("_copy"):  # the repo server's scp / sftp
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
        if repo not in used:  # added by backup_to_repo
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


def node_login(parsed: dict, node: str, requirements: dict | None) -> tuple[str, str, str]:
    """(user, address, password) the repo server logs in to ``node`` with (O-Q8): the node's own
    SSH user, address and password secret of the workflow; ``requirements.node_addresses`` (node →
    address, e.g. a CIQ node value ``${MRF.OAM_IP}``) overrides the address."""
    spec = (parsed.get("nodes") or {}).get(node)
    ssh = spec.get("ssh") if isinstance(spec, dict) and isinstance(spec.get("ssh"), dict) else {}
    auth = ssh.get("auth") if isinstance(ssh.get("auth"), dict) else {}
    override = ((requirements or {}).get("node_addresses") or {}) if isinstance(requirements, dict) else {}
    address = str((override.get(node) if isinstance(override, dict) else "") or ssh.get("host") or node)
    return str(ssh.get("user") or "root"), address, str(auth.get("password") or ssh.get("password") or "")


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


def repo_copy(method: str, user: str, address: str, node_path: str, repo_path: str, download: bool) -> str:
    """The command the repo server runs to copy a file from (``download``) or to the node."""
    remote = f"{user}@[{address}]:"
    if method == "scp":
        return f"scp {remote}{node_path} {repo_path}" if download else f"scp {repo_path} {remote}{node_path}"
    if download:
        return f"sftp {remote}{node_path} {repo_path}"
    folder, _, name = node_path.rpartition("/")
    return f"sftp {remote}{folder or '/'} <<< 'put {repo_path} {name}'"


def _repo_transfer(tag: str, repo: str, node: str, login: tuple[str, str, str], method: str,
                   node_path: str, repo_path: str, download: bool, when: str, what: str) -> list[dict]:
    """Checksum at the source, the repo server's copy (node password by ``expect_reply``), checksum
    at the destination compared with the source's. Ids ``nc_<tag>_sum_node`` / ``_copy`` /
    ``_sum_repo`` (``flatten`` reads the node side back)."""
    user, address, password = login
    src_var, dst_var = f"{tag}Src", f"{tag}Dst"
    node_sum = (node, node_path, f"nc_{tag}_sum_node", f"file on {node}")
    repo_sum = (repo, repo_path, f"nc_{tag}_sum_repo", "file on the repo server")
    src, dst = (node_sum, repo_sum) if download else (repo_sum, node_sum)
    copy_ = _step(f"nc_{tag}_copy", repo, what, when,
                  send=repo_copy(method, user, address, node_path, repo_path, download))
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


def backup_to_repo(parsed: dict, requirements: dict | None = None) -> int:
    """Store every backup archive on the repo server; restore it from there on rollback.

    Only when the run chose Backup "via repo server" (spec 010 O-D2); otherwise the backup stays
    on the node (``/tmp``) where the backup step wrote it and the rollback restores it from there.
    The repo server runs the copies to and from the node (O-D7)."""
    if not via_repo(requirements):
        return 0
    cfg = _repo_config(requirements)
    local = _local_node(parsed)
    backups = []  # (node, archive, repo_path, dropped vars)
    for name, body in _phase_bodies(parsed):
        # a backup phase, or a backup archive in another phase (the LLM put it in the
        # pre-checks of a fresh MRF workflow)
        phase_is_backup = bool(_BACKUP_PHASE.search(name) or _BACKUP_PHASE.search(str(body.get("name") or "")))
        out = []
        for step in body["steps"]:
            out.append(step)
            if not isinstance(step, dict) or _is_auto(step) or str(step.get("node", "")) == local:
                continue
            send = str(step.get("send") or "")
            parsed_tar = tar_archive(send)
            if not parsed_tar:
                continue
            archive, sources = parsed_tar
            if not (phase_is_backup or _BACKUP_PHASE.search(archive)
                    or _BACKUP_PHASE.search(str(step.get("command_description") or ""))):
                continue
            canonical = _SHELL_SUBST.sub("", archive)
            basename = canonical.rsplit("/", 1)[-1]
            if canonical != archive:  # the name must be known to fetch it again
                send = send.replace(archive, canonical, 1)
            # The variables the step registered for the archive (tar prints no archive name, so
            # the LLM's regexes never matched) now hold its file name, from a listing of it:
            # later `/tmp/${backupFileName}` and "no backup taken" checks keep their meaning
            dropped = _registers(step)
            listing = f" && ls -1 {canonical}"
            if dropped and listing not in send:
                send += listing
            step["send"] = send
            if dropped:
                step["register"] = [{"regex": f"(?<{v}>{re.escape(basename)})"} for v in sorted(dropped)]
            repo = _repo_node(parsed, cfg)
            node = str(step.get("node"))
            k = len(backups) + 1
            src = sources[0].rstrip("/") if sources else ""
            mirror = src if src.startswith("/") else (f"/{src}" if src.startswith("${") else "")
            # Per node by its hostname: ${node} empties the MOP of a node group with several
            # nodes (cli-automation generate, 2026-11-03) and node fields need not be unique
            host = f"bkHost{k}"
            repo_tpl = f"{cfg['backup_dir'].rstrip('/')}/${{crGroup}}/{{HOST}}{mirror}"
            repo_dir = repo_tpl.replace("{HOST}", "${" + host + "}")
            when = str(step.get("when") or "")
            out += [
                _hostname_step(f"{AUTO_ID}bk{k}_host", node, host, when),
                _step(f"{AUTO_ID}bk{k}_dir", repo, "Create the backup directory on the repo server", when,
                      send=f"mkdir -p {repo_dir}"),
                *_repo_transfer(f"bk{k}", repo, node, node_login(parsed, node, requirements),
                                file_transfer(requirements, node), canonical, f"{repo_dir}/{basename}", True, when,
                                f"The repo server fetches the backup {basename} from {node}"),
            ]
            absolute = bool(sources) and all(s.startswith(("/", "${")) for s in sources)
            backups.append((node, canonical, f"{repo_tpl}/{basename}", dropped, absolute))
        body["steps"] = out
    if not backups:
        return 0
    restored = sum(_restore_from_repo(parsed, cfg, b, k + 1, requirements) for k, b in enumerate(backups))
    logger.info("Structure: %d backups stored on the repo server, %d restores from it", len(backups), restored)
    return len(backups)


def _restore_from_repo(parsed: dict, cfg: dict, backup: tuple, k: int, requirements: dict | None) -> int:
    node, archive, repo_tpl, dropped, absolute = backup
    host = f"rsHost{k}"
    repo_path = repo_tpl.replace("{HOST}", "${" + host + "}")
    repo = _repo_node(parsed, cfg)
    basename = archive.rsplit("/", 1)[-1]
    # the archive as the rollback names it: literally or through a variable the backup registered
    names = [re.escape(basename)] + [r"\$\{" + re.escape(v) + r"\}" for v in dropped]
    extract = re.compile(r"\btar\s+-?[A-Za-z]*x[A-Za-z]*\s+(\S*(?:" + "|".join(names) + r"))(\s+-C\s+\S+)?")
    for name, body in _phase_bodies(parsed):
        if not (_ROLLBACK_PHASE.search(name) or _ROLLBACK_PHASE.search(str(body.get("name") or ""))):
            continue
        out, done = [], False
        for step in body["steps"]:
            if not done and isinstance(step, dict) and str(step.get("node", "")) == node and not _is_auto(step):
                text = str(step.get("send") or "")
                m = extract.search(text)
                if m:
                    when = str(step.get("when") or "")
                    # the restored file is the archive itself; members are the backed-up absolute
                    # paths without the leading "/"
                    tail = " -C /" if absolute else (m.group(2) or "")
                    step["send"] = text[:m.start(1)] + archive + tail + text[m.end():]
                    out += [
                        _hostname_step(f"{AUTO_ID}rs{k}_host", node, host, when),
                        *_repo_transfer(f"rs{k}", repo, node, node_login(parsed, node, requirements),
                                        file_transfer(requirements, node), archive, repo_path, False, when,
                                        f"The repo server copies the backup {basename} back to {node}"),
                    ]
                    done = True
            out.append(step)
        body["steps"] = out
        if done:
            return 1
    return 0


def _hostname_step(sid: str, node: str, var: str, when: str) -> dict:
    return _step(sid, node, f"Host name of {node} (its folder on the repo server)", when,
                 send="hostname", register=[{"regex": f"(?<{var}>[A-Za-z0-9][A-Za-z0-9._-]*)"}])


def files_via_repo(parsed: dict, requirements: dict | None = None) -> int:
    """Every file the workflow moves between the automation server and a node goes through the
    repo server (O-D2 "via repo server" moves everything, O-D7): an upload becomes automation
    server → repo (framework sftp, the LLM's step on node REPO, id ``nc_xr<k>_stage``) → node (the
    repo server's copy); a download node → repo (the repo's copy) → automation server."""
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
            copy_ = _repo_transfer(f"xr{count}", repo, node, node_login(parsed, node, requirements),
                                   file_transfer(requirements, node), node_path, staged, op == "download", when,
                                   f"The repo server copies {name} {'from' if op == 'download' else 'to'} {node}")
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
    changes += backup_to_repo(parsed, requirements)
    changes += files_via_repo(parsed, requirements)
    changes += loop_record_steps(parsed)
    changes += verify_transfers(parsed)
    changes += contract.apply(parsed)
    return changes
