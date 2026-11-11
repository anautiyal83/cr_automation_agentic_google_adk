"""The generated files a user can edit at a review, their versions, and reading them (spec 012).

The run folder is authoritative: a step reads these files from the run folder (``read``), so an edit
made at a review is what the later steps use; ADK session state is only the fallback (it is not
reliable across agents and never sees a file edited on disk).

Versions live in ``<run_dir>/edits/<file>/v<N>.<ext>`` with ``meta.json``; the file in the run
folder is always the current version. The first edit stores the generated file as v1.
"""
from __future__ import annotations

import difflib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

EDITS_DIR = "edits"

# session-state artifact key -> file name in the run folder ({uc} = the use case)
ARTIFACT_FILES = {
    "validation_rules_yaml": "{uc}_validation-rules.yaml",
    "json_template_yaml": "{uc}_json-output.yaml",
    "workflow_yaml": "{uc}.yaml",
}

# review step -> what can be edited there (Y-D1)
REVIEW_FILES = {
    4: ("validation_rules_yaml", "json_template_yaml"),
    6: ("workflow_yaml", "scripts"),
}


@dataclass
class EditableFile:
    rel: str            # path under the run folder ("EIR_X.yaml", "scripts/compare_tac.py")
    kind: str           # workflow | validation_rules | json_output | script
    label: str          # what a reviewer reads


_KINDS = {"validation_rules_yaml": ("validation_rules", "Validation rules · step 3"),
          "json_template_yaml": ("json_output", "CIQ JSON config · step 4"),
          "workflow_yaml": ("workflow", "Execution workflow · step 6")}


def file_name(key: str, uc_name: str) -> str:
    return ARTIFACT_FILES[key].format(uc=uc_name)


def read(state: dict, key: str) -> str:
    """The current text of a generated file: the run folder first, the session state second."""
    run_dir, uc = state.get("run_dir", ""), state.get("uc_name", "")
    if run_dir and uc and key in ARTIFACT_FILES:
        path = Path(run_dir) / file_name(key, uc)
        if path.is_file():
            try:
                return path.read_text(encoding="utf-8")
            except OSError as e:
                logger.warning("Could not read %s: %s", path, e)
    return (state.get("artifacts") or {}).get(key, "")


def editable_files(run_dir: str, uc_name: str, review_step: int) -> list[EditableFile]:
    """The files of a review that exist in the run folder, in review order."""
    root = Path(run_dir)
    files: list[EditableFile] = []
    for key in REVIEW_FILES.get(review_step, ()):
        if key == "scripts":
            for f in sorted((root / "scripts").glob("*.py")) if (root / "scripts").is_dir() else []:
                files.append(EditableFile(f"scripts/{f.name}", "script", f"Helper script · {f.name}"))
            continue
        name = file_name(key, uc_name)
        if (root / name).is_file():
            kind, label = _KINDS[key]
            files.append(EditableFile(name, kind, label))
    return files


# ---------------------------------------------------------------- versions

def _dir(run_dir: str, rel: str) -> Path:
    return Path(run_dir) / EDITS_DIR / rel.replace("/", "__")


