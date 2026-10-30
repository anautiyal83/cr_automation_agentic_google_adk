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
        # Sanitize validation-rules for JAR compatibility
        rules_for_jar = validation_rules_path
        if rules_for_jar:
            rules_for_jar = _sanitize_rules_for_jar(rules_for_jar)

        ciq_cmd = [
            java, "-jar", str(ciq_processor_jar),
            "--ciq", str(ciq_file_path),
            "--node-type", node_type,
            "--activity", activity,
            "--rules", str(rules_for_jar or json_output_path),
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
        # Check if ciq-processor reported validation failures
        ciq_stdout = ciq_result.get("stdout", "") if ciq_file_path else ""
        if "STATUS=FAILED" in ciq_stdout:
            logger.warning("CIQ validation failed — no JSON data for MOP generation")
            return {
                "success": False,
                "exit_code": 2,
                "stdout": ciq_stdout,
                "stderr": "CIQ validation failed — cannot generate MOP without valid CIQ data. "
                          "Fix validation-rules.yaml or CIQ data first.",
            }
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
        rules_for_jar = _sanitize_rules_for_jar(validation_rules_path) if validation_rules_path else ""
        ciq_cmd = [
            java, "-jar", str(ciq_processor_jar),
            "--ciq", str(ciq_file_path),
            "--node-type", node_type,
            "--activity", activity,
            "--rules", str(rules_for_jar or json_output_path),
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


def _sanitize_rules_for_jar(rules_path: str) -> str:
    """Strip LLM-generated fields unsupported by the ciq-processor JAR.

    The JAR uses SnakeYAML with strict JavaBean mapping, so any unknown
    property causes a fatal deserialization error. This function strips:
    - crossRef: keep only {sheet, column}
    - Row rules: strip 'message' from one_of, only_one, all_or_none, unique_key
    - workbook_rules: strip 'message' from subset, superset, etc.
    Writes a sanitized copy next to the original and returns its path.
    """
    from io import StringIO
    p = Path(rules_path)
    if not p.exists():
        return rules_path

    try:
        from ruamel.yaml import YAML
        yaml = YAML()
        yaml.preserve_quotes = True
        data = yaml.load(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return rules_path

        changed = False

        # --- Sanitize sheet columns ---
        sheets = data.get("sheets", {})
        if isinstance(sheets, dict):
            for sheet_name, sheet_data in sheets.items():
                if not isinstance(sheet_data, dict):
                    continue

                # Sanitize column definitions
                columns = sheet_data.get("columns", {})
                if isinstance(columns, dict):
                    for col_name, col_def in columns.items():
                        if not isinstance(col_def, dict):
                            continue
                        cr = col_def.get("crossRef")
                        if isinstance(cr, dict):
                            unsupported = [k for k in cr if k not in ("sheet", "column")]
                            for k in unsupported:
                                del cr[k]
                                changed = True

                # Remove sheet-level row rules entirely — LLM-generated
                # when/require/forbid structures are often incompatible
                # with the JAR's strict SnakeYAML deserialization
                if "rules" in sheet_data:
                    del sheet_data["rules"]
                    changed = True

        # --- Remove top-level keys the LLM often generates incorrectly ---
        # Only keep keys the JAR can reliably parse from LLM output
        _SAFE_TOP_KEYS = {"sheets", "settings"}
        for key in list(data.keys()):
            if key not in _SAFE_TOP_KEYS:
                del data[key]
                changed = True

        if not changed:
            return rules_path

        sanitized = p.with_stem(p.stem + "_sanitized")
        stream = StringIO()
        yaml.default_flow_style = False
        yaml.width = 200
        yaml.dump(data, stream)
        sanitized.write_text(stream.getvalue(), encoding="utf-8")
        logger.info("Wrote sanitized validation rules: stripped unsupported fields -> %s", sanitized.name)
        return str(sanitized)

    except Exception as e:
        logger.warning("Failed to sanitize rules: %s", e)
        return rules_path


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
