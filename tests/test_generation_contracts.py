"""Generic generation contracts (spec 004 DEP-5 / DEP-11) — deliberately not tied to one UC.

- requirement gathering extracts real command → output samples from any shell transcript
- step 6 rejects validation/register regexes that don't match the recorded real output,
  and self-corrects with that feedback
- helper scripts must accept exactly the flags the workflow passes them

    python -m pytest tests/test_generation_contracts.py -v
"""
from __future__ import annotations

import asyncio

from orchestrator.pipeline_agent import _first_agent_of_step
from skills.helper_script_gen.skill import (
    HelperScriptGenSkill,
    check_script_contract,
    contract_flags,
)
from skills.network_config_gen.skill import NetworkConfigGenSkill, SkillInput
from skills.network_config_gen.validator import check_against_samples, validate_workflow
from skills.requirement_gathering.parsers.command_output_parser import parse_command_outputs

TRANSCRIPT = """\
=== core_router.log ===
router1# show interfaces summary
Interface   Status   Protocol
Gi0/0       up       up
Gi0/1       down     down
router1# show version | include uptime
router1 uptime is 12 weeks, 3 days
router1#
=== app_server.log ===
[admin@app01 ~]$ systemctl is-active billing
active
[admin@app01 ~]$ df -h /data
Filesystem      Size  Used Avail Use% Mounted on
/dev/sdb1       500G  210G  290G  42% /data
admin@app01:~> cat /etc/build_id
BUILD-2026.10.3
"""


def test_parser_handles_different_prompt_styles():
    samples = parse_command_outputs(TRANSCRIPT)
    by_cmd = {s["command"]: s["output"] for s in samples}
    assert list(by_cmd) == ["show interfaces summary", "show version | include uptime",
                            "systemctl is-active billing", "df -h /data", "cat /etc/build_id"]
    assert by_cmd["show interfaces summary"].splitlines()[-1].startswith("Gi0/1")
    assert by_cmd["systemctl is-active billing"] == "active"
    assert "42% /data" in by_cmd["df -h /data"]
    assert by_cmd["cat /etc/build_id"] == "BUILD-2026.10.3"


WORKFLOW = r"""
nodes:
  APP: {type: remote, plugin: ssh, prompt_regex: '\[admin@app01 ~\]\$'}
phases:
  precheck:
    steps:
    - node: APP
      send: df -h ${configSequences.0.records.0.data.Mount}
      validation:
        success: {criteria: {regex: 'Use%\s+\d+%\s+(?<pct>[0-7]\d)%'}}
    - node: APP
      send: systemctl is-active billing
      validation:
        success: {criteria: {regex: '^active$'}}
      register:
      - {regex: 'state=(?<svcState>\w+)'}
    - node: APP
      send: su - appuser
      validation:
        success: {criteria: {regex: 'appuser@app01'}}
"""


def test_regexes_are_checked_against_real_output():
    import yaml
    problems = check_against_samples(yaml.safe_load(WORKFLOW), parse_command_outputs(TRANSCRIPT))
    # df regex expects the percentage in the wrong column; register regex captures nothing
    assert len(problems) == 2, problems
    assert "validation regex" in problems[0] and "df -h" in problems[0]
    assert "42% /data" in problems[0]                    # relevant real output is shown
    assert "register regex" in problems[1] and "systemctl" in problems[1]
    # the su step is prompt-only and never checked; the ^active$ regex matches
    assert not any("su - appuser" in p for p in problems)


def test_no_samples_means_no_sample_errors():
    assert not any("real output" in e for e in validate_workflow(WORKFLOW, []))


