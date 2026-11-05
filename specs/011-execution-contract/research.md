# Research: execution contract — cli-automation JAR probes (2026-11-04)

Harness: LOCAL-only workflows run with `cli-automation-standalone.jar execute` (EAST / CR1 of a PGW run's CIQ JSON),
execution report JSON + console log. Script: scratchpad `probe_contract.py` (cases A–D, N1–N3).

| Probe | Workflow | Result |
|-------|----------|--------|
| E001 phase `when` on a flag set by a branch `vars` | pre-health step 2 fails its regex, `on_failure: continue`, failure `vars: {PRECHECK_FAILED: 'true'}`; activity `when: ROLLBACK_ONLY != 'true' && PRECHECK_FAILED != 'true'` | Step 3 of the pre-health **still ran**; activity and rollback **skipped** ✔ |
| E002 `${var}` in branch `vars` | failure `vars: {ROLLBACK_REQUIRED: '${CHANGES_MADE}'}` after a step whose success set `CHANGES_MADE: 'true'` | Rollback ran (flag = `true`) ✔; with no earlier change (B2) rollback skipped ✔ |
| E003 failure branch on an **exit-code** failure | `send: exit 3` (validation `skipped`, success = false) | Failure `vars` applied ✔ |
| E004 activity `on_failure: stop` | stop in the activity | Remaining activity steps skipped; **every later phase skipped except phases whose `name` contains "rollback" (any case)**; their `when` is still evaluated ✔ — no `then:` needed |
| E004b rollback phase naming | after a stop: `name: RollbackConfiguration` / `Restore previous state` (key `rollbackConfiguration`) / both without "rollback" | Runs only with "rollback" in `name` (N1 ✔, N2 ✘, N3 ✘) — the phase **name** must contain "Rollback" |
| E005 `--var ROLLBACK_ONLY=true` | all phases `when: ROLLBACK_ONLY != 'true'`, rollback `when: ROLLBACK_REQUIRED == 'true' \|\| ROLLBACK_ONLY == 'true'` | Only the rollback phase ran; overall SUCCESS ✔ |
| E006 "every item" | `register: [{regex: '(?m)^NODE \d+', loop: true, count_var: TOTAL}, {regex: '(?m)^NODE \d+.*Link UP', loop: true, count_var: OKN}]`, `expr: OKN == TOTAL && TOTAL > 0` | All UP → success; one DOWN → validation failed ✔ |
| E007 parentheses in `when` | phase `when: "(A == 'true' \|\| B == 'true') && C != ''"` | **Not supported**: the phase never runs, whatever the values ✘ |
| E008 precedence | `A == 'true' && C != '' \|\| B == 'true' && C != ''` | `&&` binds tighter than `\|\|` (all four value combinations as expected) ✔ |
| E009 JAR acceptance | PGW control workflow + `structure.apply` (contract) → `generate` | MOP generated; validator errors unchanged (flat form) ✔ |

## Consequences for the contract

- Flags in `globals.vars` + branch `vars` + phase `when` implement rules 1–5 without `then:`.
- Rule 4 "only rollback starts" is the JAR's own behaviour after a stop — **rollback phase names must contain
  "Rollback"**; PostNodeHealthCheck is skipped automatically after an activity stop.
- A successful run no longer executes rollback (OB-25) once rollback phases carry `when: ROLLBACK_REQUIRED == 'true' ||
  ROLLBACK_ONLY == 'true'`.
- Health checks: step `on_failure: continue` + failure `vars: {PRECHECK_FAILED: 'true'}` (post-health: report only).
- The LLM's own phase condition is joined without parentheses: `(contract) && (own)` expanded into OR-of-AND terms
  (`contract.combine`); `strip` recovers it (`split_own`).
