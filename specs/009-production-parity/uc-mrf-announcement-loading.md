# MRF_ANNOUNCEMENT_LOADING — generated workflow vs production

**Compared** (2026-11-04): pipeline `C:\Users\DELL\Desktop\CR-Config\MRF\local\MRF_ANNOUNCEMENT_LOADING.yaml` (run
fa48eb50, cached step 6, 420 lines) with production `…\production\MRF_24.7_ANNOUNCEMENT_LOADING.yaml` (731 lines).
**Inputs the pipeline had**: MOP (3 phase headings, 15 steps, no commands for the health checks), CIQ sheets INDEX /
ANNOUNCEMENT_FILES (GROUP, INPUT_FILE, MRF_DESTINATION_PATH) / Node_Details (Circle, NODE TYPE, VER, NODE_NAME,
NIAM NAME), 12 command samples (`ls`, `tar -xf`, `cd /tmp`), developer answers (below), no developer notes.
Causes: R requirement · C conflict · P pipeline · F framework knowledge · E convention (see [spec.md](spec.md)).

## What each workflow does

| Concept | Production | Pipeline |
|---------|------------|----------|
| Phases | pre-health → backup → activity → activity post-check → post-health → **rollback + rollback post-check only if `ROLLBACK_REQUIRED`**; every forward phase skipped when `ROLLBACK_ONLY` | pre-health → backup → activity → post-health → **rollback always** |
| Input files | Staged on the OM server `/mnt/shared_data/${PARENT_REQ_ID}`; missing → downloaded from GitLab (REST, OAuth) → existence verified (`OM_FILE_FOUND`) | Assumed in the automation server's working directory; never checked |
| Route to the node | OM → repo server (`/tmp/${ORDER_NO}`) → MRF `/tmp` (`scp` run on MRF, password via `expect_reply`) | Automation server → MRF `/tmp` (framework SFTP), sha256 verified |
| Version branch | `VER` starts with 13 → plain copy (`\cp -p`); otherwise untar (`tar -xvf`); `chmod -R 777` on the destination | Same rule (bash `if [[ VER == 13.* ]]`), `chmod 777` |
| Backup | Only the files the load will overwrite: tar → its members (`tar -tf`) that exist in the destination; plain → the file; to repo `/tmp/${ORDER_NO}/${NODE_NAME}/<dest path as a_b_c>/` | Whole destination directory as `announcement_backup.tar.gz` when `ls | grep <INPUT_FILE>` counts > 0, via the automation server to `/backup/netconfig/${crGroup}/<host>/<dest>/` |
| Failure handling | Every critical step's failure branch sets a flag (`REPO_FILE_TRANSFERED`, `NODE_FILE_TRANSFERED`, `FILE_PERMISSION_UPDATED`) **and `ROLLBACK_REQUIRED=true`**; later loops gate on them, `break_when` stops a failed row | No flags; a failed transfer is reported, the next steps still run |
| Activity post-check | `find <dest>/* -printf "%M %p" | grep -v '^-rwxrwxrwx'` — fails when **any** file lacks 777 → rollback | `ls -ltrh <dest>/` matches `^-rwxrwxrwx` — passes when **one** file has 777 |
| Rollback | Remove the files just loaded (tar members / the file), then restore the backed-up files from the repo; post-check lists the destination | Backup exists → extract the whole-directory tar at `/`; none → `rm` the INPUT_FILE |
| Health check | `systemctl status swms --no-pager` → `Active: active (running)`; post-health failure → rollback | `service swms status` → `swms is running` (invented) |
| Run modes, reporting | `ROLLBACK_ONLY`; e-mail notifications on error / phase / execution end with the execution report (disabled) | None |
| Connectivity | One `niam_server` jump node (port 31022, M2M user) | Node `MRF` with `ne_id` + `elevate_to_root` (framework NIAM) |

## Gaps

