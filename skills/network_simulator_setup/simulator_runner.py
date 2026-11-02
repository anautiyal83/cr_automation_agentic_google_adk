"""MockWire simulator process management — per-run layout, start, health check, stop.

MockWire CLI: java -jar mockwire.jar --name <name> --port <port> --ttl-minutes <n>
MockWire derives its paths from the JAR location:
    config/<name>.yaml           — server config
    outputs/<name>/responses/    — file-based responses
so every run gets its own copy of the JAR under ``<run_dir>/simulator/``. Runs stay
isolated from each other and ``lib/mockwire`` is never modified.

Per-run layout::

    <run_dir>/simulator/
        mockwire.jar
        config/<instance>.yaml
        outputs/<instance>/responses/<file>
        logs/<instance>.log
        manifest.json
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import socket
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

STARTUP_TIMEOUT = 30
HEALTH_CHECK_INTERVAL = 1
DEFAULT_TTL_MINUTES = 60
BASE_PORT = 22200
LOG_TAIL_LINES = 50
JAR_NAME = "mockwire.jar"
MANIFEST_NAME = "manifest.json"


class SimulatorStartError(RuntimeError):
    """MockWire failed to start. Message includes log paths and log tails."""


@dataclass
class SimulatorInstance:
    """One MockWire server process (one simulated endpoint)."""
    name: str
    port: int
    nodes: list[str] = field(default_factory=list)  # workflow nodes served by this instance
    log_file: str = ""
    pid: int | None = None
    status: str = "pending"  # pending | running | stopped | failed


class SimulatorProcess:
    """Manages the running MockWire instances of one pipeline run."""

    def __init__(self, sim_dir: str, host: str = "localhost"):
        self.sim_dir = sim_dir
        self.host = host
        self.instances: list[SimulatorInstance] = []
        self._processes: list[asyncio.subprocess.Process] = []
        self._log_handles: list = []

    @property
    def is_running(self) -> bool:
        return any(p.returncode is None for p in self._processes)

    @property
    def ports(self) -> list[int]:
        return [i.port for i in self.instances]

    def port_for(self, node: str) -> int | None:
        """Port of the instance serving a workflow node (case-insensitive)."""
        for inst in self.instances:
            if node.lower() in (n.lower() for n in inst.nodes) or node.lower() == inst.name.lower():
                return inst.port
        return None

    def log_tail(self, instance: SimulatorInstance, lines: int = LOG_TAIL_LINES) -> str:
        return read_log_tail(instance.log_file, lines)

    async def stop(self):
        """Stop all simulator processes and record the final state in the manifest."""
        for proc, inst in zip(self._processes, self.instances):
            if proc.returncode is None:
                logger.info("Stopping MockWire %s (pid=%s, port=%d)", inst.name, proc.pid, inst.port)
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=10)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                except Exception as e:
                    logger.error("Error stopping simulator %s: %s", inst.name, e)
            inst.status = "stopped"
        self._processes.clear()
        for fh in self._log_handles:
            try:
                fh.close()
            except Exception:
                pass
        self._log_handles.clear()
        write_manifest(self)

    def manifest(self) -> dict:
        return {
            "sim_dir": self.sim_dir,
            "host": self.host,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "instances": [asdict(i) for i in self.instances],
        }


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def prepare_simulator_dir(run_dir: str, mockwire_jar: str) -> Path:
    """Create a clean ``<run_dir>/simulator/`` with its own copy of the MockWire JAR.

    Previous configs and responses are removed so stale instances never start.
    """
    jar = Path(mockwire_jar).resolve()
    if not jar.exists():
        raise SimulatorStartError(f"MockWire JAR not found: {jar}")

    sim_dir = Path(run_dir) / "simulator"
    for sub in ("config", "outputs"):
        shutil.rmtree(sim_dir / sub, ignore_errors=True)
    for sub in ("config", "outputs", "logs"):
        (sim_dir / sub).mkdir(parents=True, exist_ok=True)

    local_jar = sim_dir / JAR_NAME
    if not local_jar.exists() or local_jar.stat().st_size != jar.stat().st_size:
        shutil.copy2(jar, local_jar)
        logger.info("Copied MockWire JAR to %s", local_jar)
    return sim_dir


def write_instance_files(
    sim_dir: str | Path,
    instance_name: str,
    config_yaml: str,
    responses: dict[str, str] | None = None,
) -> Path:
    """Write ``config/<instance>.yaml`` and ``outputs/<instance>/responses/*``."""
    sim_dir = Path(sim_dir)
    config_file = sim_dir / "config" / f"{instance_name}.yaml"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(config_yaml, encoding="utf-8")

    responses_dir = sim_dir / "outputs" / instance_name / "responses"
    responses_dir.mkdir(parents=True, exist_ok=True)
    for filename, content in (responses or {}).items():
        target = responses_dir / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    logger.info("Simulator instance %s written (%d response files)",
                instance_name, len(responses or {}))
    return config_file


def allocate_ports(count: int, base: int = BASE_PORT, exclude: set[int] | None = None) -> list[int]:
    """Find ``count`` free TCP ports starting at ``base`` (bind probe)."""
    exclude = exclude or set()
    ports: list[int] = []
    port = base
    while len(ports) < count and port < 65535:
        if port not in exclude and _port_is_free(port):
            ports.append(port)
        port += 1
    if len(ports) < count:
        raise SimulatorStartError(f"Could not allocate {count} free ports from {base}")
    return ports


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------

async def start_simulator(
    sim_dir: str | Path,
    instances: list[SimulatorInstance],
    java_home: str | None = None,
    timeout: int = STARTUP_TIMEOUT,
    ttl_minutes: int = DEFAULT_TTL_MINUTES,
    host: str = "localhost",
) -> SimulatorProcess:
    """Start one MockWire process per instance from the run's own JAR copy.

    Output goes to ``logs/<instance>.log``. ``--ttl-minutes`` makes MockWire exit on
    its own if the pipeline never stops it.

    Raises:
        SimulatorStartError: with log paths and tails when any instance fails.
    """
    sim_dir = Path(sim_dir)
    jar = sim_dir / JAR_NAME
    if not jar.exists():
        raise SimulatorStartError(f"MockWire JAR missing in {sim_dir} — call prepare_simulator_dir first")
    if not instances:
        raise SimulatorStartError("No simulator instances to start")

    java = str(Path(java_home) / "bin" / "java") if java_home else "java"
    env = dict(os.environ)
    if java_home:
        env["JAVA_HOME"] = java_home

    sim = SimulatorProcess(str(sim_dir), host=host)
    for inst in instances:
        config_file = sim_dir / "config" / f"{inst.name}.yaml"
        if not config_file.exists():
            await sim.stop()
            raise SimulatorStartError(f"Config for instance {inst.name} not found: {config_file}")

        inst.log_file = str(sim_dir / "logs" / f"{inst.name}.log")
        log_fh = open(inst.log_file, "w", encoding="utf-8")
        cmd = [java, "-jar", str(jar), "--name", inst.name, "--port", str(inst.port),
               "--ttl-minutes", str(ttl_minutes)]
        logger.info("Starting MockWire %s on port %d (cwd=%s)", inst.name, inst.port, sim_dir)
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=log_fh, stderr=asyncio.subprocess.STDOUT, env=env, cwd=str(sim_dir),
        )
        inst.pid = proc.pid
        inst.status = "running"
        sim.instances.append(inst)
        sim._processes.append(proc)
        sim._log_handles.append(log_fh)

    try:
        await asyncio.wait_for(_wait_for_ready(sim), timeout=timeout)
    except (asyncio.TimeoutError, SimulatorStartError) as e:
        reason = str(e) or f"not ready within {timeout}s"
        detail = _failure_detail(sim)
        await sim.stop()
        for inst in sim.instances:
            inst.status = "failed"
        write_manifest(sim)
        raise SimulatorStartError(f"MockWire failed to start: {reason}\n{detail}") from None

    write_manifest(sim)
    logger.info("MockWire ready: %s", ", ".join(f"{i.name}:{i.port}" for i in sim.instances))
    return sim


async def _wait_for_ready(sim: SimulatorProcess) -> None:
    """Wait until every instance accepts TCP connections; fail fast on crashes."""
    pending = list(zip(sim._processes, sim.instances))
    while pending:
        still_pending = []
        for proc, inst in pending:
            if proc.returncode is not None:
                raise SimulatorStartError(
                    f"instance {inst.name} exited during startup (code={proc.returncode})")
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(sim.host, inst.port), timeout=2)
                writer.close()
                await writer.wait_closed()
            except (ConnectionRefusedError, asyncio.TimeoutError, OSError):
                still_pending.append((proc, inst))
        pending = still_pending
        if pending:
            await asyncio.sleep(HEALTH_CHECK_INTERVAL)


def _failure_detail(sim: SimulatorProcess) -> str:
    parts = []
    for inst in sim.instances:
        parts.append(f"--- {inst.name} (port {inst.port}) log: {inst.log_file}\n"
                     f"{read_log_tail(inst.log_file)}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Manifest + logs
# ---------------------------------------------------------------------------

def write_manifest(sim: SimulatorProcess) -> str:
    path = Path(sim.sim_dir) / MANIFEST_NAME
    try:
        path.write_text(json.dumps(sim.manifest(), indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("Could not write simulator manifest %s: %s", path, e)
    return str(path)


def read_manifest(run_dir: str) -> dict:
    path = Path(run_dir) / "simulator" / MANIFEST_NAME
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def read_log_tail(log_file: str, lines: int = LOG_TAIL_LINES) -> str:
    try:
        text = Path(log_file).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return "(log not available)"
    return "\n".join(text.splitlines()[-lines:])
