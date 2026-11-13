"""Simulator user guide (step 8): how to start the UC's MockWire instances by hand, log in, and run the
execution YAML against them — with start / stop scripts beside the configs::

    <run_dir>/simulator/SIMULATOR_GUIDE.md
    <run_dir>/simulator/start_simulator.ps1 · start_simulator.sh   — every instance in the background
    <run_dir>/simulator/stop_simulator.ps1  · stop_simulator.sh

Built from the files on disk (``manifest.json``, ``config/<instance>.yaml``), so the UC export
(``tools/uc_export.py``) writes it for a run set up before the guide existed. The scripts use the
``mockwire.jar`` beside them (the export puts it there) unless given another path.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from ruamel.yaml import YAML

logger = logging.getLogger(__name__)

GUIDE_NAME = "SIMULATOR_GUIDE.md"
SCRIPT_NAMES = ("start_simulator.ps1", "start_simulator.sh", "stop_simulator.ps1", "stop_simulator.sh")
DEFAULT_TTL_MINUTES = 240
MAX_LISTED_COMMANDS = 25
MAX_SHOWN_CHARS = 110
_RESERVED_KEYS = {"config", "auth", "niam", "transfer", "vfs", "state", "ctrl+p+e"}


def write_guide(sim_dir: str | Path, uc_name: str, run_id: str = "") -> list[Path]:
    """Write the guide and the start / stop scripts for the instances in ``manifest.json``.
    Returns the files written (none without instances)."""
    sim_dir = Path(sim_dir)
    instances = _instances(sim_dir)
    if not instances:
        return []
    configs = _configs(sim_dir, instances)
    texts = {
        GUIDE_NAME: guide_markdown(sim_dir, uc_name, instances, configs, run_id),
        "start_simulator.ps1": _start_ps1(uc_name, instances),
        "start_simulator.sh": _start_sh(uc_name, instances),
        "stop_simulator.ps1": _STOP_PS1,
        "stop_simulator.sh": _STOP_SH,
    }
    written = []
    for name, text in texts.items():
        path = sim_dir / name
        # the .sh scripts must keep LF line ends on Windows too
        with open(path, "w", encoding="utf-8", newline="\r\n" if name.endswith(".ps1") else "\n") as fh:
            fh.write(text)
        written.append(path)
    logger.info("Simulator guide written: %s", sim_dir / GUIDE_NAME)
    return written


def _instances(sim_dir: Path) -> list[dict]:
    try:
        manifest = json.loads((sim_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [i for i in manifest.get("instances") or [] if i.get("name") and i.get("port")]


def _configs(sim_dir: Path, instances: list[dict]) -> dict[str, dict]:
    """The config of every process and of every node behind a gateway (``config/<name>.yaml``)."""
    names = [i["name"] for i in instances] + [pr["name"] for i in instances for pr in i.get("profiles") or []]
    return {n: _load(sim_dir / "config" / f"{n}.yaml") for n in names}


def _servers(instances: list[dict]) -> list[dict]:
    """The simulated nodes: a gateway's nodes each reached on the gateway's port (``gateway`` names it, MW-13),
    the other processes as they are."""
    out = []
    for i in instances:
        if i.get("profiles"):
            out += [{"name": pr["name"], "port": i["port"], "nodes": pr.get("nodes") or [pr["name"]],
                     "gateway": i["name"]} for pr in i["profiles"]]
        else:
            out.append(i)
    return out


def _gateway_lines(instances: list[dict], configs: dict[str, dict]) -> list[str]:
    """How a gateway routes: one login, the neId picks the node."""
    out = []
    for gw in (i for i in instances if i.get("profiles")):
        cfg = configs.get(gw["name"]) or {}
        user, password = _login(cfg)
        niam = cfg.get("niam") or {}
        prompts = niam.get("prompts") or {}
        out.append(f"- **{gw['name']}** (port {gw['port']}) is the access gateway every node below is reached "
                   f"through, as in production (one NIAM host for all of them): `ssh {user}@localhost -p {gw['port']}`, "
                   f"password `{password}`, then `{str(prompts.get('neIdPrompt') or 'neId: ').strip()}` — the neId "
                   "picks the node:")
        for pr in niam.get("profiles") or []:
            ids = [str(x) for x in pr.get("neIds") or []]
            out.append(f"  - {', '.join(f'`{x}`' for x in ids[:3])}{' …' if len(ids) > 3 else ''} → **{pr.get('name')}**"
                       f" ({len(ids)} neId{'s' if len(ids) != 1 else ''})")
        out.append("  - Any other neId is refused (`NE (<id>) is not found in the inventory.`) and asked again.")
    return out


def _load(path: Path) -> dict:
    try:
        return YAML(typ="safe").load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _java_line(name: str, port: int, jar: str = "mockwire.jar", sep: str = "/") -> str:
    return (f"java -jar {jar} --config config{sep}{name}.yaml --responses outputs{sep}{name}{sep}responses "
            f"--port {port} --events-file logs{sep}{name}.events.jsonl --vfs-root vfs{sep}{name} "
            f"--ttl-minutes {DEFAULT_TTL_MINUTES}")


def simulator_sections(sim_dir: str | Path, uc_name: str, run_id: str = "") -> str:
    """The simulator part of the UC export's user guide (``tools/uc_export.py``): the guide below without its title
    and file table, headings one level down. "" without a started simulator."""
    sim_dir = Path(sim_dir)
    instances = _instances(sim_dir)
    if not instances:
        return ""
    configs = _configs(sim_dir, instances)
    body = guide_markdown(sim_dir, uc_name, instances, configs, run_id, standalone=False)
    out, fenced = [], False
    for line in body.split("\n"):
        if line.startswith("```"):
            fenced = not fenced
        out.append("#" + line if not fenced and re.match(r"#+ ", line) else line)
    return "\n".join(out)


def guide_markdown(sim_dir: Path, uc_name: str, instances: list[dict], configs: dict[str, dict],
                   run_id: str = "", standalone: bool = True) -> str:
    """The UC's simulator: its servers and ports, how they work together (NIAM login, su, nested shells, repo
    server copies, file triggers), the workflow's commands on each server with what the simulator answers, and a
    session to try each server by hand (user, 2026-11-13: the guide must be specific to the UC)."""
    overlay = _load(sim_dir / f"{uc_name}.yaml")
    wf_nodes = overlay.get("nodes") if isinstance(overlay.get("nodes"), dict) else {}
    servers = _servers(instances)
    served = {n: i for i in servers for n in (i.get("nodes") or [i["name"]])}
    names = ", ".join(f"**{i['name']}** (port {i['port']}"
                      + (f", nodes {', '.join(pr['name'] for pr in i['profiles'])}" if i.get("profiles") else "")
                      + ")" for i in instances)
    lines = [
        f"# {uc_name} — network simulator" if standalone else "# Network simulator",
        "",
        ("" if standalone else "Everything in this part runs inside the `simulator/` folder (`cd simulator`). ")
        + f"This UC's workflow talks to {len(instances)} server{'s' if len(instances) > 1 else ''}: {names}. "
        "Each is one MockWire SSH server on localhost that answers like the real node did in the uploaded "
        "transcripts, as step 8 (Simulator Setup) prepared it" + (f" in run `{run_id}`" if run_id else "")
        + ". Step 9 (QA-Execution) ran the workflow against them.",
        "",
        "## Servers",
        "",
        "| Server | Port | Simulates (workflow nodes) | Host name | Login | What it is |",
        "|---|---|---|---|---|---|",
    ]
    for inst in instances:
        if inst.get("profiles"):
            cfg = configs.get(inst["name"]) or {}
            user, password = _login(cfg)
            lines.append(f"| {inst['name']} | {inst['port']} | (gateway) | — | {user or '—'} / {password or '—'} | "
                         "Access gateway: one login, the neId picks the node |")
    for inst in servers:
        cfg = configs.get(inst["name"]) or {}
        user, password = _login(cfg)
        port = f"{inst['port']} (via {inst['gateway']})" if inst.get("gateway") else inst["port"]
        lines.append(f"| {inst['name']} | {port} | {', '.join(inst.get('nodes') or [inst['name']])} | "
                     f"{(cfg.get('config') or {}).get('hostname') or '—'} | {user or '—'} / {password or '—'} | "
                     f"{_role(inst, cfg, wf_nodes)} |")
    lines += [
        "",
        "Requirements: Java 8 or later on the PATH (`java -version`); `mockwire.jar` in this folder (the UC export "
        "includes it) or its path given to the start script.",
        "",
        "## Start",
        "",
        "All servers at once, in the background (ready when each `logs/<server>.log` shows `listening on port`):",
        "",
        "| Where | Command |",
        "|---|---|",
        "| Windows (PowerShell) | `.\\start_simulator.ps1` &nbsp; options `-Jar <path>`, `-TtlMinutes <n>`, `-Clean` |",
        "| Linux / WSL / macOS | `./start_simulator.sh [path/to/mockwire.jar]` &nbsp; `CLEAN=1`, `TTL_MINUTES=<n>` |",
        "",
        "Or each server in its own terminal, from this folder:",
        "",
        "```",
    ]
    lines += [f"# {i['name']} ({', '.join(pr['name'] for pr in i['profiles']) if i.get('profiles') else ', '.join(i.get('nodes') or [i['name']])})"
              f"\n{_java_line(i['name'], i['port'])}" for i in instances]
    lines += [
        "```",
        "",
        f"They stop on their own after the TTL ({DEFAULT_TTL_MINUTES} minutes), or with `.\\stop_simulator.ps1` / "
        "`./stop_simulator.sh`. Clean (`-Clean` / `CLEAN=1`) empties the simulated file systems (`vfs/`) first — "
        "every step 9 case started from empty ones; without it the files of an earlier session are still there.",
        "",
        "## How the servers work together",
        "",
    ]
    lines += _gateway_lines(instances, configs)
    lines += _together(servers, configs, wf_nodes, served)
    lines += _walkthrough(overlay, configs, served)
    lines += _by_hand(servers, configs, wf_nodes, overlay, served)
    if standalone:
        lines += _files_section(uc_name)
    lines += _workflow_section(sim_dir, uc_name, instances)
    return "\n".join(lines) + "\n"


def _login(cfg: dict) -> tuple[str, str]:
    users = ((cfg.get("auth") or {}).get("users") or [{}])
    return str(users[0].get("username") or ""), str(users[0].get("password") or "")


def _is_repo(inst: dict) -> bool:
    return any("repo" in str(n).lower() for n in [inst["name"], *(inst.get("nodes") or [])])


def _role(inst: dict, cfg: dict, wf_nodes: dict) -> str:
    if _is_repo(inst):
        return "Repository server: input files and backups are copied to / from it"
    if (cfg.get("niam") or {}).get("enabled"):
        return "Network node, reached through NIAM (asks `neId:` after the password)"
    return "Network node"


def _wf_login(wf_nodes: dict, node: str) -> dict:
    v = wf_nodes.get(node) if isinstance(wf_nodes.get(node), dict) else {}
    return v.get("ssh") or v.get("sftp") or {}


def _together(instances: list[dict], configs: dict[str, dict], wf_nodes: dict, served: dict) -> list[str]:
    out = []
    for inst in instances:
        cfg = configs.get(inst["name"]) or {}
        name, port = inst["name"], inst["port"]
        user, password = _login(cfg)
        for node in inst.get("nodes") or [name]:
            v = wf_nodes.get(node) if isinstance(wf_nodes.get(node), dict) else {}
            if str(v.get("type") or "").lower() == "sftp" or "sftp" in str(v.get("plugin") or ""):
                out.append(f"- **{name}** ← the automation server (where the workflow runs) copies files to and from it "
                           f"with SFTP on port {port} (workflow node `{node}`, user `{user}`, password `{password}`).")
                continue
            ssh = _wf_login(wf_nodes, node)
            via = f" through gateway **{inst['gateway']}**" if inst.get("gateway") else ""
            text = (f"- **{node}** → server **{name}**{via}: the workflow logs in with "
                    f"`ssh {ssh.get('user') or user}@localhost -p {port}`, password `{password}`")
            if ssh.get("ne_id"):
                text += f"; NIAM asks `neId:` and the workflow answers `{ssh['ne_id']}`"
            elevate = (cfg.get("ctrl+p+e") or {}).get("pushPrompt")
            if ssh.get("elevate_to_root") and elevate:
                text += f"; then **Ctrl+P, Ctrl+E** gives the root prompt `{elevate}`"
            out.append(text + ".")
        for u in ((cfg.get("auth") or {}).get("users") or [])[1:]:
            out.append(f"  - On {name}, `su - {u.get('username')}` (password `{u.get('password')}`) → `{u.get('prompt')}`.")
        for key, entry in cfg.items():
            if key in _RESERVED_KEYS or not isinstance(entry, dict):
                continue
            prompt = entry.get("pushPrompt") or next(
                (a.get("pushPrompt") for q in entry.get("interactive") or [] if isinstance(q, dict)
                 for a in (q.get("answers") or {}).values() if isinstance(a, dict) and a.get("pushPrompt")), None)
            if prompt:
                question = next((q.get("question") for q in entry.get("interactive") or [] if isinstance(q, dict)), "")
                out.append(f"  - On {name}, `{_shown(key)}` opens a nested shell"
                           + (f" (asks `{question.strip()}`)" if question else "") + f" → `{prompt}`; `exit` leaves it.")
        for t in (cfg.get("vfs") or {}).get("triggers") or []:
            if isinstance(t, dict) and t.get("onCreate"):
                out.append(f"  - On {name}, a file written to `{_pattern_path(t['onCreate'])}` makes the node create "
                           f"`{t.get('create')}` ({len(t.get('lines') or [])} lines: the recorded response) — "
                           "the node's own processing of an uploaded file.")
        for peer, spec in ((cfg.get("transfer") or {}).get("peers") or {}).items():
            target = served.get(peer, {}).get("name", peer)
            out.append(f"  - On {name}, `scp` / `sftp … @{peer}` reach server **{target}**'s files (`vfs/{target}/`, "
                       f"password `{(spec or {}).get('password', '')}`): what the node copies there lands on {target}.")
        dirs = (cfg.get("vfs") or {}).get("dirs") or []
        if dirs:
            out.append(f"  - {name}'s simulated folders: " + ", ".join(f"`{d}`" for d in dirs[:8])
                       + (" …" if len(dirs) > 8 else "") + f" (in `vfs/{name}/`; `ls cat cp mv tar diff …` work on them).")
    local = [n for n, v in wf_nodes.items() if isinstance(v, dict)
             and "local" in (str(v.get("type") or "").lower(), str(v.get("plugin") or "").lower())]
    if local:
        out.append(f"- Steps on node `{local[0]}` run on the machine that runs the workflow (helper scripts in "
                   "`scripts/`), not in the simulator.")
    return out


def _pattern_path(regex: str) -> str:
    return re.sub(r"\(\?<(\w+)>[^)]*\)", r"<\1>", regex).replace("\\", "")


def _shown(key: str, limit: int = MAX_SHOWN_CHARS) -> str:
    text = key[len("regex:"):] if key.startswith("regex:") else key
    if key.startswith("regex:"):  # `^ssh … dsadmin@\S+\s*$` → `ssh … dsadmin@<…>`
        text = re.sub(r"^\^|\\s\*\$$|\$$", "", text)
        text = re.sub(r"\(\?<\w+>[^)]*\)|\\S\+|\.\+\??|\[\^'\]\*", "<…>", text)
        text = re.sub(r"\\s[*+]", " ", text)
        text = re.sub(r"\\(.)", r"\1", text)
    text = text.replace("\n", " ⏎ ")
    return text if len(text) <= limit else text[:limit] + "…"


def _answer(cfg: dict, cmd: str) -> str:
    """What the server answers to a workflow command, in a few words."""
    from .skill import _SIM_BUILTINS, _find_entry, command_key_regex
    from .vfs_commands import FILE_COMMANDS, pod_script
    entries = {k: v for k, v in cfg.items() if k not in _RESERVED_KEYS}
    # as at run time: a placeholder holds one value (`${rec.data.Test IMSI}` has a space, a `\S+` key never matched it)
    key, entry = _find_entry(entries, cmd, re.sub(r"\$\{[^{}]+\}", "X1", cmd))
    if key is None:
        try:
            body = re.compile(command_key_regex(cmd.strip()))
            key = next((k for k in entries if not str(k).startswith("regex:") and body.fullmatch(str(k))), None)
            entry = entries.get(key) if key is not None else None
        except re.error:
            pass
    if isinstance(entry, dict):
        if entry.get("pushPrompt"):
            return f"opens a nested shell → `{entry['pushPrompt']}`"
        if entry.get("interactive"):
            q = next((x.get("question") for x in entry["interactive"] if isinstance(x, dict)), "")
            return f"asks `{str(q).strip()}`, then opens a nested shell"
        if entry.get("responses"):
            states = [r for r in entry["responses"] if isinstance(r, dict)]
            return (f"changes once the activity ran: {_first(states[-1].get('lines'))} before, "
                    f"{_first(states[0].get('lines'))} after")
        return _first(entry.get("lines"))
    first = cmd.strip().split(" ", 1)[0] if cmd.strip() else ""
    if first == "su":
        user = next((u for u in (cfg.get("auth") or {}).get("users") or [] if cmd.strip().endswith(str(u.get("username")))), None)
        return f"switches user → `{user.get('prompt')}`" if user else "switches user (`su`)"
    if first in ("exit", "logout"):
        return "leaves the user / nested shell it is in"
    if pod_script(cmd):
        return "the command inside the pod runs on the node's simulated files"
    if first in _SIM_BUILTINS | FILE_COMMANDS | {"if", "for", "cd", "export", "source", ".", "printf", "date", "ls"} \
            or re.match(r"^\w+=", first):
        return "the node's built-in shell (its simulated files)"
    return "**no answer** (`command not found`)"


def _first(lines) -> str:
    rows = [str(x) for x in (lines or []) if str(x).strip()]
    if not rows:
        return "(no output)"
    head = rows[0].strip()
    head = head if len(head) <= 70 else head[:70] + "…"
    return f"`{head}`" + (f" (+{len(rows) - 1} lines)" if len(rows) > 1 else "")


def _steps(steps, depth=0):
    for st in steps or []:
        if not isinstance(st, dict):
            continue
        if st.get("steps"):
            yield from _steps(st["steps"], depth + 1)
        else:
            yield st, depth


def _flow(rows: list, served: dict) -> str:
    """The servers a phase moves between, in order (`RDS (5) → automation server (2) → PGW (7) → …`), with what
    happens on PGW-like nodes when a file is uploaded."""
    parts: list[list] = []
    for st, _ in rows:
        node = str(st.get("node") or "")
        inst = served.get(node)
        name = inst["name"] if inst else "automation server"
        what = "SFTP" if isinstance(st.get("sftp"), dict) else ""
        if parts and parts[-1][0] == name:
            parts[-1][1] += 1
            parts[-1][2] |= {what} - {""}
        else:
            parts.append([name, 1, {what} - {""}])
    return " → ".join(f"**{n}** ({c} step{'s' if c > 1 else ''}{', file transfer' if w else ''})" for n, c, w in parts)


def _walkthrough(overlay: dict, configs: dict[str, dict], served: dict) -> list[str]:
    phases = overlay.get("phases") if isinstance(overlay.get("phases"), dict) else {}
    if not phases:
        return []
    out = ["", "## What the workflow does on each server", "",
           "In order, per phase: the server each step runs on, its command (as in the workflow; `${…}` are "
           "filled from the CIQ and earlier steps) and what the simulator answers.", ""]
    for key, body in phases.items():
        if not isinstance(body, dict):
            continue
        rows = list(_steps(body.get("steps")))
        if not rows:
            continue
        title = body.get("name") or key
        from skills.network_config_gen.contract import ROLLBACK, phase_kind
        note = (" — runs when an earlier step failed, or alone in a rollback-only run"
                if phase_kind(str(key), body) == ROLLBACK else "")
        out += [f"### {title}{note}", "", f"Flow: {_flow(rows, served)}", ""]
        for n, (st, depth) in enumerate(rows[:60], 1):
            node = str(st.get("node") or "")
            inst = served.get(node)
            each = " (each CIQ record)" if depth else ""
            if isinstance(st.get("sftp"), dict):
                sf = st["sftp"]
                where = f"**{inst['name']}** ({inst['port']})" if inst else f"`{node}`"
                out.append(f"{n}. {where}{each} — SFTP {sf.get('operation')} `{sf.get('local_path')}` ↔ "
                           f"`{sf.get('remote_path')}`")
                continue
            send = str(st.get("send") or "").strip()
            if not send:
                continue
            shown = send.replace("\n", " ⏎ ")
            shown = shown if len(shown) <= 140 else shown[:140] + "…"
            if inst is None:
                out.append(f"{n}. `{node}` (the machine running the workflow){each} — `{shown}`")
            else:
                out.append(f"{n}. **{inst['name']}** ({inst['port']}){each} — `{shown}` → "
                           f"{_answer(configs.get(inst['name']) or {}, send)}")
        if len(rows) > 60:
            out.append(f"… and {len(rows) - 60} more steps.")
        out.append("")
    return out


def _by_hand(instances: list[dict], configs: dict[str, dict], wf_nodes: dict, overlay: dict,
             served: dict) -> list[str]:
    out = ["## Try it by hand", "",
           "Windows: use `UserKnownHostsFile=NUL` instead of `/dev/null`.", ""]
    phases = overlay.get("phases") if isinstance(overlay.get("phases"), dict) else {}
    sends: dict[str, list[str]] = {}
    for body in phases.values():
        for st, _ in _steps((body or {}).get("steps") if isinstance(body, dict) else []):
            inst = served.get(str(st.get("node") or ""))
            if inst and isinstance(st.get("send"), str) and "${" not in st["send"] and "\n" not in st["send"]:
                sends.setdefault(inst["name"], []).append(st["send"].strip())
    for inst in instances:
        cfg = configs.get(inst["name"]) or {}
        if _is_repo(inst) and not sends.get(inst["name"]):
            user, password = _login(cfg)
            out += [f"### {inst['name']} (port {inst['port']})", "", "```",
                    f"sftp -P {inst['port']} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null {user}@localhost",
                    f"# password: {password}", "ls", "```", ""]
            continue
        user, password = _login(cfg)
        node = next(iter(inst.get("nodes") or [inst["name"]]))
        ssh = _wf_login(wf_nodes, node)
        session = [f"ssh -p {inst['port']} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
                   f"{ssh.get('user') or user}@localhost", f"# password: {password}"]
        if ssh.get("ne_id"):
            session.append(f"# neId: {ssh['ne_id']}")
        if ssh.get("elevate_to_root") and (cfg.get("ctrl+p+e") or {}).get("pushPrompt"):
            session.append("# press Ctrl+P, then Ctrl+E (root)")
        tried = 0
        for cmd in dict.fromkeys(sends.get(inst["name"], [])):
            answer = _answer(cfg, cmd)
            if "no answer" in answer or "built-in" in answer:
                continue
            session += [cmd, f"#   → {answer.replace('`', '')}"]
            tried += 1
            if tried == 4:
                break
        out += [f"### {inst['name']} (port {inst['port']})", "", "```", *session, "```", ""]
    return out


def _files_section(uc_name: str) -> list[str]:
    return [
        "", "## Files", "",
        "| Path | What it is |",
        "|---|---|",
        "| `config/<instance>.yaml` | The node: prompts, users, NIAM, and the answer to every workflow command |",
        "| `outputs/<instance>/responses/` | Answers kept as files (referenced from the config) |",
        "| `vfs/<instance>/` | The node's simulated file system (uploads, archives, backups land here) |",
        "| `logs/<instance>.log` | The instance's log |",
        "| `logs/<instance>.events.jsonl` | One line per command received; `\"matchedKey\": null` with a "
        "non-builtin `source` = the simulator had no answer |",
        f"| `{uc_name}.yaml` | The execution YAML with every node pointed at its instance here (localhost) |",
    ]


def _workflow_section(sim_dir: Path, uc_name: str, instances: list[dict]) -> list[str]:
    run_dir = sim_dir.parent
    node, cr, data = "<node>", "<CR group>", "<CIQ JSON>"
    jsons = sorted((run_dir / "json_data").glob("*.json"))
    jsons = [j for j in jsons if "VALIDATION_REPORT" not in j.name.upper()]
    if jsons:
        data = f"../json_data/{jsons[0].name}"
        try:
            from tools.ciq_json import node_groups
            cr_group, group = node_groups(json.loads(jsons[0].read_text(encoding="utf-8")))[0]
            names = [str(n.get("node")) for n in group.get("nodes") or [] if isinstance(n, dict) and n.get("node")]
            node, cr = (names or [str(group.get("nodeGroup") or node)])[0], cr_group or cr
        except (OSError, ValueError, IndexError):
            pass
    try:
        from core.config import runtime_params
        params = runtime_params()
    except Exception:
        params = {}
    ports = ", ".join(f"{i['name']} → {i['port']}" for i in instances)
    command = (f"java -jar <cli-automation-standalone.jar> execute --json {data} "
               f"--schema ../{uc_name}_validation-rules.yaml --yaml ../simulator/{uc_name}.yaml "
               f"--node {node} --cr {cr} --output-dir ../exec_output")
    command += "".join(f" --var {k}={v}" for k, v in params.items())
    return [
        "", "## Run the execution YAML against the simulator", "",
        f"With the simulator started, run `{uc_name}.yaml` from this folder (ports: {ports}) with the "
        "cli-automation JAR (not part of this bundle; it needs **Java 8** — it uses Nashorn), from the "
        "`scripts/` folder, where the workflow's LOCAL steps find their helper scripts and input files:", "",
        "```", command, "```", "",
        "`--node` / `--cr` pick one node group of the CIQ JSON; the `--var` values are the framework's "
        "runtime parameters as step 9 passed them. The simulator answers like the real nodes did in the "
        "uploaded transcripts — it is not the production network.",
    ]


def _start_ps1(uc_name: str, instances: list[dict]) -> str:
    rows = ",\n".join(f'    @{{ Name = "{i["name"]}"; Port = {int(i["port"])} }}' for i in instances)
    return f"""# Starts the {uc_name} network simulator: one MockWire instance per node, in the background.
