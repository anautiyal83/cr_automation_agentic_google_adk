# Tasks: Workflows in production format (spec 013)

Stages ship separately; each ends with EIR / MRF / PGW re-generated, MOP `generate`, step 9 on the simulator and the
full test suite. Generic only — nothing keyed to a use case.

## Stage A — file shape (no change to what runs)

Verified 2026-11-11: EIR / MRF / PGW run workflows converted — MOP `generate` gives the same commands, execution
criteria, validations and targets as before; JAR probes: `${…}` = plain form in 18 `when` + 4 `expr` cases, step
without `on_failure` stops (so `on_failure: stop` is explicit), rollback named `ROLLBACK_CONFIGURATION` still runs
after a stop.

- [x] PF001 `dump_yaml`: multi-line `send` as `>-`, no double-quoted scalar with a backslash (PF-D7, P-8); test
- [x] PF002 `structure.apply`: `version: 1.2`, `name` (kebab of the UC), `globals.defaults`, production `globals.vars`
  (`ROLLBACK_REQUIRED`, `ROLLBACK_ONLY`, `RETRY_COUNT`, `LOOP_RETRY`, …); phase keys / `name` / `description` /
  `when` / `on_failure` as the production table; field order of steps
- [x] PF003 `${…}` expressions everywhere: autofix rewrites bare `when` / `expr` (`X != ''`, `a == b`) to
  `${X != ""}` / `${a == b}`; validator, workflow_table (spec 012) and edit_checks read both
- [x] PF004 Drop Windows-only rules from step 6 (PF-D6): no rewrite / rejection of `"` in LOCAL steps
- [ ] PF006 Step 9 under WSL (PF-D5): run the cli-automation JAR in WSL (Linux Java), paths translated, simulator reachable
  from WSL; Settings → External Modules option; needs `wsl --install` + Java in WSL on the machine
- [~] PF005 Validation shape: `enabled: true` + `description` on every validation (done); `warning` + `vars` where the
  step feeds a retry and the error capture register come with the patterns (Stage C) and the prompt (Stage D)

## Stage B — node model (PF-D2) — steps 6 + 8

- [ ] PF010 Step 1/6: hosts reached from the NIAM login (pods, DS servers, other shells) become nested sessions on one
  `niam_<type>_server` node (enter / steps / `exit`), generic prompts; one node per NIAM entry of the CIQ
- [ ] PF011 Validator: reject `for_each` outside `type: loop`, self-comparing expressions, a step reading a file on a
  host where no earlier step put it, a node switch right after `oc exec` / `ssh`
- [ ] PF012 Step 8 MockWire topology: one instance per NIAM node with nested shells (enter command → new prompt,
  `exit` → back), `repo_server` ssh + sftp; MockWire repo change if nested sessions need it (commit + push there,
  rebuild `lib/mockwire.jar`)
- [ ] PF013 Step 9 overlay: production node names → simulator instances

## Stage C — runtime parameters + file routes (PF-D3, PF-D4 — after the user confirms)

- [ ] PF020 `execution.runtime_params`: `NIAM_IP`, `M2MPORT`, `M2MUSER`, `M2MPASSWORD`, `REPO_USER`, `REPO_PASSWORD`,
  `ORDER_NO`, `CHILD_REQ_ID`, `PARENT_REQ_ID`, `NODE_NAME` (test values for step 9)
- [ ] PF021 `structure.backups_by_convention`: `BACK_UP_LOCATION_REPO` + `/BACKUP/`, `/Activity/`, `/Rollback/`
- [ ] PF022 Transfers: production checksum + `LOOP_RETRY` loop + guard; node ↔ repo `printf "put|get …" | sftp` with
  `expect_reply`; repo IP by IPv4/IPv6
- [ ] PF023 Input files: `LOCAL_PATH` presence check → repo → node (no GitLab, PF-D4)
- [ ] PF024 Rollback-only: rollback re-runs the lookups it needs (`when: ${ROLLBACK_ONLY == "true"}`)

## Stage D — prompt + docs

- [ ] PF030 Step 6 prompt: production-style examples (from the corpus, generic), the patterns of production-style.md
- [ ] PF031 CLAUDE.md / README / jar facts; bugs.md entries for the probes (P-6, P-8)
- [ ] PF032 Re-generate EIR, MRF, PGW; compare with production; update uc-*.md
