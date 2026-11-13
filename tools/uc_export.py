"""UC export: one zip of what a successful run delivers for its UC (step 10 / Run Artifacts → Export).

Layout (mirrors the run folder, so the simulator guide's relative paths hold in both)::

    <UC>/README.md
    <UC>/<UC>_validation-rules.yaml · <UC>_json-output.yaml · <UC>.yaml (execution YAML)
    <UC>/json_data/<CIQ JSON>          — the data the execution YAML runs on (--json)
    <UC>/scripts/                      — helper scripts + inputs the LOCAL steps run (step 6)
    <UC>/simulator/mockwire.jar
    <UC>/simulator/config/ · outputs/ · <UC>.yaml (execution YAML on the simulator's ports)
    <UC>/simulator/SIMULATOR_GUIDE.md · start_/stop_simulator.ps1/.sh
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
    from skills.network_simulator_setup.guide import GUIDE_NAME, SCRIPT_NAMES, write_guide
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
    # a run set up before step 8 wrote the guide
    if not all((sim / n).is_file() for n in (GUIDE_NAME, *SCRIPT_NAMES)):
        write_guide(sim, uc_name, run_id=rd.name)

    out = export_path(rd, uc_name)
    out.parent.mkdir(parents=True, exist_ok=True)
    root = uc_name
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        def add(path: Path, arc: str) -> None:
            info = zipfile.ZipInfo.from_file(path, f"{root}/{arc}")
            if arc.endswith(".sh"):
                info.external_attr = (0o100755 << 16)  # executable when unzipped on Linux
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, path.read_bytes())

        def add_tree(folder: Path, arc: str) -> None:
            for f in sorted(folder.rglob("*")) if folder.is_dir() else []:
                if f.is_file() and "__pycache__" not in f.parts:
                    add(f, f"{arc}/{f.relative_to(folder).as_posix()}")

        def add_dir(arc: str) -> None:
            info = zipfile.ZipInfo(f"{root}/{arc}/")
            info.external_attr = (0o40755 << 16) | 0x10  # a directory (unix mode + MS-DOS flag)
            z.writestr(info, b"")

        readme = zipfile.ZipInfo(f"{root}/README.md")
        readme.external_attr = 0o100644 << 16
        z.writestr(readme, _readme(uc_name, rd.name, instances), zipfile.ZIP_DEFLATED)
        for p in deliverables:
            add(p, p.name)
        for f in sorted((rd / "json_data").glob("*.json")):
            if "VALIDATION_REPORT" not in f.name.upper():
                add(f, f"json_data/{f.name}")
        add_tree(rd / "scripts", "scripts")
        add(jar, "simulator/mockwire.jar")
        add_tree(sim / "config", "simulator/config")
        add_tree(sim / "outputs", "simulator/outputs")
        for name in instances:
            add_dir(f"simulator/outputs/{name}/responses")
            add_dir(f"simulator/vfs/{name}")
        add_dir("simulator/logs")
        overlay = sim / f"{uc_name}.yaml"
        if overlay.is_file():
            add(overlay, f"simulator/{overlay.name}")
        for name in (GUIDE_NAME, *SCRIPT_NAMES):
            add(sim / name, f"simulator/{name}")
    logger.info("UC export written: %s (%d bytes)", out, out.stat().st_size)
    return out


def _instances(sim: Path) -> list[str]:
    try:
        manifest = json.loads((sim / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [str(i["name"]) for i in manifest.get("instances") or [] if i.get("name")]


def _readme(uc_name: str, run_id: str, instances: list[str]) -> str:
    return "\n".join([
        f"# {uc_name} — export",
        "",
        f"Exported from run `{run_id}` of the Azalio Autonomous Network Platform.",
        "",
        "| Path | What it is |",
        "|---|---|",
        f"| `{uc_name}_validation-rules.yaml` | CIQ validation rules |",
        f"| `{uc_name}_json-output.yaml` | CIQ JSON output configuration |",
        f"| `{uc_name}.yaml` | Execution YAML (the production workflow) |",
        "| `json_data/` | CIQ JSON data the execution YAML runs on |",
        "| `scripts/` | Helper scripts (and inputs) the workflow's LOCAL steps run |",
        f"| `simulator/` | MockWire network simulator for this UC ({', '.join(instances)}), with its JAR, configs "
        "and answers |",
        "| `simulator/SIMULATOR_GUIDE.md` | How to start / stop the simulator, log in, and run the workflow "
        "against it |",
        "",
        "Quick start: `cd simulator` then `.\\start_simulator.ps1` (Windows) or `./start_simulator.sh` "
        "(Linux / WSL / macOS).",
        "",
    ])
