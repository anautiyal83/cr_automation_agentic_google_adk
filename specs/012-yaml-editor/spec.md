# Feature Specification: Edit generated files at a review (YAML editor)

**Feature Branch**: `feature/yaml-editor` | **Created**: 2026-11-11 | **Status**: Draft

## Request (2026-11-11, user)

"After capturing requirement, we want to give option to user to edit the requirement or generated YAML's in a proper
way using editor or some other way." Agreed plan: the YAML editor first (phase 1), the Activity Spec table later.
Sample screens: the "Activity Spec & YAML Editor" canvas, board "YAML editor — step 6 review".

## Problem

When the AI is close but wrong (one bad check, one wrong command), the only options at a review are Re-run (the AI
tries again, often failing differently), Edit requirements (back to step 1), or Cancel. An engineer who can see the
fix cannot make it. And because later steps read the files from ADK session state, a file changed on disk would be
ignored anyway.

## Users and scope

- **Who**: users with Update rights on Live Run, on their own run (admin: any run) — spec 006.
- **Where**: the review pauses that show generated files:

| Review | Files that can be edited |
|--------|--------------------------|
| Steps 3–4 · Validation Rules & CIQ JSON | `<UC>_validation-rules.yaml`, `<UC>_json-output.yaml` |
| Step 6 · Execution Workflow & Scripts | `<UC>.yaml` (workflow), `scripts/*.py` (helper scripts) |

  Not in this feature: step 8 simulator configs (the simulator is already running; editing needs a step 8 re-run),
  reports (steps 5, 7, 9, 10 — outputs of checks, not inputs), the requirements (Activity Spec, next feature).

## Decisions

| ID | Topic | Decision |
|----|-------|----------|
| Y-D1 | When a file can be edited | Only while the run waits at the review that shows it (the step that wrote it has finished; nothing reads it until Continue). Anywhere else (another review, Run Artifacts, a finished run) the editor opens read-only, with History and Diff |
| Y-D2 | Editor | A code editor (NiceGUI `ui.codemirror`): line numbers, YAML / Python highlighting, search. Opens full screen from the review's file row (**Edit**), one tab per editable file of that review |
| Y-D3 | Problems | **Validate** (also run on Save) checks the file with the same checks its step uses: workflow → the step 6 validator (on the flat form, with the run's requirements: CIQ sheets, recorded command outputs); validation rules / CIQ JSON → their schema validators; helper scripts → Python syntax + the step 6 script contract (accepts the workflow's flags, prints what its check expects). Problems are shown in plain language (`shared.plain_errors`), each with the line it points at when it names a step or command, and the technical text on request. YAML that does not parse is reported with its line |
| Y-D4 | Save and versions | **Save** keeps every version: `run_dir/edits/<file>/v<N>.<ext>` + `meta.json` (version, who, when, source `generated` / `user` / `restored`, problems at save). The first edit stores the generated file as v1. The file in the run folder is always the current version. Saving with problems is allowed (a draft) |
| Y-D5 | Continue after an edit | The review offers **Continue with my edit** when every edited file of the review validates. With problems left: a QA review (5, 7, 9) may continue anyway ("Continue with N problems", as Skip today); a generation review (3–4, 6) cannot — fix, Re-run, Edit requirements or Cancel. A failed step 6 whose edited workflow validates is passable: its error file goes, its status becomes passed |
| Y-D6 | Later steps read the edited file | The run folder is authoritative: every step reads these files from the run folder (fallback: session state), not from ADK session state (`shared/run_files.read`) |
| Y-D7 | Re-run after an edit | The regenerated file becomes a new version (source `generated`); the user's last version stays in History. The review then shows "Re-run replaced your edited version vN — Compare · Restore" |
| Y-D8 | Artifact cache | The cache stores what the user approved: when an edited agent's cache entry is saved (at Continue for steps 3–4; at step 9 for step 6, DEP-55), its files are taken from the run folder |
| Y-D9 | Diff and history | **Diff** compares the current text with the generated version (default) or any version; **History** lists the versions (who, when, source, problems) with View / Restore |
| Y-D10 | Concurrent edits | Save refuses when the file changed since the editor opened (another tab, a re-run): "This file changed — Reload" |
| Y-D11 | Audit | `artifact.edit` (file, version, problems count), `artifact.restore` (file, from version) — never file content (spec 006 D11) |

## Opening the editor (2026-11-11)

| Where | Control | Opens |
|-------|---------|-------|
| Review panel (steps 3–4, 6) | **Edit files** beside Approve / Re-run / Cancel | the editor on the review's first file — the workflow in Table view |
| Review panel file row | **Edit** beside View / Download | the editor on that file's tab |
| "Why step N failed" box | **Fix in editor** on a problem | the editor at that row (Table) / line (YAML), problem highlighted |
| Re-run banner | **Compare** | the editor in Diff: the user's version vs the regenerated one, with Restore |
| Step tracker → step → Artifacts tab | **Open** on a file | the editor, read-only unless the run waits at that file's review |
| Run Artifacts / Run History | **Open** on a file | read-only: view, history, diff, download |

- Full screen over the page; closing with unsaved changes asks, and keeps the draft (E4).
- Read-only anywhere but the run's own review (F2), with "Editable at the step N review of this run".
- View-only roles see View, never Edit / Edit files (G1).
- The workflow opens in Table view (the plain view), YAML one click away; rules and CIQ JSON open in YAML.

