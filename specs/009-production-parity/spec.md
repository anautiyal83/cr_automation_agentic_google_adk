# Feature Specification: Production parity — compare generated workflows with production, per use case

**Feature Branch**: `feature/skill-packages` | **Created**: 2026-11-04 | **Status**: In progress (MRF analysed)

## Request (2026-11-04, user)

"Refine our UC by comparing to production artifacts. Compare not command by command but logically: did we miss
any step or any core concept. Start with MRF … find out the gaps and fix. First check and show details. Also list
why we missed each gap — is it a requirement gap … later we use this knowledge to create a guideline on how to
prepare requirement documents (CIQ, MOP, I/O documents, command outputs …) so the pipeline asks the minimum
questions and generates a proper YAML with the fewest mistakes. We will do this for each UC. Document it."

## Process (each use case)

1. **Inputs**: the pipeline's YAML (`<CR-Config>/<UC>/local/`) and the production YAML (`<CR-Config>/<UC>/production/`),
   plus what the pipeline was given (the run's or cache's `requirements.json`: MOP, CIQ sheets, command samples,
   developer answers/notes).
2. **Logical comparison** — per concept, not per command: phases and their purpose, data flow (where inputs come
   from, where outputs go), branching (per version, per row, per existing state), backup/rollback contract,
   failure handling (what stops, what triggers rollback), verification strength, run modes, reporting.
3. **Classify each gap by cause** (below) and by severity: **Critical** (wrong or destructive result),
   **High** (activity fails or leaves the node inconsistent), **Medium** (weaker check, missing traceability),
   **Low** (convention, reporting).
4. **Record** the analysis in `uc-<uc>.md`, the fixes in `tasks.md`, generic learnings in
   [requirement-guidelines.md](requirement-guidelines.md) (what a document must contain so the gap cannot recur).
5. **Fix** generically (never UC-specific): pipeline prompts/validator/autofix/structure for P and F gaps; step 1
   questions for R and C gaps; the guideline for all of them. Re-run the UC and compare again.

## Gap causes

| Code | Cause | Fixed by |
|------|-------|----------|
| **R** | Requirement gap — the documents do not contain it (command, source, convention, topology) | Guideline + a step 1 question when the documents lack it |
| **C** | Conflicting / ambiguous requirement — documents or answers contradict each other or leave the logic open | Guideline + step 1 conflict detection (ask to confirm) |
| **P** | Pipeline gap — the information was there; the pipeline did not use it or got the logic wrong | Pipeline fix (prompt, validator, autofix, structure) + test |
| **F** | Framework knowledge gap — the cli-automation schema supports it, the pipeline never generates it | Teach step 6 (prompt + deterministic structure) + JAR probe |
| **E** | Environment / framework convention difference — not a gap (e.g. NIAM handled by `ne_id`) | Note only |

## Use cases

| UC | Analysis | Status |
|----|----------|--------|
| MRF_ANNOUNCEMENT_LOADING | [uc-mrf-announcement-loading.md](uc-mrf-announcement-loading.md) | Gaps listed, fixes proposed |
| PGW_RDS_1058_IFC_CAPABILITY_MODIFICATION | — | Pending (production YAML needed) |
