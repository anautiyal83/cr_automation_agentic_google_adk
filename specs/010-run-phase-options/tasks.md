# Tasks: Run phase options (spec 010)

## Step 0: Decisions
- [ ] T000 Confirm O-Q1 … O-Q5 (spec)

## Step 1: Probes
- [ ] T001 JAR: node-side `scp` with `expect_reply` password; workflow without some phases
- [ ] T002 MockWire: node-side `scp` between instances (design) → `research.md`

## Step 2: Options end to end (phase selection)
- [ ] T003 `uc_document_sets.run_options`; runner keeps it through queue / resume / re-run
- [ ] T004 New Run Activity step: phase checkboxes, "via repo server", summary, pre-fill
- [ ] T005 Step 1 `run_options` in the requirements; phases = selection
- [ ] T006 Step 6 prompt + `structure.py` drop + validator; cache key
- [ ] T007 Tests

## Step 3: Backup on the automation server
- [ ] T008 `structure.py` route "backup, not via repo"; restore from LOCAL; tests

## Step 4: Via repo server
- [ ] T009 `structure.py` route "via repo": LOCAL → REPO upload, node pull/push with `scp` + `expect_reply`, checksums, `${ORDER_NO}` path
- [ ] T010 Step 8 / MockWire node-side `scp`
- [ ] T011 MRF E2E both routes (step 9 green)

## Step 5: Docs
- [ ] T012 README / CLAUDE.md / guideline
