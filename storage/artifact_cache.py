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


class ArtifactCache:
    """Manages UC-level artifact caching."""

    def __init__(self, cache_dir: str = "data/cache"):
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def uc_cache_dir(self, uc_name: str) -> Path:
        return self._cache_dir / uc_name

    def has_cache(self, uc_name: str, agent_name: str) -> bool:
        """Check if valid cached artifacts exist for this agent+UC."""
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
        return agent_status == "passed"

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
                   artifacts: dict[str, Any], status: str = "passed") -> None:
        """Save artifacts to cache for this agent+UC."""
        entries = _AGENT_CACHE_MAP.get(agent_name, [])
        if not entries:
            return

        uc_dir = self.uc_cache_dir(uc_name)
        uc_dir.mkdir(parents=True, exist_ok=True)

        for cache_file, artifact_key in entries:
            content = artifacts.get(artifact_key)
            if content is None:
                continue

            path = uc_dir / cache_file
            if cache_file.endswith("/"):
                # Directory — content is dict of {filename: text}
                if isinstance(content, dict):
                    path.mkdir(parents=True, exist_ok=True)
                    for fname, text in content.items():
                        (path / fname).write_text(str(text), encoding="utf-8")
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(str(content), encoding="utf-8")

        # Update metadata
        meta = self._read_meta(uc_name)
        agent_status = meta.setdefault("agent_status", {})
        agent_status[agent_name] = status
        meta.setdefault("timestamps", {})[agent_name] = datetime.now(timezone.utc).isoformat()
        self._write_meta(uc_name, meta)

        logger.info("Cached %d artifacts for %s/%s (status=%s)",
                     len(entries), uc_name, agent_name, status)

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
            self._write_meta(uc_name, meta)
            logger.info("Cleared cache for %s/%s", uc_name, agent_name)
        else:
            shutil.rmtree(uc_dir, ignore_errors=True)
            logger.info("Cleared all cache for %s", uc_name)

    def clear_from_step(self, uc_name: str, from_agent: str) -> None:
        """Clear cache for an agent and all downstream agents."""
        # Order of agents in pipeline
        agent_order = [
            "requirements_gatherer",
            "ciq_validation_json_gen",
            "network_config_gen",
            "helper_script_gen",
        ]
        try:
            idx = agent_order.index(from_agent)
        except ValueError:
            idx = 0
        for agent in agent_order[idx:]:
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
