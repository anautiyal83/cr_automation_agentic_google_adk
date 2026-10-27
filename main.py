"""NetConfig Pipeline — Google ADK Orchestrator.

Usage:
    python main.py --ciq <ciq_file> --uc-name <uc_name> [--model <model>] [--api-key <key>]

Or as a library:
    from src.orchestrator.runner import PipelineRunner
    runner = PipelineRunner(config={...})
    run_id = await runner.start(document_set={...})
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("src").setLevel(logging.DEBUG)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Ensure project root is in path
if str(Path(__file__).parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent))

from src.orchestrator.runner import PipelineRunner


def on_event(event_type: str, payload: dict):
    """Print pipeline events to console."""
    print(f"\n{'='*60}")
    print(f"  EVENT: {event_type}")
    for k, v in payload.items():
        print(f"  {k}: {v}")
    print(f"{'='*60}\n")


async def run_pipeline(args):
    config = {
        "default_model": args.model,
        "openrouter_api_key": args.api_key or os.environ.get("OPENROUTER_API_KEY", ""),
        "ciq_processor_jar": args.ciq_jar or "./lib/ciq-processor-1.0.3-cli.jar",
        "cli_automation_jar": args.cli_jar or "./lib/cli-automation-standalone.jar",
        "execution_report_jar": args.report_jar or "./lib/execution-report-generator-1.0.2-cli.jar",
        "output_directory": args.output_dir or "./data/runs",
        "test_retry_limit": 3,
        "infra_retry_limit": 3,
    }

    runner = PipelineRunner(config=config, on_event=on_event)

    document_set = {
        "uc_name": args.uc_name,
        "ciq_file_path": str(Path(args.ciq).resolve()),
    }
    if args.mop:
        document_set["mop_file_path"] = str(Path(args.mop).resolve())
    if args.constraints:
        document_set["constraints_file_path"] = str(Path(args.constraints).resolve())

    print(f"\nStarting pipeline for UC: {args.uc_name}")
    print(f"CIQ: {args.ciq}")
    print(f"Model: {args.model}")
    print()

    run_id = await runner.start(document_set)
    print(f"Run ID: {run_id}")

    # Poll for milestones and auto-continue
    while True:
        await asyncio.sleep(2)
        status = runner.get_status(run_id)

        if status["status"] == "paused_milestone":
            step = status.get("milestone_step", 0)
            state = runner.get_state(run_id)
            step_status = state.get(f"step_{step}_status", "passed")

            if step_status == "failed":
                print(f"\n>>> Step {step} FAILED — auto-rerunning...")
                runner.respond_milestone(run_id, step, "rerun")
            else:
                print(f"\n>>> Step {step} passed — auto-continuing...")
                runner.respond_milestone(run_id, step, "continue")

        elif status["status"] in ("completed", "failed", "cancelled"):
            print(f"\nPipeline finished: {status['status']}")
            break

    # Print final state summary
    state = runner.get_state(run_id)
    print("\n" + "=" * 60)
    print("PIPELINE RESULTS")
    print("=" * 60)
    for sr in state.get("step_results", []):
        icon = "✓" if sr["status"] == "passed" else "✗"
        print(f"  {icon} Step {sr['step_number']}: {sr['step_name']} — {sr['status']}")
        if sr.get("error_message"):
            print(f"    Error: {sr['error_message'][:100]}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="NetConfig Pipeline (Google ADK)")
    parser.add_argument("--ciq", required=True, help="Path to CIQ Excel file")
    parser.add_argument("--uc-name", required=True, help="UC name (e.g. DSR_1058_IFC_CAPABILITY_MODIFICATION)")
    parser.add_argument("--mop", help="Path to MOP document")
    parser.add_argument("--constraints", help="Path to constraints file")
    parser.add_argument("--model", default="openrouter/google/gemini-2.5-flash", help="LLM model")
    parser.add_argument("--api-key", help="OpenRouter API key")
    parser.add_argument("--ciq-jar", help="Path to ciq-processor JAR")
    parser.add_argument("--cli-jar", help="Path to cli-automation JAR")
    parser.add_argument("--report-jar", help="Path to execution-report-generator JAR")
    parser.add_argument("--output-dir", default="./data/runs", help="Output directory")

    args = parser.parse_args()
    asyncio.run(run_pipeline(args))


if __name__ == "__main__":
    main()
