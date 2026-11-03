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

    # Run the real step 8 + 9 agents (simulator setup + execution QA) on a copy of a run
    # (data/step_tests/<run>_<time>/; the source run is not modified)
    python test_step.py --agents 8,9 --run-id <run_id>
    python test_step.py --agents 8,9 --run-id <run_id> --refix-workflow   # apply current step 6 autofix first

    # Regenerate step 6 (workflow + helper scripts, real LLM) and test it on the simulator;
    # --transcripts adds node session logs to the real-output samples step 6 checks against
    python test_step.py --agents 6,8,9 --run-id <run_id> --transcripts a.txt b.log

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


_STEP_AGENTS = {
    "6": [("agents.network_config_gen_agent", "NetworkConfigGenAgent"),
          ("agents.helper_script_gen_agent", "HelperScriptGenAgent")],
    # helper scripts only, for the (re-fixed) workflow of the copied run
    "6h": [("agents.helper_script_gen_agent", "HelperScriptGenAgent")],
    "8": [("agents.network_simulator_setup_agent", "NetworkSimulatorSetupAgent")],
    "9": [("agents.network_config_qa_agent", "NetworkConfigQAAgent")],
}


def _command_samples(doc_set: dict, extra_paths: list[str]) -> list[dict]:
    """Real command → output samples, built like requirement gathering does (spec 004 DEP-5)."""
    from agents.requirements_agent import _text_document_paths
    from skills.requirement_gathering.parsers import parse_command_outputs
    paths = [doc_set.get("command_outputs_path") or ""]
    paths += _text_document_paths(doc_set.get("additional_documents")) + list(extra_paths)
    texts = [Path(p).read_text(encoding="utf-8", errors="replace") for p in paths if p and Path(p).exists()]
    return parse_command_outputs("\n\n".join(texts))


async def test_agents(run_id: str, steps: list[str], refix_workflow: bool,
                      transcripts: list[str] | None = None, developer_notes: str = "",
                      source: str = ""):
    """Run the real pipeline agents for steps 6/8/9 through ADK on a copy of a run.

    The source run is never modified: its directory is copied to
    data/step_tests/<run>_<time>/ (simulator/, exec_output/ and the error files of the
    steps being run are left out) and the agents run against the copy with a fresh session.
    With step 6 the workflow and helper scripts are regenerated (real LLM).
    """
    import importlib
    import shutil
    import time
    from uuid import uuid4

    from google.adk.agents import SequentialAgent
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    from agents.requirements_agent import requirements_store
    from shared.state import stop_simulator
    from storage.db import init_database

    if "9" in steps and "8" not in steps:
        print("Step 9 needs the simulator started by step 8 — use --agents 8,9")
        return

    config = await _ensure_api_key()
    src = Path(source) if source else Path("data/runs") / run_id
    if not src.exists():
        print(f"Run dir not found: {src}")
        return

    db = await init_database("data/pipeline.db")
    rows = await db.execute(
        "SELECT d.uc_name, d.ciq_file_path, d.command_outputs_path, d.additional_documents "
        "FROM pipeline_runs r "
        "JOIN uc_document_sets d ON r.document_set_id = d.id WHERE r.id = ?", (run_id,))
    if not rows:
        print(f"Run {run_id} not found in data/pipeline.db")
        return
    uc_name, ciq_file_path = rows[0]["uc_name"], rows[0]["ciq_file_path"]

    work = Path("data/step_tests") / f"{run_id[:8]}_{time.strftime('%Y%m%d_%H%M%S')}"
    shutil.copytree(src, work, ignore=shutil.ignore_patterns(
        "simulator", "exec_output", *(f"step_{s}_error.json" for s in steps),
        *(["scripts"] if "6" in steps or "6h" in steps else []),
        *(["*_test-cases.json", "*_network-qa-report.html"] if "9" in steps else [])))

    def read(name: str) -> str:
        p = work / name
        return p.read_text(encoding="utf-8") if p.exists() else ""

    workflow_yaml = read(f"{uc_name}.yaml")
    if refix_workflow:
        from skills.network_config_gen.autofix import fix_workflow
        from skills.network_config_gen.validator import validate_workflow
        workflow_yaml = fix_workflow(workflow_yaml)
        (work / f"{uc_name}.yaml").write_text(workflow_yaml, encoding="utf-8")
        problems = validate_workflow(workflow_yaml)
        print(f"Workflow re-fixed with current step 6 autofix ({len(problems)} validator findings)")
        for p in problems:
            print(f"  - {p[:140]}")

    session_id = str(uuid4())
    requirements = json.loads(read("requirements.json") or "{}")
    if "6" in steps:
        doc = requirements.setdefault("doc_extracted", {})
        doc["command_samples"] = _command_samples(dict(rows[0]), transcripts or [])
        print(f"Real-output samples for step 6: {len(doc['command_samples'])} commands")
        if developer_notes:
            requirements["developer_notes"] = developer_notes
    requirements_store[session_id] = requirements
    state = {
        "run_id": session_id,
        "run_dir": str(work),
        "uc_name": uc_name,
        "ciq_file_path": ciq_file_path,
        "pipeline_config": config,
        "artifacts": {
            "workflow_yaml": workflow_yaml,
            "json_template_yaml": read(f"{uc_name}_json-output.yaml"),
            "validation_rules_yaml": read(f"{uc_name}_validation-rules.yaml"),
        },
    }

    sub_agents = []
    for step in steps:
        for module, cls in _STEP_AGENTS[step]:
            sub_agents.append(getattr(importlib.import_module(module), cls)())
    root = SequentialAgent(name="step_test", sub_agents=sub_agents)

    service = InMemorySessionService()
    await service.create_session(app_name="netconfig", user_id="step_test",
                                 session_id=session_id, state=state)
    runner = Runner(agent=root, app_name="netconfig", session_service=service)

    print(f"\nRunning steps {', '.join(steps)} for {uc_name}")
    print(f"Source run: {src}\nWork dir:   {work}\n")
    t0 = time.monotonic()
    try:
        async for _ in runner.run_async(
                user_id="step_test", session_id=session_id,
                new_message=types.Content(role="user", parts=[types.Part(text="run")])):
            pass
    finally:
        await stop_simulator(session_id)
    print(f"Finished in {time.monotonic() - t0:.1f}s\n")
    _print_step_test_summary(work, uc_name, steps)


