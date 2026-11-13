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
    configs = {i["name"]: _load(sim_dir / "config" / f"{i['name']}.yaml") for i in instances}
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


def _load(path: Path) -> dict:
    try:
        return YAML(typ="safe").load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _java_line(name: str, port: int, jar: str = "mockwire.jar", sep: str = "/") -> str:
    return (f"java -jar {jar} --config config{sep}{name}.yaml --responses outputs{sep}{name}{sep}responses "
            f"--port {port} --events-file logs{sep}{name}.events.jsonl --vfs-root vfs{sep}{name} "
            f"--ttl-minutes {DEFAULT_TTL_MINUTES}")


def guide_markdown(sim_dir: Path, uc_name: str, instances: list[dict], configs: dict[str, dict],
                   run_id: str = "") -> str:
    first = instances[0]
    lines = [
        f"# {uc_name} — network simulator",
        "",
        "MockWire simulates the nodes of this activity, one instance (SSH server) per node, and answers the "
        "commands of the execution YAML the way step 8 (Simulator Setup) prepared them"
        + (f" in run `{run_id}`" if run_id else "") + ". Step 9 (QA-Execution) ran the workflow against it.",
        "",
        "## Requirements",
        "",
        "- Java 8 or later on the PATH (`java -version`).",
        "- `mockwire.jar` in this folder (the UC export includes it), or pass its path to the start script.",
        "",
        "## Start",
        "",
        "| Where | Command |",
        "|---|---|",
        "| Windows (PowerShell) | `.\\start_simulator.ps1` &nbsp; options `-Jar <path>`, `-TtlMinutes <n>`, `-Clean` |",
        "| Linux / WSL / macOS | `./start_simulator.sh` &nbsp; or `./start_simulator.sh <path to mockwire.jar>`; "
        "`CLEAN=1`, `TTL_MINUTES=<n>` |",
        "",
        "Every instance starts in the background. An instance is ready when `logs/<instance>.log` shows "
        "`listening on port <port>`. It stops on its own after the TTL "
        f"({DEFAULT_TTL_MINUTES} minutes by default).",
        "",
        "Clean (`-Clean` / `CLEAN=1`) empties the simulated file systems (`vfs/`) first. Every step 9 case "
        "started from an empty one: without it, files a previous session uploaded or created are still there.",
        "",
        "One instance by hand, from this folder:",
        "",
        "```",
        _java_line(first["name"], first["port"]),
        "```",
        "",
        "## Stop",
        "",
        "`.\\stop_simulator.ps1` (Windows) or `./stop_simulator.sh`. They stop the processes the start "
        "script recorded in `logs/<instance>.pid`.",
        "",
        "## Nodes",
        "",
        "| Instance | Port | Workflow nodes | Login user | Password |",
        "|---|---|---|---|---|",
    ]
    for inst in instances:
        user, password = _login(configs.get(inst["name"]) or {})
        nodes = ", ".join(inst.get("nodes") or [inst["name"]])
        lines.append(f"| {inst['name']} | {inst['port']} | {nodes} | {user or '—'} | {password or '—'} |")
    for inst in instances:
        lines += [""] + _node_section(inst, configs.get(inst["name"]) or {})
    lines += _files_section(uc_name)
    lines += _workflow_section(sim_dir, uc_name, instances)
    return "\n".join(lines) + "\n"


def _login(cfg: dict) -> tuple[str, str]:
    users = ((cfg.get("auth") or {}).get("users") or [{}])
    return str(users[0].get("username") or ""), str(users[0].get("password") or "")


def _node_section(inst: dict, cfg: dict) -> list[str]:
    name, port = inst["name"], inst["port"]
    user, password = _login(cfg)
    out = [f"### {name} — port {port}", "",
           f"- Connect: `ssh -p {port} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
           f"{user or '<user>'}@localhost` (Windows: `UserKnownHostsFile=NUL`), password `{password}`"]
    niam = cfg.get("niam") or {}
    if niam.get("enabled"):
        ne_ids = [str(n) for u in niam.get("users") or [] for n in u.get("neIds") or []]
        out.append("- NIAM: after the password it asks `neId:` — answer one of: "
                   + ", ".join(f"`{n}`" for n in ne_ids))
    prompt = str(((cfg.get("auth") or {}).get("users") or [{}])[0].get("prompt") or
                 (cfg.get("config") or {}).get("prompt") or "")
    if prompt:
        out.append(f"- Login prompt: `{prompt}`")
    elevate = cfg.get("ctrl+p+e") or {}
    if elevate.get("pushPrompt"):
        out.append(f"- Root: press **Ctrl+P**, then **Ctrl+E** (what `elevate_to_root` sends) → "
                   f"`{elevate['pushPrompt']}`; `exit` goes back")
    for u in ((cfg.get("auth") or {}).get("users") or [])[1:]:
        out.append(f"- `su - {u.get('username')}`: password `{u.get('password')}` → `{u.get('prompt')}`")
    nested, commands = [], []
    for key, entry in cfg.items():
        if key in _RESERVED_KEYS:
            continue
        shown = key[len("regex:"):] if key.startswith("regex:") else key
        shown = shown if len(shown) <= MAX_SHOWN_CHARS else shown[:MAX_SHOWN_CHARS] + "…"
        if isinstance(entry, dict) and (entry.get("pushPrompt") or entry.get("interactive")):
            nested.append(shown)
        else:
            commands.append(("pattern " if key.startswith("regex:") else "") + f"`{shown}`")
    if nested:
        out.append("- Commands that open a nested shell (`exit` leaves it): " + ", ".join(f"`{n}`" for n in nested))
    vfs = cfg.get("vfs") or {}
    if vfs.get("dirs"):
        out.append("- Folders on the node: " + ", ".join(f"`{d}`" for d in vfs["dirs"]))
    if vfs.get("triggers"):
        out.append(f"- {len(vfs['triggers'])} file trigger(s): a file uploaded there makes the node write its "
                   "response file (e.g. an order's `.res`)")
    if commands:
        out.append(f"- Answers {len(commands)} command(s) of the workflow"
                   + (f" (first {MAX_LISTED_COMMANDS})" if len(commands) > MAX_LISTED_COMMANDS else "") + ":")
        out += [f"  - {c}" for c in commands[:MAX_LISTED_COMMANDS]]
    out.append("- Shell builtins on its file system answer too: `ls cat cp mv rm mkdir chmod tar diff …`")
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
