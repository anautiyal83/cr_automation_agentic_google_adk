"""Report reading tools for reviewer agents."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


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
