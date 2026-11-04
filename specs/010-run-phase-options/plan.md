# Plan: Run phase options (spec 010)

| # | Component | Files | Change |
|---|-----------|-------|--------|
| C1 | Storage | `storage/db.py` | `uc_document_sets.run_options` (JSON) migration |
| C2 | New Run UI | `ui/pages/upload_section.py` | Phase checkboxes in the Activity step, "via repo server" under Backup, summary, pre-fill from the UC's last run, `run_options` in the document set |
| C3 | Runner / queue | `orchestrator/runner.py` | `run_options` saved with the document set and restored on dispatch / resume / re-run |
| C4 | Step 1 | `skills/requirement_gathering` | `requirements.run_options`; phases list = selected phases; no repo/backup-location question when answered by the options |
| C5 | Step 6 prompt | `skills/network_config_gen/prompts.py`, `skill.py` | Selected phases only; route rules per option |
| C6 | Step 6 structure | `skills/network_config_gen/structure.py` | Drop deselected phases; route builder: backups in node `/tmp` or via repo; SFTP or SCP per node; input files via repo (LOCAL → REPO upload, node pulls with `scp` + `expect_reply`); restores; checksums on every hop; repo path with `${ORDER_NO}` |
| C7 | Step 6 validator | `skills/network_config_gen/validator.py` | Deselected phase present → error |
| C8 | Cache key | `storage/artifact_cache.py`, `orchestrator/pipeline_agent.py` | `run_options` in the step 6 fingerprint only |
| C9 | Step 8 / MockWire | `skills/network_simulator_setup`, `lib/mockwire` | REPO instance; node-side `scp user@repo:path local` / `scp local user@repo:path` copies between instance VFS dirs, password prompt |
| C11 | Vendor profiles | `shared/profiles.py`, `ui/pages/vendor_section.py` | Node type `ssh_config.file_transfer` = sftp / scp |
| C10 | Docs | README, CLAUDE.md, spec 009 guideline | Run options; guideline: route is a run option, not a question |

## Steps

1. **Probe** (before code): cli-automation JAR — `scp` from a node session with `expect_reply` password, phase drop;
   MockWire — what a node-side `scp` needs. Record results in `research.md`.
2. **Options end to end without route changes**: C1–C4, C7 (phase selection), C8. Test: deselected phases are absent.
3. **Backup without repo** (C6 part 1): backups on the automation server; restore from it.
4. **Via repo** (C6 part 2, C9): input files and backups through the repo; simulator support; step 9 green for MRF
   both ways.
5. **Docs** (C10), E2E: MRF with each option combination on the preview.
