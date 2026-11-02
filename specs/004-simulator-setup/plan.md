# Implementation Plan: Step 8 — Network Simulator Setup (Rework)

**Spec**: [spec.md](spec.md) | **Date**: 2026-11-02

## Target Design

```
workflow.yaml ─┐
CIQ JSON data ─┼─► [8.1 Topology builder]  (deterministic)
requirements ──┤        │  instances: node-server(s) [+ repo server], users, prompts,
vendor profile ┘        │  escalation, NIAM neIds, ports (free-port probe)
                        ▼
               [8.2 Command catalogue]  (deterministic)
                        │  per remote command: resolved text / regex key,
                        │  node, phase, validation criteria
                        ▼
               [8.3 Response builder]   builtin → SSH-log → LLM (only gaps)
                        │  + offline check vs validation criteria
                        ▼
               [8.4 Writer]  data/runs/<id>/simulator/
                        │    config/<instance>.yaml
                        │    responses/<NODE>/<CMD>.txt
                        │    manifest.json   <uc>_sim.yaml (overlay workflow)
                        ▼
               [8.5 Lifecycle]  stop old → start (--ttl-minutes, logs to file)
                        │        → wait-for-port → registry[run_id]
                        ▼
               [8.6 SSH sanity]  login → neId → prompt → su → UC cmd → HTML report
                        ▼
Step 9: cli-automation execute --yaml <uc>_sim.yaml  ──►  localhost:<ports>
```

The LLM is used **only** in 8.3 for commands with no builtin/log output, and on Step 9 → 8 retries.

## Decisions Needed

| ID | Question | Options | Recommendation |
|----|----------|---------|----------------|
| D1 | Which simulator JAR? | (a) bundled MockWire `lib/mockwire` (Aug 2026, NIAM neIds + keyboard-interactive, CLI only `--name/--port/--ttl-minutes`, paths derived from JAR location); (b) reference `D:\Nokia\Simulator\ssh-simulator` (`--config --responses --instance --port --ttl-minutes`, Apr 2026, older NIAM model) | **(a)** + run it from a per-run copy of the JAR (`run_dir/simulator/mockwire.jar`, 7.7 MB) so `config/` and `outputs/<name>/responses/` resolve per run, with no JAR change. Later, add `--config/--responses` to MockWire upstream. |
| D2 | NIAM topology | (a) one NIAM-fronted instance per node type (RDS, PGW…), each with its own prompts and commands; (b) one NIAM gateway instance for all nodes, selected by neId | **(a)**: MockWire has one command set and one prompt per instance, so per-node instances are the only way to get distinct prompts and outputs. |
| D3 | Sanity SSH client | (a) Python `paramiko` (new dependency); (b) run `cli-automation execute` on a generated mini sanity workflow | **(a)** for fast per-check diagnostics, then (b) as a final smoke test in Phase 5. |

**Decided 2026-11-02 (project owner):** D1 = (a) bundled MockWire from a per-run JAR copy; D2 = (a) one NIAM-fronted instance per node; D3 = paramiko + final JAR smoke test. Start with the Phase 0 spike.

## Phases

### Phase 0: Spike: prove the end-to-end connection (blocking, ~0.5 day)
- [X] T001 Hand-write a 2-instance MockWire setup (RDS, PGW) with NIAM neIds, `pushPrompt` on `su`, and a 3-command overlay workflow. Run `cli-automation-standalone.jar execute` against it. **Done 2026-11-02: path works, 3/7 pass; the other 4 are Step 6 defects (DEP-4, DEP-5).**
- [X] T002 Confirm running MockWire from a per-run JAR copy resolves `config/` and `outputs/` from that dir (D1). **Confirmed (F1).**
- [X] T003 Record findings in `research.md` (F1–F10). **Gate passed:** the overlay design holds. Required extras: `strict_host_key_checking: "no"`, `terminalMode: PLAIN`, `stty`/sentinel entries, `exit` popPrompt.

### Phase 1: Plumbing and lifecycle (fixes S8-03, S8-05, S8-06, S8-09, S8-12, S8-13): done 2026-11-02

