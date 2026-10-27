# CLAUDE.md — NetConfig Pipeline (Google ADK)

## Project Overview
- **Type**: Python agentic pipeline for telecom network activity automation using Google ADK
- **Orchestrator**: Custom PipelineAgent(BaseAgent) with backtracking retry loops
- **LLM**: LiteLlm/OpenRouter (default: Gemini 2.5 Flash)
- **UI**: NiceGUI (sidebar 140px, content area, single-row step tracker)
- **Storage**: SQLite for metadata + file system for artifacts
- **Port**: 8000, start via `start.ps1`

## Architecture — 26 Agents + 6 Skills

### Skills (Reusable Packages)
| # | Skill | Files | Lines | Description |
|---|-------|-------|-------|-------------|
| 1 | `requirement_gathering` | 15 | 2,644 | Parse CIQ/MOP/SSH/XML, LLM analysis, questionnaire, compile requirements |
| 2 | `ciq_validation_json_gen` | 6 | 995 | LLM generates validation-rules.yaml + json-output.yaml, autofix, regenerate() |
| 3 | `ciq_validation_json_test` | 7 | 1,087 | QA engine: LLM test cases, JAR execution, HTML QA report |
| 4 | `network_config_gen` | 5 | 759 | LLM generates workflow YAML with ${...} placeholders, autofix, regenerate() |
| 5 | `network_simulator_setup` | 6 | 1,131 | Config gen, command-response mapping, start MockWire, sanity checks |
| 6 | `network_config_test` | 7 | 1,160 | MOP test + execution test, failure classification, HTML QA report |

### Skill Data Flow
```
requirement_gathering → requirements.json
    ↓
ciq_validation_json_gen → validation-rules.yaml + json-output.yaml
    ↓
ciq_validation_json_test ←→ ciq_validation_json_gen.regenerate()
    ↓
network_config_gen → workflow.yaml (${nodeGroups}, ${configData}, ${register})
    ↓
network_simulator_setup → simulator config + running MockWire + sanity verified
    ↓
network_config_test ←→ network_config_gen.regenerate() / network_simulator_setup.regenerate()
```

### RequirementsGatherer (replaces Steps 1+2)
- **File**: `src/agents/requirements_agent.py`
- **Skill**: `src/skills/requirement_gathering/`
- **Type**: BaseAgent — gatekeeper that blocks pipeline until requirements are gathered
- **6 phases**: Parse → Analyze → Ask (iterative HIL, max 3 rounds) → Compile → Summary Doc → Review Pause
- **3 requirement categories**: Node & Activity, Commands & Parameters, Orchestration
- **Milestones**: Step 0 = interactive questionnaire dialog, Step 2 = summary review
- **Outputs**: requirements.json, {uc}_requirements-summary.html, {uc}_questionnaire_round{N}.html
- **Shared state**: `requirements_store` dict (bypasses ADK session state)

### Pipeline Steps (8 remaining)
| Step | Agent | Skill | Description |
|------|-------|-------|-------------|
| 3 | ValidationRulesNode | `ciq_validation_json_gen` | Generate validation-rules.yaml via LLM |
| 4 | JsonTemplateNode | `ciq_validation_json_gen` | Generate json-output.yaml via LLM |
| 5 | CiqQANode | `ciq_validation_json_test` | QA cycle: generate TCs, run JAR, QA report |
| 6 | WorkflowGenNode | `network_config_gen` | Generate workflow.yaml with ${...} placeholders |
| 7 | MopTestNode | `network_config_test` | Test MOP generation via JAR |
| 8 | SimulatorSetupNode | `network_simulator_setup` | Config gen → start MockWire → sanity checks |
| 9 | ExecutionTestNode | `network_config_test` | Execute workflow against simulator, QA report |
| 10 | FinalReportNode | — | Generate HTML + Excel reports |

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

