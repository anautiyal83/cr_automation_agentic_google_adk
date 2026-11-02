"""Simulator overlay workflow for step 9 (spec 004 T050).

A copy of the generated workflow in which every simulated node connects to its MockWire
instance. The production workflow is never modified. Per simulated node:

- ``ssh.host/port`` → the instance; ``ssh.user`` / ``auth.password`` → simulator account
- ``ssh.ne_id`` → a literal NIAM ID the instance accepts (dropped when NIAM is off)
- ``ssh.strict_host_key_checking: 'no'`` (cli-automation uses JSch strict checking)
- ``prompt_regex`` extended with any simulator prompt it does not match — with
  ``elevate_to_root`` the JAR waits for ``prompt_regex`` already at the login prompt (DEP-10)

``elevate_to_root`` is kept: MockWire answers Ctrl+P,Ctrl+E with the root prompt.
"""
from __future__ import annotations

import re

from ruamel.yaml.scalarstring import SingleQuotedScalarString

from .autofix import dump_yaml, parse_yaml
from .topology import NodeSpec

_REGEX_META = re.compile(r"([\\.\[\]{}()*+?^$|])")


def build_overlay(workflow_yaml: str, nodes: list[NodeSpec], ports: dict[str, int],
                  host: str = "localhost") -> str:
    """Return the overlay workflow YAML for the running simulator."""
    workflow = parse_yaml(workflow_yaml)
    if not isinstance(workflow, dict):
        raise ValueError("workflow YAML could not be parsed")
    wf_nodes = workflow.get("nodes") or {}

    for spec in nodes:
        node = wf_nodes.get(spec.name)
        port = ports.get(spec.name)
        if not isinstance(node, dict) or port is None:
            continue
        node["prompt_regex"] = extend_prompt_regex(str(node.get("prompt_regex") or ""), spec.all_prompts())
        ssh = node.get("ssh")
        if not isinstance(ssh, dict):
            ssh = node["ssh"] = {}
        ssh["host"] = host
        ssh["port"] = str(port)
        ssh["user"] = spec.user
        ssh["auth"] = {"password": spec.password}
        ssh.pop("password", None)
        if spec.ne_ids:
            ssh["ne_id"] = spec.ne_ids[0]
        else:
            ssh.pop("ne_id", None)
        # Quoted: the JAR reads YAML 1.1 (SnakeYAML), where a bare `no` is boolean false
        ssh["strict_host_key_checking"] = SingleQuotedScalarString("no")
    return dump_yaml(workflow)


def extend_prompt_regex(regex: str, prompts: list[str]) -> str:
    """Append an escaped alternative for every prompt the regex does not already match."""
    parts = [regex] if regex else []
    for prompt in prompts:
        text = prompt.rstrip()
        if not text or _matches(regex, text):
            continue
        parts.append(_REGEX_META.sub(r"\\\1", text))
    return "|".join(parts)


def _matches(regex: str, text: str) -> bool:
    if not regex:
        return False
    try:
        return bool(re.search(regex, text))
    except re.error:
        return False