| # | Gap | Severity | Cause | Why we missed it |
|---|-----|----------|-------|------------------|
| M-1 | **Rollback runs after a successful load** and undoes it (restores the old backup or deletes the new file) | Critical | P, F, C | Step 6 never makes rollback conditional: the schema has phase `when` and branch `vars` (`ROLLBACK_REQUIRED`) but the prompt teaches neither (known open OB-25). The MOP also lists the rollback steps under the heading "MRF Post-Health check" with no trigger ("IF YES: restore") |
| M-2 | **Failures do not stop the row or trigger rollback**: no status flags, no `break_when`, a failed transfer is followed by copy/chmod | High | F, R | Branch `vars`, loop `break_when` and gating on flags are not in the step 6 prompt; the MOP never says what a failure must cause |
| M-3 | **Input files have no source**: assumed on the automation server; no existence check; no download from the file repository (GitLab) | High | R, P | Neither the MOP ("Transfer (SFTP) the announcement files … in /tmp") nor the CIQ says where the files come from. Step 1 knew the local staging path `/mnt/shared_data/${CHILD_REQ_ID}/${NODE}` (orchestration `paths.local`) but step 6 did not use it |
| M-4 | **Backup checks the wrong thing and saves the wrong scope**: it greps the INPUT_FILE name (a `.tar` never exists in the destination, so tar loads are never backed up) and tars the whole directory under one fixed name | High | C, P | Two answers disagree: the branch answer says "check if the destination already has the files in the tar, then back up those files"; `cmd_q5` gives `tar -czvf … <whole dir>`. Step 6 followed the command answer. No command sample of `tar -tf` |
| M-5 | **Rollback logic incomplete**: with a backup it restores old files but leaves new files that did not exist before; without one it removes the `.tar` name (its members stay) | High | C, P | The answer "if backup exists restore old files, else remove the newly copied files" describes two exclusive cases; the correct contract is *remove what was loaded, then restore what was backed up*. `cmd_q7` (`rm -rf <dir>/*`) would even delete everything |
| M-6 | **Post-check proves one file, not all**: passes if any line has 777 | Medium | P | The MOP says "verify … copied correctly with right permission" without a command; `orch_q2` gave `ls -ltrh`. Step 6 wrote an "exists" regex instead of a "none violates" check |
| M-7 | **Health check command invented** (`service swms status` vs `systemctl status swms --no-pager`) and post-health failure does not trigger rollback | Medium | R | The MOP's health-check phases have no commands and no output transcripts |
| M-8 | **Transfer route differs**: production stages new and backup files on the repo server and pulls them from MRF; the pipeline pushes from the automation server. **User (2026-11-04)**: both routes are valid — make it a run option (Backup → "via repo server": every file goes through the repo server) → spec 010 | Medium | R | The documents never say which hosts can reach which; the repo-server answer only says it "keeps the backup files" |
| M-9 | **No order traceability**: backups not stored under the order number | Low | R | Repo path convention (`/tmp/${ORDER_NO}/${NODE_NAME}/…`) not in any document |
| M-10 | **No rollback-only run mode** (`ROLLBACK_ONLY`) | Low | R, F | Not requested in the documents; the pipeline has no concept of run modes |
| M-11 | **No reporting/notification** ("Generate a report showing the status … in each MRF") | Low | R, F | MOP asks for a report without saying what or to whom; notifications are not taught to step 6 (production has them disabled) |
| M-12 | Test data contradicts the rule: CIQ rows give `.tar` files with `VER 13.0.3.7` (13 → plain copy), and the version answer states the opposite of MOP and production ("13.X.X.X … extra step to untar"). **User (2026-11-04): the MOP is authoritative** — 13.X.X.X copies each file and sets 777, other versions untar | Medium | C | Step 1 does not check answers and sample data against the MOP's branch rules; the pipeline followed the MOP (correct) |
| — | One `niam_server` jump node vs per-node `ne_id` | — | E | Framework handles NIAM through `ne_id`; equivalent |

## Not to copy from production

Production uses the older data model and a few constructs our JAR rejects or that are wrong in YAML:
`configData` / `file_row` (ours: `configSequences.0.records` + `rec.data`), `${node == nodeName}` (`${node}` empties
multi-node MOPs, DEP-51), loops with **two `when:` keys** (YAML keeps only the last — the node filter is lost),
`criteria: regex: ''` (always true), string-vs-boolean flags (`OM_FILE_FOUND==false` vs `'false'`), REST
`download_to` (not in our v1.2 schema), a no-op `sed 's/|/|/g'`.

## Proposed fixes

See [tasks.md](tasks.md) (P009-*). In short: rollback contract (M-1, M-2), file-load backup/rollback pattern (M-4,
M-5), "all items" verification (M-6), input-file source and route as step 1 orchestration questions (M-3, M-8, M-9),
health-check commands and transcripts required by the guideline (M-7), conflict check of answers vs MOP/CIQ (M-12).
