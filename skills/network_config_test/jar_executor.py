"""CLI automation JAR execution — MOP generation test + execution test.

Two JARs:
- ciq-processor: CIQ xlsx + rules + json-output-config → JSON data for MOP
- cli-automation: JSON data + schema + workflow template → MOP HTML (generate)
                  JSON data + schema + workflow template + node → execute commands
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from pathlib import Path

from core.config import runtime_params

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
    # Separate dirs: validation report vs JSON data for MOP
    ciq_val_dir = Path(output_dir) / "ciq_validation"
    ciq_val_dir.mkdir(parents=True, exist_ok=True)
    json_data_dir = Path(output_dir) / "json_data"
    json_data_dir.mkdir(parents=True, exist_ok=True)

    # Derive node_type and activity from the UC name (workflow path stem)
    uc_name = Path(workflow_path).stem
    node_type, activity = _parse_uc_name(uc_name)

    # --- Step 1: Run ciq-processor to produce JSON data ---
    ciq_result = {"success": False, "stdout": "", "stderr": "ciq-processor not run"}
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
            "--output", str(ciq_val_dir),
            "--format", "JSON",
        ]
        if json_output_path:
            ciq_cmd += ["--json-output-config-file", str(json_output_path)]
            ciq_cmd += ["--json-output-dir", str(json_data_dir)]

        ciq_result = await _run_jar(ciq_cmd, java_home, timeout, "ciq-processor (MOP prep)")
        if not ciq_result["success"]:
            logger.warning("ciq-processor failed (exit %d): %s",
                          ciq_result["exit_code"], ciq_result["stderr"][:300])

    # Find the generated JSON data file (NOT the validation report)
    json_files = [f for f in json_data_dir.glob("*.json")
                  if "VALIDATION_REPORT" not in f.name]
    json_input = str(json_files[0]) if json_files else ""

    if not json_input:
        # CIQ validation failed or no JSON output produced
        ciq_stdout = ciq_result.get("stdout", "")
        if "STATUS=FAILED" in ciq_stdout:
            logger.warning("CIQ validation failed — no JSON data for MOP generation")
            return {
                "success": False,
                "exit_code": 2,
                "stdout": ciq_stdout,
                "stderr": "CIQ validation failed — fix validation-rules or CIQ data, then re-run from step 3.",
            }
        logger.error("No JSON data file produced by ciq-processor")
        return {
            "success": False,
            "exit_code": -1,
            "stdout": ciq_result.get("stdout", ""),
            "stderr": ciq_result.get("stderr", "") or "No JSON data file produced by ciq-processor.",
        }

    # --- Step 2: Run cli-automation generate ---
    mop_output = str(mop_dir / f"{uc_name}_mop.html")
    # Use sanitized rules for schema too (same stripping needed)
    schema_path = rules_for_jar or validation_rules_path or json_output_path
    cmd = [
        java, "-jar", str(cli_jar),
        "generate",
        "--json", json_input,
        "--schema", str(schema_path),
        "--yaml", str(workflow_path),
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
    java_home: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    ciq_processor_jar: str = "lib/ciq-processor-1.0.3-cli.jar",
    validation_rules_path: str = "",
    runtime_vars: dict[str, str] | None = None,
) -> dict:
    """Run cli-automation JAR in execution mode against the simulator.

    ``runtime_vars`` (default: config ``execution.runtime_params``) are the parameters the
    production framework passes to every execution; test values go in as ``--var``.

    Uses the JSON data produced during MOP test (or re-runs ciq-processor).
    The JAR executes one node group per invocation (``--node <zone> --cr <crGroup>``),
    so it is invoked once per node group found in the CIQ JSON data. Each run
    writes its report to ``exec_output/<crGroup>/``.
    """
    plan = await prepare_execution(workflow_path, json_output_path, ciq_file_path, output_dir,
                                   java_home, timeout, ciq_processor_jar, validation_rules_path)
    if plan.get("error"):
        return {"success": False, "exit_code": -1, "stdout": "", "stderr": plan["error"]}

    exec_dir = Path(output_dir) / "exec_output"
    # Drop reports from previous attempts so results reflect this run only
    if exec_dir.exists():
        shutil.rmtree(exec_dir, ignore_errors=True)
    exec_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for target in plan["targets"]:
        result = await execute_target(plan, cli_jar, workflow_path, target,
                                      exec_dir / (target[2] or "default"), java_home, timeout,
                                      runtime_vars)
        results.append(result)

    failed = [r for r in results if not r["success"]]
    return {
        "success": not failed,
        "exit_code": failed[0]["exit_code"] if failed else 0,
        "stdout": "\n".join(f"=== {r['cr_group']}/{r['node']} ===\n{r['stdout']}" for r in results),
        "stderr": "\n".join(f"=== {r['cr_group']}/{r['node']} ===\n{r['stderr']}"
                            for r in results if r["stderr"]),
        "targets": [{"node": r["node"], "cr_group": r["cr_group"], "success": r["success"]}
                    for r in results],
    }


async def prepare_execution(
    workflow_path: str,
    json_output_path: str,
    ciq_file_path: str,
    output_dir: str,
    java_home: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    ciq_processor_jar: str = "lib/ciq-processor-1.0.3-cli.jar",
    validation_rules_path: str = "",
) -> dict:
    """Everything ``execute`` needs, computed once: CIQ JSON targets
    ``[(json_file, node_group, cr_group)]``, schema, node type/activity and the LOCAL cwd.
    Returns ``{"error": …}`` when there is nothing to execute."""
    java = _java_cmd(java_home)
    json_data_dir = Path(output_dir) / "json_data"

    uc_name = Path(workflow_path).stem
    node_type, activity = _parse_uc_name(uc_name)

    # Sanitize rules for JAR compatibility
    sanitized_rules = _sanitize_rules_for_jar(validation_rules_path) if validation_rules_path else ""

    # Find JSON data from earlier ciq-processor run (one file per CR group)
    json_inputs = _find_json_data(json_data_dir)

    # If no JSON data, run ciq-processor first
    if not json_inputs and ciq_file_path and Path(ciq_processor_jar).exists():
        json_data_dir.mkdir(parents=True, exist_ok=True)
        ciq_cmd = [
            java, "-jar", str(ciq_processor_jar),
            "--ciq", str(ciq_file_path),
            "--node-type", node_type,
            "--activity", activity,
            "--rules", str(sanitized_rules or json_output_path),
            "--output", str(json_data_dir),
            "--format", "JSON",
        ]
        if json_output_path:
            ciq_cmd += ["--json-output-config-file", str(json_output_path)]
            ciq_cmd += ["--json-output-dir", str(json_data_dir)]

        await _run_jar(ciq_cmd, java_home, timeout, "ciq-processor (exec prep)")
        json_inputs = _find_json_data(json_data_dir)

    if not json_inputs:
        return {"error": "No JSON data file for execution test."}

    targets = _execution_targets(json_inputs)
    if not targets:
        return {"error": "No node groups found in CIQ JSON data (expected data.nodeGroups[].nodes[].node)."}

    # LOCAL workflow steps run "python create_xml.py ..." in the JAR's working directory:
    # use the helper scripts folder from step 6 (all other paths are passed absolute)
    scripts_dir = Path(output_dir) / "scripts"
    return {
        "targets": targets,
        "schema_path": sanitized_rules or validation_rules_path or json_output_path,
        "node_type": node_type,
        "activity": activity,
        "local_cwd": str(scripts_dir.resolve()) if scripts_dir.is_dir() else None,
    }


async def execute_target(
    plan: dict,
    cli_jar: str,
    workflow_path: str,
    target: tuple[str, str, str],
    out_dir: str | Path,
    java_home: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    runtime_vars: dict[str, str] | None = None,
) -> dict:
    """One ``execute`` run for one (json_file, node_group, cr_group); report in ``out_dir``."""
    json_input, node, cr_group = target
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    cmd = [
        _java_cmd(java_home), "-jar", _abs(cli_jar),
        "execute",
        "--json", _abs(json_input),
        "--schema", _abs(plan["schema_path"]),
        "--yaml", _abs(workflow_path),
        "--node", node,
        "--output-dir", _abs(out_dir),
    ]
    if cr_group:
        cmd += ["--cr", cr_group]
    if plan.get("node_type"):
        cmd += ["--node-type", plan["node_type"]]
    if plan.get("activity"):
        cmd += ["--activity", plan["activity"]]
    for key, value in (runtime_params() if runtime_vars is None else runtime_vars).items():
        cmd += ["--var", f"{key}={value}"]

    result = await _run_jar(cmd, java_home, timeout, f"execution test ({cr_group}/{node})",
                            cwd=plan.get("local_cwd"))
    result.update(node=node, cr_group=cr_group)
    return result


def write_yaml_to_file(content: str, run_dir: str, filename: str) -> str:
    """Write YAML content to a file in the run directory."""
    path = Path(run_dir) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# Private
# ---------------------------------------------------------------------------

def _find_json_data(json_data_dir: Path) -> list[str]:
    """Return CIQ JSON data files (one per CR group), excluding validation reports."""
    if not json_data_dir.exists():
        return []
    return [str(f) for f in sorted(json_data_dir.glob("*.json"))
            if "VALIDATION_REPORT" not in f.name.upper()]


def _execution_targets(json_files: list[str]) -> list[tuple[str, str, str]]:
    """List (json_file, node, cr_group) for every node group in the CIQ JSON data.

    The execute subcommand's ``--node`` is the node-group key from
    ``data.nodeGroups[].nodes[].node`` (e.g. a zone like ``EAST``), not the node type.
    """
    targets: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for jf in json_files:
        try:
            doc = json.loads(Path(jf).read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Could not read CIQ JSON data %s: %s", jf, e)
            continue
        data = doc.get("data", doc) if isinstance(doc, dict) else {}
        for group in data.get("nodeGroups", []) or []:
            if not isinstance(group, dict):
                continue
            cr_group = str(group.get("crGroup", "") or "")
            names = [n.get("node") for n in group.get("nodes", []) or [] if isinstance(n, dict)]
            names = [str(n) for n in names if n] or [str(group.get("nodeGroup", "") or "")]
            for name in names:
                key = (jf, name, cr_group)
                if name and key not in seen:
                    seen.add(key)
                    targets.append(key)
    return targets


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

        # --- Sanitize sheets: whitelist safe column properties only ---
        # The JAR uses strict SnakeYAML JavaBean mapping. LLM-generated columns
        # often include complex properties (allowedValuesWhen, conditionalPattern,
        # etc.) with structures that don't match the JAR's expected types.
        # Only keep simple, scalar properties that reliably deserialize.
        _SAFE_COL_KEYS = {
            "type", "required", "unique", "ignoreCase", "description",
            "minLength", "maxLength", "pattern", "patternMessage",
            "allowedValues", "values",  # enum values
            "minValue", "maxValue", "precision",
            "minDecimal", "maxDecimal",
            "format", "accept", "accepts",
            "multi", "sheetRef", "sheetRefIgnoreCase",
            "aliases", "dropdownDisabled",
            "crossRef",  # sanitized separately below
        }
        _SAFE_CROSSREF_KEYS = {"sheet", "column"}
        _SAFE_SHEET_KEYS = {"columns", "required", "required_if_listed_in", "aliases", "settings"}

        sheets = data.get("sheets", {})
        if isinstance(sheets, dict):
            for sheet_name, sheet_data in sheets.items():
                if not isinstance(sheet_data, dict):
                    continue

                # Strip unknown sheet-level keys (rules, etc.)
                for k in list(sheet_data.keys()):
                    if k not in _SAFE_SHEET_KEYS:
                        del sheet_data[k]
                        changed = True

                # Sanitize column definitions
                columns = sheet_data.get("columns", {})
                if isinstance(columns, dict):
                    for col_name, col_def in columns.items():
                        if not isinstance(col_def, dict):
                            continue
                        # Strip unknown column properties
                        for k in list(col_def.keys()):
                            if k not in _SAFE_COL_KEYS:
                                del col_def[k]
                                changed = True
                        # allowedValues only valid on enum columns — strip from string
                        # LLM hallucinates allowedValues on string columns; only enum
                        # columns should constrain values (user must explicitly define)
                        col_type = col_def.get("type", "string")
                        if col_type != "enum" and "allowedValues" in col_def:
                            del col_def["allowedValues"]
                            changed = True
                        # Sanitize crossRef
                        cr = col_def.get("crossRef")
                        if isinstance(cr, dict):
                            for k in list(cr.keys()):
                                if k not in _SAFE_CROSSREF_KEYS:
                                    del cr[k]
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


def _abs(path) -> str:
    return str(Path(path).resolve()) if path else ""


def _local_step_env(env: dict[str, str]) -> dict[str, str]:
    """Environment in which the cli-automation JAR can run LOCAL steps (S9-04).

    The JAR runs LOCAL commands with ``bash``. On Windows a server started from PowerShell
    may not have it on PATH ("Cannot run program "bash""), and ``System32\\bash.exe`` is WSL,
    not a shell for this machine's files. Put Git Bash first, and this Python's folder, so
    ``python <script>.py`` resolves inside bash.
    """
    if os.name != "nt":
        return env
    import sys
    path = env.get("PATH", "")
    extra: list[str] = []
    bash = shutil.which("bash", path=path)
    if not bash or "system32" in bash.lower():
        candidates = []
        git = shutil.which("git", path=path)
        if git:
            root = Path(git).resolve().parent.parent  # <Git>/cmd/git.exe → <Git>
            candidates += [root / "bin", root / "usr" / "bin"]
        upper = {k.upper(): v for k, v in env.items()}  # Windows env names are case-insensitive
        local = upper.get("LOCALAPPDATA", "")
        for base in (upper.get("PROGRAMFILES"), upper.get("PROGRAMFILES(X86)"),
                     str(Path(local) / "Programs") if local else "", r"C:\Program Files"):
            if base:
                candidates += [Path(base) / "Git" / "bin", Path(base) / "Git" / "usr" / "bin"]
        found = next((c for c in candidates if (c / "bash.exe").exists()), None)
        if found:
            extra.append(str(found))
        else:
            logger.warning("No bash found for LOCAL workflow steps (install Git for Windows)")
    py_dir = str(Path(sys.executable).parent)
    if not shutil.which("python", path=path):
        extra.append(py_dir)
    if extra:
        env["PATH"] = os.pathsep.join(extra + [path])
    return env


async def _run_jar(cmd: list[str], java_home: str | None, timeout: int, label: str,
                   cwd: str | None = None) -> dict:
    """Run a JAR subprocess and return result dict."""
    logger.info("Running %s: %s", label, " ".join(cmd[:6]) + " ...")

    env = _local_step_env(dict(os.environ))
    if java_home:
        env["JAVA_HOME"] = java_home

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=cwd,
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