# Needs Java 8 or later on the PATH. Stop it with .\\stop_simulator.ps1 (see SIMULATOR_GUIDE.md).
param(
    [string]$Jar = (Join-Path $PSScriptRoot "mockwire.jar"),
    [int]$TtlMinutes = {DEFAULT_TTL_MINUTES},
    [switch]$Clean
)
$ErrorActionPreference = "Stop"
$instances = @(
{rows}
)
if (-not (Test-Path $Jar)) {{ throw "MockWire JAR not found: $Jar (pass -Jar <path to mockwire.jar>)" }}
$Jar = (Resolve-Path $Jar).Path
$logs = Join-Path $PSScriptRoot "logs"
New-Item -ItemType Directory -Force $logs | Out-Null
foreach ($i in $instances) {{
    $n = $i.Name
    $vfs = Join-Path $PSScriptRoot "vfs\\$n"
    $responses = Join-Path $PSScriptRoot "outputs\\$n\\responses"
    if ($Clean -and (Test-Path $vfs)) {{ Remove-Item -Recurse -Force $vfs }}
    New-Item -ItemType Directory -Force $vfs, $responses | Out-Null
    $javaArgs = @("-jar", "`"$Jar`"",
        "--config", "`"$(Join-Path $PSScriptRoot "config\\$n.yaml")`"",
        "--responses", "`"$responses`"",
        "--port", $i.Port,
        "--events-file", "`"$(Join-Path $logs "$n.events.jsonl")`"",
        "--vfs-root", "`"$vfs`"",
        "--ttl-minutes", $TtlMinutes)
    $p = Start-Process java -ArgumentList $javaArgs -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logs "$n.log") -RedirectStandardError (Join-Path $logs "$n.err.log")
    Set-Content -Path (Join-Path $logs "$n.pid") -Value $p.Id
    Write-Host "$n -> localhost:$($i.Port) (pid $($p.Id), log logs\\$n.log)"
}}
Write-Host "Ready when each logs\\<instance>.log shows 'listening on port'."
"""


def _start_sh(uc_name: str, instances: list[dict]) -> str:
    starts = "\n".join(f"start {i['name']} {int(i['port'])}" for i in instances)
    return f"""#!/usr/bin/env bash
# Starts the {uc_name} network simulator: one MockWire instance per node, in the background.
# Needs Java 8 or later on the PATH. Usage: ./start_simulator.sh [path/to/mockwire.jar]
# CLEAN=1 empties the simulated file systems first; TTL_MINUTES (default {DEFAULT_TTL_MINUTES}). Stop: ./stop_simulator.sh
set -e
cd "$(dirname "$0")"
JAR="$(cd "$(dirname "${{1:-mockwire.jar}}")" 2>/dev/null && pwd)/$(basename "${{1:-mockwire.jar}}")"
TTL="${{TTL_MINUTES:-{DEFAULT_TTL_MINUTES}}}"
[ -f "$JAR" ] || {{ echo "MockWire JAR not found: $JAR (usage: ./start_simulator.sh <path to mockwire.jar>)"; exit 1; }}
mkdir -p logs

start() {{  # <instance> <port>
    [ "${{CLEAN:-0}}" = "1" ] && rm -rf "vfs/$1"
    mkdir -p "vfs/$1" "outputs/$1/responses"
    nohup java -jar "$JAR" --config "config/$1.yaml" --responses "outputs/$1/responses" --port "$2" \\
        --events-file "logs/$1.events.jsonl" --vfs-root "vfs/$1" --ttl-minutes "$TTL" >"logs/$1.log" 2>&1 &
    echo $! >"logs/$1.pid"
    echo "$1 -> localhost:$2 (pid $!, log logs/$1.log)"
}}

{starts}
echo "Ready when each logs/<instance>.log shows 'listening on port'."
"""


_STOP_PS1 = """# Stops the simulator instances .\\start_simulator.ps1 started (their pids are in logs\\*.pid).
foreach ($f in Get-ChildItem (Join-Path $PSScriptRoot "logs") -Filter *.pid -ErrorAction SilentlyContinue) {
    $id = [int](Get-Content $f.FullName -TotalCount 1)
    try { Stop-Process -Id $id -Force -ErrorAction Stop; Write-Host "$($f.BaseName) stopped (pid $id)" }
    catch { Write-Host "$($f.BaseName) was not running" }
    Remove-Item $f.FullName
}
"""

_STOP_SH = """#!/usr/bin/env bash
# Stops the simulator instances ./start_simulator.sh started (their pids are in logs/*.pid).
cd "$(dirname "$0")"
for f in logs/*.pid; do
    [ -f "$f" ] || continue
    name="$(basename "$f" .pid)"
    if kill "$(cat "$f")" 2>/dev/null; then echo "$name stopped"; else echo "$name was not running"; fi
    rm -f "$f"
done
"""
