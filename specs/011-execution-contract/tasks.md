# Tasks: Execution contract (spec 011)

## Step 1: Probes (cli-automation JAR) → research.md
- [ ] E001 Phase `when` reads a flag set by an earlier step's branch `vars`
- [ ] E002 Branch `vars` with `${var}` values (`ROLLBACK_REQUIRED: '${CHANGES_MADE}'`)
- [ ] E003 Failure branch `vars` applied when a step fails on its exit code / timeout (no regex reached)
- [ ] E004 Activity `on_failure: stop`: do later phases still run (with `when`)? Is `then:` needed for Rollback?
- [ ] E005 `--var ROLLBACK_ONLY=true` overrides `globals.vars`
- [ ] E006 `register` `loop` + `count_var` and `expr` with `==`, `>`, `&&` on counts

## Step 2: Contract in step 6 (deterministic)
- [ ] E010 `structure.py`: flags in `globals.vars`; phase `when` per the table; step `on_failure` per phase; failure /
  success branch `vars`; change detection (`CHANGES_MADE`); dependants of `su` / used registers
- [ ] E011 Prompt: phases and the contract (the LLM writes the steps, structure applies the rules)
- [ ] E012 Validator: contract present; tests
- [ ] E013 Step 9 passes `ROLLBACK_ONLY` (`execution.runtime_params`) and runs a rollback-only case

## Step 3: Text, not patterns (step 1 + step 6)
- [ ] E020 Step 1 prompt: ask for the expected text (+ scope every/one), never a regex; de-duplicate questions by text across rounds (PGW Q-1)
- [ ] E021 Step 1 prompt: remove the old data-model placeholders (`${nodeGroups}`, `${node_row…}`, `${configData}`) it teaches (PGW Q-4)
- [ ] E022 Step 6: build check regexes from the text + real output; "every" scope → count pattern; regex-looking answers are hints
- [ ] E023 Tests

## Step 4: Check
- [ ] E030 MRF + PGW re-run: success run (no rollback), injected activity failure (rollback runs only after a change), precheck failure (nothing after it), `ROLLBACK_ONLY` run
