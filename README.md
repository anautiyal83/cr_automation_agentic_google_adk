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

## Login & Access

The web UI needs a login (email + password). Spec: [specs/006-rbac-login](specs/006-rbac-login/spec.md).

- **First start**: the administrator account `admin@netconfig.local` with password `admin` is created; at the
  first login it must be changed. To choose them up front, set before the first start:
  `NETCONFIG_ADMIN_EMAIL` and `NETCONFIG_ADMIN_PASSWORD`.
- **Forgotten password** (any user, run on the server): `python -m core.auth set-password <email>`
- **Users & Roles** (admin): create users (they set their own password at first login), give each one a role;
  a role grants each screen **None**, **View** (data only, action buttons disabled) or **Update**.
- **Runs**: everyone sees and acts on the runs they started; the administrator sees all runs (runs from
  before the login belong to the administrator).
- **Artifact Cache** (admin, below Run Artifacts): cached steps per use case; Clear one or all.
  Every delete / clear / remove asks for confirmation first.
- **Audit Trail** (admin): every user action and refused attempt (no system entries) — filter, details, CSV export.

## Run Queue & Restart Recovery

Spec: [specs/007-run-queue](specs/007-run-queue/spec.md).

- **One pipeline runs at a time**; every other start (New Run, Run History → Re-run, CLI) waits in a FIFO queue
  and starts by itself when the pipeline is free. One job per use case; at most
  `execution.max_queued_per_user` (default 3) waiting jobs per user — beyond that a start is refused.
- **Pipeline Queue** page (sidebar, below Live Run; compact view on Dashboard and New Run): the job in the pipeline, the waiting jobs
  with their position and estimated start, and jobs finished in the last 24 h. Estimates use working time (run time
  without review pauses) of the use case's past runs; while a run waits for a review, the next jobs start
  "after the review". Owners remove their own waiting jobs (the administrator any); a run paused for review
  is not cancelled automatically — the administrator can cancel it.
- **Server restart**: the pipeline saves a checkpoint before every agent and at every review pause
  (`run_checkpoints`). At start, interrupted runs resume first: the interrupted step runs again; a review pause comes
  back as the same pause; step 9 resumes at step 8 (its simulator is gone). After 2 failed resumes at the same point,
  or without a checkpoint or run folder, the run is marked failed with the reason.
- Sessions last 12 h; 5 failed logins lock an email for 5 minutes. The session cookie is signed with
  `NETCONFIG_STORAGE_SECRET`, else a secret generated once into `data/.storage_secret` (do not share `data/`).
  Run behind HTTPS (reverse proxy) in production.
- The CLI (`main.py`, `test_run.py`, `test_step.py`) needs no login; its runs are visible to the administrator.

## Run Phases & File Routes

Spec: [specs/010-run-phase-options](specs/010-run-phase-options/spec.md).

- **Phases are set per activity**: Settings → Vendor Profiles → Activity has a checkbox per phase, in this order:
  PreNodeHealthCheck · Backup (+ *via repo server*) · Activity (ActivityPrecheck, ActivityConfiguration,
  ActivityPostCheck) · PostNodeHealthCheck · Rollback (RollbackPrecheck, RollbackConfiguration, RollbackPostCheck).
  New Run shows the selected activity's phases read-only; without an activity every phase runs. Step 6 generates only
  the ticked phases (`skills/network_config_gen/structure.py` drops the others).
- **File routes**:

  | Backup | via repo server | Input files | Backup kept | Rollback restores from |
  |--------|-----------------|-------------|-------------|------------------------|
  | off | — | automation server → node (framework SFTP) | — | — |
  | on | off | automation server → node (framework SFTP) | node `/tmp` | node `/tmp` |
  | on | on | automation server → repo (framework SFTP), then the **node** copies it from the repo | repo server (the node copies it there) | repo server (the node copies it back) |

- **Via repo server**: the framework logs in to the node as usual (NIAM for Nokia nodes) and runs `scp` or `sftp` on
  the node against the repo server; the repo password is answered by `expect_reply`. Which of the two depends on the
  node type: Settings → Vendor Profiles → Node type → *File transfer* (SFTP, or SCP for nodes without SFTP). The repo
  server never logs in to a node, so nodes need no address beyond their own login. Every transfer is checked with
  `sha256sum` on both ends.
- **Repo server**: `execution.repo_server` in `config/pipeline_config.yaml` (host, user, password secret, backup
  directory; workflow node `REPO`); step 1 asks for it when the MOP takes a backup. Paths:
  `<backup_dir>/${crGroup}/files/` (input files), `<backup_dir>/${crGroup}/<hostname>/<source path>/` (backups).
