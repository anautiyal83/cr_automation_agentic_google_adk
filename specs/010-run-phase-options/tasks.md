# Tasks: Run phase options (spec 010)

## Step 0: Decisions
- [X] T000 O-D1 … O-D4 decided (user, 2026-11-04)
- [X] T000b O-D5 every phase and sub-phase a checkbox; O-D6 SFTP support per node type (user, 2026-11-04)

## Step 1: Probes
- [X] T001 JAR: node-side `scp` with `expect_reply` answers the password and passes; without it the step hangs and fails ([research.md](research.md))
- [X] T002 MockWire: in-shell scp/sftp only copy into the same instance, no download, no password prompt → needs peer instances (design in research.md)

## Step 2: Options end to end (phase selection)
- [X] T003 `uc_document_sets.run_options` (JSON, migration), `UCDocumentSet.run_options`; runner saves / restores it (queue dispatch, resume, `_document_set_from_row`), Run History re-run keeps it
- [X] T004 New Run Activity step: every phase / sub-phase a checkbox with its purpose, groups tick/clear their sub-phases and stay ticked while one is, "via repo server" only under Backup, no phase → Start refused; review summary line; pre-filled from the UC's last run (preview-checked)
- [X] T004b Vendor profile node type: "File transfer" SFTP / SCP select (`ssh_config.file_transfer`, default sftp), shown in the node type summary; reaches the run through the vendor profile
- [X] T005 The step 6 agent passes the run's `run_options` (from the run's document set — cached requirements stay valid); the step 6 prompt lists exactly the selected phase keys with their purpose
- [X] T006 `structure.select_phases` (first in `apply`): generated phases mapped to the nine model phases (`run_options.canonical_phase`, same-phase bodies merged), model order, deselected phases dropped; cache: `run_options` out of the inputs fingerprint (steps 1–4 keep their cache), a per-agent options fingerprint for step 6
- [X] T007 Tests `tests/test_run_options.py` (14)

## Step 3: Backup kept on the node (/tmp)
- [X] T008 Backup without "via repo server": `backup_to_repo` does nothing (the archive stays in the node's /tmp where the backup step wrote it, the rollback restores from there); the step 6 prompt says where backups go; test

## Step 4: Via repo server
- [ ] T009 `structure.py` route "via repo": LOCAL → REPO upload, node pull/push with `scp` + `expect_reply`, checksums, `${ORDER_NO}` path
- [ ] T010 Step 8 / MockWire node-side `scp`
- [ ] T011 MRF E2E both routes (step 9 green)

## Step 5: Docs
- [ ] T012 README / CLAUDE.md / guideline
