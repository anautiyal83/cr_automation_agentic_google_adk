"""CIQ processor JAR execution — runs validation + JSON output tests."""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Default timeout for JAR execution (seconds)
DEFAULT_TIMEOUT = 300


async def run_ciq_processor(
    jar_path: str,
    ciq_file_path: str,
    validation_rules_path: str,
    json_output_path: str,
    output_dir: str,
    java_home: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    """Run the ciq-processor JAR and return execution metadata.

    Args:
        jar_path: Path to ciq-processor-*.jar
        ciq_file_path: Path to CIQ Excel file
        validation_rules_path: Path to validation-rules.yaml
        json_output_path: Path to json-output.yaml
        output_dir: Directory for JAR output (qa/ subdirectory)
        java_home: Optional JAVA_HOME override
        timeout: Subprocess timeout in seconds

    Returns:
        dict with keys: success, exit_code, stdout, stderr, output_dir
    """
    java = "java"
    if java_home:
        java = str(Path(java_home) / "bin" / "java")

    qa_dir = Path(output_dir) / "qa"
    qa_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        java, "-jar", str(jar_path),
        "--ciq", str(ciq_file_path),
        "--validation-rules", str(validation_rules_path),
        "--json-output-config-file", str(json_output_path),
        "--output-dir", str(qa_dir),
        "--report-format", "JSON",
    ]

    logger.info("Running ciq-processor: %s", " ".join(cmd[:6]) + " ...")

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

        logger.info("ciq-processor finished: exit_code=%d, stdout=%d chars, stderr=%d chars",
                     exit_code, len(stdout), len(stderr))

        return {
            "success": exit_code == 0,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "output_dir": str(qa_dir),
        }

    except asyncio.TimeoutError:
        logger.error("ciq-processor timed out after %ds", timeout)
        return {
            "success": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Timeout after {timeout}s",
            "output_dir": str(qa_dir),
        }
    except Exception as e:
        logger.error("ciq-processor execution failed: %s", e)
        return {
            "success": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": str(e),
            "output_dir": str(qa_dir),
        }


def write_yaml_to_file(content: str, run_dir: str, filename: str) -> str:
    """Write YAML content to a file in the run directory. Returns the file path."""
    path = Path(run_dir) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path)
