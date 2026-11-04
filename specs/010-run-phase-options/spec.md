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
| O-D7 | Repo route (user, 2026-11-04, final) | **The node runs the transfers with the repo server**: automation server ↔ repo is the framework's SFTP plugin; node ↔ repo: the framework logs in to the node (via NIAM for Nokia nodes — the node's own login, nothing vendor-specific in the route) and runs `scp` or `sftp` (node type's `file_transfer`, O-D6 — some nodes have no SFTP) against the repo: input files are copied from the repo, backups to the repo, rollback copies them back. The repo password is answered by `expect_reply` on the node step (JAR-verified, research.md T001a). Generic for every vendor / node type. Repo paths `<backup_dir>/${crGroup}/…` (production `${ORDER_NO}` not applied yet) |

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
| on | on | automation server → repo (framework SFTP, `<backup_dir>/${crGroup}/files/`), then **the node** copies it from the repo (`scp` / `sftp` per node type) | repo server: **the node** copies it to the repo (`<backup_dir>/${crGroup}/<host>/<source path>/`) | **the node** copies it back from the repo |

Every hop keeps the `sha256sum` check on both ends (`criteria.expr: dst == src`). The node's copy logs in to the repo
as `<repo user>@<repo host>` (the workflow's `REPO` node = `execution.repo_server`; brackets only for an IPv6 address)
and answers the repo password secret with `expect_reply` (O-Q8). Files downloaded from a node (node → automation
server) also go node → repo → automation server. A copy onto the node outside `/tmp` counts as a node change
(contract E-D3); a copy to the repo and every step on the repo do not. The production `${ORDER_NO}` path
convention (spec 009 M-9) is not applied yet (`${crGroup}`).

### Pipeline

- **Step 1** puts the options in the requirements (`run_options`) and the phase list; it no longer asks what the
  options answer (where backups are kept, the route).
- **Step 6** generates the selected phases with the O-D1 names (prompt) and `structure.py` enforces them
  deterministically: drops deselected phases, builds the routes above with the node's protocol, keeps checksums. The
  validator rejects a deselected phase or a missing selected one.
- **Step 8** simulates the repo server: each node instance lists the repo instance as a `transfer.peers` entry
  (MockWire copies between their VFS, asks for the password); the overlay names the repo instance in the copy and
  answers the simulator password.
- **Cache**: the options are part of step 6's cache key, not of steps 1–4.

## To confirm

| ID | Question | Proposed |
|----|----------|----------|
| O-Q8 | How does the repo server reach a node (address, user, password)? | **Resolved (user, 2026-11-04)**: it does not — the node runs the copy (O-D7), so no node address is needed (a NIAM node is reached through NIAM by the framework); the node uses the repo's host, user and password secret |

## Out of scope

- Per-node or per-row phase choice; phases other than O-D1.
- Rollback-only run mode (spec 009 M-10) — separate.
