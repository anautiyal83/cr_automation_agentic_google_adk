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

## Workflow Variable Patterns (verified against cli-automation JAR, spec 004)
```
${configSequences.0.records.0.data.<Column>}  — CIQ value (first record of the node group; spaces OK)
${<NODE>.niamID-<n>}                          — NIAM ID of a CIQ node, e.g. ${RDS.niamID-1}
${node} ${nodeGroup} ${crGroup} ${email}      — current node group / CR group
${NODE_TYPE} ${SUB_ACTIVITY_NAME}             — provided by the JAR at runtime
${varName}                                    — registered (?<varName>…) output or --var value
${SECRET.key} ${ENV.NAME}                     — globals.secrets / OS environment
```
- NOT supported (empty at execute): `configData.*`, `nodeGroups.*`, `node_row.*`, `register.*`, `env.*`, `${{…}}`
- Regexes are Java: `(?<name>…)`, names letters/digits only (no `(?P<…>)`, no `_`)
- `su`/`exit` steps: step-level `prompt_regex`, no output regex (prompts are stripped)
- Optional columns: `when: "configSequences.0.records.0.data.<Col> != ''"` (bare operand, no `${}`)
- CIQ column names are bare, never quoted (`data.'Test IMSI'` is empty); no `${…}` inside regexes (not interpolated)
- Framework runtime parameters (e.g. `${ORDERNO}`) are listed in `config/pipeline_config.yaml` `execution.runtime_params`;
  step 9 passes their test values as `--var`
- Validation regexes: `find()`, `^`/`$` per line, `.` does not cross lines
- `network_config_gen` autofix rewrites the old forms; its validator rejects unresolvable placeholders and regexes that
  don't match the real outputs in `requirements.doc_extracted.command_samples` (from uploaded `.txt`/`.log` transcripts)
- Helper scripts must accept exactly the workflow's flags and print what its success regex expects (step 6 checks both)
- `criteria.expr` compares REGISTERED variables only (`cpuIdle >= 90`): no `Float()`/`Int()`, a literal `True` always
  fails, named groups of the validation regex are invisible to expr → `register:` the value on the step (autofix)
- A non-zero exit fails a step before its regex (`use_exit_code: false` does not help): "must be absent" checks are
  `ls -l <file> 2>&1 || true` (autofix adds it)
- Files move with step `sftp: {operation: upload|download, local_path, remote_path}` on the node, never `put`/`get` in a
  shell (autofix rewrites); a node's file must be downloaded before a LOCAL step reads it (validator)
- Step 8 derives MockWire `vfs.dirs` + `triggers` from those flows (`network_simulator_setup/file_flows.py`):
  upload `inbox/${X}.xml` + later check of `outbox/${X}.xml.res` → the simulator produces the file (UC sample response)

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
- **MockWire**: lib/mockwire/mockwire-simulator/mockwire.jar (embedded clone of the MockWire repo,
  branch `feature/netconfig-testing-gaps`; source at D:\Nokia\workspace\mockwire)
- **Templates**: lib/templates/*.html
- **Schemas**: schemas/*.yaml

## Simulator (steps 8 + 9) — spec 004
- How a UC simulator must behave for cli-automation: `lib/mockwire/AGENTS.md` (read before changing step 8)
- Step 8 builds one MockWire instance per remote node (`skills/network_simulator_setup/topology.py`):
  login prompt → `ctrl+p+e` → root prompt, `su` via `auth.users`, NIAM neIds from CIQ JSON
- Per-run layout `run_dir/simulator/`: `config/`, `outputs/`, `logs/<inst>.log`, `logs/<inst>.events.jsonl`,
  `vfs/`, `manifest.json`, and `<uc>.yaml` = **overlay workflow** step 9 executes (never production hosts)
- Step 9 classifies failures from the MockWire event log: unmatched clean command → step 8, otherwise → step 6
- Running simulators are registered in `shared.state.simulator_processes` and stopped on every exit path
