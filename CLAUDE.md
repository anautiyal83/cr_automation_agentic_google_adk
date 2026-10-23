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
- **File**: `src/nodes/requirements_agent.py`
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
pipeline_actions      — cancel/rerun signals
reviewer_feedback     — retry feedback from reviewers
milestone_events      — asyncio.Event per milestone step
milestone_decisions   — user decision per milestone step
milestone_waiting     — milestone pause state + questions
requirements_store    — requirements package (RequirementsGatherer → step nodes)
_previous_answers     — cached questionnaire answers by UC name (for re-runs)
```

## Key Files
```
src/orchestrator/pipeline_agent.py  — PipelineAgent with state-machine loop + backtracking + _MULTI_STEP_AGENTS
src/orchestrator/runner.py          — PipelineRunner with DB persistence + EventBus + milestones
src/nodes/requirements_agent.py     — RequirementsGatherer (replaces Steps 1+2) + _previous_answers cache
src/nodes/step_nodes.py             — 8 BaseAgent step wrappers (Steps 3-10, call agents via cr_import)
src/nodes/milestone_nodes.py        — 7 milestone pause agents (asyncio.Event based)
src/nodes/router_nodes.py           — 3 failure router agents (classify + set _retry_target)
src/nodes/reviewer_agents.py        — 7 reviewer agents (3 QA + 4 milestone reviewers)
src/nodes/tools.py                  — Shared tools (YAML validation, report reading, classification)
src/nodes/cr_import.py              — Cross-project import bridge to cr_automation-agentic
src/core/                           — models.py, events.py, config.py, retry.py
src/storage/                        — db.py, artifact_store.py
src/ui/app.py                       — NiceGUI entry point + CSS + layout
src/ui/pages/progress_section.py    — Pipeline tracker + pre-built questionnaire dialog + milestone restore
src/ui/pages/                       — upload, progress, settings, history, output_viewer, cleanup, usage
```

## Cross-Project Imports
Agents from `D:/my_workspace/cr_automation-agentic/src/agents/` are loaded via `cr_import.py`
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
