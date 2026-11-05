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

- [X] T004c O-D8 (user, 2026-11-04): phases fixed per activity — Settings → Vendor Profiles → Activity dialog has the
  checkboxes (`ui.components.PhaseOptions`, replaces the free-text "Default Phases"; no phase → not saved);
  `Activity.run_options` (column `default_phases`, old lists → all phases); `build_profile_context` puts `run_options`
  + the selected phases in the profile; New Run shows the activity's phases read-only, no last-run pre-fill (preview
  on a DB copy: saved `{…activityPrecheck: false…, backup_via_repo: true}`, New Run "Without ActivityPrecheck · backup
  via repo server"); test `test_activity_holds_the_run_phases`

## Step 4: Via repo server (O-D7 final: the node runs the copies with the repo)
- [X] T009 `structure.py`: `backup_to_repo` — the repo fetches the archive from the node and copies it back before
  the rollback extracts it; `files_via_repo` — every LLM sftp upload / download to a node is staged on the repo
  (`nc_xr<k>_stage`, framework sftp) and copied by the repo; `repo_copy` (`scp` / `sftp` per node type
  `file_transfer`, vendor profile wins in `merge_vendor_profile`), `expect_reply` with the node password secret,
  `sha256sum` both ends; `flatten` restores staged steps (`_unstage`); `_moved_files` knows the repo copies (the
  restore stays in its record loop); contract: on a repo node only a copy onto the node outside `/tmp` is a change;
  without run options = default (backup on the node). Tests in `test_workflow_structure.py`
- [X] T010 MockWire 49cee9b + b748860: in-shell scp / sftp to `transfer.peers` instances, both directions,
  password prompt, `user@[addr]:path`; step 8 writes the peers into the repo instance (`topology.add_peers`), peer
  copies are builtins (`file_builtin`), the overlay names the node and answers the simulator password
  (`overlay._peer_copies`)
- [ ] T011 MRF E2E both routes — fast check done (steps 8 + 9 on run 3d75c561 via repo, research.md T011a): SCP and
  SFTP 4/4 each; open: full pipeline run (step 6 LLM with the options), negatives (rollback restore from the repo)
- [X] T013 O-Q8 / O-D7 final (user, 2026-11-04): the node runs `scp` / `sftp` against the repo (framework logs in to
  the node, via NIAM for Nokia; generic per node type `file_transfer`) — `_repo_transfer` puts the copy on the node with
  `<repo user>@<repo host>` and the repo password in `expect_reply`; `node_login` reads the repo node's login (the
  `node_addresses` override is gone); contract: a copy from the repo onto the node outside `/tmp` is a change, nothing
  on the repo is; step 8: the node instance gets the repo as a peer, the overlay names it by any workflow node's
  address (bracketed or not); `is_peer_copy` accepts `user@host:`. Steps 8 + 9 on run 3d75c561: see research.md T013a

## Step 5: Docs
- [X] T012 README "Run Phases & File Routes" (phases per activity, route table, node-side scp / sftp, repo server
  config, simulator peers); CLAUDE.md step 6 / step 8 notes; requirement guideline (spec 009) §3 phase names, §5
  routes + repo server, checklist, changelog
