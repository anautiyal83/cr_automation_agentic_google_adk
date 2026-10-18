# CLAUDE.md — CR Automation Pipeline (Google ADK)

## Project Overview
- **Type**: Python agentic pipeline for telecom UC automation using Google ADK
- **Orchestrator**: Custom PipelineAgent(BaseAgent) with backtracking retry loops
- **LLM**: LiteLlm/OpenRouter (default: Gemini 2.5 Flash)
- **UI**: NiceGUI (sidebar 140px, content area, single-row step tracker)
- **Storage**: SQLite for metadata + file system for artifacts
- **Port**: 8000, start via `start.ps1`

## Architecture — 23 Agents

### Pipeline Steps (10)
| Step | Agent | Type | Description |
|------|-------|------|-------------|
| 1 | DocParserNode | BaseAgent | Parse CIQ/MOP documents |
| 2 | UCAnalyzerNode | BaseAgent | Enrich IR via LLM |
| 3 | ValidationRulesNode | BaseAgent | Generate validation-rules.yaml via LLM |
| 4 | JsonTemplateNode | BaseAgent | Generate json-output.yaml via LLM |
| 5 | CiqQANode | BaseAgent | QA cycle via ciq-processor JAR |
| 6 | WorkflowGenNode | BaseAgent | Generate workflow.yaml via LLM |
| 7 | MopTestNode | BaseAgent | Test MOP generation via JAR |
| 8 | SimulatorSetupNode | BaseAgent | Generate MockWire configs via LLM |
| 9 | ExecutionTestNode | BaseAgent | Execute workflow via JAR + MockWire |
| 10 | FinalReportNode | BaseAgent | Generate HTML + Excel reports |

### Peer Reviewer Agents (3 LlmAgents)
| Reviewer | Triggered By | Analyzes | Routes To |
|----------|-------------|----------|-----------|
| qa_validation_reviewer | Step 5 fail | Test case failures | Step 3 |
| qa_mop_reviewer | Step 7 fail | JAR parse errors | Step 6 |
| qa_execution_reviewer | Step 9 fail | Execution failures | Step 6 or 8 |

### Milestone Nodes (7) + Router Nodes (3)
- Milestones at Steps 3,4,5,6,7,8,9 — pause for user review
- Router5: Step 5 fail → retry Step 3
- Router7: Step 7 fail → retry Step 6
- Router9: Step 9 fail → retry Step 6 (workflow) or Step 8 (simulator)

## Feedback Loops (Backtracking)
```
Step 5 fail → QA Val Reviewer → Router5 → _retry_target=3 → PipelineAgent jumps to Step 3
Step 7 fail → QA MOP Reviewer → Router7 → _retry_target=6 → PipelineAgent jumps to Step 6
Step 9 fail → QA Exec Reviewer → Router9 → _retry_target=6 or 8 → PipelineAgent jumps back
```

## Key Files
```
src/orchestrator/pipeline_agent.py  — PipelineAgent with state-machine loop + backtracking
src/orchestrator/runner.py          — PipelineRunner with DB persistence + EventBus + milestones
src/nodes/step_nodes.py             — 10 BaseAgent step wrappers (call existing agents via cr_import)
src/nodes/milestone_nodes.py        — 7 milestone pause agents (asyncio.Event based)
src/nodes/router_nodes.py           — 3 failure router agents (classify + set _retry_target)
src/nodes/reviewer_agents.py        — 3 LlmAgent reviewers with FunctionTools
src/nodes/tools.py                  — Shared tools (YAML validation, report reading, classification)
src/nodes/cr_import.py              — Cross-project import bridge to cr_automation-agentic
src/core/                           — models.py, events.py, config.py, retry.py (copied from reference)
src/storage/                        — db.py, artifact_store.py (copied from reference)
src/ui/app.py                       — NiceGUI entry point + CSS + layout
src/ui/pages/                       — upload, progress, settings, history, output_viewer, cleanup
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
