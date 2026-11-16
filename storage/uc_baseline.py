"""Per-UC baseline: what a run whose step 9 passed proved for its UC (user, 2026-11-13).

The artifact cache (``artifact_cache.py``) holds a step's last passed output and goes when the inputs change or the
user re-runs with "Clear cache"; step 6 then wrote the workflow from scratch, and a UC that had passed failed again
(run b81305ff: the LLM wrote the in-pod dumps, the count check and the delta check differently, OB-65..70). The
baseline stays — "Clear cache" never drops it; the Artifact Cache page discards it, a newer passing run replaces it:

    data/knowledge/<uc>/baseline/
        meta.json                  — run_id, saved_at, requirements / workflow / simulator fingerprints
        workflow.yaml              — step 6 execution workflow (as the run wrote it)
        scripts/<name>             — step 6 helper scripts
        simulator/config/<inst>.yaml, simulator/outputs/<inst>/…, simulator/synthetic_keys.json — step 8

Step 6 starts from ``workflow.yaml`` (same requirements: reused after the checks, no LLM; otherwise the LLM edits
it), the helper scripts are reused for the same workflow, and step 8 reuses the simulator configs for the same
workflow and CIQ data.
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from core.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

_BASE = PROJECT_ROOT / "data" / "knowledge"
_SCRIPT_SUFFIXES = (".py", ".sh")


@dataclass
class Baseline:
    uc_name: str
    run_id: str = ""
    saved_at: str = ""
    requirements_fp: str = ""
    workflow_yaml: str = ""
    scripts: dict[str, str] = field(default_factory=dict)          # name → content
    simulator_fp: str = ""
    simulator_configs: dict[str, str] = field(default_factory=dict)  # instance → config yaml
    synthetic_keys: dict = field(default_factory=dict)
    path: Path | None = None


def baseline_dir(uc_name: str, base: Path | None = None) -> Path:
    return (base or _BASE) / uc_name / "baseline"


def _sha(*parts: str | bytes) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p if isinstance(p, bytes) else str(p).encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()[:16]


# What the user gave the UC: the documents as extracted (no AI), the developer's answers and notes, the XML templates
# and the run's options. Not step 1's own reading of them (category_*, generation_hints, developer_questions): an AI
# writes that again on every step 1 run, and run 1ee47d78 — same documents, answers and options as the proven
# 6110bc53 — counted as "changed" and step 6 rewrote 567 of 1026 lines (OB-72)
_USER_INPUT_KEYS = ("doc_extracted", "developer_answers", "developer_notes", "xml_templates", "run_options")


def requirements_fingerprint(requirements: dict) -> str:
    """What the user gave step 6 (``_USER_INPUT_KEYS``): equal → the baseline was proven on these inputs."""
    req = requirements or {}
    return _sha(json.dumps({k: req.get(k) for k in _USER_INPUT_KEYS}, sort_keys=True, default=str))


def workflow_fingerprint(workflow_yaml: str) -> str:
    return _sha((workflow_yaml or "").replace("\r\n", "\n").strip())


def simulator_fingerprint(workflow_yaml: str, ciq_json_files: list[str]) -> str:
    """What step 8 builds from: the workflow and the CIQ data (NIAM ids, record values)."""
    parts: list[str | bytes] = [workflow_fingerprint(workflow_yaml)]
    for f in sorted(ciq_json_files or []):
        try:
            parts.append(Path(f).read_bytes())
        except OSError:
            parts.append(f)
    return _sha(*parts)


def run_ciq_json_files(run_dir: str | Path) -> list[str]:
    """CIQ JSON data of a run (``json_data/*.json``, validation reports left out)."""
    json_dir = Path(run_dir) / "json_data"
    return [str(f) for f in sorted(json_dir.glob("*.json")) if "VALIDATION_REPORT" not in f.name.upper()] \
        if json_dir.is_dir() else []


def save(run_dir: str | Path, uc_name: str, requirements: dict, ciq_json_files: list[str] | None = None,
         base: Path | None = None) -> Path | None:
    """The run's step 6 + step 8 output as the UC's baseline (replacing an older one). None without a workflow."""
    rd = Path(run_dir)
    wf = rd / f"{uc_name}.yaml"
    if not uc_name or not wf.is_file():
        return None
    workflow = wf.read_text(encoding="utf-8")
    target = baseline_dir(uc_name, base)
    staging = target.with_name("baseline.new")
    shutil.rmtree(staging, ignore_errors=True)
    (staging / "scripts").mkdir(parents=True)
    (staging / "workflow.yaml").write_text(workflow, encoding="utf-8")
    for f in sorted((rd / "scripts").glob("*")) if (rd / "scripts").is_dir() else []:
        if f.is_file() and f.suffix in _SCRIPT_SUFFIXES:
            shutil.copy2(f, staging / "scripts" / f.name)
    sim = rd / "simulator"
    has_sim = (sim / "config").is_dir() and any((sim / "config").glob("*.yaml"))
    if has_sim:
        shutil.copytree(sim / "config", staging / "simulator" / "config")
        if (sim / "outputs").is_dir():
            shutil.copytree(sim / "outputs", staging / "simulator" / "outputs")
        if (sim / "synthetic_keys.json").is_file():
            shutil.copy2(sim / "synthetic_keys.json", staging / "simulator" / "synthetic_keys.json")
    meta = {
        "uc_name": uc_name,
        "run_id": rd.name,
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "requirements_fp": requirements_fingerprint(requirements),
        "workflow_fp": workflow_fingerprint(workflow),
        "simulator_fp": simulator_fingerprint(workflow, run_ciq_json_files(rd) if ciq_json_files is None
                                              else ciq_json_files) if has_sim else "",
    }
    (staging / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    shutil.rmtree(target, ignore_errors=True)
    staging.rename(target)
    logger.info("Baseline of %s saved from run %s", uc_name, rd.name)
    return target


def load(uc_name: str, base: Path | None = None) -> Baseline | None:
    d = baseline_dir(uc_name, base)
    try:
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        workflow = (d / "workflow.yaml").read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    b = Baseline(uc_name=uc_name, run_id=str(meta.get("run_id") or ""), saved_at=str(meta.get("saved_at") or ""),
                 requirements_fp=str(meta.get("requirements_fp") or ""), workflow_yaml=workflow,
                 simulator_fp=str(meta.get("simulator_fp") or ""), path=d)
    for f in sorted((d / "scripts").glob("*")) if (d / "scripts").is_dir() else []:
        if f.is_file():
            b.scripts[f.name] = f.read_text(encoding="utf-8")
    for f in sorted((d / "simulator" / "config").glob("*.yaml")) if (d / "simulator" / "config").is_dir() else []:
        b.simulator_configs[f.stem] = f.read_text(encoding="utf-8")
    try:
        b.synthetic_keys = json.loads((d / "simulator" / "synthetic_keys.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return b


def restore_simulator_outputs(uc_name: str, sim_dir: str | Path, base: Path | None = None) -> None:
    """The baseline's ``outputs/<inst>/responses`` files (answers kept as files) into a run's simulator folder."""
    src = baseline_dir(uc_name, base) / "simulator" / "outputs"
    if src.is_dir():
        shutil.copytree(src, Path(sim_dir) / "outputs", dirs_exist_ok=True)


def discard(uc_name: str, base: Path | None = None) -> bool:
    d = baseline_dir(uc_name, base)
    if not d.exists():
        return False
    shutil.rmtree(d, ignore_errors=True)
    logger.info("Baseline of %s discarded", uc_name)
    return True


def list_baselines(base: Path | None = None) -> list[dict]:
    """[{uc_name, run_id, saved_at, scripts, simulator}] of every UC with a baseline."""
    out = []
    root = base or _BASE
    for meta_file in sorted(root.glob("*/baseline/meta.json")) if root.is_dir() else []:
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        d = meta_file.parent
        out.append({"uc_name": d.parent.name, "run_id": meta.get("run_id", ""), "saved_at": meta.get("saved_at", ""),
                    "scripts": len(list((d / "scripts").glob("*"))) if (d / "scripts").is_dir() else 0,
                    "simulator": bool(meta.get("simulator_fp"))})
    return out
