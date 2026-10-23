# CR Automation Pipeline — Google ADK

Telecom UC automation pipeline using Google ADK (Agent Development Kit) for orchestration. Parses CIQ/MOP documents, gathers developer requirements via interactive questionnaire, generates YAML configs via LLM agents, validates via Java JARs, tests against MockWire simulator, with automatic retry loops on failures.

## Quick Start

```powershell
# Install dependencies
pip install -r requirements.txt

# Start the web UI
.\start.ps1
# Open http://localhost:8000

# Or run via CLI
python main.py --ciq path/to/ciq.xlsx --uc-name DSR_1058_IFC_CAPABILITY_MODIFICATION
```

## Architecture

```
┌─────────────────────── PipelineAgent (26 agents) ─────────────────────────┐
│                                                                            │
│  [RequirementsGatherer] ─── Parse → Analyze → Questionnaire (HIL)         │
│          │                    └── Compile → Summary Review (HIL)           │
│          ▼                                                                 │
│  [3] Rules → R → ⏸ → [4] JSON → R → ⏸                                  │
│                                                                            │
│  → [5] CIQ QA → QA-R → ⏸ → [Router: retry 3?]                          │
│                                                                            │
│  → [6] Workflow → R → ⏸ → [7] MOP QA → QA-R → ⏸ → [Router: retry 6?] │
│                                                                            │
│  → [8] Simulator → R → ⏸ → [9] Exec QA → QA-R → ⏸ → [Router: 6/8?]   │
│                                                                            │
│  → [10] Report                                                             │
│                                                                            │
│  Legend: R = Milestone Reviewer, QA-R = QA Reviewer, ⏸ = Milestone Pause  │
└────────────────────────────────────────────────────────────────────────────┘

Retry loops:  Step 5 fail → Step 3  |  Step 7 fail → Step 6  |  Step 9 fail → Step 6 or 8
```

## Pipeline Steps

| # | Step | Type | Description |
|---|------|------|-------------|
| 1+2 | Requirements Gathering | LLM + HIL | Parse docs, analyze UC, interactive questionnaire, compile requirements |
| 3 | CIQ Config Generation | LLM | Generate validation-rules.yaml |
| 4 | Generate CIQ JSON Config | LLM | Generate json-output.yaml |
| 5 | QA-Validation | JAR | Test rules via ciq-processor |
| 6 | Generate Execution Config | LLM | Generate workflow.yaml |
| 7 | QA-MOP | JAR | Test MOP generation |
| 8 | Simulator Setup | LLM | Generate MockWire configs |
| 9 | QA-Execution | JAR+MockWire | Execute workflow against simulator |
| 10 | Final Report | Deterministic | HTML + Excel reports |

## RequirementsGatherer Agent

Replaces the original Steps 1 (DocParser) + 2 (UCAnalyzer) with an intelligent agent that:

1. **Parses** CIQ/MOP/constraint documents
2. **Analyzes** the UC type and identifies missing information
3. **Asks** the developer via interactive questionnaire dialog (up to 3 rounds)
4. **Compiles** a structured requirements package for downstream steps
5. **Generates** an HTML summary document for review
6. **Pauses** for developer review before proceeding

Features:
- Interactive questionnaire with category grouping, importance badges, pre-filled defaults
- Light+dark hybrid themed dialog (dark header/footer, light content area)
- "Other" option on radio/select inputs reveals a free-text field
- Answer caching by UC name — re-runs skip questionnaire if all questions already answered

## Reviewer Agents

### Milestone Reviewers (4)
After each LLM generation step (3, 4, 6, 8), a reviewer analyzes the output and provides feedback if there are issues. Skipped if the step passed.

### QA Reviewers (3)
When a test step fails, a QA reviewer analyzes the failure and generates fix instructions:
- **QA Validation Reviewer**: Step 5 failures → fix instructions for Step 3
- **QA MOP Reviewer**: Step 7 failures → fix instructions for Step 6
- **QA Execution Reviewer**: Step 9 failures → classifies as workflow (→Step 6) or simulator (→Step 8)

## Milestones (Human-in-the-Loop)

The pipeline pauses at 9 milestones for human review:
- **Step 0**: Requirements questionnaire — interactive dialog to answer questions
- **Step 2**: Requirements summary — review compiled requirements before YAML generation
- **Steps 3-9**: Standard review — view artifact, continue, re-run, or cancel

## Project Structure

```
├── main.py                    CLI entry point
├── start.ps1                  Server launcher
├── CLAUDE.md                  Project documentation for AI assistants
├── config/pipeline_config.yaml
├── lib/                       JARs + templates + MockWire
├── schemas/                   YAML validation schemas
├── src/
│   ├── orchestrator/          PipelineAgent + PipelineRunner
│   │   ├── pipeline_agent.py  State-machine loop + backtracking + _MULTI_STEP_AGENTS
│   │   └── runner.py          DB persistence + EventBus + milestone polling
│   ├── nodes/
│   │   ├── requirements_agent.py  RequirementsGatherer (Steps 1+2) + answer caching
│   │   ├── step_nodes.py      8 step agent wrappers (Steps 3-10)
│   │   ├── milestone_nodes.py 7 milestone pause agents
│   │   ├── router_nodes.py    3 failure router agents
│   │   ├── reviewer_agents.py 7 reviewer agents (4 milestone + 3 QA)
│   │   ├── tools.py           Shared tools
│   │   └── cr_import.py       Cross-project import bridge
│   ├── core/                  Models, events, config, retry, logging
│   ├── storage/               SQLite DB + artifact store
│   └── ui/
│       ├── app.py             NiceGUI entry point + CSS + layout
│       └── pages/             Upload, progress, settings, history, output, usage, cleanup
├── data/                      Runtime (runs, uploads, DB)
└── specs/                     Feature specifications
```

## Requirements

- Python 3.11+
- Java 8+ (for JAR modules)
- `google-adk>=2.11.0`, `litellm`, `nicegui>=2.0`
- OpenRouter API key
