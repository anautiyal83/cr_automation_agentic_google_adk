"""UC export: one zip of what a successful run delivers for its UC (step 10 / Run Artifacts → Export).

Layout (mirrors the run folder, so the simulator guide's relative paths hold in both)::

    <UC>/USER_GUIDE.md                 — every file with what it is, and the simulator: servers, ports, how they
                                       work together, the workflow per server, trying it by hand
    <UC>/<UC>_validation-rules.yaml · <UC>_json-output.yaml · <UC>.yaml (execution YAML)
    <UC>/json_data/<CIQ JSON>          — the data the execution YAML runs on (--json)
    <UC>/scripts/                      — helper scripts + inputs the LOCAL steps run (step 6)
    <UC>/simulator/mockwire.jar
    <UC>/simulator/config/ · outputs/ · <UC>.yaml (execution YAML on the simulator's ports)
    <UC>/simulator/start_/stop_simulator.ps1/.sh
    <UC>/simulator/vfs/<instance>/ (empty: every step 9 case started there) · logs/ (empty)

Left out: the run's logs, events, LLM outputs, manifest (absolute paths, pids) and the file systems step 9
left behind. Written to ``<run_dir>/export/<UC>_export.zip``.
"""
from __future__ import annotations

import json
import logging
import zipfile
from pathlib import Path

from core.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

EXPORT_DIR = "export"
USER_GUIDE = "USER_GUIDE.md"
_DEFAULT_MOCKWIRE_JAR = "lib/mockwire.jar"


class ExportError(RuntimeError):
    """The run lacks a file the export needs."""


def export_path(run_dir: str | Path, uc_name: str) -> Path:
    return Path(run_dir) / EXPORT_DIR / f"{uc_name}_export.zip"


def mockwire_jar() -> Path:
    """The MockWire JAR of the live config (Settings → External Modules), else ``lib/mockwire.jar``."""
    from core.config import ConfigManager
    config = getattr(ConfigManager._live_instance, "_config", None)
    for path in (getattr(config, "mockwire_jar", None), _DEFAULT_MOCKWIRE_JAR):
        if path:
            p = Path(path) if Path(path).is_absolute() else PROJECT_ROOT / path
            if p.is_file():
                return p.resolve()
    raise ExportError(f"MockWire JAR not found: {PROJECT_ROOT / _DEFAULT_MOCKWIRE_JAR}")


def build_export(run_dir: str | Path, uc_name: str, jar: str | Path | None = None) -> Path:
    """Write the UC's export zip and return its path. Raises ExportError when a deliverable is missing."""
    from skills.network_simulator_setup.guide import SCRIPT_NAMES, write_guide
    rd = Path(run_dir)
    sim = rd / "simulator"
    deliverables = [rd / f"{uc_name}_validation-rules.yaml", rd / f"{uc_name}_json-output.yaml", rd / f"{uc_name}.yaml"]
    missing = [p.name for p in deliverables if not p.is_file()]
    instances = _instances(sim)
    if not instances:
        missing.append("simulator/manifest.json (step 8 never started the simulator)")
    missing += [f"simulator/config/{i}.yaml" for i in instances if not (sim / "config" / f"{i}.yaml").is_file()]
    if missing:
        raise ExportError("Missing from the run: " + ", ".join(missing))
    jar = Path(jar) if jar else mockwire_jar()
    # the start / stop scripts (and the run folder's own guide) as the current code writes them
    write_guide(sim, uc_name, run_id=rd.name)

    files: list[tuple[str, Path]] = [(p.name, p) for p in deliverables]
    files += [(f"json_data/{f.name}", f) for f in sorted((rd / "json_data").glob("*.json"))
              if "VALIDATION_REPORT" not in f.name.upper()]
    # from the scripts folder only what the workflow uses: the helper scripts it calls and the input files the CIQ
    # names — not the dumps a test run left there, nor a helper of an earlier step 6 the workflow no longer calls
    callers, inputs = _script_callers(rd / f"{uc_name}.yaml"), _ciq_files(rd)
    files += [(arc, f) for arc, f in _tree(rd / "scripts", "scripts") if f.name in callers or f.name in inputs]
    files.append(("simulator/mockwire.jar", jar))
    files += _tree(sim / "config", "simulator/config") + _tree(sim / "outputs", "simulator/outputs")
    if (sim / f"{uc_name}.yaml").is_file():
        files.append((f"simulator/{uc_name}.yaml", sim / f"{uc_name}.yaml"))
    files += [(f"simulator/{n}", sim / n) for n in SCRIPT_NAMES]
    dirs = [f"simulator/outputs/{n}/responses" for n in instances] + [f"simulator/vfs/{n}" for n in instances]
    dirs.append("simulator/logs")
    guide = user_guide(rd, uc_name, [a for a, _ in files], dirs)

    out = export_path(rd, uc_name)
    out.parent.mkdir(parents=True, exist_ok=True)
    root = uc_name
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        info = zipfile.ZipInfo(f"{root}/{USER_GUIDE}")
        info.external_attr = 0o100644 << 16
        z.writestr(info, guide, zipfile.ZIP_DEFLATED)
        for arc, path in files:
            info = zipfile.ZipInfo.from_file(path, f"{root}/{arc}")
            if arc.endswith(".sh"):
                info.external_attr = (0o100755 << 16)  # executable when unzipped on Linux
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, path.read_bytes())
        for arc in dirs:
            info = zipfile.ZipInfo(f"{root}/{arc}/")
            info.external_attr = (0o40755 << 16) | 0x10  # a directory (unix mode + MS-DOS flag)
            z.writestr(info, b"")
    logger.info("UC export written: %s (%d bytes)", out, out.stat().st_size)
    return out


