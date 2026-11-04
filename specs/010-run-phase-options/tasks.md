# Tasks: Run phase options (spec 010)

## Step 0: Decisions
- [X] T000 O-D1 … O-D4 decided (user, 2026-11-04)
- [X] T000b O-D5 every phase and sub-phase a checkbox; O-D6 SFTP support per node type (user, 2026-11-04)

## Step 1: Probes
- [ ] T001 JAR: `scp` with `expect_reply` password (node side and LOCAL side); 9-phase workflow; workflow without some phases
- [ ] T002 MockWire: node-side `scp` between instances (design) → `research.md`

## Step 2: Options end to end (phase selection)
- [ ] T003 `uc_document_sets.run_options`; runner keeps it through queue / resume / re-run
- [ ] T004 New Run Activity step: phase / sub-phase checkboxes (O-D1, O-D5), "via repo server", summary, pre-fill
- [ ] T004b Vendor profile node type: `File transfer: SFTP / SCP` (O-D4, O-D6)
- [ ] T005 Step 1 `run_options` in the requirements; phases = selection
- [ ] T006 Step 6 prompt + `structure.py` drop + validator; cache key
- [ ] T007 Tests

## Step 3: Backup kept on the node (/tmp)
- [ ] T008 `structure.py` route "backup, not via repo": backup copy in node `/tmp`, restore from there; tests

## Step 4: Via repo server
- [ ] T009 `structure.py` route "via repo": LOCAL → REPO upload, node pull/push with `scp` + `expect_reply`, checksums, `${ORDER_NO}` path
- [ ] T010 Step 8 / MockWire node-side `scp`
- [ ] T011 MRF E2E both routes (step 9 green)

## Step 5: Docs
- [ ] T012 README / CLAUDE.md / guideline
