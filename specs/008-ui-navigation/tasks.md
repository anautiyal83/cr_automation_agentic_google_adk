# Tasks: UI navigation (spec 008)

- [X] U001 `core/auth.py`: screen `queue` (derived from `progress` for older roles), `cache` admin screen, `outputs` label "Run Artifacts"
- [X] U002 Pipeline Queue page (full `QueuePanel`, Remove guarded by `queue`); removed from Live Run; banner text
- [X] U003 Rename Output Documents → Run Artifacts (sidebar, header, breadcrumbs, texts, README/CLAUDE.md)
- [X] U004 Artifact Cache page (`ui/pages/cache_section.py`, out of the Run Artifacts page)
- [X] U005 `ui.components.confirm` + confirmations: queue Remove, cache Clear / Clear All, LLM Usage Clear
- [X] U006 LLM Usage tree: use case → step → calls (lazy)
- [X] U007 Tests (`queue` level derivation, labels) + preview check (admin, analyst)
- [X] U008 LLM Usage: each use case a bordered box like the tables, its steps one bordered list (user request)
- [X] U009 Dashboard / New Run queue: status as a coloured dot (tooltip = detail) with a legend; also the last 5 finished jobs; Waiting violet everywhere (donut too) (user request)
- [X] U010 Mark as finished (U-D7) — button next to View full details / Browse outputs on the completed run (user): `finished_at`/`finished_by`, `runner.mark_finished`, Live Run button, `_show_latest_run` skips finished runs, trail `run.finish`, test

## Check 2026-11-04 (preview, DB copy)
- ana (analyst role stored before spec 008): sidebar Dashboard, Live Run, Pipeline Queue | Run Artifacts, Run History | LLM Usage, Artifact Cleanup — queue level derived from Live Run (Update): Remove on her waiting job; Cancel keeps it, confirm removes it; `/?page=cache` refused (recorded)
- admin: Artifact Cache under Run Artifacts; Clear All Cache and LLM Usage Clear ask first; LLM Usage: use case → steps in pipeline order → calls (date + time, 25 per page), headers with calls / tokens / cost / time
- Mark as finished (preview, ana owns the latest completed MRF run): button beside View full details / Browse outputs on step 10; click → "marked as finished — it stays in Run History", Live Run idle, still idle after reload

## Bugs
- U-B1 (user, 2026-11-04): Mark as finished left the Final Report step details (overview, artifacts, test-case count) on Live Run — `_reset_to_default` reset the banner and step badges but kept the run folder, UC, step durations and the open step panel (Cancel had the same gap). Fixed: the reset forgets the run's folder/UC/durations and re-selects step 1 on Overview → "No run selected"; Artifacts / Test cases empty, KPIs "—" (preview-checked)
