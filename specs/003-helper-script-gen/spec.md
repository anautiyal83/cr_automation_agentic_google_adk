# Feature Specification: Helper Script Generation for LOCAL Workflow Steps

**Feature Branch**: `003-helper-script-gen` (implemented on `feature/skill-packages`)

**Created**: 2026-11-02 (retroactive, from commits 0e4cef7, 1cbef81, cff5d3c)

**Status**: Implemented — H-1, H-2, H-5 fixed; open H-3, H-4

**Parent**: [001-adk-orchestrator-pipeline](../001-adk-orchestrator-pipeline/spec.md) FR-029

**Input**: The generated workflow runs Python scripts on the `LOCAL` node (e.g. `create_xml.py`, `parse_response.py`, `create_html_report.py`). These scripts don't exist per UC. Generate them from sample request/response XMLs and CIQ structure so the workflow is executable.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Generate Scripts Referenced by the Workflow (Priority: P1)

After workflow generation (step 6), the pipeline finds every `send: python <script>.py …` on LOCAL steps and generates each script via the LLM. Scripts are written to `run_dir/scripts/` and shown as extra artifacts at the step 6 milestone.

**Independent Test**: Run the skill from CLI against an existing run (verified ad hoc in cff5d3c; `test_step.py` has no `helper_script_gen` entry yet) — generates `create_xml.py`, `parse_response.py`, `create_html_report.py`.

**Acceptance Scenarios**:

1. **Given** a workflow with 3 LOCAL python steps, **When** step 6 completes, **Then** 3 scripts exist in `run_dir/scripts/` and appear at the step 6 milestone
2. **Given** a workflow with no LOCAL python steps, **Then** the agent passes with no scripts
3. **Given** sample request XMLs, **Then** `create_xml.py` builds SPML modification XML from CIQ data arguments, skips UNCHANGED fields, and combines all modifications into a single `batchRequest`
4. **Given** sample `batchResponse` XMLs, **Then** `parse_response.py` reports success/failure per request
5. **Given** pre/post LDAP outputs, **Then** `create_html_report.py` compares them against CIQ values

### User Story 2 - Re-generate on Workflow Retry (Priority: P2)

When step 7 or 9 routes a retry to step 6, scripts are regenerated together with the workflow (agent index 4 runs after index 3).

### Edge Cases

- No sample XMLs uploaded → generate from CIQ columns + MOP only; flag in the milestone
- One script fails to generate → step status failed, other scripts still written
- Workflow references a script name not in the known set → generate with a generic prompt

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `helper_script_gen` agent MUST run after `network_config_gen` and share display step 6; the step 6 milestone MUST follow it
- **FR-002**: Script discovery MUST parse `send: python <name>.py <args>` from the workflow
- **FR-003**: Inputs MUST come from `requirements.doc_extracted` (`xml_files`, `ciq_sheets`) and `developer_answers`; request vs response samples are split by `batchResponse`
- **FR-004**: Generated code MUST be stripped of markdown fences and written to `run_dir/scripts/<name>`
- **FR-005**: Status is `passed` only if every needed script was generated; on failure the agent MUST set `step_6_status=failed` and merge its error into `step_6_error.json` (keeping `network_config_gen`'s) *(done — H-1)*
- **FR-006**: Generated scripts MUST compile before the step passes *(done — H-2)*
- **FR-009**: The workflow is the source of truth for each script's CLI: every flag of every `python <script>` invocation MUST be defined by the script's argparse, spelled exactly; one regeneration with the violations listed, then step 6 fails *(done — H-5 / 004 DEP-11)*
- **FR-007**: Scripts MUST be cacheable per UC by content (see [002](../002-artifact-cache/spec.md) C-2)
- **FR-008**: LLM auth MUST work from CLI (DB settings fallback, cff5d3c)

## Known Issues

| ID | Severity | Issue | Evidence |
|----|----------|-------|----------|
| H-1 | High | **Fixed 2026-11-02.** Agent ignores `output.status`: never sets `step_6_status` nor writes `step_6_error.json`, so a failed or partial generation shows **passed** at the step 6 milestone and can be cached | `agents/helper_script_gen_agent.py:51-75` |
| H-2 | Medium | **Fixed 2026-11-02** (compile check + retry, `check_script_contract`). No syntax/compile check of generated scripts; errors surface only at step 9 execution | `skills/helper_script_gen/skill.py` |
| H-3 | High | `artifacts["helper_scripts"]` holds paths; artifact cache writes paths as file content (= 002 C-2) | `agents/helper_script_gen_agent.py:66-68` |
| H-4 | Medium | Step 9 has never executed a command (004 DEP-2), so the scripts are untested end to end | 004 research F8 |
| H-5 | High | **Fixed 2026-11-02.** Scripts invented their own flag names (`--refinmoc_name`, `--packet_data_network_type`) while the workflow passed `--refinmocName`, `--pdn_type`; prompts hard-coded UC-specific flag names. Fix: flags parsed from every workflow invocation (`contract_flags`), given to the LLM as a CLI contract, checked in the generated argparse (`check_script_contract`), one retry; UC-specific flag lists removed from the prompts. Same for output: the step's success regex is given as an output contract and checked against the script's messages (`check_output_contract`). The old prompts hard-coded `XML created: …` while workflows expected other text | 004 DEP-11; `tests/test_generation_contracts.py` |

## Success Criteria *(mandatory)*

- **SC-001**: For PGW_RDS_1058, all 3 scripts are generated, compile, and `create_xml.py` output validates against the sample request XML structure
- **SC-002**: A failed generation is visible as a failed step 6 at the milestone

## Assumptions

- The three script roles (create XML, parse response, HTML report) cover current SPML/LDAP UCs
- Scripts run on the LOCAL node with the Python available to the cli-automation JAR
