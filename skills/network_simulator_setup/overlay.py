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

from .autofix import block_scalars, dump_yaml, parse_yaml
from .topology import NodeSpec

_REGEX_META = re.compile(r"([\\.\[\]{}()*+?^$|])")


def build_overlay(workflow_yaml: str, nodes: list[NodeSpec], ports: dict[str, int],
                  host: str = "localhost") -> str:
    """Return the overlay workflow YAML for the running simulator."""
    workflow = parse_yaml(workflow_yaml)
    if not isinstance(workflow, dict):
        raise ValueError("workflow YAML could not be parsed")
    wf_nodes = workflow.get("nodes") or {}
    _peer_copies(workflow, nodes)

    for spec, name, k in [(sp, n, i) for sp in nodes for i, n in enumerate([sp.name, *sp.aliases])]:
        node = wf_nodes.get(name)  # NIAM failover entries (aliases) log in to the same instance
        port = ports.get(spec.name)
        if not isinstance(node, dict) or port is None:
            continue
        node["prompt_regex"] = extend_prompt_regex(str(node.get("prompt_regex") or ""), spec.all_prompts(),
                                                   spec.prompt_templates)
        ssh = node.get("ssh")
        if not isinstance(ssh, dict):
            ssh = node["ssh"] = {}
        ssh["host"] = host
        ssh["port"] = str(port)
        ssh["user"] = spec.user
        ssh["auth"] = {"password": spec.password}
        ssh.pop("password", None)
        if spec.ne_ids:
            ssh["ne_id"] = spec.ne_ids[min(k, len(spec.ne_ids) - 1)]
        else:
            ssh.pop("ne_id", None)
        # Quoted: the JAR reads YAML 1.1 (SnakeYAML), where a bare `no` is boolean false
        ssh["strict_host_key_checking"] = SingleQuotedScalarString("no")
    _step_prompts(workflow, nodes)
    _secret_replies(workflow)
    _shorten_polls(workflow.get("phases") or {})
    return dump_yaml(block_scalars(workflow))


def _secret_replies(workflow: dict) -> None:
    """A password a nested login answers from ``${SECRET.…}`` (``ssh dsadmin@${IP}``, spec 013): the simulator
    shell takes any answer, and the run has no secrets — the simulator's password."""
    from .topology import SIM_PASSWORD
    stack = [s for b in (workflow.get("phases") or {}).values() if isinstance(b, dict) for s in b.get("steps") or []]
    while stack:
        step = stack.pop()
        if not isinstance(step, dict):
            continue
        stack.extend(step.get("steps") or [])
        for reply in step.get("expect_reply") or []:
            if isinstance(reply, dict) and "${SECRET." in str(reply.get("reply", "")):
                reply["reply"] = SIM_PASSWORD


def _step_prompts(workflow: dict, nodes: list[NodeSpec]) -> None:
    """A step's own ``prompt_regex`` replaces the node's: extend it with the simulator prompts too
    (the LLM gave the rollback `cd` a `user@host:` prompt the simulated node never shows; the step
    waited for it and failed, run c89f65c2)."""
    by_name = {name: spec for spec in nodes for name in [spec.name, *spec.aliases]}

    def walk(steps) -> None:
        for step in steps if isinstance(steps, list) else []:
            if not isinstance(step, dict):
                continue
            walk(step.get("steps"))
            spec = by_name.get(str(step.get("node") or ""))
            if spec is not None and step.get("prompt_regex"):
                step["prompt_regex"] = extend_prompt_regex(str(step["prompt_regex"]), spec.all_prompts(),
                                                           spec.prompt_templates)

    for body in (workflow.get("phases") or {}).values():
        walk(body.get("steps") if isinstance(body, dict) else body)


def _peer_copies(workflow: dict, nodes: list[NodeSpec]) -> None:
    """A node's copies to / from the repo server (spec 010 O-D7) name the repo by its production
    address and answer its password secret: in the overlay they name the repo's instance (a
    ``transfer.peers`` key of the node instance) and answer the simulator password."""
    from skills.network_config_gen.structure import node_login  # same address / password as step 6
    logins = {spec.name: node_login(workflow, spec.name) for spec in nodes}

    def walk(steps) -> None:
        for step in steps if isinstance(steps, list) else []:
            if not isinstance(step, dict):
                continue
            walk(step.get("steps"))
            send = str(step.get("send") or "")
            for spec in nodes:
                _, address, password = logins[spec.name]
                hits = [f"@{a}:" for a in (f"[{address}]", address) if f"@{a}:" in send]
                batch = [f"@{a}" for a in (f"[{address}]", address) if send.rstrip().endswith(f"@{a}")]
                if hits:
                    step["send"] = send = send.replace(hits[0], f"@{spec.name}:")
                elif batch:  # printf "put|get …" | sftp … user@<address> (spec 013)
                    step["send"] = send = send.rstrip()[:-len(batch[0])] + f"@{spec.name}"
                else:
                    continue
                for reply in step.get("expect_reply") or []:
                    if isinstance(reply, dict) and password and password in str(reply.get("reply", "")):
                        reply["reply"] = str(reply["reply"]).replace(password, spec.password)

    for body in (workflow.get("phases") or {}).values():
        if isinstance(body, dict):
            walk(body.get("steps"))


# The simulator answers at once (trigger files appear on upload): a poll that has not
# passed after a few quick tries never will, and 30 x 10 s outlasted the case timeout,
# which left no report at all (S9-08)
SIM_MAX_RETRIES = 3
SIM_MAX_RETRY_DELAY = 2


def _shorten_polls(phases) -> None:
    def walk(steps):
        for step in steps or []:
            if not isinstance(step, dict):
                continue
            if isinstance(step.get("retries"), int) and step["retries"] > SIM_MAX_RETRIES:
                step["retries"] = SIM_MAX_RETRIES
            for key in ("retry_delay_sec", "retry_delay"):
                if isinstance(step.get(key), (int, float)) and step[key] > SIM_MAX_RETRY_DELAY:
                    step[key] = SIM_MAX_RETRY_DELAY
            walk(step.get("steps"))

    for body in (phases.values() if isinstance(phases, dict) else phases):
        walk(body.get("steps") if isinstance(body, dict) else body)


def extend_prompt_regex(regex: str, prompts: list[str], templates: dict[str, str] | None = None) -> str:
    r"""Append an escaped alternative for every prompt the regex does not already match; a
    prompt that shows the working directory (MockWire template with \W / \w) matches any one."""
    parts = [regex] if regex else []
    for prompt in prompts:
        text = prompt.rstrip()
        if not text or _matches(regex, text):
            continue
        template = (templates or {}).get(prompt, "").rstrip()
        if template:
            escaped = _REGEX_META.sub(r"\\\1", template)
            parts.append(escaped.replace("\\\\W", r"[^\]\s]*").replace("\\\\w", r"\S*"))
        else:
            parts.append(_REGEX_META.sub(r"\\\1", text))
    return "|".join(parts)


def _matches(regex: str, text: str) -> bool:
    if not regex:
        return False
    try:
        return bool(re.search(regex, text))
    except re.error:
        return False
