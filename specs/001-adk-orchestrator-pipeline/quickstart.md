# Quickstart Validation Guide

## Prerequisites

- Python 3.11+
- `google-adk>=2.11.0`, `litellm`, `nicegui>=2.0`, `ruamel.yaml`, `openpyxl`, `xlsxwriter`
- Java 8+ (for ciq-processor, cli-automation, MockWire JARs)
- OpenRouter API key
- CIQ Excel test file (e.g., `1058_IFCCapabilityModification_CIQ_v3.xlsx`)

## Setup

```bash
cd D:/my_workspace/cr_automation_agentic_google_adk
pip install -r requirements.txt
```

## Validation Scenarios

### V1: Pipeline Agent Backtracking (Unit)

Verify the custom PipelineAgent supports conditional routing:

```bash
python -m pytest tests/test_pipeline_agent.py -v
```

**Expected**: Tests pass for sequential execution, Step 5→3 retry, Step 9→6/8 retry, cancellation, and retry limit exhaustion.

### V2: End-to-End Pipeline (Integration)

Run the full pipeline with a test CIQ:

```bash
python test_run.py
```

**Expected**:
- Steps 1-10 execute in sequence
- Milestones pause and auto-continue
- If failures occur, reviewers analyze and retry automatically
- Final report generated in `data/runs/<run_id>/`

### V3: UI Launch and Interaction

Start the NiceGUI server:

```bash
python -m src.ui.app
# or
./start.ps1
```

**Expected**:
- Open http://localhost:8000
- Dashboard shows with stats
- Upload CIQ → Pipeline starts
- Progress tracker shows all 10 steps
- Milestones show View/Download/Continue/Re-run/Cancel
- Settings save and persist across restart

### V4: Server Restart Resume

1. Start a pipeline run
2. Wait for Step 3 milestone
3. Kill the server (Ctrl+C)
4. Restart the server
5. Navigate to Progress section

**Expected**: Pipeline shows paused at Step 3 milestone, user can Continue.

### V5: Reviewer Agent Analysis

Trigger a Step 9 failure:
1. Manually modify the workflow YAML to have an invalid validation expression
2. Re-run from Step 9

**Expected**: QA Execution Reviewer runs, classifies as "workflow" issue, generates fix instructions JSON, pipeline retries from Step 6 with instructions.
