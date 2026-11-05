# Feature Specification: UI navigation — Pipeline Queue and Artifact Cache pages, confirmations, LLM Usage tree

**Feature Branch**: `feature/skill-packages` | **Created**: 2026-11-04 | **Status**: In progress

## Request (2026-11-04, user)

1. Pipeline queue gets its own page: sidebar link **Pipeline Queue** below Live Run.
2. Rename **Output Documents** to **Run Artifacts**.
3. **Artifact Cache** becomes its own page: sidebar link below Run Artifacts.
4. Every delete and clear control asks for confirmation.
5. LLM Usage: each **use case** collapses; its **pipeline steps** (also collapsible) are inside it, and each step's
   **call log** is inside the step.

## Decisions

| ID | Topic | Decision |
|----|-------|----------|
| U-D1 | Queue page permission | New screen `queue` (Workspace). Roles saved before this change get the Live Run level for it (View → View, Update → Update) until the role is saved again. Update = Remove own waiting jobs |
| U-D2 | Queue on other pages | The full queue (all jobs, Remove) is only on Pipeline Queue. Dashboard and New Run keep the compact view (running + waiting). Live Run no longer embeds it; its "waiting" banner points to Pipeline Queue |
| U-D3 | Artifact Cache page access | Admin only (as before: the cache is shared by every user's runs). Screen `cache` in `ADMIN_SCREENS`, sidebar group Results |
| U-D4 | Rename | Label only: the screen key stays `outputs` (stored role permissions keep working) |
| U-D5 | Confirmations | One shared dialog (`ui.components.confirm`): what is deleted/cleared and that it cannot be undone; the action runs only on the confirm button. Covers: queue Remove, Artifact Cache Clear / Clear All, LLM Usage Clear, plus the existing dialogs (run Delete, Bulk Cleanup, Cleanup Old Runs, user/role/vendor Delete) |
| U-D6 | LLM Usage layout | Summary cards, then **Per Use Case**: one collapsible row per use case (calls, tokens, cost, time in the header), inside it one collapsible row per pipeline step in pipeline order, inside it that step's calls (newest first, date + time). **Per Model** stays as a table. The separate Per Pipeline Step table and global Call Log go away (their data is inside the tree). Step call tables are built when the step is opened |
| U-D7 | Finishing a run (2026-11-04, user: "when pipeline completes successfully, user should be given option to mark it as completed or finished, and then it should be removed from the live Run page") | A completed run shows **Mark as finished** on Live Run (owner or admin, Update on Live Run). It stores `pipeline_runs.finished_at` / `finished_by` (status stays `completed`), records `run.finish` and clears Live Run; Live Run never brings a finished run back on its own (Run History → Open still shows it, without the button). Failed runs get the button too (2026-11-05, OB-20: user: "there is no way to remove it" — a failed run otherwise stayed on Live Run with no way out) |

## Acceptance

- Sidebar: Workspace = Dashboard, New Run, Live Run, Pipeline Queue; Results = Run Artifacts, Artifact Cache (admin), Run History
- A non-admin with Live Run access sees Pipeline Queue without a role change; nobody but the admin sees Artifact Cache
- No delete/clear/remove acts before a confirmation
- LLM Usage: use case → step → calls, all collapsed at first; totals equal the summary cards
