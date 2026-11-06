# CLAUDE.md — Azalio Autonomous Network Platform (Google ADK)

Product name (UI, documents, reports): **Azalio Autonomous Network Platform** — `core.config.PRODUCT_NAME`.
Internal ids keep `netconfig` (ADK app name, `NETCONFIG_*` env vars, `admin@netconfig.local`).

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
- **Retry routing**: reads `_retry_target` from state, maps via `_RETRY_STEP_TO_INDEX`; "Continue" at a review drops it;
  every retry counts per agent, past `MAX_RETRIES` (3) the run fails (OB-4/5)
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
  auth.py                          # users, roles, View/Update levels, set-password CLI (spec 006)
  audit.py                         # append-only activity trail (audit_log)

models/                            # Domain models
  domain.py, validation.py

storage/                           # Persistence
  db.py, artifact_store.py

tools/                             # Shared tools
  yaml_tools.py, report_tools.py, ciq_tools.py

ui/                                # NiceGUI frontend
  app.py                           # Entry point + CSS + layout + login middleware + screen gating
  access.py                        # action() marking, @requires/@requires_admin, RunScope, trail helpers
  pages/                           # upload, progress, settings, history, etc.
                                   # + login_page, users_section, audit_section (spec 006)

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
${nodeGroup} ${crGroup} ${email}              — current node group / CR group (${node}: never — it
                                              empties the MOP of a node group with several nodes)
${NODE_TYPE} ${SUB_ACTIVITY_NAME}             — provided by the JAR at runtime
${varName}                                    — registered (?<varName>…) output or --var value
${SECRET.key} ${ENV.NAME}                     — globals.secrets / OS environment
```
- NOT supported (empty at execute): `configData.*`, `nodeGroups.*`, `node_row.*`, `register.*`, `env.*`, `${{…}}`
- Regexes are Java: `(?<name>…)`, names letters/digits only (no `(?P<…>)`, no `_`)
- `su`/`exit` steps: step-level `prompt_regex`, no output regex (prompts are stripped)
- Optional columns: `when: "configSequences.0.records.0.data.<Col> != ''"` (bare operand, no `${}`)
- `when` joins conditions with `&&`/`||` — with `and`/`or` the step is always skipped (autofix rewrites)
- LOCAL steps: the JAR splits double-quoted arguments at spaces (`--a "x y"` → `['--a','x']`); single quotes
  arrive intact (autofix rewrites); `${configSequences.0.records.0.data}` (whole record) is `[object Object]`
- LOCAL steps break on a double quote anywhere, even inside single quotes: no JSON/dict values — one flag per
  field (autofix expands `--x '{'K': '${…}'}'` into `--x_k '${…}'`)
- Either-or CIQ columns (Test IMSI / Test MSISDN, found by step 1): the workflow must use both; CIQ values in the
  sample request must reach the script that builds the uploaded request (validator, DEP-46/47)
- CIQ column names are bare, never quoted (`data.'Test IMSI'` is empty) nor subscripted (`data['Test IMSI']`; autofix rewrites); no `${…}` inside regexes (not interpolated)
- Framework runtime parameters (e.g. `${ORDERNO}`) are listed in `config/pipeline_config.yaml` `execution.runtime_params`;
  step 9 passes their test values as `--var`
- Validation regexes: `find()`, `^`/`$` per line, `.` does not cross lines
- `tar -c` success regexes must match GNU tar (``tar: Removing leading `/' …``); autofix replaces a missing one with
  the archive's `ls -1` line (DEP-58)
- `network_config_gen` autofix rewrites the old forms; its validator rejects unresolvable placeholders and regexes that
  don't match the real outputs in `requirements.doc_extracted.command_samples` (from uploaded `.txt`/`.log` transcripts)
- Helper scripts must accept exactly the workflow's flags and print what its success regex expects (step 6 checks both)
- JAR facts (probed 2026-11-04): on EMPTY output a regex with `^` never matches (`^$` fails, `.*` passes —
  autofix `empty_output_success`); `expr` fails on any parenthesis and binds `&&` tighter than `||` (autofix drops
  them); a looped register counts `.*` twice per line (autofix → `.+`); an unregistered variable reads as `''`;
  silent commands (`chmod cp mv chown`) whose check reads output get `&& ls -l <paths>` (autofix); guarded steps
  with an `expr` pass when skipped (`|| ncSkipped == 'SKIPPED_BY_CONDITION'`)
