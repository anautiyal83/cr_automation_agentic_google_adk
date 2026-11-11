"""Node model as production writes it (spec 013 Stage B, B-R1/B-R2) — generic, for every use case.

- One workflow node per NIAM login of a CIQ node type: ``niam_<type>_server`` (``ne_id`` / ``host``
  ``${<TYPE>.niamID-1}``, the CIQ JSON key of the node type).
- Hosts reached from that login — a pod (``oc exec -it … -- bash``), another server (``ssh user@host``,
  ``telnet``), another user (``su - user``, ``sudo -i``) — are nested sessions on the same node: the command
  that opens the shell waits for the new prompt, the steps inside follow, ``exit`` returns to the outer prompt.
  A node the LLM made for such a host is folded into the login node; an ``exit`` is added where the LLM went
  back to the outer host, and at the end of a phase for a session still open.
- Prompts of the ``[user@host dir]$`` shape become production's generic prompt regex.
"""
from __future__ import annotations

import logging
import re
import shlex
from typing import Any

logger = logging.getLogger(__name__)

NODE_PROMPT = r"(?m)\[[^\]\r\n]+\][#$][^\r\n]*$"
STEP_PROMPT = r"(?m).*\[[^\]\r\n]+\][#$][^\r\n]*$"

_SHELLS = r"(?:/bin/|/usr/bin/)?(?:bash|sh|ksh|zsh)(?:\s+-l)?"
_EXEC_SHELL = re.compile(rf"^\s*(?:sudo\s+)?(?:oc|kubectl|docker|podman|crictl)\s+exec\b.*?(?:--\s*)?{_SHELLS}\s*$")
_RSH = re.compile(r"^\s*(?:sudo\s+)?oc\s+rsh\b(?:\s+-\S+(?:\s+\S+)?)*\s+\S+\s*$")
_SU = re.compile(r"^\s*(?:sudo\s+)?su\b(?!.*\s-c\b)(?:\s+-l?)?(?:\s+[\w.-]+)?\s*$|^\s*sudo\s+-[is]\s*$")
_TELNET = re.compile(r"^\s*telnet\s+\S+(?:\s+\d+)?\s*$")
_EXIT = re.compile(r"^\s*(exit|logout|quit)\s*$")
_SSH_FLAGS_WITH_ARG = set("BbcDEeFIiJLlmOoPpQRSWw")
_NIAM_REF = re.compile(r"\$\{([A-Za-z_][\w-]*)\.niamID-(\d+)\}")
_BRACKET = re.compile(r"^(?:\(\?m\))?\^?(?:\.\*)?\\\[")


def _ssh_destination(send: str) -> str | None:
    """``user@host`` / ``host`` of an interactive ssh (no remote command), else None."""
    try:
        words = shlex.split(send.strip(), posix=True)
    except ValueError:
        return None
    if not words or words[0] != "ssh":
        return None
    rest, i, dest = words[1:], 0, None
    while i < len(rest):
        w = rest[i]
        if w.startswith("-") and len(w) > 1:
            if len(w) == 2 and w[1] in _SSH_FLAGS_WITH_ARG:
                i += 2
                continue
            i += 1
            continue
        if re.fullmatch(r"[A-Za-z][\w]*=\S*", w):  # an option written without -o (fixed by ssh_options)
            i += 1
            continue
        if dest is None:
            dest = w
            i += 1
            continue
        return None  # a remote command: one-shot, no session
    return dest


def enter_kind(send: Any) -> str:
    """'exec' | 'ssh' | 'telnet' | 'su' when ``send`` opens an interactive shell, else ''."""
    s = str(send or "").strip()
    if not s or "\n" in s:
        return ""
    if _EXEC_SHELL.match(s) and re.search(r"\s-(?:it|ti|i\s+-t|t\s+-i)\b|--stdin\b|--tty\b|\s-i\b", s) \
            or _RSH.match(s):
        return "exec"
    if _ssh_destination(s):
        return "ssh"
    if _TELNET.match(s):
        return "telnet"
    if _SU.match(s):
        return "su"
    return ""


def is_exit(send: Any) -> bool:
    return bool(_EXIT.match(str(send or "")))


def bracket_prompt(regex: Any) -> bool:
    """A prompt regex of the ``[user@host dir]$`` / ``[…]#`` shape (production writes it generically)."""
    r = str(regex or "").strip()
    return bool(_BRACKET.match(r)) and bool(re.search(r"(?:\\\$|#|\[#\$\]|\[\$#\])(?:\\s\*|\s|\[\^\\r\\n\]\*)?\$?\s*$", r))


def ciq_type(parsed: dict, node: str) -> str:
    """The CIQ node type key a workflow node logs in to (``${EIR1.niamID-1}`` → EIR1), '' when none."""
    spec = (parsed.get("nodes") or {}).get(node)
    ssh = spec.get("ssh") if isinstance(spec, dict) and isinstance(spec.get("ssh"), dict) else {}
    m = _NIAM_REF.search(f"{ssh.get('ne_id') or ''} {ssh.get('host') or ''}")
    return m.group(1) if m else ""


