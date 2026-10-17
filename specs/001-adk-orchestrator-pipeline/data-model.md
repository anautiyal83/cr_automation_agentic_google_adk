# Data Model: Google ADK Orchestrator Pipeline

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
| command_outputs_path | String | Path to command outputs (nullable) |
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

### ReviewerAnalysis
Structured output from a peer reviewer agent.

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

### SessionState
ADK session state dictionary carrying pipeline context.

| Key | Type | Set By | Read By |
|-----|------|--------|---------|
| run_id | String | Runner | All |
| run_dir | String | Runner | All steps |
| uc_name | String | Runner | All steps |
| pipeline_config | Dict | Runner | All steps |
| intermediate_repr | Dict | Step 1 | Steps 2-6 |
| ciq_data | Dict | Step 1 | Steps 3,4,6 |
| mop_data | Dict | Step 1 | Steps 6,8 |
| artifacts | Dict | Steps 3,4,6,8 | Steps 5,7,9 |
| qa_feedback | List | Step 5/Router5 | Step 3 (retry) |
| exec_feedback | List | Step 9/Router9 | Steps 6,8 (retry) |
| reviewer_instructions | Dict | Reviewers | Steps 3,6,8 (retry) |
| step_N_status | String | Each step | Milestones, Routers |
| milestone_N_waiting | Bool | Milestones | Runner (UI) |
| milestone_N_decision | String | Runner (UI) | Milestones |
| _retry_target | Int | Routers | PipelineAgent |
| retry_count_N | Int | Routers | Routers (limit check) |
| pipeline_cancelled | Bool | Runner | PipelineAgent |
| pipeline_failed | Bool | Routers | PipelineAgent |

## State Transitions

### PipelineRun Status
```
running → paused_milestone → running (continue)
running → paused_milestone → running → ... (rerun loop)
running → paused_milestone → cancelled
running → failed (retry limit exhausted)
running → completed (Step 10 done)
```

### Step Retry Flow
```
Step 3 → Milestone 3 → Step 4 → Milestone 4 → Step 5
  ↑                                               │
  └──── QA Validation Reviewer ← Router5 ←────────┘ (if failed)

Step 6 → Milestone 6 → Step 7 → QA MOP Reviewer → Router7
  ↑                                                    │
  └────────────────────────────────────────────────────┘ (if failed)

Step 6 → ... → Step 8 → Milestone 8 → Step 9 → QA Exec Reviewer → Router9
  ↑                ↑                                                    │
  └────────────────┴────────────────────────────────────────────────────┘
  (workflow issue)  (simulator issue)
```
