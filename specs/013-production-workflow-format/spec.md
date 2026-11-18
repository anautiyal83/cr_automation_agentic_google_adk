# Feature Specification: Workflows in production format

**Feature Branch**: `feature/yaml-editor` | **Created**: 2026-11-11 | **Status**: In progress (Stage A)

## Request (2026-11-11, user)

"Lets compare YAML from local and production and see the gaps" (EIR_1029_TAC_LOADING_EIR) → "its a different jar but
the code is same which we are running" → "we need to keep YAML exactly like production" → "Production's CIQ JSON has
no crGroups wrapper, with nodeGroups at the root — ignore this as this is an enhancement".

## Goal

Step 6 writes the execution workflow the way production workflows are written: same top-level layout, node model,
phase keys and names, step fields, validation shape, retry / checksum / guard patterns, rollback-only handling and
YAML scalar style. A production engineer reading our workflow sees a production workflow.

**Not changed (our enhancement)**: the CIQ JSON layout (`crGroups[].nodeGroups[]`, records under
`configSequences`). CIQ values in the workflow therefore keep our references — `${<NODE>.niamID-<n>}`,
`${configSequences.0.records.0.data.<Column>}`, `${rec.data.<Column>}` in record loops — because production's bare
`${niamID}` / `${BULK_PROVISIONING_FILE}` are empty with our layout (probe P-7).

## Rule for every stage (user, 2026-11-11)

"These changes should be generic and must be followed for further UC as well if applicable." Every change is a
rule over what the documents show — command kinds, node types from the CIQ / vendor profile, phases, transfers —
never a use case's names, commands or paths. EIR, MRF and PGW are the test cases, not the design. A rule that does
not apply to a use case leaves it unchanged.

## Sources

- **Corpus**: the 24 production workflows bundled in `lib/cli-automation-standalone.jar` (`templates/yaml/`, CFX, DPA,
  DSR ×4, EIR, MRF ×3, PGW ×2, SBC ×12, WBX) + `templates/schema/cli-automation-schema_v1.2.html`. The EIR one is
  byte-identical to `CR-Config/EIR/production`. Conventions in [production-style.md](production-style.md) come from
  counts over the whole corpus, not from one file.
- **Comparison** EIR local vs production (2026-11-11): [uc-eir-1029-tac-loading.md](uc-eir-1029-tac-loading.md).

## JAR probes (2026-11-11, our `cli-automation-standalone.jar`, LOCAL steps on the dev machine)

| # | Form (production) | Result |
|---|-------------------|--------|
| P-1 | Phase keys `activity_configuration` / `rollback_configuration` (and camelCase) | both run |
| P-2 | `when: "${ROLLBACK_ONLY != \"true\"}"` — the whole expression inside `${…}`, phase + step | works (true runs, false skips) |
| P-3 | `criteria.expr: ${A == "x"}`, `${BAD == ""}` | works |
| P-4 | register `- name: REPO_IP / when: ${IPTYPE == "IPv6"} / value: …` | works |
| P-5 | `validation.warning` with `vars`; `type: loop` over `${LOOP_RETRY}` with `when` + `break_when`; `hide_when_skipped`; `globals.defaults` | works (loop stopped after the first success) |
| P-6 | Double quotes in a LOCAL `send` | **Linux: fine. Windows dev machine only**: the JAR runs `bash -c <cmd>` for commands ≤ 8000 chars on Windows and Windows' command line drops `"`; longer commands run from a temp `.sh` file (quotes intact). `WindowsCommandAdapter` also rewrites `/word` to `C:/word` (breaks awk `/re/`) |
| P-7 | Bare `${niamID}`, `${BULK_PROVISIONING_FILE}` with our CIQ JSON | **empty**; `${EIR1.niamID-1}`, `${configSequences.0.records.0.data.X}`, `${rec.data.X}` work |
| P-8 | `\\n` inside a **double-quoted** YAML `send` | reaches the shell as a real newline; plain and block (`>-`, `|-`) scalars keep `\n` |

## Decisions

| # | Decision | Status |
|---|----------|--------|
| PF-D1 | CIQ JSON layout and CIQ value references stay ours (user) | Decided |
| PF-D2 | Node model: one `niam_<type>_server` (`niam_<type>_<n>` for several NIAM entries) per node, login via NIAM runtime params; pods / DS servers / other hosts are **nested sessions** on that node (`oc exec … -- bash`, `ssh …` + `expect_reply`, then `exit`) with production's generic prompt — no node per prompt seen in a log | Proposed |
| PF-D3 | Runtime parameters named as production: `NIAM_IP`, `M2MPORT`, `M2MUSER`, `M2MPASSWORD`, `REPO_USER`, `REPO_PASSWORD`, `ORDER_NO`, `CHILD_REQ_ID`, `PARENT_REQ_ID`, `NODE_NAME`; repo nodes `repo_server` (ssh) + `repo_server_sftp`; backup path `BACK_UP_LOCATION_REPO: /repo-server/config/CR_AUTOMATION/${CHILD_REQ_ID}/${NODE_NAME}` + `/BACKUP/`, `/Activity/`, `/Rollback/` (replaces `${ORDERNO}` paths; still a naming convention — the rollback rule holds) | Decided (user 2026-11-11) |
| PF-D4 | Input files: `LOCAL_PATH` presence check → repo → node, sha256 on each hop with production's retry loop + guard. **No GitLab download** — input files must be on the automation server | Decided (user 2026-11-11) |
| PF-D5 | Step 9 runs the JAR under Linux (WSL) so LOCAL steps behave as in production (no Windows quote / path rewriting); WSL + Java must be installed on the pipeline machine | Decided (user 2026-11-11) |
| PF-D6 | The Windows-only rules leave step 6: no autofix / validator rule against `"` in LOCAL steps | Proposed |
| PF-D7 | `dump_yaml` writes multi-line sends as `>-` blocks and never a double-quoted scalar with a backslash (P-8) | Proposed |
| PF-D8 | Every folder is a variable at the top (`globals.vars`), steps use its name (user 2026-11-18): helper scripts `${ACTIVITY_PYTHON_SCRIPT_PATH}/<uc prefix>_<script>.py` (`execution.script_path`, production's CLICR folder — one folder for every UC, so the UC prefix, as production), the order's files `${LOCAL_PATH}/…` (`execution.local_path`, created by `nc_localdir`), node folders `<TYPE>_<FOLDER>_PATH`; scripts read / write only files their command names. Step 9 and the export point the two automation-server folders at their own (`--var` overrides `globals.vars`, probed 2026-11-18) | Decided (user 2026-11-18) |

## Acceptance

1. EIR, MRF, PGW re-generated: a diff of structure against production (`production-style.md` checklist) shows only
   PF-D1 differences and use-case logic gaps (tracked in spec 009), no format differences.
2. The workflows pass `generate` (MOP) and step 9 on the simulator.
3. Spec 012 editor tables still read and rebuild them (phase keys, auto rows).
