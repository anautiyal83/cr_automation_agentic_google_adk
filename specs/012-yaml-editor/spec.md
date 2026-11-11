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
