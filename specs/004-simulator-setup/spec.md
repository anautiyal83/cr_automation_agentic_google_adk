# Feature Specification: Step 8 — Network Simulator Setup (Rework)

**Feature Branch**: `004-simulator-setup`

**Created**: 2026-11-02

**Status**: In progress — decisions D1–D3 made 2026-11-02, Phase 0 spike running

**Input**: Step 8 is critical for testing: it must stand up a faithful SSH simulation of every
remote node a UC touches so that Step 9 can execute the generated workflow YAML with
`cli-automation-standalone.jar execute` without touching production. Deep-dive on
2026-11-02 found that Step 8 cannot currently produce a usable simulator and Step 9
never connects to it.

## Context

Step 8 = `network_simulator_setup` agent → `skills/network_simulator_setup/`. Expected behaviour
(from project owner):

1. **Prepare simulator config** for the UC: one server definition per simulated endpoint,
   with users, prompts, NIAM, escalation.
2. **Prepare command input → output** per node in a UC-specific responses folder
   (reference layout: `D:\Nokia\Simulator\ssh-simulator\responses\UDM\*.txt`, referenced
   from config as `file: "UDM/<FILE>.txt"`).
3. **Start simulator server(s)** on separate ports: NIAM-fronted node server(s), a repo
   server when the UC has a backup phase, etc. LOCAL node commands run natively and need
   no port.
4. **Step 9** runs the execution workflow with `cli-automation-standalone.jar execute`
   against those servers.

## Known Issues (found 2026-11-02, evidence: run `b61b977c`)

