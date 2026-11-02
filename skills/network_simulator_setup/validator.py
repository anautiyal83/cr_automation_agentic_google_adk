"""Structural validation of MockWire simulator configs.

MockWire config is a flat YAML map: reserved sections (``config``, ``auth``,
``niam``, …) plus one top-level key per command (exact string or ``regex:``).
"""
from __future__ import annotations

from pathlib import Path

from .autofix import RUNTIME_COMMANDS, parse_yaml

RESERVED_KEYS = {"config", "auth", "niam", "transfer", "netconf", "curl", "rest", "recording"}
_COMMAND_FIELDS = {"lines", "file", "responses", "interactive", "pushPrompt", "popPrompt"}


def validate_mockwire_config(yaml_text: str, responses_dir: str | Path | None = None) -> list[str]:
    """Return a list of problems (empty when the config is usable).

    Args:
        yaml_text: MockWire config YAML.
        responses_dir: ``outputs/<instance>/responses`` — when given, ``file:``
            references are checked for existence.
    """
    parsed = parse_yaml(yaml_text)
    if parsed is None:
        return ["Invalid YAML syntax"]
    if not isinstance(parsed, dict):
        return ["Config root must be a map (got a list)"]

    errors: list[str] = []

    config = parsed.get("config")
    if not isinstance(config, dict) or not config.get("prompt"):
        errors.append("Missing config.prompt")
    elif str(config.get("terminalMode", "")).upper() != "PLAIN":
        errors.append("config.terminalMode must be PLAIN (echo breaks cli-automation output capture)")

    auth = parsed.get("auth")
    if isinstance(auth, dict) and auth.get("enabled"):
        users = auth.get("users") or []
        if not users or not all(isinstance(u, dict) and u.get("username") for u in users):
            errors.append("auth.enabled is true but auth.users has no usernames")

    niam = parsed.get("niam")
    if isinstance(niam, dict) and niam.get("enabled"):
        if not any(isinstance(u, dict) and u.get("neIds") for u in niam.get("users") or []):
            errors.append("niam.enabled is true but no niam user has neIds")

    commands = {k: v for k, v in parsed.items() if k not in RESERVED_KEYS}
    if not any(str(k) not in RUNTIME_COMMANDS for k in commands):
        errors.append("No UC command responses defined")

    for key in RUNTIME_COMMANDS:
        if key not in commands:
            errors.append(f"Missing runtime command {key!r} (required by cli-automation JAR)")

    resp_dir = Path(responses_dir) if responses_dir else None
    for key, val in commands.items():
        if not isinstance(val, dict) or not (_COMMAND_FIELDS & set(val)):
            errors.append(f"Command {str(key)[:60]!r} has no lines/file/responses")
            continue
        if resp_dir is not None:
            for ref in _file_refs(val):
                if not (resp_dir / ref).exists():
                    errors.append(f"Command {str(key)[:60]!r} references missing file {ref!r}")

    return errors


def _file_refs(cmd: dict) -> list[str]:
    refs = [cmd["file"]] if isinstance(cmd.get("file"), str) else []
    for item in cmd.get("responses") or []:
        if isinstance(item, dict) and isinstance(item.get("file"), str):
            refs.append(item["file"])
    return refs
