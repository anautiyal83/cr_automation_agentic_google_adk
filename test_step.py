"""Unit test CLI for individual pipeline steps and components.

Usage:
    # Test a single step using data from a previous run
    python test_step.py --step 3 --run-id <run_id>
    python test_step.py --step 6 --run-id <run_id>

    # Test auto-fix on an existing YAML artifact
    python test_step.py --autofix-validation <yaml_path> --ciq <ciq_path>
    python test_step.py --autofix-workflow <yaml_path>

    # Validate YAML against CIQ
    python test_step.py --validate <yaml_path> --ciq <ciq_path>

    # Test reviewer on a failed step
    python test_step.py --review --step 3 --run-id <run_id>

    # Test MockWire config generation from workflow
    python test_step.py --simulator --run-id <run_id>

    # List available runs
    python test_step.py --list-runs

    # Show step status for a run
    python test_step.py --status --run-id <run_id>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.core.logging_config import setup_logging
setup_logging()

logger = logging.getLogger(__name__)


async def _ensure_api_key():
    """Load API key from DB settings / config, same as the UI."""
    from src.storage.db import init_database
    config_dict = {}

    # First check DB settings table (highest priority — set via UI)
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

    # Load full config
    try:
        from src.core.config import ConfigManager
        mgr = ConfigManager()
        config = await mgr.get_config()
        config_dict = config.model_dump()
        if config.openrouter_api_key and not os.environ.get("OPENROUTER_API_KEY"):
            os.environ["OPENROUTER_API_KEY"] = config.openrouter_api_key
    except Exception:
        pass

    return config_dict


async def list_runs():
    """List recent pipeline runs."""
    from src.storage.db import init_database
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
        fb_file = run_dir / f"step_{step}_reviewer_feedback.json"
        status = "FAILED" if err_file.exists() else "passed" if any(run_dir.glob(f"*step{step}*")) or step <= 2 else "?"

        # Check for artifacts
        artifacts = []
        artifact_map = {
            3: "_validation-rules.yaml", 4: "_json-output.yaml",
            6: ".yaml", 8: "_simulator-config.yaml",
        }
        if step in artifact_map:
            for f in run_dir.glob(f"*{artifact_map[step]}"):
                artifacts.append(f.name)

        line = f"  Step {step:2d}: {status}"
        if err_file.exists():
            err = json.loads(err_file.read_text())
            msg = str(err.get("error", ""))[:80]
            line += f"  | {msg}"
        if artifacts:
            line += f"  | artifacts: {', '.join(artifacts)}"
        if fb_file.exists():
            line += f"  | reviewer feedback available"
        print(line)


async def run_step(step_num: int, run_id: str):
    """Run a single step using state from a previous run."""
    loaded_config = await _ensure_api_key()
    # Clear module cache to pick up latest cr_automation-agentic changes
    from src.nodes.cr_import import clear_cache
    clear_cache()
    from src.nodes.cr_import import get_agent_class, import_cr_module
    from src.nodes.step_nodes import _get_step_data, _save_step_result, _call_existing_agent, _inject_retry_context

    run_dir = Path("data/runs") / run_id
    if not run_dir.exists():
        print(f"Run dir not found: {run_dir}")
        return

    # Build state from existing run artifacts
    state = _build_state_from_run(run_dir, run_id)
    if loaded_config:
        state["pipeline_config"] = loaded_config
    step_data = _get_step_data(state)
    config = state.get("pipeline_config", {})

    # Inject retry context if available
    _inject_retry_context(state, step_num, step_data)

    # Map step to agent class
    agent_map = {
        1: ("src.agents.doc_parser_agent", "DocParserAgent"),
        2: ("src.agents.uc_analyzer_agent", "UCAnalyzerAgent"),
        3: ("src.agents.validation_gen_agent", "ValidationGenAgent"),
        4: ("src.agents.json_template_agent", "JsonTemplateAgent"),
        5: ("src.agents.test_executor", "TestExecutor"),
        6: ("src.agents.workflow_gen_agent", "WorkflowGenAgent"),
        7: ("src.agents.mop_gen_tester", "MopGenTester"),
        8: ("src.agents.simulator_setup", "SimulatorSetupAgent"),
        9: ("src.agents.test_executor", "TestExecutor"),
        10: ("src.agents.report_generator", "ReportGenerator"),
    }

    if step_num not in agent_map:
        print(f"Unknown step: {step_num}")
        return

    mod_path, cls_name = agent_map[step_num]
    AgentClass = get_agent_class(mod_path, cls_name)

    # Special handling for TestExecutor (needs mode)
    if step_num == 5:
        agent = AgentClass(mode="ciq")
    elif step_num == 9:
        agent = AgentClass(mode="exec")
    else:
        agent = AgentClass()

    print(f"\n=== Running Step {step_num}: {cls_name} ===")
    print(f"Run dir: {run_dir}")
    print(f"UC: {state.get('uc_name', '?')}")
    print()

    t0 = time.monotonic()
    try:
        result = await _call_existing_agent(agent, step_data, config)
        elapsed = time.monotonic() - t0
        print(f"\nResult: {result.get('status', '?')} ({elapsed:.1f}s)")

        # Save result
        _save_step_result(state, step_num, result)

        if result.get("status") == "failed":
            error = result.get("error", {})
            print(f"Error: {json.dumps(error, indent=2, default=str)[:1000]}")
        else:
            output_keys = list(result.get("output_data", {}).keys())
            print(f"Output keys: {output_keys}")

        # Show generated artifacts
        for key in ("validation_rules_yaml", "json_template_yaml", "workflow_yaml", "simulator_config_yaml"):
            content = result.get("output_data", {}).get(key, "")
            if content:
                print(f"\n--- {key} ({len(content)} chars) ---")
                print(content[:200] + "..." if len(content) > 200 else content)

    except Exception as e:
        elapsed = time.monotonic() - t0
        print(f"\nException after {elapsed:.1f}s: {e}")
        import traceback
        traceback.print_exc()


async def test_autofix_validation(yaml_path: str, ciq_path: str):
    """Test auto-fix on a validation rules YAML."""
    from src.nodes.cr_import import import_cr_module
    mod = import_cr_module("src.agents.validation_gen_agent")

    yaml_content = Path(yaml_path).read_text(encoding="utf-8")
    print(f"Input: {yaml_path} ({len(yaml_content)} chars)")
    print(f"CIQ: {ciq_path}")

    # Run auto-fix
    print("\n=== Running _autofix_column_names ===")
    fixed = mod._autofix_column_names(yaml_content, ciq_path)

    if fixed != yaml_content:
        print(f"FIXED: {len(fixed)} chars (was {len(yaml_content)})")
        out_path = Path(yaml_path).with_suffix(".fixed.yaml")
        out_path.write_text(fixed, encoding="utf-8")
        print(f"Saved to: {out_path}")
    else:
        print("No changes needed")

    # Run validation
    print("\n=== Running _validate_against_ciq ===")
    mismatches = mod._validate_against_ciq(fixed, ciq_path)
    if mismatches:
        print(f"MISMATCHES ({len(mismatches)}):")
        for m in mismatches:
            print(f"  - {m}")
    else:
        print("PASSED - no mismatches")


async def test_autofix_workflow(yaml_path: str):
    """Test auto-fix on a workflow YAML."""
    from src.nodes.cr_import import import_cr_module
    mod = import_cr_module("src.agents.workflow_gen_agent")

    yaml_content = Path(yaml_path).read_text(encoding="utf-8")
    print(f"Input: {yaml_path} ({len(yaml_content)} chars)")

    # Run auto-fix
    print("\n=== Running _fix_workflow_yaml ===")
    fixed = mod._fix_workflow_yaml(yaml_content)

    if fixed != yaml_content:
        print(f"FIXED: {len(fixed)} chars (was {len(yaml_content)})")
        out_path = Path(yaml_path).with_suffix(".fixed.yaml")
        out_path.write_text(fixed, encoding="utf-8")
        print(f"Saved to: {out_path}")
    else:
        print("No changes needed")

    # Validate YAML syntax
    print("\n=== Validating YAML syntax ===")
    try:
        mod._validate_yaml(fixed)
        print("PASSED - valid YAML")
    except Exception as e:
        print(f"FAILED: {e}")

    # Validate structure
    print("\n=== Validating workflow structure ===")
    errors = mod._validate_workflow_structure(fixed)
    if errors:
        print(f"ERRORS ({len(errors)}):")
        for err in errors:
            print(f"  - {err}")
    else:
        print("PASSED - valid structure")


async def test_reviewer(step_num: int, run_id: str):
    """Test reviewer analysis on a failed step."""
    await _ensure_api_key()
    from src.nodes.milestone_nodes import reviewer_feedback
    from src.nodes.cr_import import import_cr_module

    run_dir = Path("data/runs") / run_id
    if not run_dir.exists():
        print(f"Run dir not found: {run_dir}")
        return

    err_file = run_dir / f"step_{step_num}_error.json"
    if not err_file.exists():
        print(f"No error file for step {step_num}")
        return

    state = _build_state_from_run(run_dir, run_id)

    # Determine reviewer type
    _STEP_CONTEXT = {
        3: {"name": "Validation Rules", "artifact_key": "validation_rules_yaml", "file_pattern": "_validation-rules.yaml"},
        4: {"name": "JSON Output Template", "artifact_key": "json_template_yaml", "file_pattern": "_json-output.yaml"},
        6: {"name": "Execution Workflow", "artifact_key": "workflow_yaml", "file_pattern": ".yaml"},
        8: {"name": "Simulator Config", "artifact_key": "simulator_config_yaml", "file_pattern": "_simulator-config.yaml"},
    }

    uc_name = state.get("uc_name", "UC")
    error_details = err_file.read_text(encoding="utf-8")[:2000]

    # Read artifact
    artifact_content = ""
    if step_num in _STEP_CONTEXT:
        ctx = _STEP_CONTEXT[step_num]
        pattern = ctx["file_pattern"]
        candidate = run_dir / f"{uc_name}{pattern}"
        if candidate.exists():
            artifact_content = candidate.read_text(encoding="utf-8")[:6000]

    if step_num in (5, 7, 9):
        # QA reviewer — read test results
        from src.nodes.reviewer_agents import read_qa_report, read_exec_report, read_mop_report
        if step_num == 5:
            report = read_qa_report(str(run_dir))
            print(f"QA Report: {report.get('total', 0)} TCs, {report.get('passed', 0)} passed, {report.get('failed', 0)} failed")
        elif step_num == 7:
            report = read_mop_report(str(run_dir), uc_name)
            print(f"MOP Report: {json.dumps(report, indent=2)[:500]}")
        elif step_num == 9:
            report = read_exec_report(str(run_dir))
            print(f"Exec Report: {report.get('total', 0)} cmds, {report.get('passed', 0)} passed, {report.get('failed', 0)} failed")

    print(f"\n=== Running Reviewer for Step {step_num} ===")
    print(f"Error: {error_details[:300]}")
    if artifact_content:
        print(f"Artifact: {len(artifact_content)} chars")

    # Call LLM for review
    agents_mod = import_cr_module("src.agents")
    call_llm = agents_mod.call_llm
    config = state.get("pipeline_config", {})

    from src.nodes.reviewer_agents import MILESTONE_REVIEWER_PROMPT
    prompt = MILESTONE_REVIEWER_PROMPT.format(
        step_name=_STEP_CONTEXT.get(step_num, {}).get("name", f"Step {step_num}"),
        step_number=step_num,
        description="",
        error_details=error_details,
        artifact_content=artifact_content or "(no content)",
    )

    t0 = time.monotonic()
    response = await call_llm(prompt, config=config)
    elapsed = time.monotonic() - t0

    print(f"\nReviewer response ({elapsed:.1f}s, {len(response)} chars):")
    print(response[:2000])

    # Save feedback
    reviewer_feedback[f"{run_id}:{step_num}"] = response
    fb_file = run_dir / f"step_{step_num}_reviewer_feedback.json"
    fb_file.write_text(response, encoding="utf-8")
    print(f"\nSaved to: {fb_file}")


async def test_validate(yaml_path: str, ciq_path: str):
    """Validate a YAML file against CIQ."""
    from src.nodes.cr_import import import_cr_module
    mod = import_cr_module("src.agents.validation_gen_agent")

    yaml_content = Path(yaml_path).read_text(encoding="utf-8")
    print(f"Validating: {yaml_path}")
    print(f"Against CIQ: {ciq_path}")

    # YAML syntax
    print("\n=== YAML Syntax ===")
    try:
        mod._validate_yaml(yaml_content)
        print("PASSED")
    except Exception as e:
        print(f"FAILED: {e}")
        return

    # CIQ validation
    print("\n=== CIQ Column/Sheet Validation ===")
    mismatches = mod._validate_against_ciq(yaml_content, ciq_path)
    if mismatches:
        print(f"FAILED ({len(mismatches)} issues):")
        for m in mismatches:
            print(f"  - {m}")
    else:
        print("PASSED")


def _build_state_from_run(run_dir: Path, run_id: str) -> dict:
    """Reconstruct pipeline state from a run directory's artifacts."""
    import yaml as _yaml

    # Find UC name from artifacts
    uc_name = "UC"
    for f in run_dir.glob("*_validation-rules.yaml"):
        uc_name = f.name.replace("_validation-rules.yaml", "")
        break
    for f in run_dir.glob("*.yaml"):
        if "validation" not in f.name and "json-output" not in f.name and "simulator" not in f.name and "manifest" not in f.name and "raw" not in f.name:
            candidate = f.name.replace(".yaml", "")
            if "_" in candidate:
                uc_name = candidate
                break

    # Find CIQ file
    ciq_path = ""
    from src.storage.db import Database
    db = Database("data/pipeline.db")
    import asyncio
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()

    # Read config
    config = {}
    try:
        from src.core.config import ConfigManager
        mgr = ConfigManager()
        cfg = loop.run_until_complete(mgr.get_config())
        config = cfg.model_dump()
    except Exception:
        pass

    # Try to find CIQ from DB
    try:
        rows = loop.run_until_complete(db.execute(
            "SELECT d.* FROM pipeline_runs r JOIN uc_document_sets d ON r.document_set_id = d.id WHERE r.id = ?",
            (run_id,)
        ))
        if rows:
            ciq_path = rows[0].get("ciq_file_path", "")
    except Exception:
        pass

    # Load artifacts
    artifacts = {}
    val_rules = run_dir / f"{uc_name}_validation-rules.yaml"
    if val_rules.exists():
        artifacts["validation_rules_yaml"] = val_rules.read_text(encoding="utf-8")
    json_tmpl = run_dir / f"{uc_name}_json-output.yaml"
    if json_tmpl.exists():
        artifacts["json_template_yaml"] = json_tmpl.read_text(encoding="utf-8")
    workflow = run_dir / f"{uc_name}.yaml"
    if workflow.exists():
        artifacts["workflow_yaml"] = workflow.read_text(encoding="utf-8")
    sim_config = run_dir / f"{uc_name}_simulator-config.yaml"
    if sim_config.exists():
        artifacts["simulator_config_yaml"] = sim_config.read_text(encoding="utf-8")

    # Load step errors for retry context
    state = {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "uc_name": uc_name,
        "ciq_file_path": ciq_path,
        "document_set": {"uc_name": uc_name, "ciq_file_path": ciq_path},
        "pipeline_config": config,
        "artifacts": artifacts,
        "intermediate_repr": {},
        "ciq_data": {},
        "mop_data": {},
        "constraints": {},
        "command_outputs": {},
        "qa_feedback": [],
        "exec_feedback": [],
        "few_shot_examples": [],
        "step_results": [],
        "run_data": {"uc_name": uc_name},
    }

    # Load step errors for retry context
    for step in range(1, 11):
        err_file = run_dir / f"step_{step}_error.json"
        if err_file.exists():
            try:
                state[f"step_{step}_error"] = json.loads(err_file.read_text())
                state[f"retry_count_{step}"] = 1
            except Exception:
                pass

    return state


