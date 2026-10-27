# NetConfig — Network Configuration Automation (Google ADK)

Agentic pipeline for telecom network activity automation using Google ADK (Agent Development Kit). Parses CIQ/MOP documents, gathers requirements via interactive questionnaire, generates YAML configs via LLM, validates via Java JARs, tests against MockWire simulator, with automatic retry loops on failures.

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

## Skills (Reusable Packages)

Each skill is a self-contained package with clear SkillInput/SkillOutput contracts:

| # | Skill | Lines | Description |
|---|-------|-------|-------------|
| 1 | `requirement_gathering` | 2,644 | Parse CIQ/MOP/SSH/XML, LLM analysis, interactive questionnaire, compile requirements |
| 2 | `ciq_validation_json_gen` | 995 | LLM generates validation-rules.yaml + json-output.yaml with autofix + regenerate() |
| 3 | `ciq_validation_json_test` | 1,087 | QA engine: LLM test cases, JAR execution, professional HTML QA report |
| 4 | `network_config_gen` | 759 | LLM generates workflow YAML with ${...} runtime placeholders + autofix |
| 5 | `network_simulator_setup` | 1,131 | Config gen + command-response mapping + start MockWire + sanity checks |
| 6 | `network_config_test` | 1,160 | MOP test + execution test + failure classification + HTML QA report |

### Skill Data Flow

```
requirement_gathering
    ↓ requirements.json (nodes, commands, phases, CIQ structure)
ciq_validation_json_gen
    ↓ validation-rules.yaml + json-output.yaml
ciq_validation_json_test ←→ ciq_validation_json_gen.regenerate()
    ↓ test results feed back for correction
network_config_gen
    ↓ workflow.yaml (with ${nodeGroups}, ${configData}, ${register} placeholders)
network_simulator_setup
    ↓ simulator config + running MockWire process + sanity verified
network_config_test ←→ network_config_gen.regenerate() / network_simulator_setup.regenerate()
    ↓ MOP test + execution test results, failure classification
```

## Pipeline Steps

| # | Step | Skill | Type | Description |
|---|------|-------|------|-------------|
| 1+2 | Requirements Gathering | `requirement_gathering` | LLM + HIL | Parse docs, analyze UC, interactive questionnaire, compile requirements |
| 3 | CIQ Config Generation | `ciq_validation_json_gen` | LLM | Generate validation-rules.yaml |
| 4 | Generate CIQ JSON Config | `ciq_validation_json_gen` | LLM | Generate json-output.yaml |
| 5 | QA-Validation | `ciq_validation_json_test` | LLM + JAR | Generate test cases, run ciq-processor, QA report |
| 6 | Generate Execution Config | `network_config_gen` | LLM | Generate workflow.yaml with ${...} placeholders |
| 7 | QA-MOP | `network_config_test` | LLM + JAR | MOP generation test |
| 8 | Simulator Setup | `network_simulator_setup` | LLM + JAR | Config gen → start MockWire → sanity checks |
| 9 | QA-Execution | `network_config_test` | LLM + JAR | Execute workflow against simulator, QA report |
| 10 | Final Report | — | Deterministic | HTML + Excel reports |

## Project Structure

```
src/
  skills/                            # 6 reusable skill packages (46 files, 7,776 lines)
    requirement_gathering/           # Parse docs, LLM analysis, questionnaire, compile
    ciq_validation_json_gen/         # LLM generates validation + JSON output YAMLs
    ciq_validation_json_test/        # QA engine: test cases, JAR execution, QA report
    network_config_gen/              # LLM generates workflow YAML with ${...} placeholders
    network_simulator_setup/         # Config gen, start MockWire, sanity checks
    network_config_test/             # MOP test, execution test, failure classification

  agents/                            # Pipeline agent definitions
    requirements_agent.py            # RequirementsGathererNode (delegates to skill)
    step_agents.py                   # Step 3-10 agent wrappers
    milestone_agents.py              # Milestone pause agents
    reviewer_agents.py               # QA + milestone reviewer agents
    router_agents.py                 # Failure routing agents

  tools/                             # Shared tools
    yaml_tools.py                    # YAML validation, file I/O
    report_tools.py                  # Report reading for reviewers
    ciq_tools.py                     # CIQ structure reading, failure classification

  shared/                            # Module-level shared state
    state.py                         # Shared dicts (milestone events, pipeline actions)
    cr_bridge.py                     # Cross-project import bridge

  orchestrator/                      # Pipeline orchestration
    pipeline_agent.py                # State-machine loop with backtracking
    runner.py                        # PipelineRunner with DB + EventBus

  core/                              # Infrastructure
    config.py, events.py, logging_config.py, retry.py

  models/                            # Domain models
    domain.py, validation.py

  storage/                           # SQLite DB + artifact store
  ui/                                # NiceGUI frontend

schemas/                             # Authoritative YAML schemas (consumed by JARs)
knowledge/                           # OKF-ready knowledge docs
config/                              # Pipeline configuration
data/                                # Runtime (runs, uploads, DB)
lib/                                 # JARs + MockWire
```

## Requirements

- Python 3.11+
- Java 8+ (for JAR modules)
- `google-adk>=2.11.0`, `litellm`, `nicegui>=2.0`
- OpenRouter API key
