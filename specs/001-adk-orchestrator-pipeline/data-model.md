# Data Model: Google ADK Orchestrator Pipeline

*Revised 2026-11-02 to the as-built 9-agent architecture. See research R6 and R7.*

## Entities

### PipelineRun
Represents a single execution of the 10-step pipeline.

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Unique run identifier |
| document_set_id | UUID | Reference to uploaded documents |
| status | Enum | running, paused_milestone, paused_hil, completed, failed, cancelled |
| current_step | Integer | Currently executing step (1-10) |
| started_at | Datetime | Run start timestamp |
| completed_at | Datetime | Run end timestamp (nullable) |
| duration_seconds | Float | Total duration (nullable) |
| config_snapshot | JSON | Pipeline config at run start |
| error_summary | String | Error message on failure (nullable) |

### UCDocumentSet
Uploaded input documents for a pipeline run.

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Unique identifier |
| uc_name | String | UC name (e.g., DSR_1058_IFC_CAPABILITY_MODIFICATION) |
| ciq_file_path | String | Path to CIQ Excel |
| mop_file_path | String | Path to MOP document (nullable) |
| constraints_file_path | String | Path to constraints file (nullable) |
| command_outputs_path | String | Path to command outputs (nullable; stored as "" — 5f236ee) |
| sample_io_xml_paths | JSON | Sample request/response files (optional) |
| developer_notes | String | Free-text notes for the requirements agent |
| vendor_profile | JSON | Selected vendor / node types / activity (8a97fb0) |
| uploaded_at | Datetime | Upload timestamp |

### StepResult
Outcome of one pipeline step execution.

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Unique identifier |
| run_id | UUID | FK → PipelineRun |
| step_number | Integer | Step 1-10 |
| step_name | String | Human-readable name |
| status | Enum | in_progress, passed, failed, skipped |
| started_at | Datetime | Step start time |
| completed_at | Datetime | Step end time (nullable) |
| error_message | String | Error details (nullable) |
| retry_count | Integer | Number of retries for this step |
| agent_model | String | LLM model used (nullable) |

### QA Feedback (formerly ReviewerAnalysis)
Structured output of a QA skill (`ciq_validation_json_test`, `network_config_test`). The QA agent stores it and sets `_retry_target`; no separate reviewer agent exists.

| Field | Type | Description |
|-------|------|-------------|
| analysis | String | Summary of failures |
| classification | Enum | workflow, simulator, mixed |
| retry_target | Integer | Step number to retry (6 or 8) |
| fix_instructions | List[FixInstruction] | Specific fixes for upstream agent |

### FixInstruction
Individual fix instruction from a reviewer.

| Field | Type | Description |
|-------|------|-------------|
| location | String | Where the issue is (e.g., "phases.preNodeHealthCheck.steps[2].register") |
| problem | String | What's wrong |
| fix | String | How to fix it |
| priority | Enum | critical, high, medium |

### Shared State (as built)
ADK session state is **not** used for control flow (research R6). Shared data lives in:

| Store | Key | Type | Set By | Read By |
|-------|-----|------|--------|---------|
| `shared.state.pipeline_actions` | run_id | `{action, target_step}` | Runner / UI | PipelineAgent |
| `shared.state.milestone_events` | step | `asyncio.Event` | Runner | PipelineAgent, RequirementsGatherer |
| `shared.state.milestone_decisions` | step | `continue\|rerun\|cancel` (or answers JSON for step 0) | Runner | PipelineAgent |
| `shared.state.milestone_waiting` | step | `{waiting, name, status, artifact_file?}` | PipelineAgent | Runner / UI |
| `shared.state.reviewer_feedback` | `run_id:step` | String | Runner / UI | UI only (legacy; agents do not use it) |
| `agents.requirements_agent.requirements_store` | run_id | Requirements package | RequirementsGatherer / cache | All downstream agents |
| Disk: `run_dir/step_{N}_error.json` | — | `{error}` | Each agent on failure | PipelineAgent milestone status |
| Disk: `data/cache/<uc>/` | — | Cached artifacts | PipelineAgent | PipelineAgent (002) |

ADK `ctx.session.state` is still used within a run for: `run_id`, `run_dir`, `uc_name`, `pipeline_config`, `artifacts`, `step_N_status`, `retry_count_N`, `_retry_target`, `pipeline_cancelled`/`pipeline_failed`, QA feedback (`qa_feedback`, `exec_feedback`, `_reviewer_analysis_7`, `_reviewer_analysis_9`), and stored skill/process handles (`_ciq_gen_skill`, `_network_config_gen_skill`, `_simulator_setup_skill`, `_simulator_process`). These are read within the same PipelineAgent invocation; cross-agent handles are a known risk (004 S8-08).

## State Transitions

### PipelineRun Status
```
running → paused_milestone → running (continue)
running → paused_milestone → running → ... (rerun loop)
running → paused_milestone → cancelled
running → failed (agent exception; retry-limit exhaustion currently continues — OB-4)
running → completed (final report exists on disk — FR-023)
```

### Step Retry Flow (as built)
```
Agent index:  0 req(1,2) → 1 ciq_gen(3,4) ⏸4 → 2 ciq_qa(5) ⏸5 → 3 config_gen(6) → 4 helper_scripts(6) ⏸6
              → 5 mop_qa(7) ⏸7 → 6 sim_setup(8) ⏸8 → 7 exec_qa(9) ⏸9 → 8 report(10)

_RETRY_STEP_TO_INDEX:  3→1, 4→1, 6→3, 8→6       MAX_RETRIES = 3 per target

ciq_qa fails   → _retry_target=3 → ciq_gen.regenerate(feedback)
mop_qa fails   → _retry_target=6 → config_gen.regenerate(feedback) → helper_scripts
exec_qa fails  → classification=workflow  → _retry_target=6
               → classification=simulator → _retry_target=8 → sim_setup.regenerate(feedback)
⏸ Re-run with no _retry_target → re-run the same agent
```
*Original flow with Router5/7/9 and reviewer agents superseded by bd35015.*
