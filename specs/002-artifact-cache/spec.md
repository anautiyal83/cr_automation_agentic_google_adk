# Feature Specification: UC-Level Artifact Cache

**Feature Branch**: `002-artifact-cache` (implemented on `feature/skill-packages`)

**Created**: 2026-11-02 (retroactive, from commits 4e4f568, d5cd4a0, 36eb46a)

**Status**: Implemented — **not working in live pipeline** (open bugs C-1..C-4)

**Parent**: [001-adk-orchestrator-pipeline](../001-adk-orchestrator-pipeline/spec.md) FR-030

**Input**: When a UC's generation artifacts (requirements, validation-rules, json-output, workflow, helper scripts) have been produced successfully, cache them per UC so later runs of the same UC skip the LLM calls for those steps.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Skip Regeneration on Repeat Runs (Priority: P1)

An engineer re-runs the same UC (e.g. to retest step 8/9). Steps 1–4 and 6 load cached artifacts instead of calling the LLM, and are shown as passed immediately.

**Independent Test**: Run a UC to completion; start a new run of the same UC; `logs/flow.log` shows `CACHE HIT` for `requirements_gatherer`, `ciq_validation_json_gen`, `network_config_gen`, `helper_script_gen`.

**Acceptance Scenarios**:

1. **Given** a previous run passed steps 1–6 for UC X, **When** a new run for UC X starts, **Then** the four cacheable agents are skipped and their artifacts are written to the new run directory
2. **Given** cached artifacts, **When** a step is skipped, **Then** the UI shows that step as passed (step_started + step_completed events)

### User Story 2 - Invalidate Downstream on Re-run (Priority: P1)

When the engineer re-runs from a generation step, the cache for that step and every cacheable step after it is cleared, so stale artifacts are not reused. Steps before it keep their cache.

**Acceptance Scenarios**:

1. **Given** a re-run targets `network_config_gen` (step 6), **Then** `network_config_gen` and `helper_script_gen` caches are cleared; `requirements_gatherer` and `ciq_validation_json_gen` remain
2. **Given** a re-run targets a non-cacheable agent downstream of all cacheable ones (step 8 or 9), **Then** no cache is cleared *(currently violated — C-1)*

### User Story 3 - Manage Cache in UI (Priority: P2)

The engineer sees cached UCs in **Output Documents** (agent, status, size, timestamp) and can clear one UC or all.

**Acceptance Scenarios**:

1. **Given** cached UCs exist, **When** Output Documents loads, **Then** a cache table lists them with clear buttons
2. **Given** user clicks clear for UC X, **Then** `data/cache/X/` is removed

### Edge Cases

- A cacheable agent fails → nothing is cached for it
- A cached artifact file is missing or empty → treat as a miss (`has_cache` False)
- A cached artifact is missing but metadata says passed → must not be recorded as passed in the first place (C-3)
- Requirements changed (new uploads, new answers) → cache is keyed only by UC name; there is no content hash invalidation *(gap — see Assumptions)*

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Cache MUST live at `data/cache/<uc_name>/` with `_cache_meta.json` recording per-agent status and timestamp
- **FR-002**: Cacheable agents and files: `requirements_gatherer` → `requirements.json`; `ciq_validation_json_gen` → `validation-rules.yaml`, `json-output.yaml`; `network_config_gen` → `workflow.yaml`; `helper_script_gen` → `scripts/*.py`
- **FR-003**: Before each cacheable agent, PipelineAgent MUST check the cache and, on hit, write artifacts into the run directory and `artifacts` state (requirements into `requirements_store`), publish step events as passed, and skip the agent
- **FR-004**: After a cacheable agent, PipelineAgent MUST save its artifacts only if all its display steps passed (status explicitly set, not defaulted)
- **FR-005**: An agent MUST be recorded `passed` in meta only if every one of its cache files was written with non-empty **content** (not paths)
- **FR-006**: On re-run to agent A, the cache MUST be cleared for A (if cacheable) and for every cacheable agent *after A in pipeline order*; nothing earlier
- **FR-007**: Cache check, save, and clear failures MUST be logged at ERROR with traceback; hits and misses at FLOW level
- **FR-008**: UI MUST list cached UCs and allow per-UC and full clear from Output Documents

## Known Issues

| ID | Severity | Issue | Evidence |
|----|----------|-------|----------|
| C-1 (= 001 OB-1) | High | `clear_from_step()` uses `agent_order.index(from_agent)` over the 4 cacheable agents; a non-cacheable agent raises ValueError → `idx = 0` → **whole UC cache wiped** on any re-run at step 5, 7, 8, or 9 | `storage/artifact_cache.py:167-181`; `logs/trace.log` 2026-11-02 13:00:37 "RERUN — cleared cache from network_simulator_setup onward" preceded by clears of all 4 agents |
| C-2 | High | `helper_script_gen` stores `artifacts["helper_scripts"] = {name: path}`; `save_cache()` writes `str(text)`, so cached "scripts" contain the **path string**, not code. A cache hit then writes those paths into `run_dir/scripts/*.py` | `agents/helper_script_gen_agent.py:66-68`, `storage/artifact_cache.py:126-129`, `orchestrator/pipeline_agent.py:165-172` |
| C-3 | Medium | `save_cache()` skips missing artifact keys but still marks the agent `passed`, and logs `len(entries)` not files written. `has_cache()` then returns False — likely cause of the `ciq_validation_json_gen` miss at 12:36:53 (saved 12:29:20, no clear logged) | `storage/artifact_cache.py:118-138` |
| C-4 | Medium | Save path gates on `step_{N}_status` defaulting to `"passed"`, and `helper_script_gen` never sets `step_6_status` on failure → partial or failed output can be cached. Save errors logged at DEBUG | `orchestrator/pipeline_agent.py:229-249` |
| C-5 | Low | `ArtifactCache()` uses relative `data/cache` (CWD-dependent) | `storage/artifact_cache.py:54` |

## Success Criteria *(mandatory)*

- **SC-001**: Second run of a UC with no changes skips all 4 cacheable agents (0 LLM calls for steps 1–6)
- **SC-002**: Re-run at step 8 or 9 leaves the cache intact
- **SC-003**: Scripts loaded from cache are byte-identical to the scripts originally generated

## Assumptions

- Cache key is UC name only. Changing uploads or answers for the same UC requires a manual clear. Content-hash invalidation is out of scope for now
- Steps 5, 7, 8, 9, 10 are never cached (they test live artifacts or start processes)