Done ahead of plan: T032 runtime entries (`terminalMode: PLAIN`, `stty -echo`, `exit` popPrompt) in `autofix.ensure_runtime_entries`. T051 part: `--node <zone> --cr <crGroup>` loop (DEP-2). Tests: `tests/test_simulator_setup.py` (10 pass, including real MockWire start, stop and port conflict).

- [X] T010 `simulator_runner.py`: per-run layout `run_dir/simulator/`; copy the JAR there; never write into `lib/`; start only this run's instances.
- [X] T011 Free-port allocator (bind probe, base 22200) and `manifest.json` (instance, node, port, users, neIds, pid, log path).
- [X] T012 Stream stdout/stderr to `simulator/logs/<instance>.log` (no PIPE); pass `--ttl-minutes 60`.
- [X] T013 `SimulatorRegistry` in `shared/state.py` (`simulator_processes: dict[run_id, SimulatorProcess]`). Stop the previous run's processes before start. Stop in runner `finally` (complete, fail, cancel) and on app shutdown.
- [X] T014 Replace `_validate()`/`_extract_ports()` with a MockWire-format validator: reserved keys; every command has `lines|file|responses`; `file:` targets exist; users have prompts.
- [X] T015 Errors include the log path and last 50 log lines (no `[:500]`).
- [X] T016 Remove the dead `simulator.endpoint/port` config, or wire it as the bind host. Resolve the JAR path from the project root.
- [X] T017 Investigate the 18 s first-command delay and the `estty -echo` stray char (F9). **Root cause: unsupported Ctrl+P,E elevation (MockWire gap MW-01). Workaround: root prompt at login + overlay `elevate_to_root: false` (T050).**

### Phase 2: Topology and server configs (fixes S8-02, S8-10, S8-12): done 2026-11-02

`topology.py` builds one `NodeSpec` per remote node: the root prompt (post-elevation, MW-01), `su` → `pushPrompt` taken from step validation / requirements / `prompt_regex`, NIAM neIds from every zone in the CIQ JSON, and `transfer:` for sftp/scp nodes. The LLM now writes command responses only, one call per node (`prompts.NODE_RESPONSES_*`), and deterministic entries always win. Parsing repairs LLM YAML (fences, `---` merge, double-quoted regex keys, truncation). A step 9 → 8 retry re-runs `setup()` with failed commands read from `step_9_error.json` + `exec_output/` (part of T052). Tests: `tests/test_simulator_topology.py` (7, including a real SSH flow: NIAM neId → root prompt → `su` push → command → `exit` pop).

Real-LLM E2E on run b61b977c: step 8 builds RDS (9 entries) and PGW (7 entries) instances in 13 s. Step 9 executes against them for EAST and NORTH (7 s). Remaining failures are DEP-4 (the `su` step aborts the health-check phase), DEP-6 (IMSI placeholder resolves empty) and regex keys that don't match the sent command (Phase 3).
- [X] T020 `topology.py`: build instances from workflow `nodes` (skip `type: local`), CIQ JSON niamIDs, requirements Cat 1, and vendor profile. Add a repo-server instance when backup/transfer commands are detected.
- [X] T021 Per instance, a deterministic `config:` / `auth:` / `niam:` section. Login user and prompt must satisfy `nodes.<N>.prompt_regex`. Escalation users come from workflow `su - <user>` steps.
- [X] T022 Drop the multi-doc `---` stripping and single-file writer. One `config/<instance>.yaml` per instance.

