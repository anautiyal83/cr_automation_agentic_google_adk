# CLAUDE.md — CR Automation Pipeline (Google ADK)

## Project Overview
- **Type**: Python agentic pipeline for telecom UC automation using Google ADK
- **Orchestrator**: Custom PipelineAgent(BaseAgent) with backtracking retry loops
- **LLM**: LiteLlm/OpenRouter (default: Gemini 2.5 Flash)
- **UI**: NiceGUI (sidebar 140px, content area, single-row step tracker)
- **Storage**: SQLite for metadata + file system for artifacts
- **Port**: 8000, start via `start.ps1`

## Architecture — 26 Agents

### RequirementsGatherer (replaces Steps 1+2)
- **File**: `src/agents/requirements_agent.py`
- **Type**: BaseAgent — gatekeeper that blocks pipeline until requirements are gathered
- **6 phases**: Parse → Analyze → Ask (iterative HIL, max 3 rounds) → Compile → Summary Doc → Review Pause
- **3 requirement categories**: Node & Activity, Commands & Parameters, Orchestration
- **Milestones**: Step 0 = interactive questionnaire dialog, Step 2 = summary review
- **Questionnaire UI**: Light+dark hybrid dialog, pre-built in `_build_ui()`, category cards,
  importance badges, "Other" option on radio/select, pre-filled defaults from previous runs
- **Answer caching**: `_previous_answers` dict keyed by UC name — re-runs skip questionnaire
  if all questions already answered from previous run
- **Outputs**: requirements.json, {uc}_requirements-summary.html, {uc}_questionnaire_round{N}.html
- **Shared state**: `requirements_store` dict (bypasses ADK session state)

### Pipeline Steps (8 remaining)
| Step | Agent | Type | Description |
|------|-------|------|-------------|
| 3 | ValidationRulesNode | BaseAgent | Generate validation-rules.yaml via LLM |
| 4 | JsonTemplateNode | BaseAgent | Generate json-output.yaml via LLM |
| 5 | CiqQANode | BaseAgent | QA cycle via ciq-processor JAR |
| 6 | WorkflowGenNode | BaseAgent | Generate workflow.yaml via LLM |
| 7 | MopTestNode | BaseAgent | Test MOP generation via JAR |
| 8 | SimulatorSetupNode | BaseAgent | Generate MockWire configs via LLM |
| 9 | ExecutionTestNode | BaseAgent | Execute workflow via JAR + MockWire |
| 10 | FinalReportNode | BaseAgent | Generate HTML + Excel reports |

### Milestone Reviewer Agents (7)
| Reviewer | Triggered By | Analyzes |
|----------|-------------|----------|
| milestone_reviewer_3 | Step 3 fail | Validation rules issues |
| milestone_reviewer_4 | Step 4 fail | JSON template issues |
| milestone_reviewer_6 | Step 6 fail | Workflow config issues |
| milestone_reviewer_8 | Step 8 fail | Simulator config issues |
| qa_validation_reviewer | Step 5 fail | Test case failures |
| qa_mop_reviewer | Step 7 fail | JAR parse errors |
| qa_execution_reviewer | Step 9 fail | Execution failures |

### Milestone Nodes (7) + Router Nodes (3)
- Milestones at Steps 0,2,3,4,5,6,7,8,9 — pause for user review
- Step 0: Requirements questionnaire (interactive dialog)
- Step 2: Requirements summary review
- Steps 3-9: Standard milestone review (View Artifact, Continue, Re-run, Cancel)
- Router5: Step 5 fail → retry Step 3
- Router7: Step 7 fail → retry Step 6
- Router9: Step 9 fail → retry Step 6 (workflow) or Step 8 (simulator)

## Pipeline Flow
```
RequirementsGatherer → [Questionnaire HIL] → [Summary Review] →
Step 3 → Reviewer → Milestone → Step 4 → Reviewer → Milestone →
Step 5 → QA Reviewer → Milestone → Router5 →
Step 6 → Reviewer → Milestone → Step 7 → QA Reviewer → Milestone → Router7 →
Step 8 → Reviewer → Milestone → Step 9 → QA Reviewer → Milestone → Router9 →
Step 10
```

