# Contract: PipelineRunner API

The PipelineRunner is the primary interface between the UI and the ADK pipeline.

## Methods

### start(document_set: dict) → str
Start a new pipeline run. Returns run_id.

**Input**:
```json
{
  "uc_name": "DSR_1058_IFC_CAPABILITY_MODIFICATION",
  "ciq_file_path": "/path/to/ciq.xlsx",
  "mop_file_path": "/path/to/mop.pdf",
  "constraints_file_path": "/path/to/constraints.txt"
}
```

**Output**: `"f6f8784e-8a8f-4ed7-a038-a5a39c450e4f"` (run_id)

### get_status(run_id: str) → dict
Get current pipeline status.

**Output**:
```json
{
  "run_id": "f6f8...",
  "status": "paused_milestone",
  "current_step": 3,
  "milestone_step": 3,
  "milestone_name": "Validation Rules"
}
```

### respond_milestone(run_id: str, step_num: int, decision: str)
Respond to a milestone pause.

**decision**: `"continue"` | `"rerun"` | `"cancel"`

### resume_run(run_id: str, from_step: int) → str
Resume a previously paused/failed run from a specific step.

### cancel_run(run_id: str)
Cancel a running pipeline.

### get_state(run_id: str) → dict
Get full pipeline state (for debugging/advanced UI).

## Events (published to EventBus)

| Event Type | Payload |
|-----------|---------|
| step_started | {run_id, step_number, step_name} |
| step_completed | {run_id, step_number, status, duration} |
| milestone_reached | {run_id, step_number, milestone_name, artifact_file, test_status} |
| retry_started | {run_id, step_number, attempt, error} |
| run_completed | {run_id, duration} |
| run_failed | {run_id, error} |
| run_cancelled | {run_id} |