def _tree(folder: Path, arc: str) -> list[tuple[str, Path]]:
    return [(f"{arc}/{f.relative_to(folder).as_posix()}", f) for f in sorted(folder.rglob("*"))
            if folder.is_dir() and f.is_file() and "__pycache__" not in f.parts]


def user_guide(run_dir: Path, uc_name: str, files: list[str], dirs: list[str]) -> str:
    """USER_GUIDE.md: what the package is, every file with what it is, then the simulator (servers, ports, how they
    work together, the workflow per server, trying it by hand, running the workflow) — one guide (user, 2026-11-13)."""
    from skills.network_simulator_setup.guide import simulator_sections
    described = _descriptions(run_dir, uc_name)
    lines = [
        f"# {uc_name} — user guide",
        "",
        f"Exported from run `{run_dir.name}` of the Azalio Autonomous Network Platform: the files that configure and "
        "run this activity, and a network simulator to try them without the production network.",
        "",
        "Quick start: `cd simulator`, then `.\\start_simulator.ps1` (Windows) or `./start_simulator.sh` (Linux / WSL / "
        "macOS) — see **Network simulator** below.",
        "",
        "## Files in this package",
        "",
        "| File | What it is |",
        "|---|---|",
        f"| `{USER_GUIDE}` | This guide |",
    ]
    for arc in files:
        lines.append(f"| `{arc}` | {described(arc)} |")
    for arc in dirs:
        lines.append(f"| `{arc}/` | {described(arc + '/')} |")
    lines += ["", simulator_sections(run_dir / "simulator", uc_name, run_dir.name)]
    return "\n".join(lines).rstrip() + "\n"


