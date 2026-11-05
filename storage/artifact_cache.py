"""UC-level artifact cache — avoids regenerating artifacts that already exist.

Stores artifacts per UC name in data/cache/<uc_name>/ so subsequent runs
of the same UC can skip LLM generation for steps that already produced
valid output.

Cache structure:
    data/cache/<uc_name>/
        requirements.json          — step 1-2
        validation-rules.yaml      — step 3
        json-output.yaml           — step 4
        workflow.yaml              — step 6
        scripts/                   — step 6 helper scripts
            create_xml.py
            parse_response.py
            ...
        _cache_meta.json           — metadata (timestamps, statuses)
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Maps agent name → list of (cache_filename, artifact_key_in_session)
_AGENT_CACHE_MAP: dict[str, list[tuple[str, str]]] = {
    "requirements_gatherer": [
        ("requirements.json", "requirements_json"),
    ],
    "ciq_validation_json_gen": [
        ("validation-rules.yaml", "validation_rules_yaml"),
        ("json-output.yaml", "json_template_yaml"),
    ],
    "network_config_gen": [
        ("workflow.yaml", "workflow_yaml"),
    ],
    "helper_script_gen": [
        ("scripts/", "helper_scripts"),
    ],
}

# Agent names that can be skipped if cache is valid
CACHEABLE_AGENTS = set(_AGENT_CACHE_MAP.keys())

# Pipeline order of the cacheable agents (a cleared agent invalidates the ones after it)
_AGENT_ORDER = ["requirements_gatherer", "ciq_validation_json_gen", "network_config_gen", "helper_script_gen"]

# Code that produces each agent's artifacts: a change invalidates its cache (OB-11) —
# a UI run kept serving a workflow generated before the step 6 fixes
_AGENT_SOURCES: dict[str, list[str]] = {
    "requirements_gatherer": ["skills/requirement_gathering", "agents/requirements_agent.py"],
    "ciq_validation_json_gen": ["skills/ciq_validation_json_gen", "agents/ciq_validation_json_gen_agent.py"],
    "network_config_gen": ["skills/network_config_gen", "agents/network_config_gen_agent.py"],
    "helper_script_gen": ["skills/helper_script_gen", "agents/helper_script_gen_agent.py"],
}
_ROOT = Path(__file__).resolve().parent.parent
_VOLATILE_KEYS = {"id", "run_id", "document_set_id", "created_at", "uploaded_at", "updated_at"}


def code_version(agent_name: str) -> str:
    """Hash of the source files that generate an agent's artifacts."""
    h = hashlib.sha256()
    for rel in _AGENT_SOURCES.get(agent_name, []):
        path = _ROOT / rel
        files = sorted(path.rglob("*")) if path.is_dir() else [path]
        for f in files:
            if f.is_file() and f.suffix in (".py", ".md", ".yaml", ".json", ".txt") and "__pycache__" not in f.parts:
                h.update(str(f.relative_to(_ROOT)).encode())
                h.update(f.read_bytes())
    return h.hexdigest()[:16]


# Run options (spec 010: phases, backup route) only matter from step 6 on: they are not part of
# the inputs fingerprint (changing them must not regenerate the requirements or the CIQ
# artifacts) but of a separate options fingerprint the step 6 agents' cache entries carry
STEP6_AGENTS = ("network_config_gen", "helper_script_gen")


def options_fingerprint(document_set: dict | None) -> str:
    """Hash of the run's options; "" for the default (all phases, backup kept on the node)."""
    from shared import run_options
    raw = (document_set or {}).get("run_options") if isinstance(document_set, dict) else None
    if run_options.is_default(raw):
        return ""
    return hashlib.sha256(json.dumps(run_options.normalize(raw), sort_keys=True).encode()).hexdigest()[:16]


def inputs_fingerprint(document_set: dict | None) -> str:
    """Hash of what the user gave the run: notes, settings and the CONTENT of the uploaded
    files (paths differ per upload). Requirements cached for other documents or without the
    developer notes must not be reused (OB-11)."""
    h = hashlib.sha256()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for k in sorted(value):
                if k not in _VOLATILE_KEYS:
                    h.update(f"<{k}>".encode())
                    walk(value[k])
        elif isinstance(value, (list, tuple)):
            for v in value:
                walk(v)
        elif isinstance(value, str) and value and len(value) < 1024 and Path(value).is_file():
            h.update(hashlib.sha256(Path(value).read_bytes()).digest())
        else:
            h.update(json.dumps(value, default=str).encode())

    walk({k: v for k, v in (document_set or {}).items() if k != "run_options"})
    return h.hexdigest()[:16]