def _print_step_test_summary(work: Path, uc_name: str, steps: list[str]):
    from collections import Counter

    from skills.network_config_test.result_parser import parse_execution_results, read_simulator_events

    for step in steps:
        err = work / f"step_{step}_error.json"
        status = "FAILED" if err.exists() else "passed"
        print(f"Step {step}: {status}")
        if err.exists():
            data = json.loads(err.read_text(encoding="utf-8"))
            print(f"  error: {str(data.get('error', ''))[:300]}")
            for key in ("classification", "retry_target"):
                if key in data:
                    print(f"  {key}: {data[key]}")
            for key in ("simulator_issues", "workflow_issues", "unmatched_commands"):
                for item in data.get(key, [])[:10]:
                    print(f"  {key[:-1] if key.endswith('s') else key}: {str(item)[:130]}")

    manifest = work / "simulator" / "manifest.json"
    if manifest.exists():
        m = json.loads(manifest.read_text(encoding="utf-8"))
        print("\nSimulator instances:")
        for inst in m.get("instances", []):
            print(f"  {inst['name']:<8} port {inst['port']:<6} status {inst['status']:<8} log {inst['log_file']}")

    if "9" in steps:
        tc_file = work / f"{uc_name}_test-cases.json"
        if tc_file.exists():
            tcs = json.loads(tc_file.read_text(encoding="utf-8"))
            print(f"\nTest cases: {tcs['passed']}/{tcs['total']} passed")
            for tc in tcs["test_cases"]:
                print(f"  {tc['status']:4} {tc['id']} {tc['title']} — {tc['actual']}")
                for chk in tc["checks"]:
                    if not chk["ok"]:
                        print(f"         x {chk['check']}: {chk['detail'][:150]}")
            for note in tcs.get("notes", []):
                print(f"  note: {note}")
        else:
            cmds = parse_execution_results(str(work))
            events = read_simulator_events(str(work))
            print(f"\nExecution: {len(cmds)} commands", dict(Counter(c.validation_status for c in cmds)))
            for c in cmds:
                if not c.success or c.validation_status == "failed":
                    print(f"  FAIL {c.node:5} {c.phase[:22]:22} {c.command[:60]!r}")
            print("Simulator events:", dict(Counter(e.get("source") for e in events)))

    print("\nArtifacts:")
    for name in (f"{uc_name}_simulator-report.html", f"simulator/{uc_name}.yaml",
                 f"{uc_name}_network-qa-report.html", f"{uc_name}_test-cases.json"):
        p = work / name
        if p.exists():
            print(f"  {p}")


def main():
    parser = argparse.ArgumentParser(description="NetConfig Pipeline — skill test CLI")
    parser.add_argument("--list-runs", action="store_true", help="List recent runs")
    parser.add_argument("--status", action="store_true", help="Show step status for a run")
    parser.add_argument("--run-id", type=str, help="Run ID")
    parser.add_argument("--skill", type=str, help="Skill to test")
    parser.add_argument("--agents", type=str,
                        help="Run real pipeline agents on a copy of the run, e.g. 8,9 or 6,8,9")
    parser.add_argument("--refix-workflow", action="store_true",
                        help="With --agents: apply the current step 6 autofix to the run's workflow first")
    parser.add_argument("--transcripts", nargs="*", default=[],
                        help="With --agents 6: extra node session logs for the real-output samples")
    parser.add_argument("--developer-notes", default="",
                        help="With --agents 6: developer notes (as typed in the UI) for step 6")
    parser.add_argument("--source", default="",
                        help="With --agents: copy this dir (e.g. an earlier data/step_tests/<dir>) "
                             "instead of data/runs/<run-id>")
    parser.add_argument("--validate", type=str, help="Validate YAML file")

    args = parser.parse_args()

    if args.list_runs:
        asyncio.run(list_runs())
    elif args.status and args.run_id:
        asyncio.run(show_status(args.run_id))
    elif args.agents and args.run_id:
        steps = [s.strip() for s in args.agents.split(",") if s.strip()]
        unknown = [s for s in steps if s not in _STEP_AGENTS]
        if unknown:
            print(f"Unsupported step(s) {unknown}; available: {', '.join(_STEP_AGENTS)}")
            return
        asyncio.run(test_agents(args.run_id, steps, args.refix_workflow, args.transcripts,
                                args.developer_notes, args.source))
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
