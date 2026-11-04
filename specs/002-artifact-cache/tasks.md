# Tasks: UC-Level Artifact Cache

**Input**: [spec.md](spec.md)

## Phase 1: Implementation (done)

- [X] T001 `storage/artifact_cache.py` — `ArtifactCache` with `has_cache`, `load_cache`, `save_cache`, `clear_cache`, `clear_from_step`, `list_cached_ucs`; `_AGENT_CACHE_MAP`, `CACHEABLE_AGENTS` (4e4f568)
- [X] T002 `orchestrator/pipeline_agent.py` — cache check before each cacheable agent, inject artifacts, publish passed events, skip (4e4f568)
- [X] T003 `orchestrator/pipeline_agent.py` — save on success; clear from re-run target on milestone re-run (4e4f568)
- [X] T004 Cache management UI in Settings (4e4f568), moved to Output Documents (d5cd4a0) — `ui/pages/output_viewer_section.py`
- [X] T005 CACHE CHECK logging at FLOW, errors at ERROR (36eb46a)

## Phase 2: Bug Fixes (open)

- [ ] T006 [C-1] `clear_from_step()`: resolve `from_agent`'s position in the **full** pipeline order and clear only cacheable agents at or after it; no-op for steps 8/9; never fall back to index 0
- [X] T007 [C-2] (done with OB-14, `save_cache` reads the script files) Cache helper scripts by **content**: either store contents in `artifacts["helper_scripts"]` or have `save_cache` read files when values are paths; on hit, keep `artifacts["helper_scripts"]` as `{name: path}` for downstream
- [ ] T008 [C-3] `save_cache()`: count files actually written; mark `passed` only if every entry was written non-empty; log the real count
- [ ] T009 [C-4] Save only when display-step status is explicitly `passed`; make `helper_script_gen` set `step_6_status`/`step_6_error.json` on failure (see 003 H-1); log save errors at ERROR
- [ ] T010 [C-5] Resolve cache dir from project root, not CWD
- [ ] T011 Verify SC-001..SC-003: two consecutive runs of PGW_RDS_1058 + a step 8 re-run; check `flow.log` and `_cache_meta.json`
- [ ] T012 Unit tests for `ArtifactCache` (`tests/test_artifact_cache.py`): save/load round-trip, scripts dir, clear_from_step for every agent name