| ID | Sev | Issue | Evidence |
|----|-----|-------|----------|
| S8-01 | Critical | **Fixed 2026-11-02 (overlay workflow, T050/T051).** Step 9 never targets the simulator. `simulator_host`/`simulator_port` are accepted by `run_execution_test()` but never passed to the JAR. The workflow's `nodes.<N>.ssh.host` is `${node_row.<N>.NIAM_NAME1}` port 22, so execution resolves to **real NIAM hosts** from the CIQ. | `skills/network_config_test/jar_executor.py:122-200`; workflow `nodes.RDS.ssh` |
| S8-02 | Critical | All nodes collapse into one config. The prompt asks for one config per node separated by `---`; `clean_yaml_response()` keeps only the first document (a497018); `write_config_files()` writes a single `<uc>.yaml`. Run b61b977c config has only `oamsys@niamserver`: no PGW commands, no `sdfrun`/`provgw` users. | `autofix.py:26-30`, `simulator_runner.py:229-231` |
| S8-03 | Critical | Step 8 always fails validation. `_validate()` requires a `servers`/`nodes`/`simulator`/`ssh_servers` key, but the MockWire format the prompt asks for has none → `"No server definitions found"`. Same root cause makes `_extract_ports()` always `[]`, and `_fix_port_collisions()` / `_ensure_all_nodes()` are no-ops. | `skill.py:330-359`; step_8_error.json of b61b977c |
| S8-04 | High | Sanity check is not SSH. It opens a raw TCP socket, matches the prompt regex against the `SSH-2.0-…` version banner, and writes shell text into the SSH protocol stream. Results are meaningless. | `sanity_check.py:110-200` |
| S8-05 | High | Cross-run pollution. Configs are copied into the shared `lib/mockwire/mockwire-simulator/config/`, and every non-builtin YAML there is started (stale UCs included). Response files are written to `run_dir/simulator/outputs/` but MockWire reads `outputs/<name>/responses/` relative to the **JAR dir**, so `file:` responses are never found. `lib/mockwire` is left dirty. | `simulator_runner.py:83-101`; `git status` shows `m lib/mockwire` |
| S8-06 | High | Process lifecycle leaks. The simulator is only stopped by Step 9. Step 8 failure, cancel, crash, or Step 8 re-run leave the process running; a re-run binds the same port and exits with code 1 (likely cause of b61b977c's `exited immediately (code=1)` — unconfirmed because of S8-09). stdout/stderr are `PIPE` and never drained, so a long run can block when the buffer fills. No `--ttl-minutes` backstop. | `simulator_runner.py:118-136`; `network_config_qa_agent.py:121` |
| S8-07 | High | **Partly fixed 2026-11-02** (recorded real outputs served verbatim). Phase 2 (command → response) is a stub returning `{}`. All outputs are LLM-invented inline in Phase 1, without per-node response folders. | `skill.py:263-279` |
| S8-08 | Medium | Retry path is broken. `regenerate()` only re-generates YAML: it doesn't write files, restart the simulator, or re-run sanity. The skill instance is stashed in ADK session state (unreliable, research R6). Step 9 then runs against the old or no process. | `skill.py:203-237`; agent `:46-73` |
| S8-09 | Medium | Root cause hidden. MockWire output is truncated to 500 chars in the error (`stderr[:500]`), and the step error message is cut off before the real exception. | `simulator_runner.py:151,164` |
| S8-10 | Medium | Prompts, escalation, and NIAM aren't modelled. The workflow expects `rdsggn01:~ #`, but the simulator prompt is `[oamsys@niamserver ~]$`. `su - sdfrun` / `su - provgw` have no `pushPrompt`. There's no `niam:` section, although the workflow sets `ssh.ne_id` and the vendor profile has `access_gateway`. | b61b977c workflow vs simulator-config |
| S8-11 | Medium | Placeholder commands are left to the LLM. Commands contain `${configData…}`, `${env.ORDERNO}`, `${register…}` (and malformed `${{…}}`), and the LLM guesses `regex:` keys. They should be derived deterministically from the CIQ JSON data the JAR will substitute. | workflow `activity_configuration` steps |
| S8-13 | High | `extract_workflow_commands()` read `cmd`/`command` from list-style phases, but workflows use `send:` and a phase map, so the LLM prompt never saw a single workflow command. **Fixed in Phase 1.** | `autofix.py` |
| S8-12 | Low | `config/pipeline_config.yaml` `simulator.endpoint/port (8080)` is unused. The `mockwire_jar` default path depends on CWD. `LOCAL` is included in `extract_workflow_nodes()` and would get a stub server. | `pipeline_config.yaml:25-27`, `autofix.py:217-247` |

### Upstream dependencies (not Step 8 code, but block Step 9 against the simulator)

| ID | Issue | Owner |
|----|-------|-------|
| DEP-1 | Workflow references `${node_row.RDS.NIAM_NAME1}` but CIQ JSON nodes expose `RDS.niamID-1` | Step 6 (`network_config_gen`) |
| DEP-2 | `execute --node` is passed `node_type` (`PGW_RDS`) instead of a node / node-group name | Step 9 (`jar_executor.run_execution_test`) |
| DEP-3 | Malformed `${{…}}` placeholders in generated workflow (`${{env.ORDERNO}}`, `${{configData…}}`) | Step 6 autofix |
| DEP-4 | `su`/`exit` steps validate the prompt, but the JAR strips prompts from output, so they can never pass (found in spike) | Step 6 |
| DEP-5 | The `sar 1 10` regex expects 5 numeric columns; real output has 6 (found in spike) | Step 6 — **Fixed** (regexes checked against recorded real output) |
| DEP-6 | `${configData.tables.'Profile Modification'.'Test IMSI'}` resolves to empty in `execute` (`-b "imsi=,dc=IMSI…"`) and stays unresolved in the MOP. Placeholder path doesn't match the CIQ JSON. | Step 6 |
| DEP-10 | With `elevate_to_root: true`, node `prompt_regex` must also match the login prompt, else "node prompt not reached after interactive login" (found 2026-11-02 with the new MockWire) | Step 6 / step 9 overlay — **Fixed**: overlay extends `prompt_regex` with the simulator prompts |
| DEP-11 | Workflow calls helper scripts with arguments they don't accept (`--refinmocName` vs script `--refinmoc_name`, `--pdn_type` vs `--packet_data_network_type`) and `--output_file '.xml'` because `${ORDERNO}` is never registered — step 6 and helper_script_gen must agree on the CLI contract (found 2026-11-02 by `test_step.py --agents 8,9`) | Step 6 / helper_script_gen — **Fixed** (CLI + output contract; DEP-14) |

### Step 6 placeholder & regex findings (2026-11-02 JAR probes; fixed in `network_config_gen` autofix + prompt)

Verified with `generate`/`execute` probe workflows against run b61b977c's CIQ JSON:

| Syntax | Resolves? |
|--------|-----------|
| `${configSequences.0.records.0.data.<Column>}` (dot-numeric index, spaces OK) | Yes. This is the only path to CIQ record values (`[0]` index does not work) |
| `${<NODE>.niamID-<n>}` | Yes (DEP-1 fix) |
| `${node}` `${nodeGroup}` `${crGroup}` `${email}` `${NODE_TYPE}` `${SUB_ACTIVITY_NAME}` | Yes |
| `${name}` for `--var name=…` and for registered `(?<name>…)` groups | Yes |
| `${ENV.NAME}` | Yes (OS env) |
| `${configData.…}`, `${nodeGroups.…}`, `${node_row.…}`, `${register.…}`, `${env.…}`, `${{…}}` | **No.** Empty in `execute`, left raw in the MOP |
| Python `(?P<name>…)` / Java names with `_` | **PatternSyntaxException** (step fails) |
| `register: {regex: 'X=(\d+)', name: v}` (unnamed group) | Registers nothing |
| `when: "configSequences.0.records.0.data.Col != ''"` (bare operand) | Works |
| `when: "${…} != ''"` (interpolated operand) | Wrong result in both directions (JAR quirk) |
| `for_each: ${configSequences.0.records}` | NullPointerException in the JAR. Only `records.0` is usable (DEP-9). |

| ID | Status |
|----|--------|
| DEP-1 | **Partly fixed**: `${node_row.X.NIAM_NAMEn}` → `${X.niamID-n}`. `NIAM_USER` is not in the CIQ JSON; the validator flags it (needs a literal user or `${SECRET.…}`). |
| DEP-3 | **Fixed**: `${{…}}` → `${…}` |
| DEP-4 | **Fixed**: `su`/`exit` steps get step-level `prompt_regex` (from their validation regex) and no output criterion. E2E: `su - sdfrun`/`exit` pass, and the whole health-check phase now runs (16 commands vs 4). |
| DEP-6 | **Fixed**: `${configData.tables.<Sheet>.<Col>}` → `${configSequences.0.records.0.data.<Col>}`. E2E: NORTH sends `imsi=404530000000132` (was empty). EAST has no IMSI **in the CIQ** (it uses MSISDN), so the prompt now teaches a `when` guard for optional columns. |
| DEP-7 | **Fixed** (new): regexes converted to Java named groups (`pre_ldap_result` → `preLdapResult`); `name:` with an unnamed group or a single named group becomes the group name; references are rewritten. |
| DEP-8 | **Fixed** (new): `${register.x}` / `${env.x}` → `${x}` |
| DEP-9 | **Open** (new): records beyond the first per node group are not executed (`for_each` NPE in the JAR). NORTH has 2 subscriber rows. JAR issue / UC design decision. |

Step 6's validator now reports every placeholder the JAR cannot resolve, so step 6 fails with actionable feedback instead of step 9 silently sending empty values. Remaining on b61b977c (LLM content errors, fed back via regenerate): `${node_row.*.NIAM_USER}`, `${ORDERNO}`, `${pre_ldap_result}`/`${post_ldap_result}` never registered, and a whole-sheet `${configData.tables.'Profile Modification'}`.

DEP-4 was worse than first thought: the `su - sdfrun` step is "On failure: Execution will stop", so its prompt-validation failure aborts the whole pre-health-check phase (2026-11-02 Phase 2 E2E). DEP-1 is confirmed by the MOP: `niamId: ""`, `address: ""` for every node.

DEP-2 was **confirmed** (Step 9 had never executed a command: `node not found: PGW_RDS`, research.md F8). **Fixed 2026-11-02**: `run_execution_test` runs once per zone × CR group from the CIQ JSON, with reports in `exec_output/<crGroup>/`.

## Requirements

### Functional

- **FR-S8-01**: Step 8 MUST derive the simulator **topology** deterministically (no LLM) from: workflow `nodes` (excluding `type: local`), CIQ JSON node data (niamIDs), requirements Cat 1 (users, prompts, escalation), and vendor profile (`access_gateway`, node types).
- **FR-S8-02**: Step 8 MUST produce one server config per simulated endpoint plus a per-node responses folder under a **per-run** directory: `data/runs/<run_id>/simulator/{config/, responses/<NODE>/, logs/, manifest.json}`. Nothing is written into `lib/`.
- **FR-S8-03**: Step 8 MUST start a **repo server** only when the workflow has a backup/transfer phase (sftp/scp/backup commands to a repo node).
- **FR-S8-04**: Every remote workflow command MUST have a response. Responses MUST be sourced in priority order: (1) built-in catalogue (`su`, `exit`, common health checks), (2) outputs captured in uploaded SSH session logs, (3) LLM, constrained by the step's `validation` criteria.
- **FR-S8-05**: Placeholders MUST be resolved from the CIQ JSON data the JAR will use (`${configData…}`, `${nodeGroups…}`). Runtime-only values (`${register…}`, `${env…}`) MUST become deterministic `regex:` keys.
- **FR-S8-06**: Each generated response MUST be checked offline against the workflow step's `validation` criteria before start. Mismatches are reported per command.
- **FR-S8-07**: Prompts and escalation MUST match the workflow: login prompt matches `nodes.<N>.prompt_regex`; `su - <user>` uses `pushPrompt`; `exit` pops back. NIAM neId flow is enabled with the node's niamIDs when the workflow sets `ssh.ne_id`.
- **FR-S8-08**: Ports MUST be allocated dynamically (free-port probe) and recorded in `manifest.json`.
- **FR-S8-09**: Simulator processes MUST be registered in a module-level registry (`shared.state`) keyed by run_id. They MUST be stopped on Step 8 re-run (before restart), pipeline completion, failure, cancel, and server shutdown. They MUST be started with `--ttl-minutes` as a backstop. Output MUST go to log files, not pipes.
- **FR-S8-10**: Sanity checks MUST use a real SSH client: password login → NIAM neId (if enabled) → prompt match → escalation `pushPrompt` → first UC command per node. The result is reported per node in an HTML sanity report (Step 8 milestone artifact).
- **FR-S8-11**: Step 8 MUST emit a **simulator overlay workflow** (`<uc>_sim.yaml`): a copy of the workflow with every remote `nodes.<N>.ssh.host/port/user/password` pointed at its simulator instance. Step 9 MUST execute the overlay, never the production workflow.
- **FR-S8-12**: On a Step 9 → Step 8 retry, Step 8 MUST regenerate only the failing commands' responses, rewrite files, restart the affected instances, and re-run sanity. State is rebuilt from disk, not ADK session state.
- **FR-S8-13**: Errors MUST include the full simulator log path and the last 50 lines of its log.

### Success Criteria

- **SC-S8-1**: For DSR_1058 / PGW_RDS_1058, Step 8 passes and sanity is 2/2 nodes with prompt + escalation + UC command PASS.
- **SC-S8-2**: Step 9 executes the overlay workflow and every SSH connection in the exec log goes to `localhost:<sim port>` (0 connections to CIQ IPs).
- **SC-S8-3**: 3 consecutive pipeline runs plus 2 Step 8 re-runs leave 0 orphan simulator processes and `lib/mockwire` unmodified.
- **SC-S8-4**: ≥90% of workflow commands get deterministic (non-LLM) responses for the reference UC.

## Tasks

See [plan.md](plan.md) § Phases.

## Generation contracts (DEP-5 / DEP-11), 2026-11-02

Fixed in the generators, not in run artifacts, and nothing is UC-specific:

| Fix | Where |
|-----|-------|
| Uploaded `.txt`/`.log` transcripts → `doc_extracted.command_samples` (command, output, prompt); any prompt style | `requirement_gathering/parsers/command_output_parser.py` |
| Step 6 validator: success and register regexes must match the recorded output for the same command (`${…}` in `send` is a wildcard); su `prompt_regex` must match the next recorded prompt (DEP-15); JAR semantics mirrored (find, MULTILINE, no DOTALL, CRLF) | `network_config_gen/validator.py` |
| Step 6 self-correction: up to 2 regenerate rounds with the validator errors and real-output excerpts; best version kept (invalid YAML ranks worst, G-2) | `network_config_gen/skill.py` |
| Helper scripts: argparse flags = all flags the workflow passes (CLI contract); success message must match the step's regex (output contract); 1 retry, else step 6 fails | `helper_script_gen/skill.py` |
| Framework runtime parameters (`${ORDERNO}`) declared in `execution.runtime_params`; not errors in step 6, passed as `--var` in step 9 (DEP-14) | `core/config.py`, `jar_executor.py` |
| Quoted CIQ columns unquoted (DEP-12); `${…}` in regexes → `.+?` (DEP-13) | `network_config_gen/autofix.py` |
| Step 8 serves recorded real output verbatim for commands without placeholders (S8-07 partly); su step's own `prompt_regex` wins (S8-15) | `network_simulator_setup/skill.py`, `topology.py` |

JAR probes behind DEP-12/13 and the regex semantics: a quoted column resolves to empty while the unquoted form resolves (`404530000000132`); `${…}` in a validation regex raises `PatternSyntaxException: Illegal repetition`; validation regexes use `find()`, `^`/`$` match per line, and `.` does not cross lines.

End to end with the real LLM (`python test_step.py --agents 6,8,9 --run-id b61b977c… --transcripts <RDS/PGW health checks> <RDS log>`):

| | Before | After |
|-|--------|-------|
| Step 6 validation errors | not checked against real output | 13 → 8 → 2 by self-correction |
| Helper scripts | flag names invented, messages hard-coded | flags + messages match the workflow |
| Step 8 | all responses LLM-invented | 6 recorded real outputs served |
| Step 9 | 20 commands, `sar`/scripts/`${ORDERNO}` failing | 27 commands, 21 succeed, 0 unmatched |

Remaining: the `provgw status` regex (flagged by step 6, and the step 9 → 6 retry gets the real output), EAST has no IMSI (DEP-16, data), and the simulator does not create `outbox/<ORDERNO>.xml.res` (S8-16, Phase 3 VFS triggers).

## Simulator Gaps

Features our UCs need that MockWire lacks (MW-01 to MW-14), with evidence, workarounds and proposed changes: [mockwire-gaps.md](mockwire-gaps.md).

## Agent-level test 2026-11-02 (`python test_step.py --agents 8,9 --run-id b61b977c… --refix-workflow`)

The real ADK agents for steps 8 and 9 run on a copy of the run (`data/step_tests/`). Bugs found and fixed:
- **S8-14** (High): `run_dir` is relative in the pipeline (`./data/runs/<id>`) and MockWire runs with `cwd=simulator/`, so `--config` resolved twice and no instance started. Fix: absolute simulator dir; regression test with a relative run dir.
- **S9-01** (High): LOCAL steps (`python create_xml.py …`) ran in the pipeline's cwd, not `run_dir/scripts/`, so "can't open file". Fix: `execute` runs with `cwd=run_dir/scripts`, and all JAR arguments are absolute.
- **S9-02** (Medium): with the event log present, a non-simulator failure (LOCAL script) fell back to "simulator". Fix: missing responses always show as `unmatched` events, so other failures go to the workflow.

Final result: step 8 passed (2/2 nodes, elevation sanity); step 9 ran 20 commands for EAST and NORTH via the overlay; classification workflow → step 6. Remaining issues, all outside steps 8/9:
- DEP-5: `sar` regex
- DEP-11: helper script arguments
- EAST CIQ has no IMSI
- LLM `ndsLoadStats` response sometimes separates the two lines the regex expects adjacent (Phase 3 T036 offline check)
