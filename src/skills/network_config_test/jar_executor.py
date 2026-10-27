"""CLI automation JAR execution — MOP generation test + execution test."""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 300


async def run_mop_test(
    cli_jar: str,
    workflow_path: str,
    json_output_path: str,
    ciq_file_path: str,
    output_dir: str,
    java_home: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    """Run cli-automation JAR in MOP generation mode.

    Tests that the workflow YAML can be parsed and produces valid MOP scenarios.
    """
    java = _java_cmd(java_home)
    mop_dir = Path(output_dir) / "mop_output"
    mop_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        java, "-jar", str(cli_jar),
        "--mode", "mop",
        "--workflow", str(workflow_path),
        "--json-output-config-file", str(json_output_path),
        "--ciq", str(ciq_file_path),
        "--output-dir", str(mop_dir),
    ]

    return await _run_jar(cmd, java_home, timeout, "MOP test")


async def run_execution_test(
    cli_jar: str,
    workflow_path: str,
    json_output_path: str,
    ciq_file_path: str,
    output_dir: str,
    simulator_host: str = "localhost",
    simulator_port: int = 2222,
    java_home: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    """Run cli-automation JAR in execution mode against the simulator.

    Executes the workflow commands against the MockWire simulator and
    captures per-command results with validation status.
    """
    java = _java_cmd(java_home)
    exec_dir = Path(output_dir) / "exec_output"
    exec_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        java, "-jar", str(cli_jar),
        "--mode", "exec",
        "--workflow", str(workflow_path),
        "--json-output-config-file", str(json_output_path),
        "--ciq", str(ciq_file_path),
        "--output-dir", str(exec_dir),
        "--host", simulator_host,
        "--port", str(simulator_port),
    ]

    return await _run_jar(cmd, java_home, timeout, "execution test")


def write_yaml_to_file(content: str, run_dir: str, filename: str) -> str:
    """Write YAML content to a file in the run directory."""
    path = Path(run_dir) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# Private
# ---------------------------------------------------------------------------

def _java_cmd(java_home: str | None) -> str:
    if java_home:
        return str(Path(java_home) / "bin" / "java")
    return "java"


async def _run_jar(cmd: list[str], java_home: str | None, timeout: int, label: str) -> dict:
    """Run a JAR subprocess and return result dict."""
    logger.info("Running %s: %s", label, " ".join(cmd[:6]) + " ...")

    env = dict(os.environ)
    if java_home:
        env["JAVA_HOME"] = java_home

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        exit_code = proc.returncode or 0

        logger.info("%s finished: exit_code=%d, stdout=%d chars", label, exit_code, len(stdout))
        return {
            "success": exit_code == 0,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
        }

    except asyncio.TimeoutError:
        logger.error("%s timed out after %ds", label, timeout)
        return {"success": False, "exit_code": -1, "stdout": "", "stderr": f"Timeout after {timeout}s"}
    except Exception as e:
        logger.error("%s failed: %s", label, e)
        return {"success": False, "exit_code": -1, "stdout": "", "stderr": str(e)}