- **Simulator** (steps 8, 9): each node's MockWire instance lists the repo instance as a transfer peer, so the copies
  really move files between the simulated hosts.

## Architecture

```
┌────────────────── PipelineAgent (7 agents) ──────────────────┐
│                                                                │
│  [1] RequirementsGatherer ── Parse, Analyze, Questionnaire    │
│          ↓                                                     │
│  [2] CiqValidationJsonGen ── validation-rules + json-output   │
│          ↓                                                     │
│  [3] CiqValidationJsonQA ── test via JAR ←─ retry [2]        │
│          ↓                                                     │
│  [4] NetworkConfigGen ── workflow.yaml with ${...}            │
│          ↓                                                     │
│  [5] NetworkSimulatorSetup ── config + start + sanity         │
│          ↓                                                     │
│  [6] NetworkConfigQA ── MOP + exec test ←─ retry [4] or [5]  │
│          ↓                                                     │
│  [7] ReportGenerator ── HTML + Excel                          │
│                                                                │
│  Built-in: milestone pauses, retry routing, failure classify  │
└────────────────────────────────────────────────────────────────┘
```

## 7 Skills (Reusable Packages)

Each skill is a self-contained package with clear SkillInput/SkillOutput contracts:

| # | Skill | Description |
|---|-------|-------------|
| 1 | `requirement_gathering` | Parse CIQ/MOP/SSH/XML, LLM analysis, interactive questionnaire, compile requirements |
| 2 | `ciq_validation_json_gen` | LLM generates validation-rules.yaml + json-output.yaml with autofix + regenerate() |
| 3 | `ciq_validation_json_test` | QA engine: LLM test cases, JAR execution, professional HTML QA report |
| 4 | `network_config_gen` | LLM generates workflow YAML with ${...} runtime placeholders + autofix |
| 5 | `network_simulator_setup` | Config gen + start MockWire + sanity checks (4 phases) |
| 6 | `network_config_test` | MOP test + execution test + failure classification + HTML QA report |
| 7 | `report_generation` | Final HTML + Excel pipeline report |

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

## Pipeline Steps (UI Display)

| # | Step | Agent | Type |
|---|------|-------|------|
| 1+2 | Requirements Gathering | RequirementsGatherer | LLM + HIL |
| 3+4 | CIQ Validation + JSON Output | CiqValidationJsonGen | LLM |
| 5 | QA-Validation | CiqValidationJsonQA | LLM + JAR |
| 6 | Execution Workflow | NetworkConfigGen | LLM |
| 7+9 | MOP + Execution Test | NetworkConfigQA | LLM + JAR |
| 8 | Simulator Setup | NetworkSimulatorSetup | LLM + JAR |
| 10 | Final Report | ReportGenerator | Deterministic |

## Project Structure

```
cr_automation_agentic_google_adk/
  agents/                          # 7 agent files (one per skill)
    requirements_agent.py
    ciq_validation_json_gen_agent.py
    ciq_validation_json_qa_agent.py
    network_config_gen_agent.py
    network_simulator_setup_agent.py
    network_config_qa_agent.py
    report_agent.py

  skills/                          # 7 reusable skill packages
    requirement_gathering/         # 15 files — parse, analyze, questionnaire, compile
    ciq_validation_json_gen/       # 5 files — LLM gen + autofix + regenerate
    ciq_validation_json_test/      # 6 files — QA engine + JAR + report
    network_config_gen/            # 4 files — LLM gen + autofix + regenerate
    network_simulator_setup/       # 5 files — config + start + sanity
    network_config_test/           # 6 files — MOP + exec + classify + report
    report_generation/             # 3 files — HTML + Excel

  orchestrator/                    # Pipeline orchestration
    pipeline_agent.py              # 7-agent loop with built-in milestones + retry
    runner.py                      # PipelineRunner with DB + EventBus

  shared/                          # Shared state + LLM wrapper
    state.py                       # Module-level shared dicts
    llm.py                         # LiteLlm/OpenRouter LLM wrapper
    cr_bridge.py                   # Legacy cross-project bridge

  core/                            # Infrastructure
  models/                          # Domain models
  storage/                         # SQLite DB + artifact store
  tools/                           # Shared tools (YAML, reports, CIQ)
  ui/                              # NiceGUI frontend

  schemas/                         # Authoritative YAML schemas
  knowledge/                       # OKF-ready knowledge docs
  config/                          # Pipeline configuration
  data/                            # Runtime (runs, uploads, DB)
  lib/                             # JARs + MockWire
```

## Requirements

- Python 3.11+
- Java 8+ (for JAR modules)
- `google-adk>=2.11.0`, `litellm`, `nicegui>=2.0`
- OpenRouter API key