## Feedback Loops (Backtracking)
```
Step 5 fail → QA Val Reviewer → Router5 → _retry_target=3 → PipelineAgent jumps to Step 3
Step 7 fail → QA MOP Reviewer → Router7 → _retry_target=6 → PipelineAgent jumps to Step 6
Step 9 fail → QA Exec Reviewer → Router9 → _retry_target=6 or 8 → PipelineAgent jumps back
```

## Shared Module-Level Dicts (ADK session state does NOT work for these)
```
pipeline_actions      — cancel/rerun signals          (src.shared.state)
reviewer_feedback     — retry feedback from reviewers  (src.shared.state)
milestone_events      — asyncio.Event per milestone    (src.shared.state)
milestone_decisions   — user decision per milestone    (src.shared.state)
milestone_waiting     — milestone pause state          (src.shared.state)
requirements_store    — requirements package           (src.agents.requirements_agent)
```

## Project Structure
```
src/
  agents/                          # All agent definitions
    requirements_agent.py          # RequirementsGathererNode (gatekeeper)
    step_agents.py                 # Step 3-10 agent wrappers
    milestone_agents.py            # Milestone pause agents
    reviewer_agents.py             # QA + milestone reviewer agents
    router_agents.py               # Failure routing agents

  tools/                           # Shared tools for agents
    yaml_tools.py                  # validate_yaml_syntax, read/write artifact
    report_tools.py                # read_exec_report, read_mop_report, etc.
    ciq_tools.py                   # read_ciq_structure, classify_failure_type

  shared/                          # Module-level shared state + cross-project bridge
    state.py                       # All module-level shared dicts
    cr_bridge.py                   # Cross-project import bridge (cr_automation-agentic)

  orchestrator/                    # Pipeline orchestration
    pipeline_agent.py              # PipelineAgent with state-machine loop + backtracking
    runner.py                      # PipelineRunner with DB persistence + EventBus

  skills/                          # Reusable skill packages
    telecom_req/                   # TelecomReqSkill (14 files, ~2,600 lines)

  core/                            # Infrastructure
    config.py                      # ConfigManager, PipelineConfiguration
    events.py                      # EventBus, PipelineEvent
    logging_config.py              # setup_logging, get_flow_logger
    retry.py                       # Retry logic

  models/                          # Domain models
    domain.py                      # UCDocumentSet, PipelineRun, StepResult, etc.
    validation.py                  # Document upload validation

  storage/                         # Persistence
    db.py                          # SQLite database
    artifact_store.py              # File system artifact store

  ui/                              # NiceGUI frontend
    app.py                         # Entry point + CSS + layout
    pages/                         # upload, progress, settings, history, etc.

knowledge/                         # OKF-ready knowledge directory (top-level)
  index.md                         # OKF root index
  schemas/                         # Schema reference docs
  domain/                          # Telecom domain knowledge
  framework/                       # CLI Automation framework docs
```

## Cross-Project Imports
Agents from `D:/my_workspace/cr_automation-agentic/src/agents/` are loaded via `src/shared/cr_bridge.py`
using `importlib.util.spec_from_file_location` with namespace isolation to avoid `src` package conflicts.

## Running
```powershell
# UI server
.\start.ps1

# CLI test (auto-continue milestones)
python test_run.py

# CLI with args
python main.py --ciq path/to/ciq.xlsx --uc-name DSR_1058_IFC_CAPABILITY_MODIFICATION
```

## Step Names (UI)
1. DOC Parser, 2. DOC Analyzer, 3. CIQ Config Generation,
4. Generate CIQ JSON Config, 5. QA-Validation, 6. Generate Execution Config,
7. QA-MOP, 8. Simulator Setup, 9. QA-Execution, 10. Final Report

## External Dependencies
- **JARs**: lib/ciq-processor-1.0.3-cli.jar, lib/cli-automation-standalone.jar, lib/execution-report-generator-1.0.2-cli.jar
- **MockWire**: lib/mockwire/mockwire-simulator/mockwire.jar
- **Templates**: lib/templates/*.html
- **Schemas**: schemas/*.yaml
- **Reference agents**: D:/my_workspace/cr_automation-agentic/src/agents/
