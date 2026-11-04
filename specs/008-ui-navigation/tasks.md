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

## Check 2026-11-04 (preview, DB copy)
- ana (analyst role stored before spec 008): sidebar Dashboard, Live Run, Pipeline Queue | Run Artifacts, Run History | LLM Usage, Artifact Cleanup — queue level derived from Live Run (Update): Remove on her waiting job; Cancel keeps it, confirm removes it; `/?page=cache` refused (recorded)
- admin: Artifact Cache under Run Artifacts; Clear All Cache and LLM Usage Clear ask first; LLM Usage: use case → steps in pipeline order → calls (date + time, 25 per page), headers with calls / tokens / cost / time
