# Tasks: Execution contract (spec 011)

## Step 1: Probes (cli-automation JAR) → [research.md](research.md) — all pass (2026-11-04)
- [X] E001 Phase `when` reads a flag set by an earlier step's branch `vars`
- [X] E002 Branch `vars` with `${var}` values (`ROLLBACK_REQUIRED: '${CHANGES_MADE}'`)
- [X] E003 Failure branch `vars` applied when a step fails on its exit code / timeout (no regex reached)
- [X] E004 Activity `on_failure: stop`: only phases whose `name` contains "rollback" still run (their `when` respected); no `then:` needed
- [X] E005 `--var ROLLBACK_ONLY=true` overrides `globals.vars`
- [X] E006 `register` `loop` + `count_var` and `expr` with `==`, `>`, `&&` on counts

## Step 2: Contract in step 6 (deterministic)
- [X] E010 `contract.py` (called by `structure.apply`, undone by `flatten`): flags in `globals.vars`; phase `when` per the table; step `on_failure` per phase; failure /
  success branch `vars`; change detection (`CHANGES_MADE`); dependants of `su` / used registers
- [X] E011 Prompt: the pipeline adds failure handling (no flags / `when` / `on_failure` from the LLM); rollback phases named "rollback"; rollback steps must work in a rollback-only run
- [X] E012 Tests `tests/test_execution_contract.py` (phase kinds, change detection E-D3, every rule, idempotence, strip round trip, condition combine); validator unchanged (checks the flat form); JAR `generate` accepts the result (E009)
- [X] E013 Step 9: one rollback-only execution per run (TC-R01, `--var ROLLBACK_ONLY=true`, on a node group whose sunny day passed): only rollback steps ran, they passed, run PASS; phase kinds pre/backup/activity/post/rollback (contract's); negative expectations follow the contract (pre-health: rest of phase runs, nothing later; activity: stop, rollback iff changed; post: reported); report KPI "Rollback only"

- [ ] E014 Rollback-only: rollback steps that read values registered in earlier phases (MRF `backupFileName`) cannot run with `ROLLBACK_ONLY` — validator warning + structure/route support (spec 010 backup location per order)

## Step 3: Text, not patterns (step 1 + step 6)
- [ ] E020 Step 1 prompt: ask for the expected text (+ scope every/one), never a regex; de-duplicate questions by text across rounds (PGW Q-1)
- [ ] E021 Step 1 prompt: remove the old data-model placeholders (`${nodeGroups}`, `${node_row…}`, `${configData}`) it teaches (PGW Q-4)
- [ ] E022 Step 6: build check regexes from the text + real output; "every" scope → count pattern; regex-looking answers are hints
- [ ] E023 Tests

## Step 4: Check
- [ ] E030 MRF + PGW re-run: success run (no rollback), injected activity failure (rollback runs only after a change), precheck failure (nothing after it), `ROLLBACK_ONLY` run