def test_step6_self_corrects_with_sample_feedback():
    good = WORKFLOW.replace(r"Use%\s+\d+%\s+(?<pct>[0-7]\d)%", r"(?<pct>\d+)%\s+/data") \
                   .replace(r"state=(?<svcState>\w+)", r"^(?<svcState>\w+)$")
    prompts_seen: list[str] = []

    async def llm(system: str, user: str) -> str:
        prompts_seen.append(user)
        return WORKFLOW if len(prompts_seen) == 1 else good

    requirements = {"doc_extracted": {"command_samples": parse_command_outputs(TRANSCRIPT)}}
    out = asyncio.run(NetworkConfigGenSkill(llm).generate(SkillInput(uc_name="UC", requirements=requirements)))
    assert len(prompts_seen) == 2                        # one self-correction round
    assert "42% /data" in prompts_seen[1]                # feedback carried the real output
    assert not any("real output" in e for e in out.validation_errors)


def test_script_contract_comes_from_the_workflow():
    workflow = """
phases:
  activity:
    steps:
    - {node: LOCAL, send: "python tools/build_payload.py --orderId '${orderId}' --dryRun --target_host h1"}
    - {node: LOCAL, send: "python tools/build_payload.py --orderId '${orderId}' --out=/tmp/p.json"}
    - {node: LOCAL, send: "python3 summarize.py --in a.txt"}
"""
    needed = HelperScriptGenSkill._find_needed_scripts(workflow)
    assert list(needed) == ["build_payload.py", "summarize.py"]
    assert len(needed["build_payload.py"]) == 2
    assert contract_flags(needed["build_payload.py"]) == ["--orderId", "--dryRun", "--target_host", "--out"]

    script = (
        "import argparse\n"
        "p = argparse.ArgumentParser()\n"
        "p.add_argument('--orderId')\n"
        "p.add_argument(\n    '--dry-run', '--dryRun', action='store_true')\n"
        "p.add_argument('--target-host')\n"
    )
    problems = check_script_contract(script, contract_flags(needed["build_payload.py"]))
    assert problems and "--target_host, --out" in problems[0]
    assert check_script_contract("def broken(:\n", ["--x"])[0].startswith("script does not compile")


def test_framework_runtime_params_count_as_defined():
    from skills.network_config_gen.validator import check_placeholders
    wf = {"phases": {"activity": {"steps": [
        {"node": "LOCAL", "send": "python mk.py --out '${ORDERNO}.xml' --id '${batchId}'"}]}}}
    problems = check_placeholders(wf, runtime_params=["ORDERNO"])
    assert len(problems) == 1 and "${batchId}" in problems[0]     # unregistered vars still flagged
    assert any("${ORDERNO}" in p for p in check_placeholders(wf, runtime_params=[]))
    assert not any("${ORDERNO}" in p for p in check_placeholders(wf))  # configured default


def test_step6_prompt_lists_runtime_params():
    seen: list[str] = []

    async def llm(system: str, user: str) -> str:
        seen.append(user)
        return "nodes: {}\nphases: {}\n"

    asyncio.run(NetworkConfigGenSkill(llm).generate(SkillInput(uc_name="UC")))
    assert "${ORDERNO}" in seen[0] and "never register" in seen[0]


def test_step9_passes_runtime_params_as_vars(tmp_path, monkeypatch):
    from skills.network_config_test import jar_executor
    calls: list[list[str]] = []

    async def fake_run_jar(cmd, *args, **kwargs):
        calls.append(cmd)
        return {"success": True, "stdout": "", "stderr": "", "returncode": 0}

    monkeypatch.setattr(jar_executor, "_run_jar", fake_run_jar)
    json_dir = tmp_path / "json_data"
    json_dir.mkdir()
    (json_dir / "data.json").write_text('{"data": {"nodeGroups": [{"crGroup": "CR1", "nodes": [{"node": "EAST"}]}]}}')
    wf = tmp_path / "PGW_RDS_UC.yaml"
    wf.write_text("nodes: {}\nphases: {}\n")
    asyncio.run(jar_executor.run_execution_test(
        "cli.jar", str(wf), str(tmp_path / "schema.yaml"), "", str(tmp_path),
        runtime_vars={"ORDERNO": "T1", "BATCH": "B1"}))
    execute = [c for c in calls if "execute" in c]
    assert execute, calls
    assert "ORDERNO=T1" in execute[0] and "BATCH=B1" in execute[0]


