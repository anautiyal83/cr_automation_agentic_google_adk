# Feature Specification: Run phase options — choose the phases, move files through the repo server

**Feature Branch**: `feature/skill-packages` | **Created**: 2026-11-04 | **Status**: Step 2 done (options end to end, phase selection); backup routes next

## Request (2026-11-04, user)

"[The transfer route] should be configurable in the activity section. Since the phases in our framework are fixed,
let's make them checkboxes; if the user selects Backup then show another checkbox (via repo server); if the user
selects this then we move everything (files) through the repo server — first upload and download."

Origin: production parity, MRF gap M-8 ([spec 009](../009-production-parity/uc-mrf-announcement-loading.md)):
production stages every file on the repo server; the pipeline sends them straight from the automation server.

## Today

- Phases come from step 1 (`category_3_orchestration.phases`, default pre-health, backup, activity, post-health,
  rollback) and whatever the MOP describes; the user cannot choose them.
- Input files go automation server → node (`sftp` upload). Backups go node → automation server → repo server
  (`structure.backup_to_repo`) and come back the same way on rollback. Every hop is checked with `sha256sum`.

## Decisions (2026-11-04, user)

| ID | Topic | Decision |
|----|-------|----------|
| O-D1 | Phase model | **PreNodeHealthCheck · Backup · Activity (ActivityPrecheck, ActivityConfiguration, ActivityPostCheck) · PostNodeHealthCheck · Rollback (RollbackPrecheck, RollbackConfiguration, RollbackPostCheck)** — the workflow's phases, in this order (production uses the same split: `activity_configuration`, `activity_postcheck`, `rollback_configuration`, `rollback_postcheck`) |
| O-D2 | Backup without the repo server | The backup **stays on the node in `/tmp`**; rollback restores it from there (no transfer) |
| O-D3 | Rollback without Backup | Allowed — an activity may have nothing to restore (e.g. configuration only: rollback re-applies or removes the configuration the MOP describes) |
| O-D4 | Transfer protocol | **Per node: SFTP where the node supports it, otherwise SCP** |
| O-D5 | Checkboxes | **Every phase and sub-phase is a checkbox**, Activity / ActivityConfiguration / RollbackConfiguration included (a run may be checks only). A group (Activity, Rollback) ticks/clears its sub-phases; a group stays ticked while one of its sub-phases is; at least one phase must be selected to start |
| O-D6 | SFTP support | Per node type in Settings → Vendor profiles: `File transfer: SFTP / SCP` (default SFTP); a node type without a profile is asked once in step 1 |
| O-D7 | Repo route (user, 2026-11-04, corrected) | **The repo server runs the transfers**: the automation server uploads an input file to the repo (framework SFTP); on the repo server an `sftp` (node type supports SFTP) or `scp` command **uploads** it to the node; backups: the repo server **downloads** them from the node; rollback: the repo server uploads them back. The node's login password is answered by `expect_reply` on the repo step (JAR-verified for a password prompt, research.md). Repo paths `<backup_dir>/${ORDER_NO}/<node>/…` |

| O-D8 | Where the phases are chosen (user, 2026-11-04) | **Fixed per activity, not per run**: Settings → Vendor Profiles → Activity has the phase checkboxes (they replace the free-text "Default Phases"); New Run shows the selected activity's phases read-only (with where to change them) and no longer asks or pre-fills them from the last run. Stored in `activities.default_phases` as the options JSON; an old free-text list reads as all phases |

## Behaviour

### Settings → Vendor Profiles → Activity (O-D8)

```
Phases
 [x] PreNodeHealthCheck
 [x] Backup
       [ ] via repo server     — every file (input files, backups, restores) goes through the repo server
 [x] Activity
       [x] ActivityPrecheck
       [x] ActivityConfiguration
       [x] ActivityPostCheck
 [x] PostNodeHealthCheck
 [x] Rollback                  (runs only when a step failed — spec 009 P009-03)
       [x] RollbackPrecheck
       [x] RollbackConfiguration
       [x] RollbackPostCheck
```

- "via repo server" is shown only while Backup is checked; unchecking Backup clears it. Unchecking Rollback hides and
  clears its sub-phases.
- An activity without any phase is not saved. The Activities list shows a one-line summary.
- New Run → Activity step: "Phases: <summary>" of the selected activity (read-only, info tooltip "Set per activity
  in Settings → Vendor Profiles → Activities"); without an activity (manual entry) all phases. The options are saved
  with the run (document set); Run History → Re-run keeps them; the Notes & review summary lists them.

### Routes

"Node transfer" = SFTP (the framework's `sftp` step, automation server ↔ node) when the node type supports SFTP,
otherwise SCP (`scp` command with the password answered by `expect_reply`) — O-D4.

| Backup | via repo | Input files to the node | Backup kept | Restore (rollback) |
|--------|----------|-------------------------|-------------|--------------------|
| off | — | automation server → node (node transfer) | — | nothing to restore; rollback runs the MOP's rollback configuration |
| on | off | automation server → node (node transfer) | node `/tmp` (copy on the node) | from node `/tmp` |
| on | on | automation server → repo (framework SFTP, `<backup_dir>/${crGroup}/files/`), then **the repo server** copies it to the node (`scp` / `sftp` per node type) | repo server: **the repo server** fetches it from the node (`<backup_dir>/${crGroup}/<host>/<source path>/`) | **the repo server** copies it back to the node |

Every hop keeps the `sha256sum` check on both ends (`criteria.expr: dst == src`). The repo's copy logs in to the node
as `<node ssh.user>@[<address>]` and answers its password secret with `expect_reply` (O-Q8). Files downloaded from a
node (node → automation server) also go node → repo → automation server. A copy onto the node outside `/tmp` counts
as a node change (contract E-D3); everything else on the repo does not. The production `${ORDER_NO}` path
convention (spec 009 M-9) is not applied yet (`${crGroup}`).

### Pipeline

- **Step 1** puts the options in the requirements (`run_options`) and the phase list; it no longer asks what the
  options answer (where backups are kept, the route).
- **Step 6** generates the selected phases with the O-D1 names (prompt) and `structure.py` enforces them
  deterministically: drops deselected phases, builds the routes above with the node's protocol, keeps checksums. The
  validator rejects a deselected phase or a missing selected one.
- **Step 8** simulates the repo server; a node-side transfer from/to the repo must work in MockWire (cross-instance
  file copy, password prompt).
- **Cache**: the options are part of step 6's cache key, not of steps 1–4.

## To confirm

| ID | Question | Proposed |
|----|----------|----------|
| O-Q8 | How does the repo server reach a node (address, user, password)? | **Implemented default (2026-11-04)**: the workflow node's own `ssh.user`, `ssh.host` and password secret; `requirements.node_addresses` (node → e.g. `${MRF.OAM_IP}`) overrides the address. **Open**: for NIAM nodes `ssh.host` is the NIAM ID (`${MRF.niamID-1}`), which a repo server cannot connect to; the MRF CIQ has no node address column (production avoids it: the node runs the copy against `${REPO_IP}`). Needs the user's answer: a CIQ / vendor-profile address field, or the node-side route |

## Out of scope

- Per-node or per-row phase choice; phases other than O-D1.
- Rollback-only run mode (spec 009 M-10) — separate.