class ArtifactCache:
    """Manages UC-level artifact caching."""

    def __init__(self, cache_dir: str = "data/cache"):
        # relative to the project, not the working directory (spec 002 C-5)
        self._cache_dir = Path(cache_dir) if Path(cache_dir).is_absolute() else _ROOT / cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def uc_cache_dir(self, uc_name: str) -> Path:
        return self._cache_dir / uc_name

    def has_cache(self, uc_name: str, agent_name: str, inputs: str = "", options: str = "") -> bool:
        """Check if valid cached artifacts exist for this agent+UC, made by the current code
        from the same inputs (``inputs``: :func:`inputs_fingerprint` of the document set)."""
        entries = _AGENT_CACHE_MAP.get(agent_name, [])
        if not entries:
            return False

        uc_dir = self.uc_cache_dir(uc_name)
        meta = self._read_meta(uc_name)

        for cache_file, _ in entries:
            path = uc_dir / cache_file
            if cache_file.endswith("/"):
                # Directory check (e.g. scripts/)
                if not path.exists() or not any(path.iterdir()):
                    return False
            else:
                if not path.exists() or path.stat().st_size == 0:
                    return False

        # Check that cache status is "passed" for this agent
        agent_status = meta.get("agent_status", {}).get(agent_name)
        if agent_status != "passed":
            return False
        if meta.get("code_version", {}).get(agent_name) != code_version(agent_name):
            logger.info("Cache for %s/%s is stale: generator code changed", uc_name, agent_name)
            return False
        if inputs and meta.get("inputs") != inputs:
            logger.info("Cache for %s/%s is stale: documents or notes changed", uc_name, agent_name)
            return False
        if agent_name in STEP6_AGENTS and (meta.get("options") or {}).get(agent_name, "") != options:
            logger.info("Cache for %s/%s is stale: the run's phase options differ", uc_name, agent_name)
            return False
        return True

    def load_cache(self, uc_name: str, agent_name: str) -> dict[str, str]:
        """Load cached artifacts for an agent. Returns {artifact_key: content}."""
        entries = _AGENT_CACHE_MAP.get(agent_name, [])
        result = {}
        uc_dir = self.uc_cache_dir(uc_name)

        for cache_file, artifact_key in entries:
            path = uc_dir / cache_file
            if cache_file.endswith("/"):
                # Directory — load all files as dict
                if path.exists():
                    scripts = {}
                    for f in path.glob("*"):
                        if f.is_file():
                            scripts[f.name] = f.read_text(encoding="utf-8")
                    result[artifact_key] = scripts
            else:
                if path.exists():
                    result[artifact_key] = path.read_text(encoding="utf-8")

        logger.info("Loaded %d cached artifacts for %s/%s", len(result), uc_name, agent_name)
        return result

    def save_cache(self, uc_name: str, agent_name: str,
                   artifacts: dict[str, Any], status: str = "passed", inputs: str = "",
                   options: str = "") -> None:
        """Save artifacts to cache for this agent+UC."""
        entries = _AGENT_CACHE_MAP.get(agent_name, [])
        if not entries:
            return

        uc_dir = self.uc_cache_dir(uc_name)
        uc_dir.mkdir(parents=True, exist_ok=True)

        written, missing = 0, []
        for cache_file, artifact_key in entries:
            content = artifacts.get(artifact_key)
            if content is None or not str(content).strip() or (isinstance(content, dict) and not content):
                missing.append(artifact_key)
                continue

            path = uc_dir / cache_file
            if cache_file.endswith("/"):
                # Directory — content is dict of {filename: text}
                if isinstance(content, dict):
                    shutil.rmtree(path, ignore_errors=True)  # no scripts left from an older version
                    path.mkdir(parents=True, exist_ok=True)
                    for fname, text in content.items():
                        # helper_scripts artifacts are {name: path in the run dir}: cache the
                        # script, not the path (a cached "script" was its own path; OB-14)
                        source = Path(str(text)) if len(str(text)) < 1024 else None
                        if source is not None and source.is_file():
                            text = source.read_text(encoding="utf-8")
                        (path / fname).write_text(str(text), encoding="utf-8")
                    written += 1
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(str(content), encoding="utf-8")
                written += 1
        if missing and status == "passed":
            # a partial entry must never be served as a hit (spec 002 C-3)
            logger.warning("Cache of %s/%s incomplete — missing or empty: %s", uc_name, agent_name, ", ".join(missing))
            status = "incomplete"

        # Update metadata
        meta = self._read_meta(uc_name)
        agent_status = meta.setdefault("agent_status", {})
        agent_status[agent_name] = status
        meta.setdefault("timestamps", {})[agent_name] = datetime.now(timezone.utc).isoformat()
        meta.setdefault("code_version", {})[agent_name] = code_version(agent_name)
        if inputs:
            if meta.get("inputs") != inputs:
                # Artifacts of other agents were made from other documents
                for other in list(meta.get("agent_status", {})):
                    if other != agent_name:
                        meta["agent_status"].pop(other, None)
            meta["inputs"] = inputs
        if agent_name in STEP6_AGENTS:
            meta.setdefault("options", {})[agent_name] = options
        if agent_name in _AGENT_ORDER:
            # The agent produced new artifacts: what was cached after it was built from the old ones
            for later in _AGENT_ORDER[_AGENT_ORDER.index(agent_name) + 1:]:
                meta.get("agent_status", {}).pop(later, None)
        self._write_meta(uc_name, meta)

        logger.info("Cached %d/%d artifacts for %s/%s (status=%s)",
                    written, len(entries), uc_name, agent_name, status)

    def clear_cache(self, uc_name: str, agent_name: str | None = None) -> None:
        """Clear cache for a UC. If agent_name given, clear only that agent's cache."""
        uc_dir = self.uc_cache_dir(uc_name)
        if not uc_dir.exists():
            return

        if agent_name:
            entries = _AGENT_CACHE_MAP.get(agent_name, [])
            for cache_file, _ in entries:
                path = uc_dir / cache_file
                if cache_file.endswith("/") and path.exists():
                    shutil.rmtree(path, ignore_errors=True)
                elif path.exists():
                    path.unlink(missing_ok=True)
            # Update meta
            meta = self._read_meta(uc_name)
            meta.get("agent_status", {}).pop(agent_name, None)
            meta.get("timestamps", {}).pop(agent_name, None)
            meta.get("code_version", {}).pop(agent_name, None)
            self._write_meta(uc_name, meta)
            logger.info("Cleared cache for %s/%s", uc_name, agent_name)
        else:
            shutil.rmtree(uc_dir, ignore_errors=True)
            logger.info("Cleared all cache for %s", uc_name)

    def invalidate_after(self, uc_name: str, agent_name: str) -> None:
        """The agent is running again (not from cache): what was cached after it was built
        from its previous output — even when this run of it fails and saves nothing (OB-14)."""
        if agent_name not in _AGENT_ORDER:
            return
        meta = self._read_meta(uc_name)
        status = meta.get("agent_status", {})
        later = [a for a in _AGENT_ORDER[_AGENT_ORDER.index(agent_name) + 1:] if a in status]
        if later:
            for a in later:
                status.pop(a, None)
            self._write_meta(uc_name, meta)
            logger.info("Invalidated cache of %s: %s runs again", later, agent_name)

    def clear_from_step(self, uc_name: str, from_agent: str) -> None:
        """Clear cache for an agent and all downstream agents. Agents after the cached ones
        (QA, simulator, report) have nothing cached: re-running them keeps the cache
        (it used to fall back to clearing everything)."""
        if from_agent not in _AGENT_ORDER:
            return
        for agent in _AGENT_ORDER[_AGENT_ORDER.index(from_agent):]:
            self.clear_cache(uc_name, agent)

    def list_cached_ucs(self) -> list[dict]:
        """List all UCs with cache info."""
        result = []
        if not self._cache_dir.exists():
            return result
        for uc_dir in sorted(self._cache_dir.iterdir()):
            if not uc_dir.is_dir() or uc_dir.name.startswith("_"):
                continue
            meta = self._read_meta(uc_dir.name)
            agents = meta.get("agent_status", {})
            timestamps = meta.get("timestamps", {})
            # Calculate total size
            total_size = sum(f.stat().st_size for f in uc_dir.rglob("*") if f.is_file())
            result.append({
                "uc_name": uc_dir.name,
                "agents": agents,
                "timestamps": timestamps,
                "size_kb": round(total_size / 1024, 1),
                "cached_steps": len([a for a, s in agents.items() if s == "passed"]),
            })
        return result

    def _read_meta(self, uc_name: str) -> dict:
        meta_path = self.uc_cache_dir(uc_name) / "_cache_meta.json"
        if meta_path.exists():
            try:
                return json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _write_meta(self, uc_name: str, meta: dict) -> None:
        meta_path = self.uc_cache_dir(uc_name) / "_cache_meta.json"
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