def test_sample_check_mirrors_jar_regex_semantics():
    from skills.network_config_gen.validator import _regex_matches_any
    out = ["line1\r\nline2\r\n"]
    assert _regex_matches_any("^line2$", out) is True                 # MULTILINE, CRLF
    assert _regex_matches_any("line1.line2", out) is not True         # no DOTALL
    # ${…} is not interpolated in regexes (DEP-13)
    assert _regex_matches_any("uid=${configSequences.0.records.0.data.IMSI},ds", ["dn: uid=123,ds=X"]) is not True


def test_quoted_ciq_columns_are_unquoted():
    from skills.network_config_gen.autofix import fix_placeholders
    from skills.network_config_gen.validator import check_placeholders
    quoted = "-b \"imsi=${configSequences.0.records.0.data.'Test IMSI'}\" --x '${configSequences.0.records.0.data.\"Cap\"}'"
    fixed = fix_placeholders(quoted)
    assert fixed == "-b \"imsi=${configSequences.0.records.0.data.Test IMSI}\" --x '${configSequences.0.records.0.data.Cap}'"
    assert any("quotes the column" in p for p in check_placeholders({"phases": {"a": {"steps": [{"send": quoted}]}}}))
    assert not check_placeholders({"phases": {"a": {"steps": [{"send": fixed}]}}})


def test_quoted_column_inside_single_quoted_scalar_still_parses():
    from skills.network_config_gen.autofix import clean_yaml_response, fix_workflow
    raw = ("nodes:\n  RDS: {type: remote}\nphases:\n  a:\n    steps:\n    - node: RDS\n"
           "      send: ls\n      validation:\n        success:\n          criteria:\n"
           "            regex: 'dn: imsi=${configSequences.0.records.0.data.'Test IMSI'},dc=IMSI'\n")
    assert validate_workflow("a: 'x'y'\n") == [
        "Invalid YAML syntax: expected <block end>, but found '<scalar>' at line 1: a: 'x'y'"]
    fixed = fix_workflow(clean_yaml_response(raw))
    assert "dn: imsi=.+?,dc=IMSI" in fixed           # unquoted, then made a wildcard (DEP-13)
    assert not any(e.startswith("Invalid YAML") for e in validate_workflow(fixed))


def test_self_correction_never_prefers_unparseable_yaml():
    from skills.network_config_gen.skill import _rank
    assert _rank(["Invalid YAML syntax: x"]) > _rank(["a", "b", "c", "d", "e", "f"])


def test_placeholders_in_regexes_are_replaced():
    from skills.network_config_gen.autofix import fix_workflow
    wf = ("nodes:\n  APP: {type: remote}\nphases:\n  a:\n    steps:\n    - node: APP\n"
          "      send: id ${configSequences.0.records.0.data.User}\n"
          "      register:\n      - {regex: 'uid=(?<uid>\\d+)\\(${configSequences.0.records.0.data.User}\\)'}\n"
          "      validation:\n        success: {criteria: {regex: 'uid=${configSequences.0.records.0.data.Uid}'}}\n")
    fixed = fix_workflow(wf)
    import yaml
    step = yaml.safe_load(fixed)["phases"]["a"]["steps"][0]
    assert step["send"] == "id ${configSequences.0.records.0.data.User}"         # send keeps it
    assert step["validation"]["success"]["criteria"]["regex"] == "uid=.+?"
    assert step["register"][0]["regex"] == r"uid=(?<uid>\d+)\(.+?\)"
    from skills.network_config_gen.validator import _regex_matches_any
    assert "does not interpolate" in _regex_matches_any("uid=${x}", ["uid=1"])


