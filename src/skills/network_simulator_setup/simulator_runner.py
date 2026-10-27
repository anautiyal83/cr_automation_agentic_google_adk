"""MockWire simulator process management — start, health check, stop.

Launches the MockWire JAR as a background process and waits until
all simulated SSH servers are accepting connections.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default timeouts
STARTUP_TIMEOUT = 30     # seconds to wait for simulator ready
HEALTH_CHECK_INTERVAL = 1  # seconds between readiness checks


class SimulatorProcess:
    """Manages a running MockWire simulator process."""

    def __init__(self):
        self._process: asyncio.subprocess.Process | None = None
        self._config_dir: str = ""
        self._ports: list[int] = []

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    @property
    def ports(self) -> list[int]:
        return self._ports

    async def stop(self):
        """Stop the simulator process."""
        if self._process and self._process.returncode is None:
            logger.info("Stopping MockWire simulator (pid=%s)", self._process.pid)
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=10)
            except asyncio.TimeoutError:
                logger.warning("Simulator did not stop gracefully, killing")
                self._process.kill()
                await self._process.wait()
            except Exception as e:
                logger.error("Error stopping simulator: %s", e)
        self._process = None


async def start_simulator(
    mockwire_jar: str,
    config_dir: str,
    java_home: str | None = None,
    timeout: int = STARTUP_TIMEOUT,
    ports: list[int] | None = None,
) -> SimulatorProcess:
    """Start the MockWire simulator and wait for it to be ready.

    Args:
        mockwire_jar: Path to mockwire.jar
        config_dir: Directory containing simulator config YAML + response files
        java_home: Optional JAVA_HOME override
        timeout: Max seconds to wait for startup
        ports: Expected ports to health-check (if known)

    Returns:
        SimulatorProcess handle (call .stop() when done)

    Raises:
        RuntimeError: If simulator fails to start within timeout
    """
    java = "java"
    if java_home:
        java = str(Path(java_home) / "bin" / "java")

    config_path = Path(config_dir)
    if not config_path.exists():
        raise RuntimeError(f"Simulator config directory not found: {config_dir}")

    cmd = [java, "-jar", str(mockwire_jar), "--config-dir", str(config_dir)]

    logger.info("Starting MockWire simulator: %s", " ".join(cmd[:4]) + " ...")

    env = dict(os.environ)
    if java_home:
        env["JAVA_HOME"] = java_home

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    sim = SimulatorProcess()
    sim._process = proc
    sim._config_dir = config_dir
    sim._ports = ports or []

    # Wait for readiness
    try:
        await asyncio.wait_for(_wait_for_ready(proc, ports or []), timeout=timeout)
        logger.info("MockWire simulator ready (pid=%s, ports=%s)", proc.pid, ports)
    except asyncio.TimeoutError:
        # Read any error output
        stderr = ""
        try:
            stderr_bytes = await asyncio.wait_for(proc.stderr.read(2000), timeout=2)
            stderr = stderr_bytes.decode("utf-8", errors="replace")
        except Exception:
            pass
        await sim.stop()
        raise RuntimeError(
            f"MockWire simulator failed to start within {timeout}s. "
            f"stderr: {stderr[:500]}"
        )

    return sim


async def _wait_for_ready(proc: asyncio.subprocess.Process, ports: list[int]) -> None:
    """Wait until the simulator process is ready (ports accepting connections)."""
    # First, wait for the process to not immediately crash
    await asyncio.sleep(1)
    if proc.returncode is not None:
        stderr = ""
        try:
            stderr_bytes = await proc.stderr.read(2000)
            stderr = stderr_bytes.decode("utf-8", errors="replace")
        except Exception:
            pass
        raise RuntimeError(f"Simulator exited immediately (code={proc.returncode}): {stderr[:300]}")

    if not ports:
        # No specific ports to check — just wait a bit for startup
        await asyncio.sleep(2)
        return

    # Health check: try connecting to each port
    for _ in range(STARTUP_TIMEOUT):
        if proc.returncode is not None:
            raise RuntimeError(f"Simulator exited during startup (code={proc.returncode})")

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


def write_config_files(
    run_dir: str,
    uc_name: str,
    simulator_config_yaml: str,
    response_configs: dict[str, str] | None = None,
) -> str:
    """Write simulator config and response files to disk.

    Args:
        run_dir: Pipeline run directory
        uc_name: UC name for file naming
        simulator_config_yaml: Main simulator config YAML
        response_configs: Optional dict of {filename: yaml_content} for per-node response files

    Returns:
        Path to the simulator config directory
    """
    config_dir = Path(run_dir) / "simulator" / "config"
    config_dir.mkdir(parents=True, exist_ok=True)

    # Write main config
    main_config = config_dir / f"{uc_name}_simulator-config.yaml"
    main_config.write_text(simulator_config_yaml, encoding="utf-8")

    # Write response files
    if response_configs:
        responses_dir = config_dir.parent / "responses"
        responses_dir.mkdir(parents=True, exist_ok=True)
        for filename, content in response_configs.items():
            (responses_dir / filename).write_text(content, encoding="utf-8")

    logger.info("Simulator config written to %s (%d response files)",
                 config_dir, len(response_configs or {}))
    return str(config_dir)
