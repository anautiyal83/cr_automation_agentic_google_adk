"""MockWire simulator process management — start, health check, stop.

MockWire CLI: java -jar mockwire.jar --name <server_name> --port <port>
Config loaded from: config/<name>.yaml (relative to CWD)
Responses from: outputs/<name>/responses/ (relative to CWD)
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

STARTUP_TIMEOUT = 30
HEALTH_CHECK_INTERVAL = 1


class SimulatorProcess:
    """Manages running MockWire simulator processes (one per node)."""

    def __init__(self):
        self._processes: list[asyncio.subprocess.Process] = []
        self._config_dir: str = ""
        self._ports: list[int] = []

    @property
    def is_running(self) -> bool:
        return any(p.returncode is None for p in self._processes)

    @property
    def ports(self) -> list[int]:
        return self._ports

    async def stop(self):
        """Stop all simulator processes."""
        for proc in self._processes:
            if proc.returncode is None:
                logger.info("Stopping MockWire simulator (pid=%s)", proc.pid)
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=10)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                except Exception as e:
                    logger.error("Error stopping simulator: %s", e)
        self._processes.clear()


async def start_simulator(
    mockwire_jar: str,
    config_dir: str,
    java_home: str | None = None,
    timeout: int = STARTUP_TIMEOUT,
    ports: list[int] | None = None,
) -> SimulatorProcess:
    """Start MockWire simulator instances for each node config.

    MockWire expects: java -jar mockwire.jar --name <name> --port <port>
    Config is loaded from config/<name>.yaml relative to CWD.

    Args:
        mockwire_jar: Path to mockwire.jar
        config_dir: Directory containing config/<name>.yaml files
        java_home: Optional JAVA_HOME override
        timeout: Max seconds to wait for startup
        ports: Expected ports (if known, used for health check)

    Returns:
        SimulatorProcess handle
    """
    java = "java"
    if java_home:
        java = str(Path(java_home) / "bin" / "java")

    config_path = Path(config_dir)
    if not config_path.exists():
        raise RuntimeError(f"Simulator config directory not found: {config_dir}")

    # Find all config YAML files
    config_files = list((config_path / "config").glob("*.yaml"))
    if not config_files:
        # Config might be directly in config_dir
        config_files = list(config_path.glob("*.yaml"))

    sim = SimulatorProcess()
    sim._config_dir = config_dir
    sim._ports = ports or []

    env = dict(os.environ)
    if java_home:
        env["JAVA_HOME"] = java_home

    if not config_files and ports:
        # No named configs found — start with default name and provided ports
        for i, port in enumerate(ports):
            name = f"server_{i}"
            cmd = [java, "-jar", str(Path(mockwire_jar).resolve()),
                   "--name", name, "--port", str(port)]
            logger.info("Starting MockWire: %s (cwd=%s)", " ".join(cmd[-4:]), config_dir)
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env=env, cwd=str(config_path),
            )
            sim._processes.append(proc)
    else:
        # Start one instance per config file
        port_idx = 0
        for cf in config_files:
            name = cf.stem
            port = ports[port_idx] if ports and port_idx < len(ports) else 2222 + port_idx
            cmd = [java, "-jar", str(Path(mockwire_jar).resolve()),
                   "--name", name, "--port", str(port)]
            logger.info("Starting MockWire: %s (cwd=%s)", " ".join(cmd[-4:]), config_dir)
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env=env, cwd=str(config_path),
            )
            sim._processes.append(proc)
            if port not in sim._ports:
                sim._ports.append(port)
            port_idx += 1

    if not sim._processes:
        raise RuntimeError("No simulator processes started — no config files found")

    # Wait for readiness
    try:
        await asyncio.wait_for(_wait_for_ready(sim, sim._ports), timeout=timeout)
        logger.info("MockWire ready: %d processes on ports %s", len(sim._processes), sim._ports)
    except asyncio.TimeoutError:
        stderr = await _collect_stderr(sim)
        await sim.stop()
        raise RuntimeError(f"MockWire failed to start within {timeout}s. stderr: {stderr[:500]}")

    return sim


async def _wait_for_ready(sim: SimulatorProcess, ports: list[int]) -> None:
    """Wait until simulator processes are ready."""
    await asyncio.sleep(1)

    # Check if any process crashed immediately
    for proc in sim._processes:
        if proc.returncode is not None:
            stderr = await _collect_stderr(sim)
            raise RuntimeError(f"Simulator exited immediately (code={proc.returncode}): {stderr[:300]}")

    if not ports:
        await asyncio.sleep(2)
        return

    for _ in range(STARTUP_TIMEOUT):
        # Check for crashes
        for proc in sim._processes:
            if proc.returncode is not None:
                stderr = await _collect_stderr(sim)
                raise RuntimeError(f"Simulator exited during startup (code={proc.returncode}): {stderr[:300]}")

        all_ready = True
        for port in ports:
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection("localhost", port), timeout=2
                )
                writer.close()
                await writer.wait_closed()
            except (ConnectionRefusedError, asyncio.TimeoutError, OSError):
                all_ready = False
                break

        if all_ready:
            return
        await asyncio.sleep(HEALTH_CHECK_INTERVAL)


async def _collect_stderr(sim: SimulatorProcess) -> str:
    """Collect stderr from all processes."""
    parts = []
    for proc in sim._processes:
        try:
            stderr_bytes = await asyncio.wait_for(proc.stderr.read(2000), timeout=2)
            parts.append(stderr_bytes.decode("utf-8", errors="replace"))
        except Exception:
            pass
    return "\n".join(parts)


def write_config_files(
    run_dir: str,
    uc_name: str,
    simulator_config_yaml: str,
    response_configs: dict[str, str] | None = None,
) -> str:
    """Write simulator config and response files to disk.

    MockWire expects config/<name>.yaml relative to CWD.
    Creates: <run_dir>/simulator/config/<uc_name>.yaml

    Returns:
        Path to the simulator base directory (to be used as CWD)
    """
    sim_dir = Path(run_dir) / "simulator"
    config_dir = sim_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)

    # Write main config with UC name as the server name
    main_config = config_dir / f"{uc_name}.yaml"
    main_config.write_text(simulator_config_yaml, encoding="utf-8")

    # Write response files
    if response_configs:
        for filename, content in response_configs.items():
            responses_dir = sim_dir / "outputs" / uc_name / "responses"
            responses_dir.mkdir(parents=True, exist_ok=True)
            (responses_dir / filename).write_text(content, encoding="utf-8")

    logger.info("Simulator config written to %s (%d response files)",
                 config_dir, len(response_configs or {}))
    return str(sim_dir)