SU_TRANSCRIPT = "app01:~ # su - appuser\nLast login: today\napp01$ whoami\nappuser\n"


def test_su_prompt_regex_is_checked_against_the_next_real_prompt():
    from skills.network_config_gen.validator import check_against_samples
    samples = parse_command_outputs(SU_TRANSCRIPT)

    def wf(rx):
        return {"phases": {"a": {"steps": [{"node": "APP", "send": "su - appuser", "prompt_regex": rx}]}}}
    problems = check_against_samples(wf(r"appuser@app01:~>\s*$"), samples)
    assert len(problems) == 1 and "'app01$'" in problems[0]
    assert check_against_samples(wf(r"app01\$\s*$"), samples) == []


def test_step8_pushes_the_su_steps_own_prompt():
    from skills.network_simulator_setup.topology import build_topology
    wf = """
nodes:
  APP: {type: remote, plugin: ssh, prompt_regex: 'app01:~ #', ssh: {host: h, user: oamsys}}
phases:
  a:
    steps:
    - {node: APP, send: su - appuser, prompt_regex: 'appuser@app01:~>\\s*$'}
    - {node: APP, send: whoami}
"""
    node = build_topology(wf)[0]
    assert node.escalations == {"su - appuser": "appuser@app01:~> "}


def test_step8_serves_recorded_output_verbatim():
    from skills.network_simulator_setup.skill import recorded_responses
    samples = parse_command_outputs(TRANSCRIPT)
    commands = [{"cmd": "systemctl  is-active billing"}, {"cmd": "df -h ${configSequences.0.records.0.data.Mount}"},
                {"cmd": "uptime"}]
    entries = recorded_responses(commands, samples)
    assert entries == {"systemctl  is-active billing": {"lines": ["active"]}}   # placeholders/unrecorded → LLM


def test_script_output_must_match_the_workflow_success_regex():
    from skills.helper_script_gen.skill import check_output_contract
    workflow = """
phases:
  a:
    steps:
    - node: LOCAL
      send: python build.py --x 1
      validation: {success: {criteria: {regex: 'Payload (?<name>\\S+) written'}}}
"""
    regexes = HelperScriptGenSkill._script_output_regexes(workflow)
    assert regexes == {"build.py": [r"Payload (?<name>\S+) written"]}
    assert check_output_contract("print(f'Payload {path} written')\n", regexes["build.py"]) == []
    assert check_output_contract("print(f'File created: {path}')\n", regexes["build.py"])
    # an f-string field can satisfy any wildcard part: fixed text is enough
    assert check_output_contract("print(f'XML file generated: {out}')\n", ["XML file generated: .+?.xml"]) == []


def test_shared_display_step_is_cleared_only_by_its_first_agent():
    assert _first_agent_of_step(6) == "network_config_gen"   # not helper_script_gen
    assert _first_agent_of_step(8) == "network_simulator_setup"


def _workflow(phases: dict) -> dict:
    return {"phases": {name: {"steps": [{"name": f"s{i}", "node": node, "command": "show"}
                                        for i, node in enumerate(nodes)]}
                       for name, nodes in phases.items()}}


_EXPECTED = {"phases": ["preNodeHealthCheck", "activity", "postNodeHealthCheck"],
             "node_commands": {"RDS": 20, "PGW": 20}}


def test_cut_short_workflow_is_incomplete():
    from skills.network_config_gen.validator import check_completeness
    errors = check_completeness(_workflow({"preNodeHealthCheck": ["RDS"] * 3}), _EXPECTED)
    assert any("activity" in e for e in errors)
    assert any("post-check" in e for e in errors)
    assert any("node PGW" in e for e in errors)
    assert any("cut short" in e for e in errors)