### Phase 3: Commands and responses (fixes S8-07, S8-11)
- [ ] T030 `command_catalog.py`: build the catalogue from step 7's MOP HTML (`mop_output/*.html` → `const MOP_GROUPS = [...]`: per zone, each command with `target` node, phase, validation and execution criteria, rendered by the same JAR as `execute`). Fall back to workflow steps when no MOP exists. Commands still containing `${…}` in the MOP are unresolved in the JAR too (DEP-6): report them and key them by regex.
- [ ] T031 Placeholder resolver: substitute `${configData…}` / `${nodeGroups…}` from the CIQ JSON (first CR group). Convert `${register…}` / `${env…}` / leftovers into escaped `regex:` keys. Tolerate `${{…}}` (DEP-3).
- [ ] T032 Builtin catalogue: mandatory per instance: `terminalMode: PLAIN`, `stty -echo` (F5, F6; the `__CMD_DONE__` marker is native to MockWire). Escalation: `su - X` → `pushPrompt`, `exit` → `popPrompt` (F7). Common commands: `sar`, `ss`, `ndsLoadStats`, `ls -l`, `sftp put`.
- [ ] T033 SSH-log harvester: map uploaded session-log command → output pairs (reuse the requirement_gathering SSH-log parser) into responses.
- [ ] T034 LLM fill only for remaining commands. Batch per node; the prompt includes the validation criteria; output is response text only, not YAML.
- [ ] T035 Write `responses/<NODE>/<CMD_SLUG>_<PHASE>.txt` and reference them via `file:` (supports pre/post-check variants via `responseStrategy: SEQUENTIAL`).
- [ ] T036 Offline validator: run each step's validation (regex/expr) against its response and report mismatches per command.

### Phase 4: SSH sanity and report (fixes S8-04): done 2026-11-02

paramiko session per node (in parallel): login → NIAM neId → login prompt vs `prompt_regex` → `stty -echo` + `__CMD_DONE__` round trip → each `su` pushes its prompt and `exit` pops back → first placeholder-free UC command answered (not "command not found"). `<uc>_simulator-report.html` (instances, sanity per node with session transcripts, issues, MockWire limitations) is now the step 8 milestone artifact, with the configs as extras. LLM responses are hardened: one retry with an "invalid YAML" note, per-entry salvage, raw answers kept in `simulator/logs/llm_<node>_raw.txt`. A node without responses no longer aborts before the report. **Real-LLM run on b61b977c: step 8 PASSED (18 s); RDS and PGW 6/6 checks each.**
- [X] T040 Rewrite `sanity_check.py` with a real SSH client (D3): password auth → neId → prompt → `su` pushPrompt → first UC command, per instance.
- [X] T041 `<uc>_simulator-report.html`: instances/ports table, per-command response source (builtin/log/LLM), offline validation results, and sanity results. Make it the Step 8 milestone artifact, with `simulator-config` files as extras.

### Phase 5: Step 9 integration and retry loop (fixes S8-01, S8-08)
- [ ] T050 Emit the overlay workflow `<uc>_sim.yaml` (`ssh.host=localhost`, `port=<instance port>`, simulator credentials, `strict_host_key_checking: "no"`, literal `ne_id`) (F2, F3).
- [ ] T051 `jar_executor.run_execution_test`: use the overlay, remove the dead `simulator_host/port` parameters, fix `--node` to loop every zone × CR from the JSON (`--node EAST --cr CR1`, …) and merge the reports (DEP-2, F8). Pick up ports from `manifest.json`, not session state.
- [ ] T054 Raise DEP-4 (su/exit prompt validation) and DEP-5 (sar column regex) against Step 6 prompts/autofix, so Step 9 → 6 retries can fix them.
- [ ] T052 `regenerate(feedback)`: rebuild only the failing commands' responses, rewrite files, restart the affected instances, and re-run sanity. Load context from `run_dir`, not ADK state.
- [ ] T053 Final smoke test: the cli-automation `execute` on a generated 1-command-per-node workflow.

### Phase 6: Tests and tooling
- [ ] T060 Unit tests: topology builder, placeholder resolver, catalogue, validator, port allocator (`tests/test_simulator_setup.py`).
- [ ] T061 Integration test: start real MockWire from a fixture, SSH sanity, stop, and assert no orphans.
- [ ] T062 `test_step.py --skill network_simulator_setup --run-id <id>` reuses an existing run's workflow and JSON.
- [ ] T063 Update `bugs.md` (001) with S8-01…S8-12 and CLAUDE.md "Step 8" notes.

## Order and Effort

Phase 0 → 1 → 2 → 3 → 4 → 5 → 6. Phases 1–2 can start in parallel with Phase 0 T002.
Rough effort: P0 0.5d, P1 1d, P2 1d, P3 1.5–2d, P4 0.5d, P5 1d, P6 0.5d. Total about 6–7 days.
DEP-1 and DEP-3 (Step 6) must be fixed before SC-S8-2 can pass.
