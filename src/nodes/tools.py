"""Shared tools for LlmAgent reviewers and pipeline utilities."""
from __future__ import annotations

import json
import logging
import re
from io import StringIO
from pathlib import Path
from typing import Any

from google.adk.tools.function_tool import FunctionTool

logger = logging.getLogger(__name__)


# ===========================================================================
# YAML / File Tools
# ===========================================================================

def validate_yaml_syntax(yaml_text: str) -> dict:
    """Validate YAML syntax. Returns {valid: bool, error: str}."""
    from ruamel.yaml import YAML
    try:
        YAML().load(StringIO(yaml_text))
        return {"valid": True, "error": ""}
    except Exception as e:
        return {"valid": False, "error": str(e)[:500]}


def read_file_content(file_path: str) -> str:
    """Read a file and return its content."""
    try:
        return Path(file_path).read_text(encoding="utf-8")
    except Exception as e:
        return f"Error reading file: {e}"


def write_artifact(run_dir: str, filename: str, content: str) -> str:
    """Write an artifact file to the run directory. Returns the file path."""
    path = Path(run_dir) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path)


def read_ciq_structure(ciq_file_path: str) -> dict:
    """Read CIQ Excel and return sheet names + headers + sample rows."""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(ciq_file_path, data_only=True, read_only=True)
        result = {}
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            rows = list(ws.iter_rows(max_row=5, values_only=True))
            if not rows:
                continue
            headers = [str(h) if h else "" for h in rows[0]]
            samples = []
            for row in rows[1:4]:
                sample = {headers[i]: str(v) if v else "" for i, v in enumerate(row) if i < len(headers)}
                samples.append(sample)
            result[sheet_name] = {"headers": headers, "sample_rows": samples}
        wb.close()
        return result
    except Exception as e:
        return {"error": str(e)}


# ===========================================================================
# Report Reading Tools (for reviewers)
# ===========================================================================

def read_exec_report(run_dir: str) -> dict:
    """Read execution test output JSON and return command results."""
    exec_dir = Path(run_dir) / "exec_output"
    if not exec_dir.exists():
        return {"total": 0, "passed": 0, "failed": 0, "commands": []}

    all_commands = []
    for jf in exec_dir.glob("*.json"):
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
            for cmd_key, cmd_info in data.get("data", {}).items():
                all_commands.append({
                    "command": cmd_key,
                    "phase": cmd_info.get("phase", ""),
                    "output": cmd_info.get("output", "")[:300],
                    "success": cmd_info.get("success", False),
                    "failure": cmd_info.get("failure", ""),
                    "validation_status": cmd_info.get("validation_status", ""),
                    "validation_criteria": cmd_info.get("validation_criteria", ""),
                    "validation_conclusion": cmd_info.get("validation_conclusion", ""),
                })
        except Exception:
            pass

    passed = sum(1 for c in all_commands if c["success"] and c["validation_status"] != "failed")
    return {"total": len(all_commands), "passed": passed, "failed": len(all_commands) - passed, "commands": all_commands}


def read_mop_report(run_dir: str, uc_name: str) -> dict:
    """Read MOP generation report and return scenario results."""
    report_path = Path(run_dir) / f"{uc_name}_mop-generation-report.html"
    if not report_path.exists():
        return {"total": 0, "passed": 0, "failed": 0, "scenarios": []}

    html = report_path.read_text(encoding="utf-8")
    scenarios = []
    for match in re.finditer(
        r'<tr class="(?:pass|fail)-row">\s*<td[^>]*>(\w+)</td>\s*<td>([^<]*)</td>\s*<td>([^<]*)</td>\s*<td>([^<]*)</td>\s*<td>(.*?)</td>',
        html, re.DOTALL
    ):
        scenarios.append({
            "status": match.group(1),
            "scenario": match.group(2),
            "error": match.group(5)[:300],
        })

    passed = sum(1 for s in scenarios if s["status"] == "PASSED")
    return {"total": len(scenarios), "passed": passed, "failed": len(scenarios) - passed, "scenarios": scenarios}


def read_workflow_yaml(run_dir: str, uc_name: str) -> str:
    """Read the current workflow YAML."""
    path = Path(run_dir) / f"{uc_name}.yaml"
    if path.exists():
        return path.read_text(encoding="utf-8")[:5000]
    return "Workflow file not found"


def read_validation_rules(run_dir: str, uc_name: str) -> str:
    """Read the current validation rules YAML."""
    path = Path(run_dir) / f"{uc_name}_validation-rules.yaml"
    if path.exists():
        return path.read_text(encoding="utf-8")[:5000]
    return "Validation rules file not found"


def read_simulator_config(run_dir: str, node_name: str = "target_node") -> str:
    """Read the simulator config YAML for a node."""
    sim_dir = Path(run_dir) / "simulator" / "config"
    if sim_dir.exists():
        configs = list(sim_dir.glob("*.yaml"))
        if configs:
            return configs[0].read_text(encoding="utf-8")[:5000]
    return "Simulator config not found"


def classify_failure_type(failures: list[dict]) -> dict:
    """Classify execution failures into workflow vs simulator categories."""
    WORKFLOW_PATTERNS = [
        "cannot create property", "mapping values are not allowed",
        "patternsyntaxexception", "named capturing group",
        "node not found: null", "unable to find property",
    ]
    SIMULATOR_PATTERNS = [
        "connection refused", "ssh login failed", "command not found",
        "prompt not reached", "failed to start mockwire",
    ]

    workflow_issues, simulator_issues = [], []

    for f in failures:
        text = json.dumps(f, default=str).lower()
        is_wf = any(p in text for p in WORKFLOW_PATTERNS)
        is_sim = any(p in text for p in SIMULATOR_PATTERNS)

        if is_wf:
            workflow_issues.append(f.get("command", "unknown"))
        elif is_sim:
            simulator_issues.append(f.get("command", "unknown"))
        elif f.get("success") and f.get("validation_status") == "failed":
            workflow_issues.append(f.get("command", "unknown"))
        else:
            simulator_issues.append(f.get("command", "unknown"))

    classification = "simulator" if len(simulator_issues) > len(workflow_issues) else "workflow"
    return {
        "classification": classification,
        "workflow_issues": workflow_issues,
        "simulator_issues": simulator_issues,
        "retry_target": 8 if classification == "simulator" else 6,
    }