def test_complete_workflow_passes_completeness():
    from skills.network_config_gen.validator import check_completeness
    wf = _workflow({"pre_checks": ["RDS", "PGW"] * 3, "activity": ["RDS"] * 4, "postNodeHealthCheck": ["PGW"] * 3})
    assert check_completeness(wf, _EXPECTED) == []
    assert check_completeness(wf, {}) == []


def test_incomplete_workflow_ranks_below_one_with_mistakes():
    from skills.network_config_gen.skill import _rank
    many_mistakes = ["Unresolvable placeholder ${x}"] * 30
    incomplete = ["The workflow has 5 steps for 106 MOP commands in the requirements — it looks cut short"]
    assert _rank(incomplete) > _rank(many_mistakes)


def test_expected_coverage_skips_local_and_niam_entries():
    from skills.network_config_gen.skill import _expected_coverage
    req = {"category_3_orchestration": {"phases": ["activity"]},
           "category_2_commands": {"commands": [{"node": "RDS"}, {"node": "LOCAL"}, {"node": "niam_lookup"},
                                                {"node": "RDS"}]}}
    assert _expected_coverage(req) == {"phases": ["activity"], "node_commands": {"RDS": 2},
                                     "request_columns": {}, "record_columns": {}}


def test_cache_is_stale_after_code_or_input_change(tmp_path, monkeypatch):
    import storage.artifact_cache as ac
    cache = ac.ArtifactCache(str(tmp_path))
    cache.save_cache("UC", "network_config_gen", {"workflow_yaml": "phases: {}"}, inputs="a")
    assert cache.has_cache("UC", "network_config_gen", "a")
    assert not cache.has_cache("UC", "network_config_gen", "b")
    monkeypatch.setattr(ac, "code_version", lambda name: "changed")
    assert not cache.has_cache("UC", "network_config_gen", "a")


def test_regenerated_agent_invalidates_later_caches(tmp_path):
    from storage.artifact_cache import ArtifactCache
    cache = ArtifactCache(str(tmp_path))
    cache.save_cache("UC", "helper_script_gen", {"helper_scripts": {"a.py": "print(1)"}})
    assert cache.has_cache("UC", "helper_script_gen")
    cache.save_cache("UC", "network_config_gen", {"workflow_yaml": "phases: {}"})
    assert not cache.has_cache("UC", "helper_script_gen")
    cache.clear_from_step("UC", "network_config_qa")  # nothing cached after step 6: keeps the cache
    assert cache.has_cache("UC", "network_config_gen")


def test_inputs_fingerprint_hashes_file_content_not_path(tmp_path):
    from storage.artifact_cache import inputs_fingerprint
    a, b = tmp_path / "a.xlsx", tmp_path / "b.xlsx"
    a.write_bytes(b"same"); b.write_bytes(b"same")
    assert inputs_fingerprint({"ciq_file_path": str(a), "id": 1}) == inputs_fingerprint({"ciq_file_path": str(b), "id": 2})
    assert inputs_fingerprint({"developer_notes": "x"}) != inputs_fingerprint({"developer_notes": "y"})


def test_helper_script_retry_repairs_the_previous_script():
    from skills.helper_script_gen.skill import SkillInput as HelperInput
    broken = "import argparse\np = argparse.ArgumentParser()\np.add_argument('--out')\na = p.parse_args()\nraise TypeError('boom')\n"
    fixed = "import argparse\np = argparse.ArgumentParser()\np.add_argument('--out')\na = p.parse_args()\nprint('ok')\n"
    prompts_seen = []

    async def llm(system, user):
        prompts_seen.append(user)
        return broken if len(prompts_seen) == 1 else fixed

    wf = ("nodes: {LOCAL: {type: local, plugin: local}}\nphases:\n  a:\n    steps:\n"
          "    - {node: LOCAL, send: \"python mk_thing.py --out 'x.txt'\"}\n")
    out = asyncio.run(HelperScriptGenSkill(llm_fn=llm).generate(HelperInput(uc_name="UC", workflow_yaml=wf)))
    assert out.status == "passed" and len(prompts_seen) == 2
    assert "raise TypeError('boom')" in prompts_seen[1] and "TypeError" in prompts_seen[1]