def _descriptions(run_dir: Path, uc_name: str):
    """arc path → one line on what the file is, from what the run knows about it."""
    sim = run_dir / "simulator"
    try:
        manifest = json.loads((sim / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {}
    ports = {str(i.get("name")): i for i in manifest.get("instances") or []}
    gateways = {str(i.get("name")) for i in manifest.get("instances") or [] if i.get("profiles")}
    for i in manifest.get("instances") or []:  # a node behind a gateway is reached on the gateway's port
        for pr in i.get("profiles") or []:
            ports[str(pr.get("name"))] = {"port": f"{i.get('port')} via {i.get('name')}", "nodes": pr.get("nodes")}
    callers = _script_callers(run_dir / f"{uc_name}.yaml")
    inputs = _ciq_files(run_dir)
    niam = set()
    for f in (sim / "config").glob("*.yaml") if (sim / "config").is_dir() else []:
        try:
            from ruamel.yaml import YAML
            if ((YAML(typ="safe").load(f.read_text(encoding="utf-8")) or {}).get("niam") or {}).get("enabled"):
                niam.add(f.stem)
        except Exception:
            pass

    def server(name: str) -> str:
        inst = ports.get(name) or {}
        nodes = ", ".join(inst.get("nodes") or [name])
        return f"server **{name}** (port {inst.get('port', '?')}, simulates {nodes})"

    def describe(arc: str) -> str:
        name = arc.rsplit("/", 1)[-1] if not arc.endswith("/") else arc.rstrip("/").rsplit("/", 1)[-1]
        if arc == f"{uc_name}_validation-rules.yaml":
            return "CIQ validation rules (steps 3-5): the checks the ciq-processor JAR runs on the CIQ workbook"
        if arc == f"{uc_name}_json-output.yaml":
            return "CIQ JSON output configuration (step 4): how the CIQ workbook becomes the JSON the workflow reads"
        if arc == f"{uc_name}.yaml":
            return ("Execution YAML (step 6): the production workflow — nodes, phases (checks, backup, activity, "
                    "rollback) and every step with its validation; run by the cli-automation JAR")
        if arc.startswith("json_data/"):
            return "CIQ JSON data: the CIQ workbook as the workflow reads it (`--json`; CR groups → node groups → records)"
        if arc.startswith("scripts/"):
            if name in callers:
                return (f"Helper script (step 6) the workflow runs on the automation server: \"{callers[name]}\" "
                        f"(`{callers[name + ' cmd']} {name} …`)")
            if name in inputs:
                return (f"Input file the CIQ names in column `{inputs[name]}` (a copy of your uploaded document): the "
                        "workflow's local steps read it")
            return "—"
        if arc == "simulator/mockwire.jar":
            return "MockWire, the network simulator (Java 8 or later)"
        if arc.startswith("simulator/config/") and name[:-5] in gateways:
            nodes = [pr.get("name") for i in manifest.get("instances") or [] if i.get("name") == name[:-5]
                     for pr in i.get("profiles") or []]
            return (f"Access gateway **{name[:-5]}** (port {ports[name[:-5]].get('port', '?')}): the one login of "
                    f"the nodes behind it and which neId reaches which of them ({', '.join(nodes)})")
        if arc.startswith("simulator/config/"):
            return (f"{server(name[:-5])}: its prompts, logins{', NIAM ids' if name[:-5] in niam else ''} and the "
                    "answer to every workflow command it receives")
        if arc.startswith("simulator/outputs/"):
            inst = arc.split("/")[2]
            return f"Answers of {server(inst)} kept as files (empty when all are in its config)"
        if arc.startswith("simulator/vfs/"):
            return f"The simulated file system of {server(name)} — empty at start; uploads, backups and dumps land here"
        if arc == "simulator/logs/":
            return "The servers' logs, pids and command event logs (`<server>.events.jsonl`) appear here when they run"
        if arc == f"simulator/{uc_name}.yaml":
            return "The execution YAML with every node pointed at its simulator server on localhost (what step 9 ran)"
        if name.startswith("start_simulator"):
            return "Starts every simulator server in the background" + (" (Windows PowerShell)" if name.endswith(".ps1")
                                                                         else " (Linux / WSL / macOS)")
        if name.startswith("stop_simulator"):
            return "Stops the servers the start script started" + (" (Windows PowerShell)" if name.endswith(".ps1")
                                                                    else " (Linux / WSL / macOS)")
        return "—"
    return describe


def _ciq_files(run_dir: Path) -> dict[str, str]:
    """{file name: CIQ column} of the CIQ record values that are file names (`DELTA_TAC_FILE: SD_TAC_….xlsx`)."""
    import re
    out: dict[str, str] = {}

    def walk(value, key=""):
        if isinstance(value, dict):
            for k, v in value.items():
                walk(v, str(k))
        elif isinstance(value, list):
            for v in value:
                walk(v, key)
        elif isinstance(value, str) and re.fullmatch(r"[\w .()-]+\.[A-Za-z0-9]{2,5}", value.strip()):
            out.setdefault(value.strip().rsplit("/", 1)[-1], key)
    for f in sorted((run_dir / "json_data").glob("*.json")) if (run_dir / "json_data").is_dir() else []:
        try:
            walk(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    return out


def _script_callers(workflow: Path) -> dict[str, str]:
    """{script file name: the description of the workflow step that runs it, "<name> cmd": its interpreter}."""
    import re
    try:
        from ruamel.yaml import YAML
        doc = YAML(typ="safe").load(workflow.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    out: dict[str, str] = {}

    def walk(steps):
        for st in steps or []:
            if not isinstance(st, dict):
                continue
            m = re.search(r"\b(python[\d.]*|bash|sh)\s+(?:\S*/)?([\w.-]+\.(?:py|sh))\b", str(st.get("send") or ""))
            if m and m.group(2) not in out:
                out[m.group(2)] = str(st.get("command_description") or "").strip() or "a local step"
                out[m.group(2) + " cmd"] = m.group(1)
            walk(st.get("steps"))
    for body in (doc.get("phases") or {}).values() if isinstance(doc.get("phases"), dict) else []:
        if isinstance(body, dict):
            walk(body.get("steps"))
    return out


def _instances(sim: Path) -> list[str]:
    try:
        manifest = json.loads((sim / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    # a gateway's nodes have configs, answers and file systems of their own (MW-13)
    return [str(n) for i in manifest.get("instances") or [] if i.get("name")
            for n in [i["name"], *[pr.get("name") for pr in i.get("profiles") or [] if pr.get("name")]]]
