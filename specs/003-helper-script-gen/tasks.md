# Tasks: Helper Script Generation

**Input**: [spec.md](spec.md)

## Phase 1: Implementation (done)

- [X] T001 Skill skeleton `skills/helper_script_gen/`; `helper_scripts` field on NetworkConfigGen SkillOutput (0e4cef7)
- [X] T002 `HelperScriptGenSkill.generate()` — discover scripts, build CIQ summary, split request/response XMLs, LLM per script, clean code (1cbef81)
- [X] T003 Prompts for `create_xml.py`, `parse_response.py`, `create_html_report.py` — `skills/helper_script_gen/prompts.py` (1cbef81)
- [X] T004 `agents/helper_script_gen_agent.py` — write to `run_dir/scripts/`; wired as agent index 4 sharing step 6; step 6 milestone moved to this agent (1cbef81)
- [X] T005 Step 6 milestone shows `scripts/*.py` as extra artifacts — `PipelineAgent._resolve_artifacts` (1cbef81)
- [X] T006 CLI LLM auth via DB settings fallback — `shared/llm.py` (cff5d3c); verified 3 scripts generated from CLI

## Phase 2: Open

- [X] T007 [H-1] Set `step_6_status` and merge the error into `step_6_error.json`; the pipeline clears a shared step's error file only before its first agent (`_first_agent_of_step`)
- [X] T008 [H-2] Compile each generated script; fail step 6 with the compile error and feed it to a single regeneration attempt
- [X] T011 [H-5] CLI contract from the workflow invocations: prompt section, argparse check, one retry, generic prompts
- [ ] T009 [H-3] Store script contents for caching (with 002 T007)
- [ ] T010 [H-4] End-to-end check once 004 unblocks step 9: run `create_xml.py` with CIQ args and diff against the sample request XML
