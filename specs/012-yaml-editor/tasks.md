# Tasks: Edit generated files at a review (spec 012)

- [x] Y001 `shared/run_files.py`: editable files per review, `read(state, key)` (run folder first), versions (save / list / read / restore), edited-file markers, line diff
- [x] Y002 Steps 5, 6 (scripts), 7, 9 read the workflow / rules / CIQ JSON from the run folder (`run_files.read`)
- [x] Y003 `shared/edit_checks.py`: YAML parse errors with lines; workflow (step 6 validator on the flat form, run context); rules / CIQ JSON (schema); scripts (syntax + contract); problems in plain language with line numbers
- [x] Y004 Editor dialog `ui/pages/file_editor.py`: tabs, code editor, Validate, Save (version), Diff, History, Restore, read-only mode, refused save on a changed file
- [x] Y005 Review panel: Edit on editable file rows; Continue with my edit (generation reviews only when valid); re-run banner (Compare / Restore)
- [x] Y006 Pipeline: continue a failed generation step after a valid edit; cache refresh from the run folder; re-run records a generated version
- [x] Y007 Access + audit: `@requires("progress", "artifact.edit")`, own run; `artifact.edit`, `artifact.restore`
- [x] Y008 Tests (`test_run_files.py`, `test_edit_checks.py`, `test_workflow_table.py`); full suite (348 passed); MRF rebuild unchanged (`test_workflow_table`)
- [x] Y009 Preview check (EIR step 6 review, DB + run copy): Edit files, table add / Undo / save (versions), Diff (folded), YAML
  with problem lines, History, MOP preview, draft on close, read-only Open (step 4 file at the step 6 review); docs.
  Not driven in the preview: Continue with my edit into step 7 (the preview never runs the pipeline) — first real run
