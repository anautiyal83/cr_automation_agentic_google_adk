# Requirement document guidelines (draft — grows with each use case)

**Goal**: documents from which the pipeline generates a production-grade workflow with the fewest questions and
mistakes. Every rule cites the gap that taught it (`M-n` = [MRF analysis](uc-mrf-announcement-loading.md), `G-n` / `Q-n` = [PGW analysis](uc-pgw-rds-1058-ifc-capability-modification.md)); rules
without a citation come from the pipeline's verified framework facts (CLAUDE.md, spec 004/005).

## 1. The document set

| Document | Format | Required | Purpose |
|----------|--------|----------|---------|
| CIQ | `.xlsx` | Yes | Every value that changes per CR, node or item — the workflow reads nothing else at run time |
| MOP | `.docx` / `.txt` | Yes | The procedure: phases, exact commands, branches, checks, backup and rollback |
| Command output transcripts | `.txt` / `.log` | Yes | Real output of **every** command (with prompts) — regexes and the simulator are built from them |
| I/O samples | `.xml` / `.json` | When files or requests are exchanged | Request/response the activity sends or receives |
| Environment sheet | in MOP or notes | Yes | Hosts, routes, file sources, repo paths, credentials *names* (never values) |
| Developer notes | text | Optional | Decisions the MOP leaves open — must not contradict the MOP |

## 2. CIQ

- **INDEX sheet**: `NODE`, `GROUP`, `CRGROUP`, `EMAIL` — one row per node; `GROUP` links nodes to data rows.
- **One data sheet per activity**: one row per item (file, subscriber, …), a `GROUP` column, one value per cell, no
  merged cells, exact column names (they become `${…data.<Column>}`; spaces allowed, no quotes).
- **Node_Details sheet**: `NODE_NAME`, `NODE TYPE`, `NIAM NAME` and every node property a rule depends on
  (`VER`, `Circle`, …) — these become node values (`${MRF.VER}`); never repeat them in the data sheet.
- **Derived values are not columns**: a base name, extension or date is computed in the command; do not expect a
  `…_BASENAME` column to exist (DEP-53).
- **Either-or columns** (e.g. Test IMSI / Test MSISDN): say which one a row may leave empty and how the other is
  resolved.
- **Test data must follow the rules**: rows used for testing must match the MOP's branches (a `VER 13` node with
- **Node access entries**: list every NIAM entry per node (`NIAM_NAME1/2/3`) and say whether they are failover alternatives (G-2).
- **Special values**: define `UNCHANGED` (do not touch) vs blank (remove) vs a value, and multi-value cells (`IFC: 101,102`) (G-5, G-8).
  plain files, not `.tar`, when 13 means "plain copy") — include at least one row per branch (M-12).

## 3. MOP

- **Phases, in this order and with these names**: Pre-health check · Backup · Activity · Activity post-check ·
  Post-health check · Rollback · Rollback post-check. Do not list rollback steps under another phase heading (M-1).
- **Every step**: the exact command (no prose like "copy each file"), the node and the user it runs as, the
  expected output and the pass criterion, what a failure means (stop / continue / **trigger rollback**) (M-2, M-7).
- **Health checks have commands**: e.g. `systemctl status swms --no-pager` → `Active: active (running)`; say whether a
- **Health checks gate the activity**: say whether a failed pre-health check stops the run before the activity (G-1).
- **Target guard**: which targets the activity may touch and how that is checked (e.g. test IMSIs listed per zone in a whitelist file) (G-3).
- **Compare before apply**: whether current values are read first and only differences are sent (G-5).
- **Referenced entities must exist**: e.g. the APN whose pdnType changes must already exist for the subscriber (G-7).
  failed post-health check triggers rollback (M-7).
- **Branches**: `IF <CIQ column or node value> <condition> THEN <steps> ELSE <steps>`, with the column/value named
  exactly as in the CIQ (`Node_Details.VER starts with 13`) (M-12).
