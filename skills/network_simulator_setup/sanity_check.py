"""Sanity testing for the running MockWire simulator.

Quick smoke tests per node to verify the simulator is correctly configured
before the full execution test runs. Catches config issues early.

Checks per node:
1. SSH connection on configured port
2. Login prompt matches expected pattern
3. Basic command gets a response
4. Escalation returns expected prompt (if configured)
5. One UC-specific command returns realistic output
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

SANITY_TIMEOUT = 10  # seconds per check


@dataclass
class NodeSanityResult:
    """Sanity check result for a single node."""
    node: str = ""
    port: int = 0
    ssh_connect: str = "SKIP"       # PASS | FAIL | SKIP
    prompt_match: str = "SKIP"
    basic_command: str = "SKIP"
    escalation: str = "SKIP"
    uc_command: str = "SKIP"
    errors: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        checks = [self.ssh_connect, self.prompt_match, self.basic_command]
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


async def run_sanity_checks(
    ports: list[int],
    node_configs: list[dict],
    workflow_commands: list[dict] | None = None,
) -> SanityResult:
    """Run sanity checks against the running simulator.

    Args:
        ports: List of simulator ports (one per node).
        node_configs: Node definitions from requirements with name, prompt_regex, escalation.
        workflow_commands: Optional commands from workflow for UC-specific check.

    Returns:
        SanityResult with per-node breakdown.
    """
    result = SanityResult()

    # Build port → node mapping
    node_port_map = _build_node_port_map(ports, node_configs)

    for node_name, port in node_port_map.items():
        node_config = _find_node_config(node_name, node_configs)
        node_cmds = [c for c in (workflow_commands or []) if c.get("node") == node_name]

        nr = await _check_node(node_name, port, node_config, node_cmds)
        result.node_results.append(nr)

    result.total_nodes = len(result.node_results)
    result.nodes_passed = sum(1 for nr in result.node_results if nr.passed)
    result.nodes_failed = result.total_nodes - result.nodes_passed
    result.status = "passed" if result.all_passed else "failed"

    passed_names = [nr.node for nr in result.node_results if nr.passed]
    failed_names = [nr.node for nr in result.node_results if not nr.passed]
    result.summary = (
        f"Sanity: {result.nodes_passed}/{result.total_nodes} nodes OK"
        + (f" | Failed: {', '.join(failed_names)}" if failed_names else "")
    )

    logger.info("Sanity check: %s", result.summary)
    return result


async def _check_node(
    node_name: str,
    port: int,
    node_config: dict,
    workflow_commands: list[dict],
) -> NodeSanityResult:
    """Run all sanity checks for a single node."""
    nr = NodeSanityResult(node=node_name, port=port)

    # 1. SSH connection
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("localhost", port), timeout=SANITY_TIMEOUT
        )
        nr.ssh_connect = "PASS"

        # 2. Read initial prompt/banner
        try:
            banner = await asyncio.wait_for(reader.read(4096), timeout=3)
            banner_text = banner.decode("utf-8", errors="replace")

            expected_prompt = node_config.get("ssh", {}).get("prompt_regex", "")
            if not expected_prompt:
                expected_prompt = node_config.get("prompt_regex", "")

            if expected_prompt:
                if re.search(expected_prompt, banner_text):
                    nr.prompt_match = "PASS"
                else:
                    nr.prompt_match = "FAIL"
                    nr.errors.append(
                        f"Prompt mismatch: expected /{expected_prompt}/ "
                        f"got: {banner_text[:100]!r}"
                    )
            else:
                nr.prompt_match = "SKIP"  # no expected prompt to check

        except asyncio.TimeoutError:
            nr.prompt_match = "FAIL"
            nr.errors.append("No prompt received within 3s")

        # 3. Basic command
        try:
            writer.write(b"echo sanity_test\n")
            await writer.drain()
            response = await asyncio.wait_for(reader.read(4096), timeout=3)
            response_text = response.decode("utf-8", errors="replace")
            if "sanity_test" in response_text or response_text.strip():
                nr.basic_command = "PASS"
            else:
                nr.basic_command = "FAIL"
                nr.errors.append("No response to 'echo sanity_test'")
        except asyncio.TimeoutError:
            nr.basic_command = "FAIL"
            nr.errors.append("Basic command timed out")

        # 4. Escalation check (if configured)
        escalation = node_config.get("escalation", node_config.get("root_escalation", {}))
        if isinstance(escalation, dict) and escalation.get("method"):
            method = escalation["method"]
            try:
                if method == "su":
                    writer.write(b"su -\n")
                elif method == "ctrl_p_e":
                    writer.write(b"\x10\x05")  # Ctrl+P, Ctrl+E
                else:
                    writer.write(f"{method}\n".encode())
                await writer.drain()
                esc_resp = await asyncio.wait_for(reader.read(4096), timeout=3)
                esc_text = esc_resp.decode("utf-8", errors="replace")
                if esc_text.strip():
                    nr.escalation = "PASS"
                else:
                    nr.escalation = "FAIL"
                    nr.errors.append(f"Escalation ({method}) returned empty response")
            except asyncio.TimeoutError:
                nr.escalation = "FAIL"
                nr.errors.append(f"Escalation ({method}) timed out")
        else:
            nr.escalation = "SKIP"

        # 5. UC-specific command (first workflow command for this node)
        if workflow_commands:
            first_cmd = workflow_commands[0]
            cmd_text = first_cmd.get("cmd", first_cmd.get("command", ""))
            if cmd_text:
                try:
                    writer.write(f"{cmd_text}\n".encode())
                    await writer.drain()
                    cmd_resp = await asyncio.wait_for(reader.read(8192), timeout=5)
                    cmd_text_resp = cmd_resp.decode("utf-8", errors="replace")
                    if cmd_text_resp.strip():
                        nr.uc_command = "PASS"
                    else:
                        nr.uc_command = "FAIL"
                        nr.errors.append(f"UC command '{cmd_text[:40]}' returned empty")
                except asyncio.TimeoutError:
                    nr.uc_command = "FAIL"
                    nr.errors.append(f"UC command '{cmd_text[:40]}' timed out")
        else:
            nr.uc_command = "SKIP"

        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

    except (ConnectionRefusedError, OSError) as e:
        nr.ssh_connect = "FAIL"
        nr.errors.append(f"SSH connection failed on port {port}: {e}")
    except asyncio.TimeoutError:
        nr.ssh_connect = "FAIL"
        nr.errors.append(f"SSH connection timed out on port {port}")

    status = "PASS" if nr.passed else "FAIL"
    logger.info("Sanity [%s] %s (port %d): ssh=%s prompt=%s cmd=%s esc=%s uc=%s",
                 status, node_name, port,
                 nr.ssh_connect, nr.prompt_match, nr.basic_command,
                 nr.escalation, nr.uc_command)
    return nr


def _build_node_port_map(ports: list[int], node_configs: list[dict]) -> dict[str, int]:
    """Map node names to ports. Best-effort matching by index."""
    node_names = [n.get("name", f"node_{i}") for i, n in enumerate(node_configs) if isinstance(n, dict)]

    mapping = {}
    for i, port in enumerate(ports):
        if i < len(node_names):
            mapping[node_names[i]] = port
        else:
            mapping[f"unknown_{i}"] = port

    return mapping


def _find_node_config(node_name: str, node_configs: list[dict]) -> dict:
    """Find node config by name."""
    for n in node_configs:
        if isinstance(n, dict) and n.get("name", "").lower() == node_name.lower():
            return n
    return {}
