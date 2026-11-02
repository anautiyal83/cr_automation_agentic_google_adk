"""SSH sanity testing for the running MockWire simulator (step 8, phase 4).

Drives each node's instance the way the cli-automation JAR will, before step 9:
1. SSH login with the simulator credentials
2. NIAM neId prompt answered (when the node is NIAM-fronted)
3. Login prompt shown
4. Root elevation: Ctrl+P, Ctrl+E shows the root prompt (``elevate_to_root`` nodes)
5. Command round trip: ``stty -echo`` and the ``__CMD_DONE__`` marker
6. Every ``su`` escalation pushes its prompt and ``exit`` returns to the working prompt
7. First UC command (without placeholders) gets a response, not "command not found"

Uses paramiko (blocking) in worker threads; nodes are checked concurrently.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

LOGIN_TIMEOUT = 10   # MockWire adds ~2 s login delay (+ NIAM)
COMMAND_TIMEOUT = 5
DONE_MARKER = "__CMD_DONE__:"
NOT_FOUND = "command not found"


@dataclass
class NodeSanityResult:
    """Sanity check result for a single node."""
    node: str = ""
    port: int = 0
    ssh_connect: str = "SKIP"       # PASS | FAIL | SKIP
    niam: str = "SKIP"
    prompt_match: str = "SKIP"
    elevation: str = "SKIP"         # Ctrl+P,Ctrl+E → root prompt
    basic_command: str = "SKIP"
    escalation: str = "SKIP"
    uc_command: str = "SKIP"
    errors: list[str] = field(default_factory=list)
    transcript: str = ""            # session output (truncated) for the report

    @property
    def passed(self) -> bool:
        checks = [self.ssh_connect, self.niam, self.prompt_match, self.elevation, self.basic_command,
                  self.escalation, self.uc_command]
        return all(c in ("PASS", "SKIP") for c in checks)


@dataclass
class SanityResult:
    """Overall sanity test result."""
    total_nodes: int = 0
    nodes_passed: int = 0
    nodes_failed: int = 0
    node_results: list[NodeSanityResult] = field(default_factory=list)
    status: str = "pending"  # "passed" | "failed"
    summary: str = ""

    @property
    def all_passed(self) -> bool:
        return self.nodes_failed == 0 and self.total_nodes > 0


async def run_sanity_checks(nodes: list, simulator, host: str = "localhost") -> SanityResult:
    """Check every node (``topology.NodeSpec``) against its running instance."""
    tasks = []
    for spec in nodes:
        port = simulator.port_for(spec.name)
        if port is None:
            tasks.append(_missing_instance(spec.name))
        else:
            tasks.append(asyncio.to_thread(_check_node, spec, host, port))
    node_results = list(await asyncio.gather(*tasks))

    result = SanityResult(node_results=node_results, total_nodes=len(node_results))
    result.nodes_passed = sum(1 for nr in node_results if nr.passed)
    result.nodes_failed = result.total_nodes - result.nodes_passed
    result.status = "passed" if result.all_passed else "failed"
    failed = [f"{nr.node} ({'; '.join(nr.errors)[:200]})" for nr in node_results if not nr.passed]
    result.summary = (f"Sanity: {result.nodes_passed}/{result.total_nodes} nodes OK"
                      + (f" | Failed: {', '.join(failed)}" if failed else ""))
    logger.info("Sanity check: %s", result.summary)
    return result


async def _missing_instance(node: str) -> NodeSanityResult:
    return NodeSanityResult(node=node, ssh_connect="FAIL", errors=["no simulator instance for node"])


def _check_node(spec, host: str, port: int) -> NodeSanityResult:
    import paramiko

    nr = NodeSanityResult(node=spec.name, port=port)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    session = _Session()
    try:
        # 1. SSH login
        try:
            client.connect(host, port=port, username=spec.user, password=spec.password,
                           look_for_keys=False, allow_agent=False, timeout=LOGIN_TIMEOUT,
                           banner_timeout=LOGIN_TIMEOUT, auth_timeout=LOGIN_TIMEOUT)
            session.channel = client.invoke_shell()
            nr.ssh_connect = "PASS"
        except Exception as e:
            nr.ssh_connect = "FAIL"
            nr.errors.append(f"SSH login failed on port {port}: {e}")
            return nr

        login_prompt = (spec.login_prompt or spec.prompt).rstrip()
        work_prompt = spec.prompt.rstrip()  # root prompt when elevated

        # 2. NIAM neId
        if spec.ne_ids:
            if "neId" in session.read_until("neId", LOGIN_TIMEOUT):
                session.send(spec.ne_ids[0])
                nr.niam = "PASS"
            else:
                nr.niam = "FAIL"
                nr.errors.append("NIAM neId prompt not shown")

        # 3. Prompt after login
        out = session.read_until(login_prompt, LOGIN_TIMEOUT)
        if login_prompt in out:
            nr.prompt_match = "PASS"
        else:
            nr.prompt_match = "FAIL"
            nr.errors.append(f"login prompt {login_prompt!r} not shown")
            return nr

        # 4. Root elevation (NIAM Ctrl+P, Ctrl+E) as cli-automation does for elevate_to_root
        session.send("stty -echo")
        session.read_until(login_prompt, COMMAND_TIMEOUT)
        if spec.root_prompt:
            session.send_raw("\x10\x05")
            if work_prompt in session.read_until(work_prompt, COMMAND_TIMEOUT):
                nr.elevation = "PASS"
            else:
                nr.elevation = "FAIL"
                nr.errors.append(f"Ctrl+P,Ctrl+E did not show root prompt {work_prompt!r}")
                return nr

        # 5. Command round trip as the JAR does it
        out = session.run(":", COMMAND_TIMEOUT)
        if DONE_MARKER in out:
            nr.basic_command = "PASS"
        else:
            nr.basic_command = "FAIL"
            nr.errors.append("no __CMD_DONE__ marker after command")

        # 6. Escalations (su pushes the service user's prompt, exit returns)
        if spec.escalations:
            nr.escalation = "PASS"
            for su_cmd, pushed in spec.escalations.items():
                out = session.run(su_cmd, COMMAND_TIMEOUT, until=pushed.rstrip())
                if pushed.rstrip() not in out:
                    nr.escalation = "FAIL"
                    nr.errors.append(f"{su_cmd!r} did not push prompt {pushed.rstrip()!r}")
                    continue
                out = session.run("exit", COMMAND_TIMEOUT, until=work_prompt)
                if not out.rstrip().endswith(work_prompt):
                    nr.escalation = "FAIL"
                    nr.errors.append(f"'exit' after {su_cmd!r} did not return to {work_prompt!r}")

        # 6. First UC command without placeholders
        uc_cmd = next((c["cmd"] for c in spec.commands if _plain_uc_command(str(c.get("cmd", "")))), "")
        if uc_cmd:
            out = session.run(uc_cmd, COMMAND_TIMEOUT)
            body = out.split(DONE_MARKER)[0]
            if DONE_MARKER not in out:
                nr.uc_command = "FAIL"
                nr.errors.append(f"UC command {uc_cmd[:50]!r} did not complete")
            elif NOT_FOUND in body:
                nr.uc_command = "FAIL"
                nr.errors.append(f"no simulator response for {uc_cmd[:50]!r}")
            else:
                nr.uc_command = "PASS"
    except Exception as e:
        nr.errors.append(f"sanity session error: {e}")
        if nr.ssh_connect == "SKIP":
            nr.ssh_connect = "FAIL"
    finally:
        nr.transcript = session.transcript[-3000:]
        try:
            client.close()
        except Exception:
            pass
        logger.info("Sanity [%s] %s (port %d): ssh=%s niam=%s prompt=%s elev=%s cmd=%s esc=%s uc=%s",
                    "PASS" if nr.passed else "FAIL", spec.name, port, nr.ssh_connect, nr.niam,
                    nr.prompt_match, nr.elevation, nr.basic_command, nr.escalation, nr.uc_command)
    return nr


class _Session:
    """Minimal interactive shell driver over a paramiko channel."""

    def __init__(self):
        self.channel = None
        self.transcript = ""

    def send(self, line: str) -> None:
        self.channel.send(line + "\n")
        self.transcript += f"\n>>> {line}\n"

    def send_raw(self, data: str) -> None:
        """Send control bytes without Enter (e.g. Ctrl+P, Ctrl+E)."""
        self.channel.send(data)
        self.transcript += "\n>>> " + "".join(f"^{chr(ord(c) + 64)}" if ord(c) < 32 else c for c in data) + "\n"

    def read_until(self, expected: str, timeout: float) -> str:
        end, out = time.time() + timeout, ""
        while time.time() < end and expected not in out:
            if self.channel.recv_ready():
                out += self.channel.recv(65535).decode("utf-8", errors="replace")
            elif self.channel.closed:
                break
            else:
                time.sleep(0.05)
        self.transcript += out
        return out

    def run(self, command: str, timeout: float, until: str = "") -> str:
        """Send ``<command>; echo __CMD_DONE__:$?`` like the JAR; read to marker or ``until``."""
        if command in ("exit",) or until:
            self.send(command)
            return self.read_until(until or DONE_MARKER, timeout)
        self.send(f"{command}; echo {DONE_MARKER}$?")
        return self.read_until(DONE_MARKER, timeout) + self.read_until("\n", 0.5)


def _plain_uc_command(cmd: str) -> bool:
    cmd = cmd.strip()
    return bool(cmd) and "${" not in cmd and not re.match(r"^(su\b|exit$|stty\b)", cmd)
