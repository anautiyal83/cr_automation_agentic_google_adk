"""Unit test CLI for individual skills and pipeline components.

Usage:
    # List available runs
    python test_step.py --list-runs

    # Show step status for a run
    python test_step.py --status --run-id <run_id>

    # Test a skill directly
    python test_step.py --skill requirement_gathering --run-id <run_id>
    python test_step.py --skill ciq_validation_json_gen --run-id <run_id>
    python test_step.py --skill network_config_gen --run-id <run_id>

    # Validate YAML syntax
    python test_step.py --validate <yaml_path>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from core.logging_config import setup_logging
setup_logging()

logger = logging.getLogger(__name__)


async def _ensure_api_key():
    """Load API key from DB settings / config."""
    from storage.db import init_database

    try:
        db = await init_database("data/pipeline.db")
        for key_name in ("api_key", "openrouter_api_key"):
            rows = await db.execute(f"SELECT value FROM settings WHERE key=?", (key_name,))
            if rows and rows[0]["value"]:
                os.environ["OPENROUTER_API_KEY"] = rows[0]["value"]
                logger.info("API key loaded from DB settings.%s", key_name)
                break
    except Exception:
        pass

    config = {}
    try:
        from core.config import ConfigManager
        mgr = ConfigManager()
        cfg = await mgr.get_config()
        config = cfg.model_dump()
        if cfg.openrouter_api_key and not os.environ.get("OPENROUTER_API_KEY"):
            os.environ["OPENROUTER_API_KEY"] = cfg.openrouter_api_key
    except Exception:
        pass

    return config


async def list_runs():
    """List recent pipeline runs."""
    from storage.db import init_database
    db = await init_database("data/pipeline.db")
    rows = await db.execute(
        "SELECT r.id, r.status, r.current_step, r.started_at, d.uc_name "
        "FROM pipeline_runs r "
        "LEFT JOIN uc_document_sets d ON r.document_set_id = d.id "
        "ORDER BY r.started_at DESC LIMIT 10", ()
    )
    print(f"\n{'Run ID':<40} {'Status':<12} {'Step':<5} {'UC Name':<40} {'Started'}")
    print("-" * 120)
    for r in rows:
        print(f"{r['id']:<40} {(r.get('status') or '?'):<12} {r.get('current_step',0):<5} "
              f"{(r.get('uc_name') or '?'):<40} {(r.get('started_at') or '?')[:19]}")


async def show_status(run_id: str):
    """Show step status for a run."""
    run_dir = Path("data/runs") / run_id
    if not run_dir.exists():
        print(f"Run dir not found: {run_dir}")
        return

    print(f"\nRun: {run_id}")
    print(f"Dir: {run_dir}\n")

    for step in range(1, 11):
        err_file = run_dir / f"step_{step}_error.json"
        status = "FAILED" if err_file.exists() else "?"

        # Check for artifacts
        artifacts = list(run_dir.glob(f"*step{step}*")) + list(run_dir.glob(f"*.yaml"))
        if not err_file.exists() and artifacts:
            status = "passed"

        line = f"  Step {step:2d}: {status}"
        if err_file.exists():
            err = json.loads(err_file.read_text())
            msg = str(err.get("error", ""))[:80]
            line += f"  | {msg}"
        print(line)

    # Show artifacts
    print("\nArtifacts:")
    for f in sorted(run_dir.iterdir()):
        if f.is_file() and f.suffix in (".yaml", ".html", ".json", ".xlsx", ".txt"):
            print(f"  {f.name} ({f.stat().st_size:,} bytes)")


async def validate_yaml(yaml_path: str):
    """Validate a YAML file syntax."""
    from tools.yaml_tools import validate_yaml_syntax
    content = Path(yaml_path).read_text(encoding="utf-8")
    result = validate_yaml_syntax(content)
    if result["valid"]:
        print(f"VALID: {yaml_path}")
    else:
        print(f"INVALID: {yaml_path}")
        print(f"  Error: {result['error']}")


async def test_skill(skill_name: str, run_id: str):
    """Test a skill using data from a previous run."""
    config = await _ensure_api_key()
    from shared.llm import get_llm_fn

    run_dir = Path("data/runs") / run_id
    if not run_dir.exists():
        print(f"Run dir not found: {run_dir}")
        return

    # Load requirements
    req_file = run_dir / "requirements.json"
    requirements = {}
    if req_file.exists():
        requirements = json.loads(req_file.read_text(encoding="utf-8"))

    uc_name = "UC"
    for f in run_dir.glob("*.yaml"):
        if "validation" not in f.name and "json-output" not in f.name and "simulator" not in f.name:
            uc_name = f.stem
            break

    llm_fn = get_llm_fn(config)
    print(f"\nTesting skill: {skill_name}")
    print(f"Run dir: {run_dir}")
    print(f"UC: {uc_name}")
    print()

    import time
    t0 = time.monotonic()

    if skill_name == "ciq_validation_json_gen":
        from skills.ciq_validation_json_gen import CiqValidationJsonGenSkill, SkillInput
        skill = CiqValidationJsonGenSkill(llm_fn=llm_fn)
        output = await skill.generate(SkillInput(
            uc_name=uc_name,
            requirements=requirements,
            generation_hints=requirements.get("generation_hints", {}),
        ))
        elapsed = time.monotonic() - t0
        print(f"Status: {output.status} ({elapsed:.1f}s)")
        print(f"Validation rules: {len(output.validation_rules_yaml)} chars, {len(output.validation_errors)} errors")
        print(f"JSON output: {len(output.json_output_yaml)} chars, {len(output.json_output_errors)} errors")

    elif skill_name == "network_config_gen":
        from skills.network_config_gen import NetworkConfigGenSkill, SkillInput
        skill = NetworkConfigGenSkill(llm_fn=llm_fn)
        output = await skill.generate(SkillInput(
            uc_name=uc_name,
            requirements=requirements,
            generation_hints=requirements.get("generation_hints", {}),
        ))
        elapsed = time.monotonic() - t0
        print(f"Status: {output.status} ({elapsed:.1f}s)")
        print(f"Workflow: {len(output.workflow_yaml)} chars, {len(output.validation_errors)} errors")

    else:
        print(f"Unknown skill: {skill_name}")
        print("Available: ciq_validation_json_gen, network_config_gen")


def main():
    parser = argparse.ArgumentParser(description="NetConfig Pipeline — skill test CLI")
    parser.add_argument("--list-runs", action="store_true", help="List recent runs")
    parser.add_argument("--status", action="store_true", help="Show step status for a run")
    parser.add_argument("--run-id", type=str, help="Run ID")
    parser.add_argument("--skill", type=str, help="Skill to test")
    parser.add_argument("--validate", type=str, help="Validate YAML file")

    args = parser.parse_args()

    if args.list_runs:
        asyncio.run(list_runs())
    elif args.status and args.run_id:
        asyncio.run(show_status(args.run_id))
    elif args.skill and args.run_id:
        asyncio.run(test_skill(args.skill, args.run_id))
    elif args.validate:
        asyncio.run(validate_yaml(args.validate))
    else:
        parser.print_help()
        print("\nExamples:")
        print("  python test_step.py --list-runs")
        print("  python test_step.py --status --run-id <id>")
        print("  python test_step.py --skill ciq_validation_json_gen --run-id <id>")
        print("  python test_step.py --validate data/runs/<id>/UC_validation-rules.yaml")


if __name__ == "__main__":
    main()
