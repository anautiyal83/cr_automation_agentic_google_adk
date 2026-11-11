# Plan: Edit generated files at a review (spec 012)

| # | Component | Files | Change |
|---|-----------|-------|--------|
| C1 | Run files | `shared/run_files.py` (new) | The editable files of a review (key → path: `validation_rules_yaml` → `<UC>_validation-rules.yaml`, `json_template_yaml` → `<UC>_json-output.yaml`, `workflow_yaml` → `<UC>.yaml`, scripts); `read(state, key)` (run folder first, then session state); versions: `save_version`, `versions`, `read_version`, `restore`, `edited_files(run_dir)` |
| C2 | Checks | `shared/edit_checks.py` (new) | `check(kind, text, run_dir, requirements) → [Problem(line, plain, technical)]`: YAML parse (line of the error); workflow → `structure.flattened` + `validate_workflow` with the run's command samples / CIQ sheets / expected coverage; validation rules / json output → `schema_validator`; scripts → `compile()` + `check_script_contract` / `check_output_contract` against the workflow; line lookup for "Step '<send>'" messages; plain text via `shared.plain_errors` |
| C3 | Steps read the run folder | `agents/ciq_validation_json_qa_agent.py`, `network_config_mop_qa_agent.py`, `helper_script_gen_agent.py`, `network_config_qa_agent.py` | `run_files.read(state, key)` instead of `artifacts.get(key)` (Y-D6) |
| C4 | Pipeline | `orchestrator/pipeline_agent.py` | Continue after an edit of a failed generation step: clear its error file / status (the UI asks only when the edit validates); cache refresh from the run folder for edited agents (Y-D8); a re-run that overwrites an edited file records the regenerated version (Y-D7) |
| C5 | Editor UI | `ui/pages/file_editor.py` (new), `ui/pages/progress_section.py` | Full-screen editor dialog (pre-built, like the questionnaire): file tabs, `ui.codemirror`, Validate / Save / Diff / History / Revert, problems panel (line links), read-only mode; review file rows get **Edit**; review shows **Continue with my edit** (Y-D5) and the re-run banner (Y-D7); guarded `@requires("progress", "artifact.edit")` + own run |
| C6 | Diff | `shared/run_files.py` | Line diff (difflib) between any two versions, rendered with + / − rows |
| C7 | Tests | `tests/test_run_files.py`, `tests/test_edit_checks.py` | Versions / restore / concurrency; checks with line numbers on the EIR workflow; agents read the run folder |
| C8 | Docs | CLAUDE.md, README, `tasks.md` | Editing at reviews; run folder authoritative |

## Steps

1. C1 + C3 (no UI): the run folder becomes authoritative; all tests and the PGW / MRF replays unchanged.
2. C2: checks with line numbers, tested on the EIR step 6 workflow.
3. C5 + C6: the editor at the step 6 and steps 3–4 reviews; versions, diff, history, restore.
4. C4: Continue with my edit, re-run banner, cache refresh.
5. Preview check (DB copy): EIR step 6 review — edit, validate, continue; MRF unchanged. C8 docs.
