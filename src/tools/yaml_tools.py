"""YAML and file I/O tools for agents and reviewers."""
from __future__ import annotations

import logging
from io import StringIO
from pathlib import Path

logger = logging.getLogger(__name__)


def validate_yaml_syntax(yaml_text: str) -> dict:
    """Validate YAML syntax. Returns {valid: bool, error: str}."""
    from ruamel.yaml import YAML
    try:
        YAML().load(StringIO(yaml_text))
        return {"valid": True, "error": ""}
    except Exception as e:
        return {"valid": False, "error": str(e)[:500]}


def read_file_content(file_path: str) -> str:
    """Read a file and return its content."""
    try:
        return Path(file_path).read_text(encoding="utf-8")
    except Exception as e:
        return f"Error reading file: {e}"


def write_artifact(run_dir: str, filename: str, content: str) -> str:
    """Write an artifact file to the run directory. Returns the file path."""
    path = Path(run_dir) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path)