- `criteria.expr` compares REGISTERED variables only (`cpuIdle >= 90`): no `Float()`/`Int()`, a literal `True` always
  fails, named groups of the validation regex are invisible to expr → `register:` the value on the step (autofix)
- A non-zero exit fails a step before its regex (`use_exit_code: false` does not help): "must be absent" checks are
  `ls -l <file> 2>&1 || true` (autofix adds it)
- Files move with step `sftp: {operation: upload|download, local_path, remote_path}` on the node, never `put`/`get` in a
  shell (autofix rewrites); a node's file must be downloaded before a LOCAL step reads it (validator)
- Step 6 `structure.py` (after the LLM, idempotent; validator + regenerate see the flat form): record steps run in a
  `type: loop` over `${configSequences.0.records}` (`${rec.data.<Col>}`, `reverse_order: false` — null NPEs); phases
  per activity (spec 010, Settings → Vendor Profiles → Activity → `run_options`): only the ticked ones; Backup "via
  repo server": automation server ↔ `REPO` (`execution.repo_server`) is framework sftp; the NODE runs `scp`/`sftp`
  (node type `file_transfer`) against the repo to copy the `tar -c` archive there, back before the rollback, and every
  input file in (`expect_reply` = repo password; no node address needed — NIAM nodes too); otherwise backups stay in
  the node's /tmp; every transfer gets `sha256sum` on both ends + `criteria.expr: dst == src`. Added steps:
  `id: nc_*`. Step 8 lists the repo instance as a `transfer.peers` entry of each node instance (MockWire copies
  between their VFS)
- Backups follow a naming convention, never a registered value (a rollback-only run has none — user 2026-11-05):
  node `/tmp/${ORDERNO}/<source folder>/<archive without $(date)>`, repo `<backup_dir>/${ORDERNO}/${<NODE>.<CIQ node
  name field>}/<source folder>/<archive>` (`structure.backups_by_convention`); the rollback lists that path
  (`nc_rb<k>_check` → `ncBackup<k>`) and decides by it. `when` takes bare names (`${x} > 0` is always false; autofix)
- Never touch anything on the node unrelated to the activity (user rule): for a folder the activity loads into,
  structure backs up only the files the load overwrites (`items.list` → `overwritten.list`, uploads moved to the
  backup phase), the rollback removes exactly the loaded files then restores; `<dest>/*` after an untar → members
- Step 9's rollback-only case starts from the files its node group's sunny day left (`vfs_after`), not a clean node
- Every review (2-9): **Edit requirements** — at a later review the decision `edit` sends the run back to
  step 1 like `clarify` (cache cleared, no retry counted), which applies the pending edit after its analysis.
  Step 2 review: **Edit requirements** (`shared/requirement_edits.py`, `requirement_edits.json`) — changed answers
  replace the UC's saved answers, the note joins the developer notes (also the run's document set) as
  authoritative; a corrected CIQ workbook / unticked sheets (`excluded_ciq_sheets`, kept per UC) are applied;
  step 1 re-analyses (no questionnaire), compiles again and pauses at step 2 again. Later steps take the CIQ
  path from `agents.requirements_agent.ciq_file_path(state)`. Hidden CIQ sheets are never read