def main():
    parser = argparse.ArgumentParser(description="Test individual pipeline steps")
    parser.add_argument("--step", type=int, help="Step number to run (1-10)")
    parser.add_argument("--run-id", type=str, help="Run ID to use for state/artifacts")
    parser.add_argument("--list-runs", action="store_true", help="List recent runs")
    parser.add_argument("--status", action="store_true", help="Show step status for a run")
    parser.add_argument("--autofix-validation", type=str, help="Test auto-fix on validation YAML")
    parser.add_argument("--autofix-workflow", type=str, help="Test auto-fix on workflow YAML")
    parser.add_argument("--validate", type=str, help="Validate YAML against CIQ")
    parser.add_argument("--ciq", type=str, help="CIQ Excel file path")
    parser.add_argument("--review", action="store_true", help="Test reviewer on failed step")
    parser.add_argument("--simulator", action="store_true", help="Test simulator config generation")

    args = parser.parse_args()

    if args.list_runs:
        asyncio.run(list_runs())
    elif args.status and args.run_id:
        asyncio.run(show_status(args.run_id))
    elif args.step and args.run_id and not args.review:
        asyncio.run(run_step(args.step, args.run_id))
    elif args.review and args.step and args.run_id:
        asyncio.run(test_reviewer(args.step, args.run_id))
    elif args.autofix_validation and args.ciq:
        asyncio.run(test_autofix_validation(args.autofix_validation, args.ciq))
    elif args.autofix_workflow:
        asyncio.run(test_autofix_workflow(args.autofix_workflow))
    elif args.validate and args.ciq:
        asyncio.run(test_validate(args.validate, args.ciq))
    else:
        parser.print_help()
        print("\nExamples:")
        print("  python test_step.py --list-runs")
        print("  python test_step.py --status --run-id <id>")
        print("  python test_step.py --step 3 --run-id <id>")
        print("  python test_step.py --step 6 --run-id <id>")
        print("  python test_step.py --review --step 3 --run-id <id>")
        print("  python test_step.py --autofix-validation data/runs/<id>/UC_validation-rules.yaml --ciq path/to/ciq.xlsx")
        print("  python test_step.py --autofix-workflow data/runs/<id>/UC.yaml")
        print("  python test_step.py --validate data/runs/<id>/UC_validation-rules.yaml --ciq path/to/ciq.xlsx")


if __name__ == "__main__":
    main()