Generation skills support regenerate(feedback) for test-driven correction loops.

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
  skills/                            # 6 reusable skill packages (46 files, 7,776 lines)
    requirement_gathering/           # Parse docs, LLM analysis, questionnaire, compile
      skill.py                       # RequirementGatheringSkill: parse_and_analyze → incorporate_answers → compile_final
      parsers/                       # 4 deterministic parsers (ciq, mop, ssh_log, xml_sample)
      analyzer.py, questionnaire.py  # LLM wrappers
      compiler.py                    # Merge + enforce + inject schema hints
      schema_discovery.py            # Read schemas/*.yaml at runtime
      models.py, prompts.py, html_report.py, schema_validator.py

    ciq_validation_json_gen/         # LLM generates validation + JSON output YAMLs
      skill.py                       # generate() + regenerate(feedback)
      validation_gen.py              # Validation rules generation + autofix
      json_output_gen.py             # JSON output generation + autofix
      autofix.py                     # Column fuzzy-matching, sheet name correction
      prompts.py

    ciq_validation_json_test/        # QA engine for CIQ testing
      skill.py                       # test(): generate TCs → run JAR → evaluate → report
      test_case_gen.py               # LLM generates test scenarios
      jar_executor.py                # Run ciq-processor JAR
      result_parser.py               # Parse results, evaluate TCs
      report.py                      # Professional HTML QA report (6 sections)
      prompts.py

    network_config_gen/              # LLM generates workflow YAML
      skill.py                       # generate() + regenerate(feedback)
      autofix.py                     # Field whitelist, validation.description, Java expr fix
      validator.py                   # Structural validation, ${...} placeholder awareness
      prompts.py                     # Variable pattern reference in prompts

    network_simulator_setup/         # MockWire simulator lifecycle
      skill.py                       # 4 phases: config → responses → start → sanity
      simulator_runner.py            # Start/stop MockWire JAR, health-check ports
      sanity_check.py                # Per-node: SSH, prompt, basic cmd, escalation, UC cmd
      autofix.py                     # Port dedup, missing node stubs
      prompts.py

    network_config_test/             # MOP + execution testing
      skill.py                       # test() + test_mop_only()
      test_case_gen.py               # LLM generates MOP/execution/integration TCs
      jar_executor.py                # Run cli-automation JAR (MOP + exec modes)
      result_parser.py               # Parse results, classify failures (workflow vs simulator)
      report.py                      # Professional HTML QA report (7 sections)
      prompts.py

  agents/                            # Pipeline agent definitions
    requirements_agent.py            # RequirementsGathererNode (delegates to skill)
    step_agents.py                   # Step 3-10 agent wrappers
    milestone_agents.py              # Milestone pause agents
    reviewer_agents.py               # QA + milestone reviewer agents
    router_agents.py                 # Failure routing agents

  tools/                             # Shared tools for agents
    yaml_tools.py                    # validate_yaml_syntax, read/write artifact
    report_tools.py                  # read_exec_report, read_mop_report, etc.
    ciq_tools.py                     # read_ciq_structure, classify_failure_type

  shared/                            # Module-level shared state + cross-project bridge
    state.py                         # All module-level shared dicts
    cr_bridge.py                     # Cross-project import bridge

  orchestrator/                      # Pipeline orchestration
    pipeline_agent.py                # PipelineAgent with state-machine loop + backtracking
    runner.py                        # PipelineRunner with DB persistence + EventBus

  core/                              # Infrastructure
    config.py, events.py, logging_config.py, retry.py

  models/                            # Domain models
    domain.py, validation.py

  storage/                           # Persistence
    db.py, artifact_store.py

  ui/                                # NiceGUI frontend
    app.py                           # Entry point + CSS + layout
    pages/                           # upload, progress, settings, history, etc.

schemas/                             # Authoritative YAML schemas (consumed by JARs)
knowledge/                           # OKF-ready knowledge directory
config/                              # Pipeline configuration
data/                                # Runtime (runs, uploads, DB, logs)
lib/                                 # JARs + MockWire
```

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
