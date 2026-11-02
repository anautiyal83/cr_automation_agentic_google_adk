# Quickstart Validation Guide

*Revised 2026-11-02. V1 and V4 cannot pass yet: there are no tests (T046) and no resume (OB-6). V5 is blocked by 004 DEP-2.*

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

**Expected**: Tests pass for sequential execution, Step 5→3 retry, Step 9→6/8 retry, cancellation, and retry limit exhaustion. *(Not available: `tests/` is empty — T046.)*

### V2: End-to-End Pipeline (Integration)

Run the full pipeline with a test CIQ:

```bash
python test_run.py
```

**Expected**:
- Steps 1-10 execute in sequence (9 agents; `PIPELINE START — 9 agents` in `logs/flow.log`)
- Milestones pause and auto-continue
- If failures occur, QA skills set `_retry_target` and the pipeline retries automatically (≤3 per target)
- Final report generated in `data/runs/<run_id>/`

### V3: UI Launch and Interaction

Start the NiceGUI server:

```bash
python ui/app.py
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
2. Wait for Step 4 milestone
3. Kill the server (Ctrl+C)
4. Restart the server
5. Navigate to Progress section

**Expected**: Pipeline shows paused at Step 4 milestone, user can Continue. *(Not implemented — OB-6.)*

### V5: Execution QA Failure Classification

Trigger a Step 9 failure:
1. Manually modify the workflow YAML to have an invalid validation expression
2. Re-run from Step 9

**Expected**: The Exec QA skill (`network_config_test`) classifies the failure as "workflow", sets `_retry_target=6`, and `network_config_gen.regenerate(feedback)` runs. *(Blocked: Step 9 has never executed a command — 004 DEP-2.)*