def node_name(ciq_key: str, taken: set[str]) -> str:
    """``niam_<type>_server`` — the type's letters (EIR1 → eir); with digits when two types share them."""
    base = re.sub(r"[^a-z0-9]+", "_", ciq_key.lower()).strip("_")
    short = re.sub(r"\d+$", "", base) or base
    name = f"niam_{short}_server"
    return name if name not in taken else f"niam_{base}_server"


def ssh_options(send: str) -> str:
    """``ssh -oPort=8099 user@h StrictHostKeyChecking=no`` → every ``Key=value`` as ``-oKey=value``."""
    try:
        words = shlex.split(send, posix=True)
    except ValueError:
        return send
    if not words or words[0] != "ssh":
        return send
    opts = [w for w in words[1:] if re.fullmatch(r"[A-Z][A-Za-z]+=\S*", w)]
    if not opts:
        return send
    out = send
    for o in opts:
        out = re.sub(rf"(?<![-\w]){re.escape(o)}", "", out)
    rebuilt = " ".join(out.split())
    head, dest = rebuilt.rsplit(" ", 1) if " " in rebuilt else (rebuilt, "")
    return " ".join([head] + [f"-o{o}" for o in opts] + ([dest] if dest else []))


def _password_ref(user: str) -> str:
    return "${SECRET." + re.sub(r"\W+", "_", user.lower() or "remote") + "_password}"


def _exit_step(node: str, what: str, prompt: str) -> dict:
    step = {"node": node, "command_description": f"exit (leave {what})", "send": "exit", "on_failure": "stop"}
    if prompt:
        step["prompt_regex"] = prompt
    return step


def _prompt_for(regex: Any, step_level: bool) -> str:
    r = str(regex or "").strip()
    if not r:
        return ""
    return (STEP_PROMPT if step_level else NODE_PROMPT) if bracket_prompt(r) else r