- Step 3: a cross-sheet business rule from the developer answers becomes `conditionalPattern` (lookup chain, e.g.
  GROUP → INDEX.NODE → Node_Details.VER decides INPUT_FILE's type); kept even when the CIQ breaks it (DEP-60)
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

## Login & Access (spec 006 — `specs/006-rbac-login/`)
- Email + password login (`ui/pages/login_page.py`, `AuthMiddleware`): every route except `/login`, `/logout`
  and NiceGUI assets needs a session (12 h) — media files too. Default admin `admin@netconfig.local`/`admin`
  (forced change) or `NETCONFIG_ADMIN_EMAIL`/`NETCONFIG_ADMIN_PASSWORD`; `python -m core.auth set-password <email>`
- A role gives each screen `view` or `update` (`core.auth.SCREENS`); `users` and `audit` are admin-only
  (`ADMIN_SCREENS`). Screens without permission are not built; navigation/deep links refuse them
- New action buttons: wrap in `action(btn[, screen])` (disabled on View screens) AND guard the handler with
  `@requires(screen, "<action>")` (server check); admin-only things `@requires_admin`
- Runs: `pipeline_runs.started_by`; sections take `scope()` at build time and use `RunScope.where()` in queries,
  `RunScope.check()` before acting on a run id (admin: all runs, others own runs). Starts go through
  `ui/pages/queue_panel.submit_run()` (run queue, spec 007)
- Trail: `await record(...)` in async handlers after success, `record_later(...)` in sync code (takes the
  user now); user actions only — the pipeline/system writes no entries (D11). Never record secrets
- Session data: `.nicegui/` (git-ignored); cookie secret `NETCONFIG_STORAGE_SECRET` or `data/.storage_secret`

## Step Names (UI)
1. DOC Parser, 2. DOC Analyzer, 3. CIQ Config Generation,
4. Generate CIQ JSON Config, 5. QA-Validation, 6. Generate Execution Config,
7. QA-MOP, 8. Simulator Setup, 9. QA-Execution, 10. Final Report

## External Dependencies
- **JARs**: lib/ciq-processor-1.0.3-cli.jar, lib/cli-automation-standalone.jar, lib/execution-report-generator-1.0.2-cli.jar
- **MockWire**: lib/mockwire.jar only (Settings → External Modules → MockWire JAR; `mockwire_jar`). Source: the
  MockWire repo (`https://github.com/azalio-io/mockwire`, branch `feature/netconfig-testing-gaps`, local
  D:\Nokia\workspace\mockwire). A MockWire change: commit + push there, `mvn -o test`,
  `mvn -o -q package -Dmaven.antrun.skip=true -DskipTests`, copy `target/mockwire-1.0.0.jar` to `lib/mockwire.jar`
- **Templates**: lib/templates/*.html
- **Schemas**: schemas/*.yaml

## Simulator (steps 8 + 9) — spec 004
- CIQ JSON (step 4): one `<UC>.json` with `crGroups[].nodeGroups[].nodes[]` (`json_output_builder`, from the CIQ layout);
  read it with `tools.ciq_json.node_groups` (also handles the older per-CR files with `nodeGroups` at the root)
- How a UC simulator must behave for cli-automation: `AGENTS.md` of the MockWire repo (read before changing step 8)
- Step 8 builds one MockWire instance per remote node (`skills/network_simulator_setup/topology.py`):
  login prompt → `ctrl+p+e` → root prompt, `su` via `auth.users`, NIAM neIds from CIQ JSON
- MockWire runs `if [[ … ]]; then …; fi`, `$(…)`, `cp mv chmod chown tar -c/-x ls cat rm …` on its VFS: step 8
  (`vfs_commands.py`) gives no response key to a command whose paths all lie where the workflow puts files (`/tmp`,
  uploads, copy / extract targets, backup sources, archives) and creates those folders per CIQ record (DEP-59);
  staged `.tar` inputs are real archives (step 9)
- Per-run layout `run_dir/simulator/`: `config/`, `outputs/`, `logs/<inst>.log`, `logs/<inst>.events.jsonl`,
  `vfs/`, `manifest.json`, and `<uc>.yaml` = **overlay workflow** step 9 executes (never production hosts)
- Step 9 writes one execution-report-generator HTML per executed case to `run_dir/exec_report/` (`--json-file`: summary
  + detail for every case, pass or fail); JAR from Settings → External Modules (`execution_report_jar`)
- Step 9 classifies failures from the MockWire event log: unmatched clean command → step 8, otherwise → step 6
- Step 10 lists the run's deliverables in order (`skills/report_generation/deliverables.py`): requirements document, validation rules,
  JSON output, execution YAML, MOP, QA-Validation, QA-Execution, execution summary + success/failure reports
- Running simulators are registered in `shared.state.simulator_processes` and stopped on every exit path

## Run Queue + Restart Recovery — spec 007
- `PipelineRunner.submit()` (= `start()`) queues every run (`pipeline_runs.status = 'queued'`); `dispatch()` starts the
  oldest (`resuming` first) when `active_run()` is None — after a submit, at every run end and at server start
- Queue view: `orchestrator/run_queue.snapshot()` + `ui/pages/queue_panel.py` (Pipeline Queue page, screen `queue`); start paths use `submit_run()`
- UI (spec 008): Run Artifacts = screen `outputs`; Artifact Cache page = admin screen `cache`; destructive buttons go through `ui.components.confirm`
- Checkpoints (`orchestrator/checkpoints.py`, table `run_checkpoints`): before every agent and at review pauses;
  `resume_queue()` at startup; step 9 resumes at step 8; `MAX_RESUME_ATTEMPTS = 2`
- `pipeline_runs.owner` (`orchestrator/ownership.py`): a server never resumes or runs beside a run another live
  process owns (OB-10); still use a DB copy for previews
- Audit trail: user actions only (`ui/access.record`) — the runner writes no trail entries
