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
- [x] PF006 Step 9 under WSL (PF-D5) — done 2026-11-12 (`execution.wsl_distro`, `jar_executor.in_wsl`; Ubuntu-24.04,
  Java 8 — the JAR needs Nashorn, gone since Java 15 —, python-is-python3, `.wslconfig` networkingMode=mirrored).
  PGW 2/2, MRF 4/4 under WSL; EIR same as on Windows (logic gaps). Was: run the cli-automation JAR in WSL (Linux Java), paths translated, simulator reachable
  from WSL; Settings → External Modules option; needs `wsl --install` + Java in WSL on the machine
- [~] PF005 Validation shape: `enabled: true` + `description` on every validation (done); `warning` + `vars` where the
  step feeds a retry and the error capture register come with the patterns (Stage C) and the prompt (Stage D)

## Stage B — node model (PF-D2) — steps 6 + 8

Rules (from the corpus, generic — any UC):
- **B-R1 One node per NIAM login.** A remote node of the CIQ (node type `<type>`, its NIAM entries `niamID-1…n`)
  is `niam_<type>_server` (one entry) or `niam_<type>_1…n` (several); `ssh.host ${NIAM_IP}` etc. are Stage C, the
  `ne_id` keeps our CIQ reference (PF-D1). Generic prompt `(?m)\[[^\]\r\n]+\][#$][^\r\n]*$` unless the node type's
  prompt is not a Linux shell (vendor profile / recorded login prompt decides).
- **B-R2 Nested sessions.** A command that opens an interactive shell on another host — `oc exec|kubectl exec
  [-it] … -- bash|sh`, `ssh [opts] user@host` (no remote command), `telnet`, `su - user` without `-c`,
  `sudo su`/`sudo -i` — runs on the node it is typed on and gets a step-level `prompt_regex` of the new shell
  (`expect_reply` for a password); the following steps stay on that node until the matching `exit`, which gets the
  outer shell's `prompt_regex`. A session left open at the end of a phase gets an `exit`. One-shot forms
  (`su - u -c "…"`, `ssh host cmd`, `oc exec pod -- cmd`) do not nest.
- **B-R3 Failover** (several NIAM entries) — done 2026-11-11 (`failover.py`, step 8 groups): login probe per entry (`echo <TYPE>_LOGIN_OK`, `on_failure: warning`,
  computed register `Target<TYPE>`), later steps on `node: ${Target<TYPE>}` (JAR-probed 2026-11-11).
- **B-R4 Local.** Automation-server steps: `node: local`, not declared; loops carry no `node` — done 2026-11-11
  (`production._local`; MOP of EIR/MRF/PGW unchanged)
- Not in Stage B: repo nodes / file routes (Stage C).

- [x] PF010 Step 6 (B1, 2026-11-11): `sessions.py` — enter commands (`oc/kubectl exec -it … -- bash`, `oc rsh`, `ssh
  user@host`, `telnet`, `su`) by kind; one `niam_<type>_server` per CIQ node type (`node_types` from the CIQ JSON
  template); nodes the LLM made for reached hosts folded in, `exit` added where the LLM went back outside and at
  phase end (not for `su`, which may span phases); sessions per NIAM connection; generic `[..]$` prompts; `-- bash`;
  `ssh -o<Opt>`, password `expect_reply`. Autofix keeps exits of nested shells; `ssh user@${IP}` no longer dropped as
  "framework login"; phases keep `description`. Validator: `${<TYPE>.niamID-n}` against CIQ types, nested hosts
  count as covered. Structure: repo copy / node name per CIQ type of the NIAM node. Prompt: node rules + CIQ types.
  EIR: 1 node, 70 MOP commands (exits + fixed forms); MRF / PGW: same commands on renamed nodes
- [x] PF011 Validator `check_iteration`: `for_each` outside `type: loop`, reading a `loop: true` register's variable
  (empty), self-comparing expressions — with production's fix (one shell `for` loop). EIR: 21 findings; MRF/PGW: 0.
  Still open: a step reading a file on a host where no earlier step put it (EIR diff on the bastion)