def nested_sessions(parsed: dict, node_types: dict[str, int] | None) -> int:
    """Fold the hosts reached from a NIAM login into nested sessions on one ``niam_<type>_server`` node per CIQ
    node type (module doc). Returns the number of changes; 0 when the CIQ node types are unknown."""
    types = {str(k): int(v or 1) for k, v in (node_types or {}).items()}
    nodes = parsed.get("nodes")
    phases = parsed.get("phases")
    if not types or not isinstance(nodes, dict) or not isinstance(phases, dict):
        return 0
    remote = [n for n, v in nodes.items() if isinstance(v, dict)
              and "local" not in (str(v.get("type", "")).lower(), str(v.get("plugin", "")).lower())
              and not re.search(r"repo|backup", str(n), re.I) and str(v.get("type", "remote")).lower() == "remote"]
    if not remote:
        return 0
    order = []
    for body in phases.values():
        for s in (body.get("steps") if isinstance(body, dict) else None) or []:
            if isinstance(s, dict) and str(s.get("node", "")) in remote and s["node"] not in order:
                order.append(s["node"])
    # Login nodes: those that log in to a CIQ node type; with one type and none valid, the first one used
    login: dict[str, str] = {n: ciq_type(parsed, n) for n in remote if ciq_type(parsed, n) in types}
    if not login:
        by_name = {n: next((t for t in types if re.sub(r"\d+$", "", t).upper() in n.upper()
                            or n.upper() in t.upper()), "") for n in remote}
        if len(types) == 1 and order:
            login = {order[0]: next(iter(types))}
        elif all(by_name.values()):
            login = by_name
        else:
            return 0
    if set(login) == set(remote) and all(n.startswith("niam_") for n in login) and \
            not any(enter_kind(s.get("send")) for s in _all(phases)):
        return 0
    taken: set[str] = set()
    renamed: dict[str, str] = {}
    for n, t in login.items():
        renamed[n] = n if n.startswith("niam_") else node_name(t, taken)
        taken.add(renamed[n])
    reached = [n for n in remote if n not in login]
    changes = 0

    for n, t in login.items():
        spec = nodes[n]
        if not isinstance(spec.get("ssh"), dict):
            spec["ssh"] = {}
        ssh = spec["ssh"]
        ref = "${" + t + ".niamID-1}"
        if ciq_type(parsed, n) != t:
            ssh["host"], ssh["ne_id"] = ref, ref
            changes += 1
        node_prompt = _prompt_for(spec.get("prompt_regex"), False)
        if node_prompt and node_prompt != spec.get("prompt_regex"):
            spec["prompt_regex"] = node_prompt
            changes += 1
    node_prompts = {n: _prompt_for((nodes.get(n) or {}).get("prompt_regex"), True) for n in remote}

    def close(st: dict, login_node: str, out: list, keep_su: bool) -> None:
        nonlocal changes
        stack = st["stacks"][login_node]
        while stack and not (keep_su and stack[-1]["kind"] == "su"):
            s = stack.pop()
            out.append(_exit_step(renamed[login_node], s["what"], s["outer"]))
            changes += 1

    def process(steps: list, st: dict) -> list:
        """One pass over a phase in execution order; ``st`` (current login node, a session stack and an
        "on the inner host" flag per login node) is shared with the loops inside."""
        nonlocal changes
        out: list = []
        for step in steps:
            if not isinstance(step, dict):
                out.append(step)
                continue
            if isinstance(step.get("steps"), list):
                step["steps"] = process(step["steps"], st)
                out.append(step)
                continue
            node = str(step.get("node") or "")
            send = str(step.get("send") or "")
            if node in login:
                st["current"] = node  # each NIAM login is its own connection: its sessions stay open
                if st["stacks"][node] and st["inner"][node] and not is_exit(send):
                    close(st, node, out, keep_su=False)  # the LLM went back to the outer host
                st["inner"][node] = False
            elif node in reached:
                cur = st["current"]
                stack = st["stacks"][cur]
                st["inner"][cur] = True
                if stack and stack[-1]["inner"] is None:
                    stack[-1]["inner"] = node
                    enter = stack[-1]["step"]
                    if not enter.get("prompt_regex") and node_prompts.get(node):
                        enter["prompt_regex"] = node_prompts[node]
                        changes += 1
                    stack[-1]["what"] = f"{node} session"
                elif not stack:
                    logger.warning("Step on %s with no command that opens its shell — runs on the NIAM login", node)
            else:
                out.append(step)  # local / repo / ${…}
                continue
            cur = st["current"]
            stack = st["stacks"][cur]
            kind = enter_kind(send)
            if kind == "exec" and not re.search(r"\s--\s", send):  # production: `oc exec -it P -n NS -- bash`
                step["send"] = send = re.sub(rf"\s+({_SHELLS})\s*$", lambda m: " -- " + m.group(1), send)
                changes += 1
            if (not kind and not is_exit(send) and bracket_prompt(step.get("prompt_regex"))
                    and step["prompt_regex"] != STEP_PROMPT):  # a prompt copied from the log (pod name…)
                step["prompt_regex"] = STEP_PROMPT
                changes += 1
            if is_exit(send):
                if stack:
                    s = stack.pop()
                    if not step.get("prompt_regex") or re.search(r"(?i)closed|logout", str(step.get("prompt_regex"))):
                        if s["outer"]:
                            step["prompt_regex"] = s["outer"]
                st["inner"][cur] = bool(stack) and stack[-1]["inner"] is not None
            elif kind:
                if kind == "ssh":
                    fixed = ssh_options(send)
                    if fixed != send:
                        step["send"] = send = fixed
                        changes += 1
                    if not step.get("expect_reply"):
                        dest = _ssh_destination(send) or ""
                        step["expect_reply"] = [{"expect": "assword:",
                                                 "reply": _password_ref(dest.split("@")[0] if "@" in dest else "")}]
                        changes += 1
                outer = stack[-1]["prompt"] if stack else node_prompts.get(cur, "")
                if kind != "su":  # su keeps its recorded prompt (DEP-34)
                    inner_prompt = _prompt_for(step.get("prompt_regex"), True)
                    if inner_prompt and inner_prompt != step.get("prompt_regex"):
                        step["prompt_regex"] = inner_prompt
                        changes += 1
                stack.append({"step": step, "inner": None, "what": f"the {kind} session", "outer": outer,
                              "prompt": step.get("prompt_regex") or outer, "kind": kind})
            if step.get("node") != renamed[cur]:
                step["node"] = renamed[cur]
                changes += 1
            out.append(step)
        return out

    for body in phases.values():
        if isinstance(body, dict) and isinstance(body.get("steps"), list):
            st = {"current": next(iter(login)), "stacks": {n: [] for n in login}, "inner": {n: False for n in login}}
            steps = process(body["steps"], st)
            for n in login:  # a pod / server shell ends with its phase (production); su may span phases as before
                close(st, n, steps, keep_su=True)
            body["steps"] = steps
    for n in reached:
        nodes.pop(n, None)
        changes += 1
    for old, new in renamed.items():
        if old != new:
            nodes[new] = nodes.pop(old)
            changes += 1
    if changes:
        logger.info("Node model: NIAM nodes %s; nested sessions for %s", sorted(renamed.values()), reached)
    return changes


def _all(phases: dict):
    stack = [s for b in phases.values() if isinstance(b, dict) for s in (b.get("steps") or [])]
    while stack:
        s = stack.pop()
        if isinstance(s, dict):
            yield s
            stack.extend(s.get("steps") or [])