## Features (2026-11-11, agreed with the user)

Priority: **M** must, **S** should, **C** could. **R1** = first release (proposed: every M, plus A3, C3, E4, F3, F6).

| # | Feature | Prio | R1 |
|---|---------|------|----|
| **A** | **Editing basics** | | |
| A1 | Code editor: line numbers, YAML / Python highlighting, search and replace, undo / redo | M | ✓ |
| A2 | One tab per editable file of the review | M | ✓ |
| A3 | Outline: phases → steps, click to jump, steps with problems marked | S | ✓ |
| A4 | Format / fix indentation | S | |
| A5 | Shortcuts: Ctrl+S save, Ctrl+F find, Ctrl+Enter validate | S | |
| **B** | **Help while typing** | | |
| B1 | Autocomplete: workflow keys, node names, CIQ columns, captured variables, runtime parameters | S | |
| B2 | Hover help on a key | C | |
| B3 | Warnings while typing for known traps (`${NAME}` shell variables, JavaScript in checks, bash arrays, quoted CIQ columns) | S | |
| **C** | **Checking** | | |
| C1 | Validate with the step's own checks; plain language with line numbers; technical text on request | M | ✓ |
| C2 | Problem markers in the margin; click a problem to jump to its line | M | ✓ |
| C3 | Apply suggested fix for problems with a known fix | S | ✓ |
| C4 | Check tester: a step's success check against the real recorded output | S | |
| C5 | Live checking while typing | C | |
| **D** | **AI help, scoped** | | |
| D1 | Ask AI to fix this problem: only the affected step, shown as a diff before accepting | S | |
| D2 | Explain this step in plain words | C | |
| **E** | **Versions and safety** | | |
| E1 | Every save is a version (who, when, generated / yours, problems at save) | M | ✓ |
| E2 | Diff: current vs generated, or any two versions | M | ✓ |
| E3 | Restore any version | M | ✓ |
| E4 | Unsaved changes kept as a draft if the browser closes | S | ✓ |
| E5 | Save refused when the file changed since it was opened | M | ✓ |
| E6 | A re-run never silently replaces an edit: "Re-run replaced your version — Compare / Restore" | M | ✓ |
| **F** | **Fitting into the run** | | |
| F1 | Continue with my edit once the edited files validate; later steps use the edited files | M | ✓ |
| F2 | Editable only while the run waits at that review; read-only elsewhere (history, diff, download) | M | ✓ |
| F3 | MOP preview of the edited workflow (cli-automation JAR, as step 7 renders it) | S | ✓ |
| F4 | Links between workflow steps and MOP lines (later: Activity Spec rows) | C | |
| F5 | The approved edit is what the cache reuses for the next run of the use case | M | ✓ |
| F6 | Download / upload a file (edit offline) | S | ✓ |
| F7 | Run one step on the step 8 simulator from the editor | C | |
| **G** | **Access and record** | | |
| G1 | Update on Live Run, own run (admin: any); View-only roles read-only | M | ✓ |
| G2 | Audit: `artifact.edit`, `artifact.restore` (file, version; never content) | M | ✓ |
| **H** | **Table view: add, delete, edit rows** | | |
| H1 | Workflow as a table, one row per step: # · Phase · Runs on · Command · Success check · Captures · When · On failure; Table ↔ YAML always in step | M | ✓ |
| H2 | Edit a row in a form (node dropdown, command, success text, captures, condition, on failure) | M | ✓ |
| H3 | Add a row: the user picks the **phase** (only the run's phases) and the **step number** in it (positions listed with the step now there, or "At the end"); the new row takes that position and the rows after it renumber, in the table and the YAML | M | ✓ |
| H4 | Delete a row, with a confirmation and Undo | M | ✓ |
| H5 | Move a row (phase + step number, as H3) and Duplicate | S | |
| H6 | Problems shown on their row (marker + plain-language tooltip) | M | ✓ |
| H7 | Validation rules as a table, one row per CIQ column rule (Sheet · Column · Required · Type / pattern · Allowed values · Cross-check), same add / delete / edit | S | |
| H8 | Rows the platform adds (backups by convention, checksums, record loops) shown as "added automatically" and locked — rebuilt from the user's rows; they count in the numbering, nothing is inserted inside them | M | ✓ |
| H9 | A new or changed row is checked at once (C1) | S | |

## Acceptance

1. At the step 6 review, Edit opens the workflow and the scripts in an editor with highlighting and line numbers.
2. Validate lists the step 6 problems in plain language with line numbers; fixing a line and validating again clears it.
3. Save creates a version; History shows v1 (generated) and v2 (yours); Diff shows the change; Restore v1 brings the
   generated text back as v3.
4. A failed step 6 whose edited workflow validates offers **Continue with my edit**; step 7 and step 9 then run the
   edited workflow (visible in the MOP and the step 9 execution).
5. Re-run step after an edit: the new generated file is v(N+1); the banner offers Compare / Restore of the user's
   version.
6. Read-only everywhere but the run's own review; no Edit for a View-only role; another user's run is refused.
7. A later run of the same use case that reuses the cache gets the edited (approved) files.
8. Existing behaviour without edits is unchanged (all current tests pass; PGW / MRF replays unchanged).
