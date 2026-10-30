"""CLI automation JAR execution — MOP generation test + execution test.

Two JARs:
- ciq-processor: CIQ xlsx + rules + json-output-config → JSON data for MOP
- cli-automation: JSON data + schema + workflow template → MOP HTML (generate)
                  JSON data + schema + workflow template + node → execute commands
"""
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
    ciq_processor_jar: str = "lib/ciq-processor-1.0.3-cli.jar",
    validation_rules_path: str = "",
    html_template_path: str = "",
) -> dict:
    """Run MOP generation test: ciq-processor → cli-automation generate.

    Step 1: Run ciq-processor to produce JSON data from CIQ xlsx.
    Step 2: Run cli-automation generate with JSON + workflow → MOP HTML.
    """
    java = _java_cmd(java_home)
    mop_dir = Path(output_dir) / "mop_output"
    mop_dir.mkdir(parents=True, exist_ok=True)
    json_data_dir = Path(output_dir) / "json_data"
    json_data_dir.mkdir(parents=True, exist_ok=True)

    # Derive node_type and activity from the UC name (workflow path stem)
    uc_name = Path(workflow_path).stem
    node_type, activity = _parse_uc_name(uc_name)

    # --- Step 1: Run ciq-processor to produce JSON data ---
    if ciq_file_path and Path(ciq_processor_jar).exists():
        ciq_cmd = [
            java, "-jar", str(ciq_processor_jar),
            "--ciq", str(ciq_file_path),
            "--node-type", node_type,
            "--activity", activity,
            "--rules", str(validation_rules_path or json_output_path),
            "--output", str(json_data_dir),
            "--format", "JSON",
        ]
        if json_output_path:
            ciq_cmd += ["--json-output-config-file", str(json_output_path)]
            ciq_cmd += ["--json-output-dir", str(json_data_dir)]

        ciq_result = await _run_jar(ciq_cmd, java_home, timeout, "ciq-processor (MOP prep)")
        if not ciq_result["success"]:
            logger.warning("ciq-processor failed (exit %d): %s",
                          ciq_result["exit_code"], ciq_result["stderr"][:300])

    # Find the generated JSON file
    json_files = list(json_data_dir.glob("*.json"))
    if not json_files:
        # Fallback: check mop_output dir
        json_files = list(mop_dir.glob("*.json"))
    json_input = str(json_files[0]) if json_files else ""

    if not json_input:
        logger.error("No JSON data file produced by ciq-processor")
        return {
            "success": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": "No JSON data file produced by ciq-processor. "
                      "Cannot run MOP generation without processed CIQ data.",
        }

    # --- Step 2: Run cli-automation generate ---
    mop_output = str(mop_dir / f"{uc_name}_mop.html")
    cmd = [
        java, "-jar", str(cli_jar),
        "generate",
        "--json", json_input,
        "--schema", str(validation_rules_path or json_output_path),
        "--yaml", uc_name,
        "--output", mop_output,
    ]
    if html_template_path and Path(html_template_path).exists():
        cmd += ["--html-template", str(html_template_path)]
    if node_type:
        cmd += ["--node-type", node_type]
    if activity:
        cmd += ["--activity", activity]

    return await _run_jar(cmd, java_home, timeout, "MOP generate")


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
    ciq_processor_jar: str = "lib/ciq-processor-1.0.3-cli.jar",
    validation_rules_path: str = "",
) -> dict:
    """Run cli-automation JAR in execution mode against the simulator.

    Uses the JSON data produced during MOP test (or re-runs ciq-processor).
    """
    java = _java_cmd(java_home)
    exec_dir = Path(output_dir) / "exec_output"
    exec_dir.mkdir(parents=True, exist_ok=True)
    json_data_dir = Path(output_dir) / "json_data"

    uc_name = Path(workflow_path).stem
    node_type, activity = _parse_uc_name(uc_name)

    # Find JSON data from earlier ciq-processor run
    json_input = ""
    if json_data_dir.exists():
        json_files = list(json_data_dir.glob("*.json"))
        if json_files:
            json_input = str(json_files[0])

    # If no JSON data, run ciq-processor first
    if not json_input and ciq_file_path and Path(ciq_processor_jar).exists():
        json_data_dir.mkdir(parents=True, exist_ok=True)
        ciq_cmd = [
            java, "-jar", str(ciq_processor_jar),
            "--ciq", str(ciq_file_path),
            "--node-type", node_type,
            "--activity", activity,
            "--rules", str(validation_rules_path or json_output_path),
            "--output", str(json_data_dir),
            "--format", "JSON",
        ]
        if json_output_path:
            ciq_cmd += ["--json-output-config-file", str(json_output_path)]
            ciq_cmd += ["--json-output-dir", str(json_data_dir)]

        await _run_jar(ciq_cmd, java_home, timeout, "ciq-processor (exec prep)")
        json_files = list(json_data_dir.glob("*.json"))
        if json_files:
            json_input = str(json_files[0])

    if not json_input:
        return {
            "success": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": "No JSON data file for execution test.",
        }

    cmd = [
        java, "-jar", str(cli_jar),
        "execute",
        "--json", json_input,
        "--schema", str(validation_rules_path or json_output_path),
        "--yaml", uc_name,
        "--node", node_type,
        "--output-dir", str(exec_dir),
    ]
    if node_type:
        cmd += ["--node-type", node_type]
    if activity:
        cmd += ["--activity", activity]

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


def _parse_uc_name(uc_name: str) -> tuple[str, str]:
    """Extract node_type and activity from UC name.

    Convention: <NODE_TYPE>_<NUMBER>_<ACTIVITY_NAME>
    Example: PGW_RDS_1058_IFC_CAPABILITY_MODIFICATION → ("PGW_RDS", "1058_IFC_CAPABILITY_MODIFICATION")

    Falls back to (uc_name, uc_name) if pattern doesn't match.
    """
    import re
    # Try pattern: letters/underscores, then digits, then rest
    m = re.match(r"^([A-Za-z_]+?)_(\d+_.+)$", uc_name)
    if m:
        return m.group(1), m.group(2)
    # Fallback: split at first digit group
    m = re.match(r"^([A-Za-z_]+)_(.+)$", uc_name)
    if m:
        return m.group(1), m.group(2)
    return uc_name, uc_name


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

        if exit_code != 0:
            logger.warning("%s failed: exit_code=%d\nstdout: %s\nstderr: %s",
                          label, exit_code, stdout[:500], stderr[:500])
        else:
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