- **Loops**: `For each row of <sheet>` and what stops a row (e.g. "if the transfer fails, skip the rest of this
  row and mark rollback") (M-2).
- **Verification states the scope**: "**every** file in the destination has 777" vs "the file exists" (M-6).
- **Backup contract**: *what* is backed up (only the items the activity will overwrite — e.g. the members of the tar
  that already exist in the destination), *how existence is checked*, *where* it is stored (path convention incl.
  order number) (M-4, M-9).
- **Rollback contract**: the trigger (which failures set it), the steps (*remove what was loaded*, then *restore what
  was backed up*), and its post-check (M-1, M-5).
- **Run modes**: whether a rollback-only run for an earlier order is needed (M-10).
- **Report**: what the report contains and who receives it (M-11).

## 4. Command output transcripts
- **Never write regexes for the pipeline** — give the real output; the pipeline derives patterns (a hand-written `Updates Rejected:\s*0…` did not match the real table, PGW Q-2).

- One transcript per node and user, **prompts included**, for every command in the MOP: health checks, listings,
  transfers, the activity, verification, rollback (M-7).
- Both outcomes where a check can fail (e.g. a service stopped, a file with wrong permissions).
- One transcript per branch (e.g. a VER 13 node and a non-13 node) (M-12).
- Commands whose output drives a decision (`tar -tf`, `ls | grep`, `find …`) must appear with real output (M-4).

## 5. Environment sheet

- **Where input files come from** (OM shared directory, file repository such as GitLab — project, ref, auth method)
  and how their presence is verified before the activity (M-3).
- **Routes**: which host pushes/pulls files to which (automation server → node directly, or via a repo/staging
  server; protocol `sftp`/`scp`; jump host) (M-8).
- **Repo server**: host, user, base path and the directory convention (`/tmp/${ORDER_NO}/${NODE_NAME}/<path>`) (M-9).
- **Runtime parameters** the platform passes (`ORDER_NO`, `PARENT_REQ_ID`, …) and their meaning.
- **Shared paths and naming**: where request files, evidence (pre/post values) and reports are written, and the file naming convention (e.g. `<type>_${CHILD_REQ_ID}_<configSeq>_<nodeGroup>.xml`) (G-12).
- **Pre-installed scripts**: whether helper scripts exist centrally (path) or must be generated (G-12).
- **Credentials**: names of the secrets (never values).

## 6. Developer answers and notes
- One request type per I/O sample: if refinmocName / refinmtcName go to the HLR and capabilities to the HSS, say so — each sample becomes its own request and response check (G-4).
- Answer with environment *names*, not test values (`localhost`, `password123`) — put real hosts in the environment sheet and secrets in the vault (Q-3).

- Answer with commands, not intentions; when an answer refines a MOP rule, restate the full rule.
- Never contradict the MOP or the CIQ test data; if the MOP is wrong, fix the MOP (M-12).
- Do not give destructive shortcuts (`rm -rf <dir>/*`) for a scoped removal (M-5).

## 7. Checklist before uploading

- [ ] Every phase of §3 present, rollback with its trigger
- [ ] Every MOP command has a transcript with prompts
- [ ] Every branch has a CIQ column/node value, test rows and transcripts for each side
- [ ] Input-file source, transfer route and repo path convention stated
- [ ] Verification scope stated ("every" / "exists")
- [ ] Backup = what will be overwritten; rollback = remove loaded + restore backup
- [ ] No answer or note contradicts the MOP or the test data
- [ ] Health-check gate, target guard, compare-before-apply and referenced-entity checks stated
- [ ] Every I/O sample says which request it is and which response proves it
- [ ] NIAM entries per node and failover stated

## Changelog

| Date | UC | Rules added |
|------|----|-------------|
| 2026-11-04 | PGW_RDS_1058_IFC_CAPABILITY_MODIFICATION | Health-check gate, target guard, compare-before-apply, referenced-entity checks, special values (UNCHANGED/blank/multi), NIAM failover entries, one request per I/O sample, no regexes from users, environment names not test values, shared paths/naming, central scripts |
| 2026-11-04 | MRF_ANNOUNCEMENT_LOADING | Phase order and rollback trigger, step failure semantics, health-check commands, branch syntax, verification scope, backup/rollback contracts, input source/route/repo convention, test data per branch, run modes, report |