def _meta(run_dir: str, rel: str) -> list[dict]:
    try:
        return json.loads((_dir(run_dir, rel) / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _write_meta(run_dir: str, rel: str, meta: list[dict]) -> None:
    d = _dir(run_dir, rel)
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def versions(run_dir: str, rel: str) -> list[dict]:
    """[{version, source, by, at, problems}] oldest first; empty when the file was never edited."""
    return _meta(run_dir, rel)


def read_version(run_dir: str, rel: str, version: int) -> str:
    path = _dir(run_dir, rel) / f"v{version}{Path(rel).suffix}"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def current(run_dir: str, rel: str) -> str:
    path = Path(run_dir) / rel
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def generated(run_dir: str, rel: str) -> str:
    """The text the step generated last (the newest `generated` version, else the file itself)."""
    for v in reversed(_meta(run_dir, rel)):
        if v.get("source") == "generated":
            return read_version(run_dir, rel, v["version"])
    return current(run_dir, rel)


def _add(run_dir: str, rel: str, text: str, source: str, by: str, problems: int | None) -> int:
    meta = _meta(run_dir, rel)
    version = (meta[-1]["version"] + 1) if meta else 1
    d = _dir(run_dir, rel)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"v{version}{Path(rel).suffix}").write_text(text, encoding="utf-8")
    meta.append({"version": version, "source": source, "by": by,
                 "at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "problems": problems})
    _write_meta(run_dir, rel, meta)
    return version


class ChangedSinceOpened(Exception):
    """The file changed after the editor opened it (another tab, a re-run): reload first (Y-D10)."""


def save(run_dir: str, rel: str, text: str, by: str, problems: int | None = None,
         expected: str | None = None, source: str = "user") -> int:
    """Store ``text`` as the next version and make it the current file. ``expected``: the text the
    editor opened; when the file no longer holds it, nothing is saved (``ChangedSinceOpened``)."""
    now = current(run_dir, rel)
    if expected is not None and now != expected:
        raise ChangedSinceOpened(rel)
    if not _meta(run_dir, rel):          # first edit: the generated file is v1
        _add(run_dir, rel, now, "generated", "", None)
    version = _add(run_dir, rel, text, source, by, problems)
    (Path(run_dir) / rel).write_text(text, encoding="utf-8")
    return version


def restore(run_dir: str, rel: str, version: int, by: str) -> int:
    """Make an earlier version current again, as a new version (source `restored`)."""
    return save(run_dir, rel, read_version(run_dir, rel, version), by, source="restored")


def record_generated(run_dir: str, rel: str) -> int | None:
    """A step regenerated a file the user had edited: the new text becomes a `generated` version and
    the user's last version stays in History (Y-D7). Nothing for a file never edited."""
    meta = _meta(run_dir, rel)
    if not meta or not any(v.get("source") in ("user", "restored") for v in meta):
        return None
    text = current(run_dir, rel)
    if text == read_version(run_dir, rel, meta[-1]["version"]):
        return None
    return _add(run_dir, rel, text, "generated", "", None)


def replaced_user_version(run_dir: str, rel: str) -> int | None:
    """The user's version a re-run replaced (for the review banner), else None."""
    meta = _meta(run_dir, rel)
    if not meta or meta[-1].get("source") != "generated":
        return None
    users = [v["version"] for v in meta if v.get("source") in ("user", "restored")]
    return users[-1] if users else None


def edited_files(run_dir: str) -> list[str]:
    """The run files whose current version is the user's."""
    root = Path(run_dir) / EDITS_DIR
    out = []
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        meta = _meta(run_dir, d.name.replace("__", "/"))
        if meta and meta[-1].get("source") in ("user", "restored"):
            out.append(d.name.replace("__", "/"))
    return out


def diff(old: str, new: str) -> list[tuple[str, str]]:
    """[(sign, line)] with sign '' / '+' / '-' — every line of both texts, in order."""
    out: list[tuple[str, str]] = []
    for line in difflib.ndiff(old.splitlines(), new.splitlines()):
        sign, text = line[:1], line[2:]
        if sign == "?":
            continue
        out.append(("" if sign == " " else sign, text))
    return out


def refresh_artifacts(data: dict, run_dir: str, uc_name: str) -> dict:
    """A cache entry's artifacts with the run folder's current files — what the user approved, edits
    included (Y-D8): the generated files by their artifact key, helper scripts from ``scripts/``."""
    out = dict(data or {})
    if not run_dir:
        return out
    for key in ARTIFACT_FILES:
        if key in out:
            path = Path(run_dir) / file_name(key, uc_name)
            if path.is_file():
                out[key] = path.read_text(encoding="utf-8")
    scripts = Path(run_dir) / "scripts"
    if isinstance(out.get("helper_scripts"), dict) and scripts.is_dir():
        out["helper_scripts"] = {**out["helper_scripts"],
                                 **{f.name: f.read_text(encoding="utf-8") for f in scripts.glob("*.py")}}
    return out
