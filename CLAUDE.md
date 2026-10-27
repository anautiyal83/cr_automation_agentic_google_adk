# CLAUDE.md — NetConfig Pipeline (Google ADK)

## Project Overview
- **Type**: Python agentic pipeline for network activity automation using Google ADK
- **Orchestrator**: Custom PipelineAgent(BaseAgent) — 7 agents with built-in milestones
- **LLM**: LiteLlm/OpenRouter via `shared/llm.py` (default: Gemini 2.5 Flash)
- **UI**: NiceGUI (sidebar 140px, content area, single-row step tracker)
- **Storage**: SQLite for metadata + file system for artifacts
- **Port**: 8000, start via `start.ps1`
- **No `src/` wrapper** — all packages at project root

## Architecture — 7 Agents + 7 Skills

### 7 Agents
| # | Agent | Skill | Display Steps |
|---|-------|-------|---------------|
| 1 | `requirements_gatherer` | `requirement_gathering` | 1, 2 |
| 2 | `ciq_validation_json_gen` | `ciq_validation_json_gen` | 3, 4 |
| 3 | `ciq_validation_json_qa` | `ciq_validation_json_test` | 5 |
| 4 | `network_config_gen` | `network_config_gen` | 6 |
| 5 | `network_simulator_setup` | `network_simulator_setup` | 8 |
| 6 | `network_config_qa` | `network_config_test` | 7, 9 |
| 7 | `report_generator` | `report_generation` | 10 |

### 7 Skills
| # | Skill | Files | Key Methods |
|---|-------|-------|-------------|
| 1 | `requirement_gathering` | 15 | parse_and_analyze, incorporate_answers, compile_final |
| 2 | `ciq_validation_json_gen` | 5 | generate() + regenerate(feedback) |
| 3 | `ciq_validation_json_test` | 6 | test() — LLM TCs + JAR + QA report |
| 4 | `network_config_gen` | 4 | generate() + regenerate(feedback) |
| 5 | `network_simulator_setup` | 5 | setup() — 4 phases: config, responses, start, sanity |
| 6 | `network_config_test` | 6 | test() + test_mop_only() — QA report |
| 7 | `report_generation` | 3 | generate() — HTML + Excel reports |

### Skill Data Flow
```
requirement_gathering -> requirements.json
  |
ciq_validation_json_gen -> validation-rules.yaml + json-output.yaml
  |
ciq_validation_json_test <-> ciq_validation_json_gen.regenerate()
  |
network_config_gen -> workflow.yaml (${nodeGroups}, ${configData}, ${register})
  |
network_simulator_setup -> simulator config + running MockWire + sanity
  |
network_config_test <-> network_config_gen / network_simulator_setup .regenerate()
  |
report_generation -> HTML + Excel pipeline report
```

## PipelineAgent Built-in Features
- **Milestones**: built-in pause after each agent (configured via `_AGENT_MILESTONES`)
- **Retry routing**: reads `_retry_target` from state, maps via `_RETRY_STEP_TO_INDEX`
- **Display steps**: maps agent name to 10 UI steps via `_AGENT_DISPLAY_STEPS`
- **No separate** milestone agents, reviewer agents, or router agents
- **app_name**: `"netconfig"`

## Shared Module-Level Dicts (ADK session state does NOT work for these)
```
pipeline_actions      — cancel/rerun signals          (shared.state)
reviewer_feedback     — retry feedback from reviewers  (shared.state)
milestone_events      — asyncio.Event per milestone    (shared.state)
milestone_decisions   — user decision per milestone    (shared.state)
milestone_waiting     — milestone pause state          (shared.state)
requirements_store    — requirements package           (agents.requirements_agent)
```

## Project Structure
```
agents/                            # 7 agent files (one per skill)
  requirements_agent.py            # RequirementsGathererNode
  ciq_validation_json_gen_agent.py # Wraps ciq_validation_json_gen skill
  ciq_validation_json_qa_agent.py  # Wraps ciq_validation_json_test skill
  network_config_gen_agent.py      # Wraps network_config_gen skill
  network_simulator_setup_agent.py # Wraps network_simulator_setup skill
  network_config_qa_agent.py       # Wraps network_config_test skill
  report_agent.py                  # Wraps report_generation skill

skills/                            # 7 reusable skill packages
  requirement_gathering/           # RequirementGatheringSkill (15 files)
  ciq_validation_json_gen/         # CiqValidationJsonGenSkill (5 files)
  ciq_validation_json_test/        # CiqValidationJsonTestSkill (6 files)
  network_config_gen/              # NetworkConfigGenSkill (4 files)
  network_simulator_setup/         # NetworkSimulatorSetupSkill (5 files)
  network_config_test/             # NetworkConfigTestSkill (6 files)
  report_generation/               # ReportGenerationSkill (3 files)

orchestrator/                      # Pipeline orchestration
  pipeline_agent.py                # 7-agent loop + built-in milestones + retry
  runner.py                        # PipelineRunner with DB + EventBus

shared/                            # Shared state + LLM wrapper
  state.py                         # Module-level shared dicts
  llm.py                           # LiteLlm/OpenRouter wrapper + usage tracking
  cr_bridge.py                     # Legacy cross-project bridge (unused by agents)

core/                              # Infrastructure
  config.py, events.py, logging_config.py, retry.py

models/                            # Domain models
  domain.py, validation.py

storage/                           # Persistence
  db.py, artifact_store.py

tools/                             # Shared tools
  yaml_tools.py, report_tools.py, ciq_tools.py

ui/                                # NiceGUI frontend
  app.py                           # Entry point + CSS + layout
  pages/                           # upload, progress, settings, history, etc.

schemas/                           # Authoritative YAML schemas (consumed by JARs)
knowledge/                         # OKF-ready knowledge directory
config/                            # Pipeline configuration
data/                              # Runtime (runs, uploads, DB, logs)
lib/                               # JARs + MockWire
```

## Workflow Variable Patterns
```
${nodeGroups.<Node>.<field>}       — node properties from JSON output
${configData.tables.<Sheet>.<Col>} — CIQ data values
${register.<varName>}              — captured command outputs
${env.<key>}                       — runtime environment variables
$<segregateVar>                    — segregate_by variable from JSON output
```

## Running
```powershell
# UI server
.\start.ps1

# CLI test (auto-continue milestones)
python test_run.py

# CLI with args
python main.py --ciq path/to/ciq.xlsx --uc-name DSR_1058_IFC_CAPABILITY_MODIFICATION

# Test individual skill
python test_step.py --skill ciq_validation_json_gen --run-id <id>
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
