"""End-to-end pipeline test — runs all 10 steps with auto-continue on milestones."""
import asyncio
import logging
import os
import sys
import sqlite3
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
logging.getLogger("src").setLevel(logging.DEBUG)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("google.adk").setLevel(logging.INFO)

# Add THIS project first, then cr_automation-agentic
sys.path.insert(0, str(Path(__file__).resolve().parent))
CR_AUTO_ROOT = Path("D:/my_workspace/cr_automation-agentic")
sys.path.insert(1, str(CR_AUTO_ROOT))

# Get API key from existing DB
db = sqlite3.connect(str(CR_AUTO_ROOT / "data" / "pipeline.db"))
row = db.execute("SELECT value FROM settings WHERE key = 'api_key'").fetchone()
API_KEY = row[0] if row else os.environ.get("OPENROUTER_API_KEY", "")
db.close()

CIQ_FILE = str(CR_AUTO_ROOT / "data" / "uploads" / "1a5ad4cd_1058_IFCCapabilityModification_CIQ_v3.xlsx")


async def main():
    from src.orchestrator.runner import PipelineRunner, event_bus

    # Subscribe to events for console output
    async def on_event(event):
        print(f"\n>>> [{event.event_type}] Step {event.step_number or '-'}: {event.payload}")

    event_bus.subscribe(on_event)

    runner = PipelineRunner(
        config={
            "default_model": "openrouter/google/gemini-2.5-flash",
            "openrouter_api_key": API_KEY,
            "ciq_processor_jar": str(CR_AUTO_ROOT / "lib" / "ciq-processor-1.0.3-cli.jar"),
            "cli_automation_jar": str(CR_AUTO_ROOT / "lib" / "cli-automation-standalone.jar"),
            "execution_report_jar": str(CR_AUTO_ROOT / "lib" / "execution-report-generator-1.0.2-cli.jar"),
            "output_directory": str(Path("./data/runs")),
            "mop_template": str(CR_AUTO_ROOT / "lib" / "templates" / "mop_summary_report_template_v2.html"),
            "exec_summary_template": str(CR_AUTO_ROOT / "lib" / "templates" / "mop_execution_summary_report_template.html"),
            "exec_failure_template": str(CR_AUTO_ROOT / "lib" / "templates" / "execution_failure_template.html"),
        },
    )

    print(f"Starting pipeline with CIQ: {CIQ_FILE}")
    print(f"API Key: {API_KEY[:8]}...{API_KEY[-4:]}")
    print(f"Pipeline agents: {len(runner._pipeline._agents_list)}")
    print()

    run_id = await runner.start({
        "uc_name": "DSR_1058_IFC_CAPABILITY_MODIFICATION",
        "ciq_file_path": CIQ_FILE,
    })
    print(f"Run ID: {run_id}\n")

    # Monitor with timeout
    for _ in range(600):  # 10 min max
        await asyncio.sleep(1)
        status = runner.get_status(run_id)
        st = status.get("status", "?")

        if st == "paused_milestone":
            ms = status.get("milestone_step", 0)
            state = await runner.get_state(run_id)
            step_status = state.get(f"milestone_{ms}_step_status", "passed")

            if step_status == "failed":
                print(f"\n=== MILESTONE Step {ms} FAILED — auto-rerunning ===")
                runner.respond_milestone(run_id, ms, "rerun")
            else:
                print(f"\n=== MILESTONE Step {ms} passed — auto-continuing ===")
                runner.respond_milestone(run_id, ms, "continue")

        elif st in ("completed", "failed", "cancelled"):
            print(f"\n{'='*60}")
            print(f"Pipeline {st.upper()}")
            print(f"{'='*60}")
            state = await runner.get_state(run_id)
            for sr in state.get("step_results", []):
                icon = "+" if sr["status"] == "passed" else "X"
                err = f" | {sr['error_message'][:100]}" if sr.get("error_message") else ""
                print(f"  [{icon}] Step {sr['step_number']}: {sr['step_name']} — {sr['status']}{err}")
            print(f"{'='*60}")
            break
    else:
        print("\n=== TIMEOUT — pipeline still running ===")


if __name__ == "__main__":
    asyncio.run(main())
