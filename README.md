# CR Automation Pipeline — Google ADK

Telecom UC automation pipeline using Google ADK (Agent Development Kit) for orchestration. Generates YAML configs via LLM agents, validates via Java JARs, tests against MockWire simulator, with automatic retry loops on failures.

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
┌──────────────────────── PipelineAgent (23 agents) ────────────────────────┐
│                                                                            │
│  [1] Parser → [2] Analyzer → [3] Rules → ⏸ → [4] JSON → ⏸               │
│                                                                            │
│  → [5] CIQ QA → ⏸ → [R1 Reviewer] → [→ Router: retry 3?]               │
│                                                                            │
│  → [6] Workflow → ⏸ → [7] MOP QA → ⏸ → [R2 Reviewer] → [→ Router: 6?] │
│                                                                            │
│  → [8] Simulator → ⏸ → [9] Exec QA → ⏸ → [R3 Reviewer] → [→ Router]   │
│                                                                            │
│  → [10] Report                                                             │
└────────────────────────────────────────────────────────────────────────────┘

Retry loops:  Step 5 fail → Step 3  |  Step 7 fail → Step 6  |  Step 9 fail → Step 6 or 8
```

## Pipeline Steps

| # | Step | Type | Description |
|---|------|------|-------------|
| 1 | DOC Parser | Deterministic | Parse CIQ Excel + MOP documents |
| 2 | DOC Analyzer | LLM | Enrich intermediate representation |
| 3 | CIQ Config Generation | LLM | Generate validation-rules.yaml |
| 4 | Generate CIQ JSON Config | LLM | Generate json-output.yaml |
| 5 | QA-Validation | JAR | Test rules via ciq-processor |
| 6 | Generate Execution Config | LLM | Generate workflow.yaml |
| 7 | QA-MOP | JAR | Test MOP generation |
| 8 | Simulator Setup | LLM | Generate MockWire configs |
| 9 | QA-Execution | JAR+MockWire | Execute workflow against simulator |
| 10 | Final Report | Deterministic | HTML + Excel reports |

## Peer Reviewer Agents

When a test step fails, a reviewer LlmAgent analyzes the failure and generates fix instructions:

- **QA Validation Reviewer**: Step 5 failures → fix instructions for Step 3
- **QA MOP Reviewer**: Step 7 failures → fix instructions for Step 6
- **QA Execution Reviewer**: Step 9 failures → classifies as workflow (→Step 6) or simulator (→Step 8)

## Project Structure

```
├── main.py                    CLI entry point
├── start.ps1                  Server launcher
├── config/pipeline_config.yaml
├── lib/                       JARs + templates + MockWire
├── schemas/                   YAML validation schemas
├── src/
│   ├── orchestrator/          PipelineAgent + PipelineRunner
│   ├── nodes/                 Step agents, milestones, routers, reviewers
│   ├── core/                  Models, events, config, retry
│   ├── storage/               SQLite DB + artifact store
│   └── ui/                    NiceGUI web interface
├── data/                      Runtime (runs, uploads, DB)
└── specs/                     Feature specifications
```

## Requirements

- Python 3.11+
- Java 8+ (for JAR modules)
- `google-adk>=2.11.0`, `litellm`, `nicegui>=2.0`
- OpenRouter API key
