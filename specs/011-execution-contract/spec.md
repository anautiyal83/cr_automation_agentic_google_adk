# Feature Specification: Execution contract — default failure, abort and rollback rules for every workflow

**Feature Branch**: `feature/skill-packages` | **Created**: 2026-11-04 | **Status**: Rules decided, JAR-verified ([research.md](research.md)) — implementing

## Request (2026-11-04, user)

Shared patterns first (spec 009: MRF M-1/M-2, PGW G-1): the rules every generated workflow follows **unless the
UC's documents say otherwise**:

1. **PreNodeHealthCheck** and **PostNodeHealthCheck**, once started, run completely even if a command fails (unless
   later commands depend on the failed one).
2. If any command fails in **PreNodeHealthCheck**, all next phases are aborted.
3. If **Backup** fails, all next phases are aborted.
4. If any command fails in **Activity**, the activity stops there and then, and only the **Rollback** phase starts —
   automatically, with the flag `ROLLBACK_REQUIRED: "true"` — if any change has been made on the node.
5. Rollback is triggered automatically by `ROLLBACK_REQUIRED: "true"`, or manually / independently by the runtime
   parameter `ROLLBACK_ONLY = "true"`, which runs **only** the Rollback phase.

And (same day): **the user is never asked for patterns** (regexes) — only for the text the output must show; the
pipeline builds the pattern from that text and the real command output (spec 009 PGW Q-2).

**Clarified (user, 2026-11-04)**: the local server is provided by the execution framework by default — a workflow need
not declare a `LOCAL` node (step 6's autofix keeps dropping an unused one).

## The contract (phase names: spec 010 O-D1)

| Phase | Steps on failure | After a failure | Runs when |
|-------|------------------|-----------------|-----------|
| PreNodeHealthCheck | continue (a step later steps depend on stops the phase) | `PRECHECK_FAILED=true` → every next phase skipped | `ROLLBACK_ONLY != 'true'` |
| Backup | stop | `BACKUP_FAILED=true` → every next phase skipped | not rollback-only, no precheck failure |
| Activity (Precheck, Configuration, PostCheck) | stop | `ROLLBACK_REQUIRED=true` **if a change was made** (`CHANGES_MADE`) → Rollback | not rollback-only, no precheck/backup failure |
| PostNodeHealthCheck | continue (dependants as above) | reported | activity completed |
| Rollback (Precheck, Configuration, PostCheck) | stop | reported | `ROLLBACK_REQUIRED == 'true'` **or** `ROLLBACK_ONLY == 'true'`; phase `name` must contain "Rollback" (the JAR runs only those after a stop) |

- **Flags** (workflow `globals.vars`, default `'false'`): `ROLLBACK_ONLY` (runtime parameter, step 9 passes it as
  `--var`), `PRECHECK_FAILED`, `BACKUP_FAILED`, `CHANGES_MADE`, `ROLLBACK_REQUIRED`. Set by the steps' failure /
  success branches (`vars`), read by the phases' `when`.
- **A change on the node** (E-D3): a step that writes outside a temporary directory or changes configuration. Its
  success sets `CHANGES_MADE=true`; its failure sets `ROLLBACK_REQUIRED=true` (it may have changed part of the node).
  Any other activity step's failure sets `ROLLBACK_REQUIRED=${CHANGES_MADE}`.
- **Dependent commands**: a user switch (`su`) and a step whose registered value later steps use — if it fails, the
  steps that depend on it are not run (the phase stops / they are skipped).
- **Rollback only**: with `ROLLBACK_ONLY=true` only the Rollback phase runs; it restores from the backup of the
  **same order** (`${ORDER_NO}` in the backup location — spec 010) and removes what that order loaded.
- **Overrides**: when the MOP / answers state a different rule for a phase (e.g. "a failed post-health check triggers
  rollback"), that rule wins; step 1 records it, step 6 applies it.

## Checks the user describes as text

- Step 1 asks: *"What text in the output shows this command succeeded?"* (and, when it applies, *"Must every line
  / item show it, or is one enough?"*) — never for a regex.
- Step 6 builds the regex from that text and the real output (transcripts / command samples); the validator keeps
  checking it against the real output. Scope "every" → count all items and those showing the text (`register`
  `loop` + `count_var`, `expr: ok == total && total > 0`).
- Answers that already are regexes are treated as text-with-hints, never pasted into the workflow.

## Decisions (2026-11-04, user)

| ID | Topic | Decision |
|----|-------|----------|
| E-D1 | PostNodeHealthCheck failure | Reported only (no rollback) unless the MOP says it triggers rollback |
| E-D2 | Rollback when nothing was changed | Not run (rule 4); a `ROLLBACK_ONLY` run always runs it |
| E-D3 | "A change on the node" | Something that can affect how the node / network element works: files written into the **actual** (non-temporary) directories, configuration changed partly or completely — anything showing the activity has (partly) happened on the node. **Staging in a temporary directory (`/tmp`, `/var/tmp`) is not a change.** Detection: a modifying command (`cp`/`mv`/`rm`/`chmod`/`chown`/`mkdir`/`ln`/`touch`, archive extraction, `> file`, `sed -i`, `ldapmodify`/`ldapadd`/`ldapdelete`, service start/stop/restart, a file upload) whose destination is outside a temporary directory; configuration commands always; an unknown destination counts as a change (safe side). Steps on the local server never change the node |
