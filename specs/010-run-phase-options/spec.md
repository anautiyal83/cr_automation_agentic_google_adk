# Feature Specification: Run phase options — choose the phases, move files through the repo server

**Feature Branch**: `feature/skill-packages` | **Created**: 2026-11-04 | **Status**: Draft — decisions to confirm

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

## Behaviour

### New Run → Activity step

```
Phases                                   (fixed order, Activity always on)
 [x] Pre-health check
 [x] Backup
       [ ] via repo server   — every file (input files, backups, restores) goes through the repo server
 [x] Activity                (always)
 [x] Post-health check
 [x] Rollback                (runs only when a step failed — spec 009 P009-03)
```

- "via repo server" is shown only while Backup is checked; unchecking Backup clears it.
- The options are saved with the run (document set) and pre-filled from the UC's last run (as the notes are).
  Run History → Re-run keeps them. The Notes & review summary lists them.

### Routes

| Backup | via repo | Input files to the node | Backup stored on | Restore (rollback) |
|--------|----------|-------------------------|------------------|--------------------|
| off | — | automation server → node (`sftp`) | — (no backup phase) | rollback only removes what was loaded |
| on | off | automation server → node (`sftp`) | automation server (node → LOCAL, `sftp`) | LOCAL → node (`sftp`) |
| on | on | automation server → repo (`sftp` upload), then the node **downloads** it from the repo | repo server (node uploads to the repo) | node downloads it from the repo |

Every hop keeps the `sha256sum` check on both ends. Repo paths follow the production convention
`<backup_dir>/${ORDER_NO}/<node>/<destination path>` (spec 009 M-9).

### Pipeline

- **Step 1** puts the options in the requirements (`run_options`); it no longer asks what the options answer
  (e.g. "where are backups kept").
- **Step 6** generates only the selected phases (prompt) and `structure.py` enforces them deterministically: drops
  deselected phases, builds the routes above (node ↔ repo transfers included), keeps checksums. The validator rejects
  a deselected phase.
- **Step 8** simulates the repo server; a node-side transfer from/to the repo must work in MockWire (cross-instance
  file copy + password prompt).
- **Cache**: the options are part of step 6's cache key, not of steps 1–4 (changing the route must not regenerate
  the requirements).

## Decisions to confirm

| ID | Question | Proposed |
|----|----------|----------|
| O-Q1 | Which phases are checkboxes? | Pre-health, Backup, Post-health, Rollback; Activity fixed. Activity post-check (verification) stays part of Activity |
| O-Q2 | Backup on, not via repo: where is the backup kept? | On the automation server (today's first hop; no repo server needed) |
| O-Q3 | Rollback without Backup? | Allowed: rollback removes what was loaded, restores nothing |
| O-Q4 | How does the node download from / upload to the repo? | `scp` run on the node with the repo password answered by `expect_reply` (as production); needs a JAR probe and MockWire support |
| O-Q5 | Defaults | All phases on, via repo off; pre-filled from the UC's last run |

## Out of scope

- Phases other than the five above; per-node or per-row phase choice.
- Rollback-only run mode (spec 009 M-10) — separate.
