"""Deterministic simulator topology — one MockWire instance per remote workflow node.

Sources (no LLM):
- workflow ``nodes``: names, ``prompt_regex``, ``ssh.user``, ``ssh.ne_id``, ``elevate_to_root``
- workflow steps: commands per node, ``su - <user>`` escalations and their validation regex
- requirements Cat 1 nodes: login / root / service-user prompts
- CIQ JSON data (``data.nodeGroups[].nodes[].<NODE>.niamID-*``): NIAM neIds

Prompts mirror the real node (MockWire AGENTS.md §2):
- login prompt — after SSH/NIAM login (requirements ``prompt_pattern``)
- root prompt  — after NIAM Ctrl+P,Ctrl+E when ``elevate_to_root`` (``ctrl+p+e`` command)
- service-user prompts — after ``su - <user>`` (MockWire builtin su over ``auth.users``)
Prompts the workflow's ``prompt_regex`` doesn't cover are added to it in the step 9
overlay (see overlay.py, DEP-10).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from .autofix import dump_yaml, extract_workflow_commands, parse_yaml

logger = logging.getLogger(__name__)

SIM_USER = "oamsys"
SIM_PASSWORD = "netconf-sim"
RESERVED_KEYS = {"config", "auth", "niam", "transfer", "netconf", "curl", "rest", "recording", "vfs"}


@dataclass
class NodeSpec:
    """Everything needed to simulate one remote workflow node."""
    name: str                    # workflow node key, e.g. RDS
    instance: str                # MockWire instance name (config/<instance>.yaml)
    prompt: str                  # working prompt: root prompt when elevated, else login prompt
    prompt_regex: str = ""       # workflow nodes.<N>.prompt_regex
    login_prompt: str = ""       # prompt right after login
    root_prompt: str = ""        # prompt after Ctrl+P,Ctrl+E (elevate_to_root only)
    hostname: str = ""
    user: str = SIM_USER
    password: str = SIM_PASSWORD
    ne_ids: list[str] = field(default_factory=list)       # empty → NIAM disabled
    escalations: dict[str, str] = field(default_factory=dict)  # "su - sdfrun" → pushed prompt
    commands: list[dict] = field(default_factory=list)    # workflow steps for this node
    transfer: bool = False       # node runs sftp/scp
    elevate_to_root: bool = False
    vfs: dict | None = None      # sandbox dirs + triggers for file flows (file_flows.py, S8-16)
    vfs_commands: set[str] = field(default_factory=set)  # answered from the VFS, no key (DEP-59)
    prompt_templates: dict[str, str] = field(default_factory=dict)  # prompt → with \W (cwd, S8-33)
    warnings: list[str] = field(default_factory=list)

    @property
    def service_users(self) -> dict[str, str]:
        """su target user → prompt (rendered as auth.users for MockWire's builtin su)."""
        return {cmd.split()[-1]: p for cmd, p in self.escalations.items() if cmd.split()[-1] != self.user}

    def all_prompts(self) -> list[str]:
        prompts = [self.login_prompt, self.root_prompt, *self.escalations.values()]
        return [p for i, p in enumerate(prompts) if p and p not in prompts[:i]]

    def summary(self) -> dict:
        return {
            "node": self.name, "instance": self.instance, "login_prompt": self.login_prompt,
            "root_prompt": self.root_prompt, "prompt": self.prompt,
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
        node_cmds = [c for c in commands if c.get("node") == name]
        from .file_flows import node_steps
        if not node_cmds and not node_steps(workflow, str(name)):  # sftp-only nodes are used too
            # Declared but used by no step (e.g. an invented gateway node): the JAR never
            # connects to it, so it needs no simulator instance (S8-17)
            logger.info("Topology: node %s is used by no step — no simulator instance", name)
            continue
        ssh = node.get("ssh") or {}
        req = req_nodes.get(str(name).lower(), {})
        req_ssh = req.get("ssh") or {}
        prompt_regex = str(node.get("prompt_regex") or "")
        elevate = bool(ssh.get("elevate_to_root") or req_ssh.get("elevate_to_root"))

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
        _set_prompts(spec, req_ssh)
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

    The deterministic parts (prompts, users, NIAM, elevation, transfer) always win over
    ``command_entries`` (UC command → response, e.g. from the LLM). ``su``/``exit``/``stty``
    and the ``__CMD_DONE__`` marker are MockWire builtins and are never mapped.
    """
    tpl = lambda p: spec.prompt_templates.get(p, p)  # noqa: E731
    users = [{"username": spec.user, "password": spec.password, "prompt": tpl(spec.login_prompt)}]
    users += [{"username": u, "password": spec.password, "prompt": p} for u, p in spec.service_users.items()]
    cfg: dict = {
        "config": {"prompt": tpl(spec.login_prompt), "hostname": spec.hostname, "loginDelayMs": 0,
                   "delayMs": 20, "jitterMs": 0, "idleTimeoutSec": 600},
        "auth": {"enabled": True, "users": users},
    }
    if spec.root_prompt:
        cfg["ctrl+p+e"] = {"pushPrompt": tpl(spec.root_prompt)}
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
    if spec.vfs:
        cfg["vfs"] = spec.vfs

    for key, val in (command_entries or {}).items():
        key = str(key)
        if key in RESERVED_KEYS or key in cfg or _is_builtin_key(key):
            continue
        cfg[key] = val

    return dump_yaml(cfg)


def _is_builtin_key(key: str) -> bool:
    """Keys MockWire answers itself; mapping them would override builtin behaviour."""
    k = key.strip().lower()
    if k.startswith("regex:"):
        k = k[len("regex:"):].lstrip("^").replace("\\ ", " ")
    # echo/true/false print exactly what a shell would: a mapped answer can only be wrong
    # (an LLM mapped 'echo <IMSI>' to a fixed number, run c43f64ef)
    first = k.split(" ", 1)[0].rstrip("\\")
    return (_is_su(k) or k in ("exit", "logout", "quit") or k.startswith(("stty", "ctrl+p"))
            or "__cmd_done__" in k or first in ("echo", "true", "false", ":") or file_builtin(k))


# Answered from the sandbox VFS: a checksum must be the one of the bytes that were really
# transferred, and a backup archive must exist to be downloaded (MockWire a025dec)
_FILE_BUILTINS = ("sha256sum", "md5sum", "mkdir")
_TAR_CREATE = re.compile(r"^tar\s+-?[a-z]*c[a-z]*f?[a-z]*\s", re.I)


def file_builtin(cmd: str) -> bool:
    """A command (or response key) MockWire's file builtins answer."""
    k = cmd.strip()
    if k.lower().startswith("regex:"):
        k = k[len("regex:"):].lstrip("^").replace("\\ ", " ").replace("\\-", "-")
    first = k.split(" ", 1)[0].rstrip("\\")
    return first in _FILE_BUILTINS or bool(_TAR_CREATE.match(k)) or (first in ("scp", "sftp") and is_peer_copy(k))


_PEER = re.compile(r"\S@(?:\\?\[|[\w.\\-]+:)")


def is_peer_copy(cmd: str) -> bool:
    """A node's copy to / from the repo server (``scp … user@host:…``, spec 010 O-D7): MockWire
    moves the file between the two instances' VFS (``transfer.peers``)."""
    return bool(_PEER.search(cmd))


def add_peers(config_yaml: str, peers: dict[str, dict]) -> str:
    """``transfer.peers`` (workflow node name → that instance's VFS root and login password) in an
    instance config: its in-shell scp / sftp to ``user@[<node>]:`` really reach the node's files."""
    cfg = parse_yaml(config_yaml)
    if not isinstance(cfg, dict) or not peers:
        return config_yaml
    transfer = cfg.get("transfer") if isinstance(cfg.get("transfer"), dict) else {}
    transfer["peers"] = peers
    cfg["transfer"] = transfer
    return dump_yaml(cfg)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

def _set_prompts(spec: NodeSpec, req_ssh: dict) -> None:
    """Login prompt, root prompt (elevated nodes) and the working prompt."""
    alternatives = _regex_examples(spec.prompt_regex)
    if spec.elevate_to_root:
        root = _first_matching([str(req_ssh.get("root_prompt") or "")]
                               + [a for a in alternatives if a.rstrip().endswith("#")], spec.prompt_regex)
        if not root:
            root = f"{spec.name.lower()}:~ #"
            spec.warnings.append(f"no root prompt satisfies prompt_regex {spec.prompt_regex!r}; using {root!r}")
        host = _hostname(root) or spec.name.lower()
        # Login prompt as the node shows it before elevation; the overlay adds it to prompt_regex
        login = str(req_ssh.get("prompt_pattern") or "") or f"[{spec.user}@{host} ~]$"
        spec.root_prompt = _with_space(root, spec.prompt_regex)
        spec.login_prompt = _with_space(login, spec.prompt_regex)
        spec.prompt = spec.root_prompt
    else:
        login = _first_matching([str(req_ssh.get("prompt_pattern") or "")] + alternatives, spec.prompt_regex)
        if not login:
            login = f"{spec.name.lower()}:~ #"
            spec.warnings.append(f"no prompt satisfies prompt_regex {spec.prompt_regex!r}; using {login!r}")
        spec.login_prompt = spec.prompt = _with_space(login, spec.prompt_regex)
    spec.hostname = _hostname(spec.login_prompt) or _hostname(spec.root_prompt) or spec.name.lower()
    # Prompts that show the working directory follow `cd` like the real node's (MockWire `\W`)
    for attr in ("login_prompt", "root_prompt"):
        found = cwd_prompt(getattr(spec, attr))
        if found and _first_matching([found[0]], spec.prompt_regex):
            spec.prompt_templates[found[0]] = found[1]
            if spec.prompt == getattr(spec, attr):
                spec.prompt = found[0]
            setattr(spec, attr, found[0])


_BRACKET_PROMPT = re.compile(r"^(\[[^\s@\]]+@[^\s\]]+) ?([^\]\s]*)(\][#$>]\s*)$")
_COLON_PROMPT = re.compile(r"^([^\s:\[\]@]+(?:@[^\s:]+)?:)(~|/\S*)(\s*[#$>]\s*)$")


def cwd_prompt(prompt: str) -> tuple[str, str] | None:
    r"""(prompt in the login directory, MockWire template) for a prompt that shows the working
    directory: "[root@host ]# " → ("[root@host ~]# ", "[root@host \W]# "); "rds:/tmp # " →
    ("rds:~ # ", "rds:\w # "). The MRF workflow's `cd /tmp` waited for "[root@rjjvrmr03 tmp]#"
    while the simulator kept a fixed prompt (S8-33)."""
    m = _BRACKET_PROMPT.match(prompt or "")
    if m:
        return f"{m.group(1)} ~{m.group(3)}", m.group(1) + r" \W" + m.group(3)
    m = _COLON_PROMPT.match(prompt or "")
    if m:
        return f"{m.group(1)}~{m.group(3)}", m.group(1) + r"\w" + m.group(3)
    return None


def _hostname(prompt: str) -> str:
    """Host from prompts like "[user@host ~]$", "user@host:~>" or "host:~ #"."""
    m = re.search(r"@([A-Za-z0-9._-]+)|^\[?([A-Za-z0-9._-]+):", (prompt or "").strip())
    return (m.group(1) or m.group(2)) if m else ""


def _escalations(spec: NodeSpec, req: dict) -> dict[str, str]:
    """Map each `su [-] <user>` command of the node to the prompt it should push."""
    result: dict[str, str] = {}
    alternatives = _regex_examples(spec.prompt_regex)
    for cmd in spec.commands:
        text = str(cmd.get("cmd", "")).strip()
        if not _is_su(text) or text in result:
            continue
        user = text.split()[-1]
        # The step's own prompt_regex is what the JAR waits for after this command: a prompt
        # matching it wins, even when the node prompt_regex does not cover it (S8-15)
        step_regex = str(cmd.get("prompt_regex") or "")
        own = _first_matching(_regex_examples(step_regex), step_regex) if step_regex else ""
        if own:
            result[text] = _with_space(own, step_regex)
            continue
        candidates = _regex_examples(str(cmd.get("prompt_regex") or "")) + _regex_examples(_validation_regex(cmd))
        if str(req.get("target_user_after_root", "")) == user:
            candidates.append(str(req.get("target_user_after_root_prompt") or ""))
        candidates += [a for a in alternatives if user in a]
        candidates += [a for a in alternatives if _with_space(a) != spec.prompt]
        chosen = _first_matching(candidates, spec.prompt_regex) or next((c for c in candidates if c), "")
        if not chosen:
            chosen = f"{user}@{spec.name.lower()}:~>"
            spec.warnings.append(f"no prompt found for {text!r}; using {chosen!r}")
        result[text] = _with_space(chosen, step_regex or spec.prompt_regex)
    return result


def _validation_regex(cmd: dict) -> str:
    val = cmd.get("validation") or {}
    if not isinstance(val, dict):
        return ""
    criteria = (val.get("success") or {}).get("criteria") or val.get("criteria") or {}
    return str(criteria.get("regex") or "") if isinstance(criteria, dict) else ""


def _regex_examples(regex: str) -> list[str]:
    """Concrete strings for each top-level alternative of a prompt regex (best effort).

    A string generated from the parsed regex comes first: the text heuristic kept inline
    flags and line-start groups, so ``su - sdfrun`` answered with the regex itself (S8-22).
    """
    examples = []
    for alt in _split_alternatives(regex or ""):
        generated = _shortest_match(alt)
        if generated:
            examples.append(generated)
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


def _shortest_match(regex: str) -> str:
    """A short string matching ``regex`` (Java syntax), from its parse tree; "" when unsure."""
    try:
        from re import _constants as C, _parser as P  # Python 3.11+
    except ImportError:  # pragma: no cover
        import sre_constants as C, sre_parse as P
    py = re.sub(r"\(\?<(?=[A-Za-z])", "(?P<", regex)  # Java named groups
    try:
        tree = P.parse(py)
    except Exception:
        return ""

    def char_of(items) -> str:
        negate = any(op is C.NEGATE for op, _ in items)
        allowed = []
        for op, av in items:
            if op is C.LITERAL:
                allowed.append(chr(av))
            elif op is C.RANGE:
                allowed.append(chr(av[0]))
            elif op is C.CATEGORY:
                allowed.append({C.CATEGORY_DIGIT: "1", C.CATEGORY_WORD: "x", C.CATEGORY_SPACE: " ",
                                C.CATEGORY_NOT_DIGIT: "x", C.CATEGORY_NOT_WORD: " ",
                                C.CATEGORY_NOT_SPACE: "x"}.get(av, "x"))
        if negate:
            return next((c for c in "xa1-" if c not in allowed), "")
        return allowed[0] if allowed else ""

    def gen(nodes) -> str:
        out = ""
        for op, av in nodes:
            if op is C.LITERAL:
                out += chr(av)
            elif op is C.NOT_LITERAL:
                out += "x" if chr(av) != "x" else "y"
            elif op is C.ANY:
                out += "x"
            elif op is C.IN:
                out += char_of(av)
            elif op in (C.MAX_REPEAT, C.MIN_REPEAT, getattr(C, "POSSESSIVE_REPEAT", None)):
                lo, _, sub = av
                out += gen(sub) * lo
            elif op is C.SUBPATTERN:
                out += gen(av[-1])
            elif op is getattr(C, "ATOMIC_GROUP", None):
                out += gen(av)
            elif op is C.BRANCH:
                out += min((gen(b) for b in av[1]), key=len)
            elif op in (C.AT, C.ASSERT, C.ASSERT_NOT):
                continue
            else:
                raise ValueError(op)
        return out

    try:
        text = gen(tree).strip("\r\n")
        return text if text and re.search(py, text) else ""
    except Exception:
        return ""


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


def _with_space(prompt: str, regex: str = "") -> str:
    """The prompt followed by a space, as shells print it — unless that breaks ``regex``
    (``rdshyd201$`` anchors right after the prompt: "rdshyd201 " never matched; S8-24)."""
    if prompt.endswith(" "):
        return prompt
    if regex:
        try:
            if re.search(regex, prompt) and not re.search(regex, prompt + " "):
                return prompt
        except re.error:
            pass
    return prompt + " "


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
        from tools.ciq_json import node_groups
        for _, group in node_groups(doc):
            for entry in group.get("nodes") or []:
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
