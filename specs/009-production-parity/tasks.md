# Tasks: Production parity (spec 009)

## MRF_ANNOUNCEMENT_LOADING (analysis 2026-11-04 — fixes proposed, awaiting go-ahead)

- [X] P009-01 Logical comparison local vs production, gap causes → [uc-mrf-announcement-loading.md](uc-mrf-announcement-loading.md)
- [X] P009-02 Requirement guideline draft → [requirement-guidelines.md](requirement-guidelines.md)
- [X] P009-03 Rollback contract (M-1, M-2; OB-25): rollback / rollback post-check phases `when: ROLLBACK_REQUIRED == 'true'`; — done by spec 011 (contract.py rules 4–5, OB-25)
  failure branches of activity, activity post-check and post-health steps set `vars: {ROLLBACK_REQUIRED: 'true'}`;
  JAR probe first (phase `when` on a branch var, string compare). Deterministic in `structure.py` + prompt + validator
- [ ] P009-04 Per-row failure flags (M-2): transfer/apply failure sets a row flag; later steps of the row gate on it
  (`when`) / `break_when`; teach branch `vars` + `break_when` in the step 6 prompt
- [ ] P009-05 File-load backup/rollback pattern (M-4, M-5): back up only items the load overwrites (archive members via
  `tar -tf`, or the file), rollback = remove loaded items, then restore the backup; prompt rule + validator warning
  for whole-directory backups and `rm` of an archive name
- [ ] P009-06 "Every item" verification (M-6): when the requirement says all files/items, validator rejects an
  "exists" regex; pattern `find … | grep -v <good>` must be empty
- [ ] P009-07 Step 1 questions when the documents lack them (M-3, M-7, M-8, M-9, M-10): input-file source and presence
  check, transfer route, repo path convention, health-check commands, rollback trigger, run modes
- [ ] P009-08 Step 1 conflict check (M-4, M-5, M-12): answers vs MOP branch rules, answers vs each other, CIQ test rows
  vs branch rules → one confirmation question each
- [ ] P009-09 Use orchestration `paths.local` as the input-file location in step 6 (M-3)
- [ ] P009-10 Re-run MRF, compare again, update the analysis

## PGW_RDS_1058_IFC_CAPABILITY_MODIFICATION (analysis 2026-11-04 — fixes proposed, awaiting go-ahead)

- [X] P009-11 Logical comparison → [uc-pgw-rds-1058-ifc-capability-modification.md](uc-pgw-rds-1058-ifc-capability-modification.md); guideline extended
- [X] P009-12 Health-check gate (G-1): a failed pre-health check stops before the activity (flag + gate, or phase `on_failure: stop`) — generic — done by spec 011 (contract.py rule 2, `PRECHECK_FAILED`)
- [ ] P009-13 NIAM failover pattern (G-2) when the CIQ has several NIAM entries per node
- [ ] P009-14 Step 1 question: subscriber/target guard (G-3) — "which targets may this activity touch and how is that checked?"
- [ ] P009-15 One request per I/O sample type (G-4): each XML sample = its own request, upload, response check
- [ ] P009-16 Compare-before-apply (G-5): read current values, send only what differs; conflict check of answers (cmd_q11 vs orch_q32)
- [ ] P009-17 APN / referenced-entity pre-validation (G-7); captured file ownership applied after upload (G-9); duplicate guard stops (G-10)
- [ ] P009-18 Step 1: de-duplicate questions across rounds (Q-1); never ask the user for regexes — derive from transcripts (Q-2); secrets as names (Q-3); no internal syntax in suggested answers (Q-4)
- [ ] P009-19 Re-run PGW, compare again

## Shared by both UCs

- P009-03 / P009-06 / P009-12 / P009-18 (regex questions) → **[spec 011 execution contract](../011-execution-contract/spec.md)** (user rules 2026-11-04)