def test_milestone_rerun_starts_at_the_earliest_failed_agent(tmp_path):
    import json
    from types import SimpleNamespace
    from orchestrator.pipeline_agent import PipelineAgent
    agents = [SimpleNamespace(name=n) for n in ("network_config_gen", "helper_script_gen", "network_config_mop_qa")]
    self = SimpleNamespace(_agents_list=agents)
    ctx = SimpleNamespace(session=SimpleNamespace(state={"run_dir": str(tmp_path)}))
    assert PipelineAgent._rerun_index(self, "r", ctx, 6, 1) == 1  # no error file: same agent
    (tmp_path / "step_6_error.json").write_text(json.dumps({"failed_agents": ["helper_script_gen"]}))
    assert PipelineAgent._rerun_index(self, "r", ctx, 6, 1) == 1
    (tmp_path / "step_6_error.json").write_text(
        json.dumps({"failed_agents": ["network_config_gen", "helper_script_gen"]}))
    assert PipelineAgent._rerun_index(self, "r", ctx, 6, 1) == 0


def test_su_prompt_regex_is_repaired_to_the_recorded_prompt():
    import re
    from skills.network_config_gen.validator import repair_prompt_regexes
    samples = [{"command": "su - sdfrun", "prompt": "[root@rds ~]#", "output": ""},
               {"command": "ndsLoadStats", "prompt": "rdshyd201$", "output": "x"}]
    wf = {"phases": {"pre": {"steps": [{"node": "RDS", "send": "su - sdfrun", "prompt_regex": "rdshyd201$"}]}}}
    assert repair_prompt_regexes(wf, samples) == 1
    regex = wf["phases"]["pre"]["steps"][0]["prompt_regex"]
    assert re.search(regex, "rdshyd201$") and re.search(regex, "rdshyd201$ ")
    assert repair_prompt_regexes(wf, samples) == 0


def test_step1_asks_about_either_or_ciq_columns():
    from skills.requirement_gathering.questionnaire import ciq_alternative_questions
    sheets = {"Profile": {"columns": ["Zone", "Test IMSI", "Test MSISDN", "VNI", "APN"], "sample_rows": [
        {"Zone": "EAST", "Test IMSI": None, "Test MSISDN": "9810217635", "VNI": "6", "APN": None},
        {"Zone": "NORTH", "Test IMSI": "404530000000132", "Test MSISDN": None, "VNI": None, "APN": "moc"}]}}
    questions = ciq_alternative_questions(sheets)
    # VNI/APN are also either-or in two rows, but a number vs text: not alternatives of each other
    assert [q["id"] for q in questions] == ["cmd_ciq_alt_test_imsi_test_msisdn"]
    assert "EAST: only 'Test MSISDN'" in questions[0]["question"]
    assert ciq_alternative_questions({"S": {"columns": ["A", "B"], "sample_rows": [{"A": "1"}]}}) == []


def test_step6_sees_answers_with_their_questions():
    from skills.network_config_gen.skill import NetworkConfigGenSkill
    skill = NetworkConfigGenSkill(llm_fn=None)
    skill._requirements = {"developer_answers": {"cmd_ciq_alt_x": "resolve IMSI via MSISDN", "na_q1": "oamsys"},
                           "developer_questions": {"cmd_ciq_alt_x": "Each CIQ row gives either IMSI or MSISDN?"}}
    text = skill._build_prompt_context()["answers"]
    assert "Q: Each CIQ row gives either IMSI or MSISDN?\n  A: resolve IMSI via MSISDN" in text
    assert "na_q1: oamsys" in text


