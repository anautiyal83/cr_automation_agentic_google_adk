# Contract: PipelineRunner API

The PipelineRunner (`orchestrator/runner.py`) is the primary interface between the UI and the ADK pipeline.

*Revised 2026-11-02: `resume_run` is not implemented (bugs.md OB-6). Milestone steps are 0 (questionnaire), 2 (requirements summary), 4, 5, 6, 7, 8, 9.*

## Methods

### start(document_set: dict) → str
Start a new pipeline run. Returns run_id.

**Input**:
```json
{
  "uc_name": "DSR_1058_IFC_CAPABILITY_MODIFICATION",
  "ciq_file_path": "/path/to/ciq.xlsx",
  "mop_file_path": "/path/to/mop.pdf",
  "constraints_file_path": "/path/to/constraints.txt",
  "sample_io_xml_paths": ["/path/to/request.xml"],
  "developer_notes": "optional free text",
  "vendor_profile": {"vendor": "...", "node_types": ["PGW", "RDS"], "activity": "..."}
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
  "current_step": 4,
  "milestone_step": 4,
  "milestone_name": "CIQ Validation & JSON Output"
}
```

### respond_milestone(run_id: str, step_num: int, decision: str)
Respond to a milestone pause.

**decision**: `"continue"` | `"rerun"` | `"cancel"`; for step 0 (questionnaire) the decision carries the answers JSON

### ~~resume_run(run_id: str, from_step: int) → str~~ — NOT IMPLEMENTED
Resume a previously paused/failed run from a specific step. History re-run instead starts a **new** run with a copied document set (1d71d9b).

### cancel_run(run_id: str)
Cancel a running pipeline. `start()` also cancels any active run before starting a new one (8a97fb0).

### get_state(run_id: str) → dict
Get full pipeline state (for debugging/advanced UI).

## Events (published to EventBus)

| Event Type | Payload |
|-----------|---------|
| step_started | {run_id, step_number, step_name} |
| step_completed | {run_id, step_number, status, duration} |
| step_failed | {run_id, step_number, status \| error} |
| milestone_reached | {run_id, step_number, milestone, artifact_file, test_status, extra_artifact_files, questions? (step 0)} |
| retry_started | {run_id, step_number, attempt, error} |
| run_completed | {run_id, duration} |
| run_failed | {run_id, error} |
| run_cancelled | {run_id} |
