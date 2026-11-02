"""Deterministic simulator topology — one MockWire instance per remote workflow node.

Sources (no LLM):
- workflow ``nodes``: names, ``prompt_regex``, ``ssh.user``, ``ssh.ne_id``, ``elevate_to_root``
- workflow steps: commands per node, ``su - <user>`` escalations and their validation regex
- requirements Cat 1 nodes: login / root / service-user prompts
- CIQ JSON data (``data.nodeGroups[].nodes[].<NODE>.niamID-*``): NIAM neIds

Every chosen prompt must satisfy the node's ``prompt_regex``: the cli-automation JAR
uses it to detect that a command finished.

Elevation: MockWire cannot simulate NIAM Ctrl+P,E root elevation (gap MW-01), so a
node with ``elevate_to_root`` lands directly on its root prompt; the step 9 overlay
turns elevation off.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from .autofix import dump_yaml, ensure_runtime_entries, extract_workflow_commands, parse_yaml

logger = logging.getLogger(__name__)

SIM_USER = "oamsys"
SIM_PASSWORD = "netconf-sim"
RESERVED_KEYS = {"config", "auth", "niam", "transfer", "netconf", "curl", "rest", "recording"}


@dataclass
class NodeSpec:
    """Everything needed to simulate one remote workflow node."""
    name: str                    # workflow node key, e.g. RDS
    instance: str                # MockWire instance name (config/<instance>.yaml)
    prompt: str                  # prompt shown after login (post-elevation)
    prompt_regex: str = ""       # workflow nodes.<N>.prompt_regex
    user: str = SIM_USER
    password: str = SIM_PASSWORD
    ne_ids: list[str] = field(default_factory=list)       # empty → NIAM disabled
    escalations: dict[str, str] = field(default_factory=dict)  # "su - sdfrun" → pushed prompt
    commands: list[dict] = field(default_factory=list)    # workflow steps for this node
    transfer: bool = False       # node runs sftp/scp
    elevate_to_root: bool = False
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "node": self.name, "instance": self.instance, "prompt": self.prompt,
            "user": self.user, "ne_ids": len(self.ne_ids), "escalations": self.escalations,
            "commands": len(self.commands), "transfer": self.transfer, "warnings": self.warnings,
        }


def build_topology(
    workflow_yaml: str,
    requirements: dict | None = None,
    ciq_json_files: list[str] | None = None,
) -> list[NodeSpec]:
    """Build one NodeSpec per remote (non-local) workflow node."""
    workflow = parse_yaml(workflow_yaml)
    if not isinstance(workflow, dict):
        return []
    wf_nodes = workflow.get("nodes") or {}
    if not isinstance(wf_nodes, dict):
        return []

    req_nodes = {
        str(n.get("name", "")).lower(): n
        for n in ((requirements or {}).get("category_1_node_activity", {}).get("nodes") or [])
        if isinstance(n, dict)
    }
    niam_ids = _niam_ids_by_node(ciq_json_files or [])
    commands = extract_workflow_commands(workflow_yaml)

    specs: list[NodeSpec] = []
    for name, node in wf_nodes.items():
        if not isinstance(node, dict) or "local" in (node.get("type"), node.get("plugin")):
            continue
        ssh = node.get("ssh") or {}
        req = req_nodes.get(str(name).lower(), {})
        req_ssh = req.get("ssh") or {}
        prompt_regex = str(node.get("prompt_regex") or "")
        elevate = bool(ssh.get("elevate_to_root") or req_ssh.get("elevate_to_root"))
        node_cmds = [c for c in commands if c.get("node") == name]

        spec = NodeSpec(
            name=str(name),
            instance=_instance_name(str(name)),
            prompt="",
            prompt_regex=prompt_regex,
            user=_literal(ssh.get("user")) or _literal(req_ssh.get("user")) or SIM_USER,
            commands=node_cmds,
            transfer=any(str(c.get("cmd", "")).lstrip().startswith(("sftp", "scp")) for c in node_cmds),
            elevate_to_root=elevate,
        )
        spec.prompt = _login_prompt(spec, req_ssh)
        spec.escalations = _escalations(spec, req)

        if ssh.get("ne_id") or req.get("ne_id") or req_ssh.get("niam_login_id"):
            spec.ne_ids = niam_ids.get(spec.name.lower(), [])
            if not spec.ne_ids:
                spec.warnings.append("workflow uses NIAM ne_id but no niamID values found in CIQ JSON; "
                                     "NIAM disabled for this node")
        specs.append(spec)

    for s in specs:
        logger.info("Topology node %s: prompt=%r neIds=%d escalations=%s commands=%d%s",
                    s.name, s.prompt, len(s.ne_ids), list(s.escalations), len(s.commands),
                    f" warnings={s.warnings}" if s.warnings else "")
    return specs


def build_instance_config(spec: NodeSpec, command_entries: dict | None = None) -> str:
    """Render the MockWire config for one node.

    The deterministic parts (prompt, auth, NIAM, transfer, escalation, runtime entries)
    always win over ``command_entries`` (UC command → response, e.g. from the LLM).
    """
    cfg: dict = {
        "config": {"prompt": spec.prompt, "delayMs": 50, "jitterMs": 10,
                   "idleTimeoutSec": 300, "terminalMode": "PLAIN"},
        "auth": {"enabled": True, "users": [
            {"username": spec.user, "password": spec.password, "prompt": spec.prompt}]},
    }
    if spec.ne_ids:
        cfg["niam"] = {
            "enabled": True,
            "prompts": {"passwordPrompt": "Password: ", "neIdPrompt": "neId: ",
                        "banner": f"NIAM proxy (NetConfig simulator) - {spec.name}"},
            "mfa": {"enabled": False},
            "users": [{"username": spec.user, "password": spec.password, "neIds": list(spec.ne_ids)}],
        }
    if spec.transfer:
        cfg["transfer"] = {"sftpPrompt": "sftp> ", "scpPrompt": "scp> "}
    for su_cmd, pushed in spec.escalations.items():
        cfg[su_cmd] = {"lines": [], "pushPrompt": pushed}

    for key, val in (command_entries or {}).items():
        key = str(key)
        if key in RESERVED_KEYS or key in cfg or key == "exit" or _is_su(key):
            continue
        cfg[key] = val

    ensure_runtime_entries(cfg)
    return dump_yaml(cfg)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

def _login_prompt(spec: NodeSpec, req_ssh: dict) -> str:
    alternatives = _regex_examples(spec.prompt_regex)
    candidates: list[str] = []
    if spec.elevate_to_root:
        candidates.append(str(req_ssh.get("root_prompt") or ""))
        candidates += [a for a in alternatives if a.rstrip().endswith("#")]
    candidates.append(str(req_ssh.get("prompt_pattern") or ""))
    candidates += alternatives
    chosen = _first_matching(candidates, spec.prompt_regex)
    if not chosen:
        chosen = f"{spec.name.lower()}:~ #"
        spec.warnings.append(f"no prompt satisfies prompt_regex {spec.prompt_regex!r}; using {chosen!r}")
    return _with_space(chosen)


def _escalations(spec: NodeSpec, req: dict) -> dict[str, str]:
    """Map each `su [-] <user>` command of the node to the prompt it should push."""
    result: dict[str, str] = {}
    alternatives = _regex_examples(spec.prompt_regex)
    for cmd in spec.commands:
        text = str(cmd.get("cmd", "")).strip()
        if not _is_su(text) or text in result:
            continue
        user = text.split()[-1]
        # Step prompt_regex (step 6 moves su prompt checks there) wins over a validation regex
        candidates = _regex_examples(str(cmd.get("prompt_regex") or "")) + _regex_examples(_validation_regex(cmd))
        if str(req.get("target_user_after_root", "")) == user:
            candidates.append(str(req.get("target_user_after_root_prompt") or ""))
        candidates += [a for a in alternatives if user in a]
        candidates += [a for a in alternatives if _with_space(a) != spec.prompt]
        chosen = _first_matching(candidates, spec.prompt_regex) or next((c for c in candidates if c), "")
        if not chosen:
            chosen = f"{user}@{spec.name.lower()}:~>"
            spec.warnings.append(f"no prompt found for {text!r}; using {chosen!r}")
        result[text] = _with_space(chosen)
    return result


def _validation_regex(cmd: dict) -> str:
    val = cmd.get("validation") or {}
    if not isinstance(val, dict):
        return ""
    criteria = (val.get("success") or {}).get("criteria") or val.get("criteria") or {}
    return str(criteria.get("regex") or "") if isinstance(criteria, dict) else ""


def _regex_examples(regex: str) -> list[str]:
    """Concrete strings for each top-level alternative of a prompt regex (best effort)."""
    examples = []
    for alt in _split_alternatives(regex or ""):
        text = alt.strip()
        if text.startswith("^"):
            text = text[1:]
        if text.endswith("$") and not text.endswith("\\$"):
            text = text[:-1]
        text = (text.replace("\\s+", " ").replace("\\s*", " ").replace("\\s", " ")
                .replace("\\d+", "1").replace("\\d", "1").replace("\\w+", "x").replace("\\S+", "x")
                .replace(".*", "").replace(".+", "x"))
        text = re.sub(r"\\(.)", r"\1", text)
        if text:
            examples.append(text)  # callers keep only candidates that re-match the regex
    return examples


def _split_alternatives(regex: str) -> list[str]:
    parts, depth, current, i = [], 0, "", 0
    while i < len(regex):
        ch = regex[i]
        if ch == "\\" and i + 1 < len(regex):
            current += regex[i:i + 2]
            i += 2
            continue
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(depth - 1, 0)
        if ch == "|" and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
        i += 1
    parts.append(current)
    return [p for p in parts if p]


def _first_matching(candidates: list[str], regex: str) -> str:
    for c in candidates:
        if not c:
            continue
        if not regex:
            return c
        try:
            if re.search(regex, c) or re.search(regex, _with_space(c)):
                return c
        except re.error:
            return c
    return ""


def _with_space(prompt: str) -> str:
    return prompt if prompt.endswith(" ") else prompt + " "


# ---------------------------------------------------------------------------
# CIQ JSON / helpers
# ---------------------------------------------------------------------------

def _niam_ids_by_node(json_files: list[str]) -> dict[str, list[str]]:
    """{node name (lower): [niamID values]} across all node groups and CR files."""
    result: dict[str, list[str]] = {}
    for jf in json_files:
        try:
            doc = json.loads(Path(jf).read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Could not read CIQ JSON %s: %s", jf, e)
            continue
        data = doc.get("data", doc) if isinstance(doc, dict) else {}
        for group in data.get("nodeGroups") or []:
            for entry in (group.get("nodes") or []) if isinstance(group, dict) else []:
                if not isinstance(entry, dict):
                    continue
                for node_name, props in entry.items():
                    if not isinstance(props, dict):
                        continue
                    ids = result.setdefault(str(node_name).lower(), [])
                    for key, value in props.items():
                        if str(key).lower().startswith("niamid") and value and str(value) not in ids:
                            ids.append(str(value))
    return {k: v for k, v in result.items() if v}


def _literal(value) -> str:
    text = str(value or "").strip()
    return "" if not text or "${" in text else text


def _is_su(command: str) -> bool:
    return bool(re.match(r"^su(\s+-)?\s+\S+$", command.strip()))


def _instance_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name) or "node"