def test_first_prompt_shows_recorded_outputs_once_per_command():
    from skills.network_config_gen.skill import format_real_outputs
    text = format_real_outputs([{"command": "ndsLoadStats", "output": "Updates Rejected\r\n  0  0"},
                                {"command": "ndsLoadStats", "output": "again"},
                                {"command": "uptime", "output": ""}])
    assert text == "$ ndsLoadStats\nUpdates Rejected\n  0  0\n"
    assert format_real_outputs([]) == "None recorded"


def test_cache_keeps_script_contents_and_invalidates_after_a_rerun(tmp_path):
    from storage.artifact_cache import ArtifactCache
    script = tmp_path / "run" / "scripts" / "create_xml.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('xml')")
    cache = ArtifactCache(str(tmp_path / "cache"))
    cache.save_cache("UC", "helper_script_gen", {"helper_scripts": {"create_xml.py": str(script)}})
    assert cache.load_cache("UC", "helper_script_gen")["helper_scripts"]["create_xml.py"] == "print('xml')"
    assert cache.has_cache("UC", "helper_script_gen")
    cache.invalidate_after("UC", "network_config_gen")  # step 6 runs again, even if it then fails
    assert not cache.has_cache("UC", "helper_script_gen")


def test_smoke_run_rejects_a_script_that_refuses_the_workflow_flags():
    from skills.helper_script_gen.skill import smoke_run
    code = ("import argparse\np = argparse.ArgumentParser()\np.add_argument('--imsi', required=True)\n"
            "p.add_argument('--out')\np.parse_args()\nprint('ok')\n")
    problems = smoke_run("mk.py", code, ["python mk.py --out 'x.xml'"], {})
    assert problems and "--imsi" in problems[0]
    assert smoke_run("mk.py", code.replace(", required=True", ""), ["python mk.py --out 'x.xml'"], {}) == []



def test_smoke_run_feeds_the_sample_response_to_parsers():
    from skills.helper_script_gen.skill import smoke_run
    sample = '<spml:batchResponse result="success" xmlns:spml="urn:x"><response result="success"/></spml:batchResponse>'
    wrong = ("import argparse, xml.etree.ElementTree as ET\np = argparse.ArgumentParser()\n"
             "p.add_argument('--response_file')\na = p.parse_args()\nroot = ET.parse(a.response_file).getroot()\n"
             "if not root.findall('spml:response', {'spml': 'urn:x'}):\n    print('FAILURE'); raise SystemExit(1)\n"
             "print('SUCCESS')\n")
    cmd = ["python parse.py --response_file '${ORDERNO}.xml.res'"]
    assert smoke_run("parse.py", wrong, cmd, {"ORDERNO": "T"}, response_sample=sample)
    right = wrong.replace("root.findall('spml:response', {'spml': 'urn:x'})", "root.findall('response')")
    assert smoke_run("parse.py", right, cmd, {"ORDERNO": "T"}, response_sample=sample, output_regexes=["SUCCESS"]) == []
    assert smoke_run("parse.py", right, cmd, {"ORDERNO": "T"}, response_sample=sample, output_regexes=["ALL OK"])


def test_nested_quotes_in_script_arguments_are_rejected():
    from skills.network_config_gen.validator import check_script_arguments

    def wf(send):
        return {"phases": {"a": {"steps": [{"node": "LOCAL", "send": send}]}}}
    assert check_script_arguments(wf("python r.py --ciq '{'Mandatory Cap': '${x}'}' --out 'r.html'"))
    assert check_script_arguments(wf("python r.py --cap '${configSequences.0.records.0.data.Mandatory Cap}' --flag")) == []


