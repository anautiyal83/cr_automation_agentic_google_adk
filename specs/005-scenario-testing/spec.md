# Feature Specification: Step 9 — Scenario Test Cases on the Simulator

**Feature Branch**: `005-scenario-testing`

**Created**: 2026-11-02

**Status**: Sunny day implemented; negative cases built but switched off (`include_negatives=False`)

**Input**: "Step 9 artifacts should be a test case report and results. It should first create
different test cases based on scenarios, positive and negative: multiple CRs, multiple node
groups; negative such as pre-node health check failed, activity failed, post-node health check
failed, covering all the scenarios, then run these on the simulator and prepare the report."
Follow-up (same day): "for step 8 and 9 let's work on only one sunny day scenario first, we will
work on negative test cases later."

## Problem

Before this feature, step 9 asked the LLM for test-case *descriptions*, ran the workflow once
per node group on an all-success simulator, and graded the descriptions against that single run.
No failure scenario was ever executed, so nothing showed that a failing pre-check really stops the
activity, or that a validation regex really rejects a failing output.

## Decisions (2026-11-02, user)

| # | Question | Decision |
|---|----------|----------|
| D1 | How the simulator switches scenarios | Restart MockWire per test case with that case's config (no MockWire change) |
| D2 | Negative coverage | Sunny day on every CR × node group; each negative once, on a representative node group |
| D3 | Pass rule | Step 9 passes only when every test case behaves as expected; mismatches route to step 6 (workflow) or step 8 (simulator) |
| D4 | Scope now | Sunny day only; negatives later |

## Design

Test cases are derived from the workflow and the CIQ JSON (`skills/network_config_test/scenarios.py`):

| Case | Built from | Expected |
|------|-----------|----------|
| Sunny day `TC-Pnn` | every (CR group, node group) target of the CIQ JSON | every step runs and passes; JAR `overall_status` PASS; simulator answers every command |
| Pre-check / activity / post-check failure `TC-Nnn` *(off)* | first validated command on a simulated node in that phase, preferring a gating (`stop`) step | fault detected by the step's validation; `stop` → nothing later runs (any phase), `continue` → later steps still run; run FAILs |
| Node login failure *(off)* | first simulated node of the workflow | node receives no command; its step fails; run FAILs |

JAR semantics behind the expectations (probed 2026-11-02): `on_failure: stop` skips every later
step in every phase; `continue` keeps going; either way the run reports FAIL; skipped steps are
absent from the execution report.

Fault output (negatives): LLM writes a realistic failing output from the healthy one; it is
accepted only if the step's success regex (Java semantics) does not match it; deterministic
fallback otherwise. Injected as the first command key of that node's MockWire config
(`regex:` key when the command has `${…}`); login faults change the node's passwords.

Evidence per case: the JAR execution report (which steps ran, validation results, output) and
MockWire's event log (which commands each node received), both kept in
`exec_output/scenarios/<TC>/`.

Artifacts: `<uc>_network-qa-report.html` (test-case report) and `<uc>_test-cases.json`.

## Requirements

- **FR-001** Test cases MUST come from the workflow + CIQ JSON, never invented text.
- **FR-002** One sunny-day case per CR group × node group.
- **FR-003** Each case runs on a freshly restarted simulator; step 8's configs are restored afterwards.
- **FR-004** The report shows per case: scenario, target, expected, actual, every check with evidence, executed steps, simulator commands.
- **FR-005** Step 9 fails if any case fails; failing sunny-day cases are classified from the event log (unmatched → step 8, otherwise step 6).
- **FR-006** *(later)* Negative cases per D2, enabled by `include_negatives`.

## Related fixes recorded with this feature

- **DEP-16 → DEP-17**: the test subscriber is identified by *either* IMSI or MSISDN (EAST rows only
  have MSISDN). The IMSI must be resolved via the MSISDN (`ldapsearch … -b "msisdn=…,dc=MSISDN,dc=C-NTDB" | grep -i provisionedIMSI`).
  Generic fix: step 6 now receives the developer notes as authoritative guidance (it never did — G-3),
  its prompt teaches "identifier from one of several columns → `when`-guarded steps registering one
  variable" (verified on the JAR: NORTH uses the IMSI, EAST resolves it, later steps share `${var}`),
  and the autofix rewrites the production framework's `${rec_row.data.<Col>}` to
  `${configSequences.0.records.0.data.<Col>}`.
- **DEP-18**: step 6 generated the MSISDN lookup but guarded it with `when: configSequences.0.records.0.data.'Test MSISDN' != ''`; a quoted column makes the condition always false (JAR-verified), so the lookup never ran and `imsi=` stayed empty. Autofix unquotes columns in bare `when` paths; prompt rule extended.

## E2E 2026-11-02 (sunny day)

| Case | Result | Cause |
|------|--------|-------|
| TC-P01 CR1/EAST | FAIL | `ndsLoadStats`, `provgw status` regexes reject real output (step 6); `imsi=` empty (DEP-18) |
| TC-P02 CR1/NORTH | FAIL | same regexes; `imsi=` empty (DEP-18) |

Step 8 passed (2/2 nodes); the simulator served recorded real output, so the regex failures are genuine workflow defects, routed to step 6 as designed.