- [x] PF012a Step 8 (B2): a NIAM node's instance finds its NIAM IDs and requirement node by CIQ type (else the login
  host); each enter command is a key pushing a prompt its step regex accepts (`ssh`/`telnet`: `Password:` first);
  `exit` stays MockWire's builtin pop; overlay answers `${SECRET.…}` password replies; step 9 never injects faults
  into enter steps. No MockWire change needed (pushPrompt / interactive / builtin exit)
- [ ] PF012 Step 8 MockWire topology: one instance per NIAM node with nested shells (enter command → new prompt,
  `exit` → back), `repo_server` ssh + sftp; MockWire repo change if nested sessions need it (commit + push there,
  rebuild `lib/mockwire.jar`)
- [x] PF013 Step 9 overlay: production node names → simulator instances; a failover group (`niam_x_1…n`,
  `${Target<TYPE>}`) is one instance (NodeSpec.aliases, `file_flows.group_names`)

Simulator (2026-11-11, step 6 output of the last runs re-fixed with Stage B): PGW 2/2 (failover, 6 NIAM nodes),
MRF 4/4 incl. rollback-only; EIR: pod / DS server sessions enter, answer, exit — its remaining failures are logic
gaps of that LLM output (pseudo `for_each` per pod → PF011 now rejects it; rollback-only reads a namespace of a
skipped phase → PF024)

## Stage C — runtime parameters + file routes (PF-D3, PF-D4 — after the user confirms)

- [x] PF020 (C2, 2026-11-12) runtime params by production's names (config + defaults, test values for step 9);
  `${ORDERNO}` → `${ORDER_NO}` (autofix `runtime_param_aliases`); NIAM nodes log in with ${NIAM_IP} / ${M2MPORT} /
  ${M2MUSER} / ${M2MPASSWORD} + production timeouts (`production._niam_nodes`); repo node `repo_server` with
  ${REPO_IP} / ${REPO_USER} / ${REPO_PASSWORD}; `order_number: ${ORDER_NO}`. MRF 4/4, PGW 2/2. Was: `NIAM_IP`, `M2MPORT`, `M2MUSER`, `M2MPASSWORD`, `REPO_USER`, `REPO_PASSWORD`,
  `ORDER_NO`, `CHILD_REQ_ID`, `PARENT_REQ_ID`, `NODE_NAME` (test values for step 9)
- [x] PF021 (C3a, 2026-11-12) `BACK_UP_LOCATION_REPO` global = `<backup_dir>/<UC type>/${CHILD_REQ_ID}/${NODE_NAME}`
  (backup_dir default /repo-server/config/CR_AUTOMATION); backups in `…/BACKUP/<source folder>/`, input files in it
  directly; no hostname lookups. MRF via repo 4/4 (47 steps), files at production's paths. (/Activity, /Rollback:
  evidence zips — not generated yet)
- [ ] PF022 Transfers: production checksum + `LOOP_RETRY` loop + guard; node ↔ repo `printf "put|get …" | sftp` with
  `expect_reply`; repo IP by IPv4/IPv6
- [ ] PF023 Input files: `LOCAL_PATH` presence check → repo → node (no GitLab, PF-D4)
- [x] PF024 Rollback-only (C1, 2026-11-12): `structure.rollback_lookups` — values a rollback reads that only earlier
  phases register are looked up again at its start (`when: ROLLBACK_ONLY == 'true'`), with their own inputs and the
  nested shell they were read in (enter + guarded exit); `nc_rbl*`. EIR rollback-only: 3 → 12 steps ran

## Stage D — prompt + docs

- [ ] PF030 Step 6 prompt: production-style examples (from the corpus, generic), the patterns of production-style.md
- [ ] PF031 CLAUDE.md / README / jar facts; bugs.md entries for the probes (P-6, P-8)
- [ ] PF032 Re-generate EIR, MRF, PGW; compare with production; update uc-*.md