def test_workflow_must_use_both_columns_of_an_either_or_pair():
    from skills.network_config_gen.validator import check_alternative_columns
    sheets = {"P": {"columns": ["Zone", "Test IMSI", "Test MSISDN"], "sample_rows": [
        {"Zone": "EAST", "Test MSISDN": "9810217635"}, {"Zone": "NORTH", "Test IMSI": "404530000000132"}]}}
    imsi_only = {"phases": {"a": {"steps": [{"node": "RDS", "when": "configSequences.0.records.0.data.Test IMSI != ''",
                                             "send": "ldapsearch imsi=${configSequences.0.records.0.data.Test IMSI}"}]}}}
    errors = check_alternative_columns(imsi_only, sheets)
    assert len(errors) == 1 and "EAST" in errors[0] and "Test MSISDN" in errors[0]
    imsi_only["phases"]["a"]["steps"].append(
        {"node": "RDS", "send": "ldapsearch msisdn=${configSequences.0.records.0.data.Test MSISDN}"})
    assert check_alternative_columns(imsi_only, sheets) == []


def test_request_builder_must_get_the_request_identifiers():
    from skills.network_config_gen.validator import check_request_inputs
    sheets = {"P": {"columns": ["Zone", "Test IMSI", "Test MSISDN"], "sample_rows": [
        {"Zone": "EAST", "Test MSISDN": "9810217635"}, {"Zone": "NORTH", "Test IMSI": "404530000000132"}]}}
    lookup = {"node": "RDS", "send": "ldapsearch msisdn=${configSequences.0.records.0.data.Test MSISDN}",
              "register": [{"regex": r"provisionedIMSI: (?<resolvedImsi>\d+)"}]}
    upload = {"node": "PGW", "sftp": {"operation": "upload", "local_path": "${ORDERNO}.xml", "remote_path": "/in/${ORDERNO}.xml"}}
    parse = {"node": "LOCAL", "send": "python parse_response.py --response_file '${ORDERNO}.xml.res'"}

    def wf(build):
        return {"phases": {"a": {"steps": [lookup, {"node": "LOCAL", "send": build}, upload, parse]}}}
    columns = {"Test IMSI": "404530000000132"}
    missing = check_request_inputs(wf("python create_xml.py --vni '6' --output_file '${ORDERNO}.xml'"), sheets, columns)
    assert len(missing) == 1 and "create_xml.py" in missing[0] and "${resolvedImsi}" in missing[0]
    given = "python create_xml.py --imsi '${resolvedImsi}' --output_file '${ORDERNO}.xml'"
    assert check_request_inputs(wf(given), sheets, columns) == []


def test_step6_is_cached_only_after_step9_passes(tmp_path):
    """DEP-55: step 6 passing its validator is no proof; its artifacts are cached when step 9
    passes, dropped (with any cached copy) when step 9 fails."""
    from orchestrator.pipeline_agent import _settle_deferred_cache

    class Cache:
        def __init__(self):
            self.saved, self.cleared = {}, []

        def save_cache(self, uc, name, data, inputs=""):
            self.saved[name] = data

        def clear_cache(self, uc, name=None):
            self.cleared.append(name)

    pending = {"network_config_gen": ({"workflow_yaml": "w"}, "fp"), "helper_script_gen": ({"s": "x"}, "fp")}
    cache = Cache()
    _settle_deferred_cache(cache, "UC", pending, [8], {}, str(tmp_path))           # not step 9: nothing yet
    assert not cache.saved and not cache.cleared and len(pending) == 2
    (tmp_path / "step_9_error.json").write_text("{}")                              # step 9 failed (disk)
    _settle_deferred_cache(cache, "UC", dict(pending), [9], {"step_9_status": "passed"}, str(tmp_path))
    assert not cache.saved and sorted(cache.cleared) == ["helper_script_gen", "network_config_gen"]
    (tmp_path / "step_9_error.json").unlink()
    cache = Cache()
    _settle_deferred_cache(cache, "UC", pending, [9], {}, str(tmp_path))           # step 9 passed
    assert cache.saved == {"network_config_gen": {"workflow_yaml": "w"}, "helper_script_gen": {"s": "x"}}
    assert not pending and not cache.cleared
