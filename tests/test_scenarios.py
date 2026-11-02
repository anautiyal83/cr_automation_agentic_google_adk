"""Step 9 scenario test cases (spec 005) and identifier resolution guidance (DEP-17).

    python -m pytest tests/test_scenarios.py -v
"""
from __future__ import annotations

import asyncio

import yaml

from skills.network_config_test.result_parser import CommandResult
from skills.network_config_test.scenarios import (
    Fault, Scenario, ScenarioResult, align, command_key, deterministic_failing_output, evaluate,
    inject_fault, matches_command, negative_scenarios, phase_kind, positive_scenarios, workflow_steps,
)

WORKFLOW = yaml.safe_load(r"""
defaults: {on_failure: stop}
phases:
  preNodeHealthCheck:
    name: Pre checks
    steps:
    - {node: APP, send: uptime, on_failure: continue, validation: {success: {criteria: {regex: 'load'}}}}
    - {node: APP, send: systemctl is-active app, validation: {success: {criteria: {regex: '^active$'}}}}
  activity:
    on_failure: continue
    steps:
    - {node: LOCAL, send: "python mk.py --id ${configSequences.0.records.0.data.Id}"}
    - {node: DB, send: "dbtool set ${configSequences.0.records.0.data.Id}", validation: {success: {criteria: {regex: 'OK'}}}}
  postNodeHealthCheck:
    steps:
    - {node: APP, send: systemctl is-active app, validation: {success: {criteria: {regex: '^active$'}}}}
""")
TARGETS = [("cr1.json", "EAST", "CR1"), ("cr1.json", "NORTH", "CR1")]


def test_steps_carry_phase_kind_and_effective_policy():
    steps = workflow_steps(WORKFLOW)
    assert [(s.phase_kind, s.on_failure) for s in steps] == [
        ("pre", "continue"), ("pre", "stop"), ("activity", "continue"), ("activity", "continue"), ("post", "stop")]
    assert phase_kind("prepare") == "pre" and phase_kind("rollback") == "activity"


def test_sunny_day_is_one_case_per_cr_and_node_group():
    cases = positive_scenarios(TARGETS)
    assert [(c.id, c.cr_group, c.node_group, c.kind) for c in cases] == [
        ("TC-P01", "CR1", "EAST", "positive"), ("TC-P02", "CR1", "NORTH", "positive")]


def test_negative_catalogue_prefers_gating_checks():
    negs, notes = negative_scenarios(workflow_steps(WORKFLOW), TARGETS[1], {"APP", "DB"})
    assert [n.category for n in negs] == ["pre_check", "activity", "post_check", "login"]
    assert negs[0].fault.send == "systemctl is-active app" and negs[0].policy == "stop"
    assert "no later step runs" in negs[0].expected
    assert negs[1].policy == "continue" and "still run" in negs[1].expected
    assert not notes


def test_commands_align_with_steps_even_when_repeated():
    cmds = [CommandResult(command=c, node=n, success=True, validation_status="success") for n, c in [
        ("APP", "uptime"), ("APP", "systemctl is-active app"), ("LOCAL", "python mk.py --id 42"),
        ("DB", "dbtool set 42"), ("APP", "systemctl is-active app [2]")]]
    assert align(cmds, workflow_steps(WORKFLOW)) == [0, 1, 2, 3, 4]
    assert matches_command("dbtool set ${x}", "dbtool  set 42") and not matches_command("dbtool set ${x}", "dbtool get 4")


def _result(case, cmds, overall="PASS", events=()):
    r = ScenarioResult(scenario=case, commands=cmds, overall_status=overall, events=list(events))
    evaluate(r, workflow_steps(WORKFLOW), {"APP": "APP", "DB": "DB"})
    return r


def test_sunny_day_passes_only_when_every_step_passes():
    case = positive_scenarios(TARGETS)[:1][0]
    ok = [CommandResult(command="uptime", node="APP", success=True, validation_status="success")]
    assert _result(case, ok).status == "PASS"
    bad = ok + [CommandResult(command="dbtool set ", node="DB", success=True, validation_status="failed",
                              validation_conclusion="no OK")]
    r = _result(case, bad, overall="FAIL")
    assert r.status == "FAIL" and "dbtool set" in r.checks[1].detail and r.classification
    unmatched = _result(case, ok, events=[{"source": "unmatched", "input": "foo"}])
    assert unmatched.status == "FAIL"


def test_negative_stop_case_checks_nothing_runs_after_the_fault():
    neg = negative_scenarios(workflow_steps(WORKFLOW), TARGETS[1], {"APP", "DB"})[0][0]
    ran = [CommandResult(command="uptime", node="APP", success=True, validation_status="success"),
           CommandResult(command="systemctl is-active app", node="APP", success=True, validation_status="failed")]
    assert _result(neg, ran, overall="FAIL").status == "PASS"
    leaked = ran + [CommandResult(command="dbtool set 4", node="DB", success=True, validation_status="success")]
    r = _result(neg, leaked, overall="FAIL")
    assert r.status == "FAIL" and r.classification == "workflow"
    undetected = [ran[0], CommandResult(command="systemctl is-active app", node="APP", success=True,
                                        validation_status="success")]
    assert not _result(neg, undetected, overall="FAIL").checks[1].ok


CONFIG = """config: {prompt: 'app:~ # '}
auth:
  enabled: true
  users: [{username: oamsys, password: pw, prompt: 'app:~ # '}]
systemctl is-active app:
  lines: [active]
'regex:^dbtool set .+?$':
  lines: [OK]
"""


def test_fault_injection_replaces_the_command_answer_first():
    exact = yaml.safe_load(inject_fault(CONFIG, Fault(kind="validation", node="APP",
                                                      send="systemctl is-active app", lines=["failed"])))
    assert exact["systemctl is-active app"] == {"lines": ["failed"]}
    assert list(exact)[:3] == ["config", "auth", "systemctl is-active app"]
    rx = yaml.safe_load(inject_fault(CONFIG, Fault(kind="validation", node="DB",
                                                   send="dbtool set ${configSequences.0.records.0.data.Id}",
                                                   lines=["ERROR"])))
    key = command_key("dbtool set ${configSequences.0.records.0.data.Id}")
    assert key.startswith("regex:^dbtool\\ set\\ ") and rx[key] == {"lines": ["ERROR"]}
    login = yaml.safe_load(inject_fault(CONFIG, Fault(kind="login", node="APP")))
    assert login["auth"]["users"][0]["password"] != "pw"


def test_fallback_failing_output_never_matches_the_success_regex():
    step = workflow_steps(WORKFLOW)[1]
    lines = deterministic_failing_output(step, "active")
    assert lines and all(line != "active" for line in lines)


def test_production_rec_row_syntax_is_rewritten():
    from skills.network_config_gen.autofix import fix_placeholders, sanitize_yaml
    from skills.network_config_gen.validator import check_placeholders
    cmd = "ldapsearch -b \"msisdn=${rec_row.data.Test MSISDN},dc=MSISDN\" | grep -i provisionedIMSI"
    assert fix_placeholders(cmd) == ("ldapsearch -b \"msisdn=${configSequences.0.records.0.data.Test MSISDN},"
                                     "dc=MSISDN\" | grep -i provisionedIMSI")
    assert "${configSequences.0.records.0.data.Id}" in sanitize_yaml("x: '${rec_row.data.'Id'}'")
    assert any("rec_row" in p for p in check_placeholders({"phases": {"a": {"steps": [{"send": cmd}]}}}))


def test_quoted_columns_in_when_paths_are_unquoted():
    from skills.network_config_gen.autofix import fix_placeholders
    assert fix_placeholders("configSequences.0.records.0.data.'Test MSISDN' != ''") == \
        "configSequences.0.records.0.data.Test MSISDN != ''"
    assert fix_placeholders('configSequences.0.records.0.data."IFC" != \'UNCHANGED\'') == \
        "configSequences.0.records.0.data.IFC != 'UNCHANGED'"


CIQ = {"Profile": {"columns": ["Zone", "Test IMSI", "Test MSISDN", "Optional"],
                   "sample_rows": [{"Zone": "EAST", "Test MSISDN": "981"},
                                   {"Zone": "NORTH", "Test IMSI": "4045", "Test MSISDN": "982"}]}}


def test_ciq_column_empty_for_some_node_groups_is_reported_with_the_filled_alternative():
    from skills.network_config_gen.validator import check_ciq_coverage
    step = {"node": "DB", "send": "ldapsearch -b imsi=${configSequences.0.records.0.data.Test IMSI}"}
    problems = check_ciq_coverage({"phases": {"a": {"steps": [step]}}}, CIQ)
    assert len(problems) == 1 and "'Test MSISDN'" in problems[0] and "EAST" in problems[0]
    guarded = dict(step, when="configSequences.0.records.0.data.Test IMSI != ''")
    assert not check_ciq_coverage({"phases": {"a": {"steps": [guarded]}}}, CIQ)
    optional = {"node": "LOCAL", "send": "python x.py --o '${configSequences.0.records.0.data.Optional}'"}
    assert not check_ciq_coverage({"phases": {"a": {"steps": [optional]}}}, CIQ)  # empty everywhere


def test_variable_never_registered_for_some_rows_is_reported():
    from skills.network_config_gen.validator import check_row_variables
    col = "configSequences.0.records.0.data.Test IMSI"
    steps = [
        {"node": "DB", "when": f"{col} != ''", "send": "ldapsearch imsi=${" + col + "}",
         "register": [{"regex": r"imsi: (?<subId>\d+)"}]},
        {"node": "DB", "when": f"{col} == ''", "send": "echo none"},           # registers nothing
        {"node": "DB", "send": "ldapsearch imsi=${subId}"},
    ]
    problems = check_row_variables({"phases": {"a": {"steps": steps}}}, CIQ, runtime_params=[])
    assert len(problems) == 1 and "EAST" in problems[0] and "${subId}" in problems[0]
    steps[1] = {"node": "DB", "when": f"{col} == ''", "send": "lookup msisdn ${configSequences.0.records.0.data.Test MSISDN}",
                "register": [{"regex": r"provisionedIMSI: (?<subId>\d+)"}]}
    assert not check_row_variables({"phases": {"a": {"steps": steps}}}, CIQ, runtime_params=[])


def test_unused_nodes_are_dropped_and_get_no_simulator():
    from skills.network_config_gen.autofix import fix_workflow
    from skills.network_simulator_setup.topology import build_topology
    wf = ("nodes:\n  APP: {type: remote, plugin: ssh, prompt_regex: 'app:~ #', ssh: {host: h, user: u}}\n"
          "  GATEWAY: {type: remote, plugin: ssh, prompt_regex: 'gw\\\\$', ssh: {host: g, user: u}}\n"
          "phases:\n  a:\n    steps:\n    - {node: APP, send: uptime}\n")
    assert [n.name for n in build_topology(wf)] == ["APP"]
    assert "GATEWAY" not in yaml.safe_load(fix_workflow(wf))["nodes"]
    dynamic = wf.replace("{node: APP, send: uptime}", "{node: '${target}', send: uptime}")
    assert "GATEWAY" in yaml.safe_load(fix_workflow(dynamic))["nodes"]   # could be the target


def test_invented_responses_must_pass_their_steps_success_check():
    from skills.network_simulator_setup.skill import synthetic_mismatches
    from skills.network_simulator_setup.topology import build_topology
    wf = r"""
nodes:
  APP: {type: remote, plugin: ssh, prompt_regex: 'app:~ #', ssh: {host: h, user: u}}
phases:
  a:
    steps:
    - {node: APP, send: "ls /out/${ORDERNO}.res", validation: {success: {criteria: {regex: 'No such file'}}}}
    - {node: APP, send: systemctl is-active app, validation: {success: {criteria: {regex: '^active$'}}}}
    - {node: APP, send: "cat /out/${ORDERNO}.log", validation: {success: {criteria: {regex: 'started'}}}}
    - {node: APP, send: "cat /out/${ORDERNO}.log", validation: {success: {criteria: {regex: 'finished'}}}}
"""
    node = build_topology(wf)[0]
    entries = {"regex:^ls /out/.+?\\.res$": {"lines": ["-rw-r--r-- 1 u u 12 TEST_ORDERNO.res"]},
               "systemctl is-active app": {"lines": ["active"]},
               "regex:^cat /out/.+?\\.log$": {"lines": ["started"]}}
    bad = synthetic_mismatches(node, entries, recorded=set(), row={})
    assert [b["cmd"] for b in bad] == ["ls /out/${ORDERNO}.res"]      # active OK; cat is stateful → skipped
    assert synthetic_mismatches(node, entries, recorded={"ls /out/${ORDERNO}.res"}, row={}) == []


FILE_FLOW = r"""
nodes:
  APP: {type: remote, plugin: ssh, prompt_regex: 'app:~ #', ssh: {host: h, user: u}}
  LOCAL: {type: local}
phases:
  a:
    steps:
    - {node: LOCAL, send: "python mk.py --out '${ORDERNO}.xml'"}
    - node: APP
      send: ls -l /srv/out/${ORDERNO}.xml.res
      validation: {success: {criteria: {regex: 'No such file'}}}
    - {node: APP, send: "put ${ORDERNO}.xml /srv/in/${ORDERNO}.xml"}
    - node: APP
      send: ls -l /srv/out/${ORDERNO}.xml.res
      validation: {success: {criteria: {regex: '\S+\.xml\.res'}}}
    - {node: LOCAL, send: "python parse.py --file '/srv/out/${ORDERNO}.xml.res'"}
"""


def test_file_transfers_and_absence_checks_are_fixed_for_the_jar():
    from skills.network_config_gen.autofix import fix_workflow
    from skills.network_config_gen.validator import check_file_flows
    steps = yaml.safe_load(fix_workflow(FILE_FLOW))["phases"]["a"]["steps"]
    assert steps[1]["send"] == "ls -l /srv/out/${ORDERNO}.xml.res 2>&1 || true"     # exits 0
    assert steps[2]["sftp"] == {"operation": "upload", "local_path": "${ORDERNO}.xml",
                                "remote_path": "/srv/in/${ORDERNO}.xml"} and "send" not in steps[2]
    assert steps[3]["send"] == "ls -l /srv/out/${ORDERNO}.xml.res"                 # presence: unchanged
    # LOCAL reads a node path: the download is inserted and the command uses the local copy (DEP-29)
    assert steps[4]["node"] == "APP" and steps[4]["sftp"] == {
        "operation": "download", "remote_path": "/srv/out/${ORDERNO}.xml.res", "local_path": "${ORDERNO}.xml.res",
        "overwrite": True}
    assert steps[5]["send"] == "python parse.py --file '${ORDERNO}.xml.res'"
    assert not check_file_flows({"phases": {"a": {"steps": steps}}})
    del steps[4]
    assert len(check_file_flows({"phases": {"a": {"steps": steps}}})) == 1


def test_simulator_file_system_follows_the_workflows_file_flow():
    from skills.network_config_gen.autofix import fix_workflow
    from skills.network_simulator_setup.file_flows import derive_vfs, is_vfs_command
    from skills.network_simulator_setup.topology import build_instance_config, build_topology
    wf = fix_workflow(FILE_FLOW)
    vfs = derive_vfs(yaml.safe_load(wf), "APP", ["<Response result=\"success\"/>"])
    assert vfs["dirs"] == ["/srv/in", "/srv/out"]
    assert vfs["triggers"] == [{"onCreate": r"/srv/in/(?<ORDERNO>[^/]+?)\.xml",
                                "create": "/srv/out/${ORDERNO}.xml.res",
                                "lines": ["<Response result=\"success\"/>"]}]
    assert is_vfs_command(r"regex:^ls\ \-l\ /srv/out/.+?\.xml\.res$", vfs)
    assert not is_vfs_command("uptime", vfs)
    node = build_topology(wf)[0]
    node.vfs = vfs
    assert yaml.safe_load(build_instance_config(node, {}))["vfs"] == vfs


def test_multiline_regex_is_relaxed_only_when_real_output_then_matches():
    from skills.network_config_gen.validator import relax_line_joins
    real = [{"command": "svc status", "output": "apache ... running (7 processes)\n"
                                                 "2026-08-27: INFO: Checking instances status:\n"
                                                 "        instance1... running"}]
    step = {"node": "APP", "send": "svc status",
            "validation": {"success": {"criteria": {"regex": r"apache .* running\n\s+instance\d+\.\.\. running"}}}}
    wf = {"phases": {"a": {"steps": [step]}}}
    assert relax_line_joins(wf, real) == 1
    assert step["validation"]["success"]["criteria"]["regex"] == r"apache .* running[\s\S]*?instance\d+\.\.\. running"
    wrong = {"node": "APP", "send": "svc status",
             "validation": {"success": {"criteria": {"regex": r"apache .* stopped\n\s+instance"}}}}
    assert relax_line_joins({"phases": {"a": {"steps": [wrong]}}}, real) == 0      # still wrong → untouched


def test_step8_never_maps_echo_and_finds_commands_without_a_response():
    from skills.network_simulator_setup.skill import unanswered_commands
    from skills.network_simulator_setup.topology import _is_builtin_key, build_topology
    assert _is_builtin_key("regex:^echo\\ .+?$") and _is_builtin_key("echo 123") and not _is_builtin_key("ldapsearch")
    wf = r"""
nodes:
  DB: {type: remote, plugin: ssh, prompt_regex: 'db:~ #', ssh: {host: h, user: u}}
phases:
  a:
    steps:
    - {node: DB, send: "echo ${configSequences.0.records.0.data.Test IMSI}"}
    - {node: DB, send: "ldapsearch -b msisdn=${configSequences.0.records.0.data.Test MSISDN} | grep -i IMSI"}
    - {node: DB, send: "dbtool status -v"}
"""
    node = build_topology(wf)[0]
    entries = {"dbtool status": {"lines": ["ok"]}}                                  # prefix answers -v
    missing = unanswered_commands(node, entries, set(), {"Test MSISDN": "981"})
    assert [m["resolved"] for m in missing] == ["ldapsearch -b msisdn=981 | grep -i IMSI"]


SAR_REAL = [{"command": "sar 1 10", "output": "Average:        all      0.13      0.00      0.13      0.00      0.00     99.75"}]


def test_expr_uses_registered_variables_only_and_is_checked_on_real_output():
    from skills.network_config_gen.autofix import fix_workflow
    from skills.network_config_gen.validator import check_against_samples, check_expr_vars
    wf = r"""
nodes:
  APP: {type: remote, plugin: ssh, prompt_regex: 'app:~ #', ssh: {host: h, user: u}}
phases:
  a:
    steps:
    - node: APP
      send: sar 1 10
      validation: {success: {criteria: {regex: 'Average:\s+all(?:\s+\S+){5}\s+(?<idleCpu>\d+\.\d+)', expr: 'Float(idleCpu) >= 90'}}}
    - node: APP
      send: sar 1 10
      validation: {success: {criteria: {regex: 'Average:', expr: 'Float(otherIdle) >= 90'}}}
    - {node: APP, send: uptime, validation: {success: {criteria: {regex: 'up', expr: 'True'}}}}
"""
    steps = yaml.safe_load(fix_workflow(wf))["phases"]["a"]["steps"]
    crit = steps[0]["validation"]["success"]["criteria"]
    assert crit["expr"] == "idleCpu >= 90" and "(?<idleCpu>" in steps[0]["register"][0]["regex"]
    assert "expr" not in steps[2]["validation"]["success"]["criteria"]           # literal True removed
    problems = check_expr_vars({"phases": {"a": {"steps": steps}}}, runtime_params=[])
    assert len(problems) == 1 and "otherIdle" in problems[0]
    # 5 numbers instead of 6: captures the %steal column (0.00) → fails on a healthy node
    wrong = {"node": "APP", "send": "sar 1 10",
             "register": [{"regex": r"Average:\s+all(?:\s+\S+){4}\s+(?<idle>\d+\.\d+)"}],
             "validation": {"success": {"criteria": {"regex": "Average:", "expr": "idle >= 90"}}}}
    found = check_against_samples({"phases": {"a": {"steps": [wrong]}}}, SAR_REAL)
    assert len(found) == 1 and "idle=0.00" in found[0]
    right = dict(wrong, register=[{"regex": r"Average:\s+all(?:\s+\S+){5}\s+(?<idle>\d+\.\d+)"}])
    assert check_against_samples({"phases": {"a": {"steps": [right]}}}, SAR_REAL) == []


def test_miscounted_capture_column_is_shifted_until_the_expr_holds():
    from skills.network_config_gen.validator import repair_capture_columns
    step = {"node": "APP", "send": "sar 1 10",
            "register": [{"regex": r"Average:\s+all(?:\s+\S+){4}\s+(?<idle>\d+\.\d+)"}],
            "validation": {"success": {"criteria": {"regex": "Average:", "expr": "idle >= 90"}}}}
    assert repair_capture_columns({"phases": {"a": {"steps": [step]}}}, SAR_REAL) == 1
    assert step["register"][0]["regex"] == r"Average:\s+all(?:\s+\S+){4}\s+(?:\S+\s+){1}(?<idle>\d+\.\d+)"
    unhealthy = [{"command": "sar 1 10", "output": "Average: all 50.0 0.00 40.0 0.00 0.00 10.00"}]
    stuck = {"node": "APP", "send": "sar 1 10",
             "register": [{"regex": r"Average:\s+all(?:\s+\S+){5}\s+(?<idle>\d+\.\d+)"}],
             "validation": {"success": {"criteria": {"regex": "Average:", "expr": "idle >= 90"}}}}
    assert repair_capture_columns({"phases": {"a": {"steps": [stuck]}}}, unhealthy) == 0   # no column passes


def test_self_correction_gets_a_third_round_only_while_improving():
    import skills.network_config_gen.skill as gen
    calls = []
    errors_by_round = [["e1", "e2", "e3"], ["e1", "e2"], ["e1"], []]

    class Fake(gen.NetworkConfigGenSkill):
        async def regenerate(self, feedback):
            calls.append(len(feedback.test_results))
            return gen.SkillOutput(workflow_yaml=f"v{len(calls)}", validation_errors=errors_by_round[len(calls)])

    async def llm(*_):
        return ""
    best, errs = asyncio.run(Fake(llm)._self_correct("v0", errors_by_round[0]))
    assert calls == [3, 2, 1] and best == "v3" and errs == []          # round 3 because round 2 improved
    calls.clear()
    errors_by_round[:] = [["e1", "e2"], ["e1", "e2"], ["e1", "e2"], []]
    asyncio.run(Fake(llm)._self_correct("v0", errors_by_round[0]))
    assert len(calls) == 2                                              # no improvement → no 3rd round


def test_register_regexes_keep_only_the_named_group_capturing():
    from skills.network_config_gen.autofix import fix_workflow
    wf = r"""
nodes:
  APP: {type: remote, plugin: ssh, prompt_regex: 'app:~ #', ssh: {host: h, user: u}}
phases:
  a:
    steps:
    - node: APP
      send: sar 1 10
      register: [{regex: 'Average: +all( +[0-9.]+){5} +(?<cpuIdle>[0-9.]+)'}]
      validation: {success: {criteria: {expr: 'cpuIdle >= 90'}}}
"""
    reg = yaml.safe_load(fix_workflow(wf))["phases"]["a"]["steps"][0]["register"][0]["regex"]
    assert reg == "Average: +all(?: +[0-9.]+){5} +(?<cpuIdle>[0-9.]+)"


def test_recorded_output_becomes_a_template_for_placeholder_commands():
    from skills.network_simulator_setup.skill import templated_responses
    cmds = [{"cmd": "ldapsearch -b \"imsi=${resolvedImsi},dc=IMSI\" -s sub"}]
    samples = [{"command": "ldapsearch  -b \"imsi=404530000000132,dc=IMSI\" -s sub",
                "output": "dn: uid=404530000000132,ds=SUBSCRIBER\nprofileName: gold"}]
    key, entry = templated_responses(cmds, samples)[cmds[0]["cmd"]]
    assert key == r'regex:^ldapsearch\s+\-b\s+"imsi=(?<resolvedImsi>.+?),dc=IMSI"\s+\-s\s+sub$'
    assert entry["lines"] == ["dn: uid=${resolvedImsi},ds=SUBSCRIBER", "profileName: gold"]


def test_expression_placeholder_becomes_variable_with_column_fallback():
    from skills.network_config_gen.autofix import fix_workflow
    from skills.network_config_gen.validator import check_row_variables
    col = "configSequences.0.records.0.data.Test IMSI"
    wf = f"""
nodes:
  DB: {{type: remote, plugin: ssh, prompt_regex: 'db:~ #', ssh: {{host: h, user: u}}}}
  LOCAL: {{type: local}}
phases:
  a:
    steps:
    - node: DB
      when: "{col} == ''"
      send: "lookup msisdn ${{configSequences.0.records.0.data.Test MSISDN}}"
      register: [{{regex: 'provisionedIMSI: (?<imsiR>\\\\d+)'}}]
    - {{node: DB, send: "ldapsearch imsi=${{imsiR != null ? imsiR : {col}}}"}}
"""
    steps = yaml.safe_load(fix_workflow(wf))["phases"]["a"]["steps"]
    assert steps[2]["send"] == "ldapsearch imsi=${imsiR}"
    assert steps[1]["send"] == "echo ${" + col + "}" and steps[1]["when"] == f"{col} != ''"
    assert not check_row_variables({"phases": {"a": {"steps": steps}}}, CIQ, runtime_params=[])


def test_sftp_steps_carry_no_output_regex():
    from skills.network_config_gen.autofix import fix_workflow
    wf = """
nodes:
  APP: {type: remote, plugin: ssh, prompt_regex: 'app:~ #', ssh: {host: h, user: u}}
phases:
  a:
    steps:
    - node: APP
      sftp: {operation: upload, local_path: a.xml, remote_path: /in/a.xml}
      validation: {success: {criteria: {regex: 'File transfer successful'}, message: ok}}
"""
    step = yaml.safe_load(fix_workflow(wf))["phases"]["a"]["steps"][0]
    assert step["sftp"]["operation"] == "upload" and "criteria" not in step["validation"]["success"] or \
        "regex" not in (step["validation"]["success"].get("criteria") or {})


def test_whitespace_joins_widen_only_where_real_output_has_lines_between():
    from skills.network_config_gen.validator import _relax_joins
    out = ["apache ... running (7 processes)\n2026: INFO: Checking instances status:\n  instance1... running"]
    relaxed = _relax_joins(r"apache \.\.\. running \((\d+) processes\)\s+instance\d+\.\.\. running", out)
    assert relaxed == r"apache \.\.\. running \((\d+) processes\)[\s\S]*?instance\d+\.\.\. running"
    assert _relax_joins(r"apache\s+stopped", out) == ""                         # wrong content stays wrong


def test_helper_scripts_are_smoke_run_with_the_workflow_invocation():
    from skills.helper_script_gen.skill import smoke_run
    broken = "import argparse\np = argparse.ArgumentParser()\np.add_argument('--id')\na = p.parse_args()\nprint(ids)\n"
    problems = smoke_run("mk.py", broken, ["python mk.py --id '${ORDERNO}'"], {"ORDERNO": "T1"})
    assert len(problems) == 1 and "NameError" in problems[0] and "--id 'T1'" in problems[0]
    ok = broken.replace("print(ids)", "print('created', a.id)")
    assert smoke_run("mk.py", ok, ["python mk.py --id '${ORDERNO}'"], {"ORDERNO": "T1"}) == []
    needs_input = "open('/no/such/input.res').read()\n"                         # environment, not code
    assert smoke_run("rd.py", needs_input, ["python rd.py"], {}) == []


def test_command_sample_library_fills_commands_a_run_did_not_record():
    from tools import command_samples as lib
    assert lib.add([{"command": "sar 1 10", "output": "Average: all 1 2 3 4 5 95.0"}], source="t") == 1
    assert lib.add([{"command": "sar  1 10", "output": "Average: all 1 2 3 4 5 95.0"}]) == 0  # duplicate
    run = [{"command": "uptime", "output": "up 3 days"}]
    merged = lib.with_library(run)
    assert merged[0] == run[0] and any(s["command"] == "sar 1 10" for s in merged)
    assert len(lib.with_library([{"command": "sar 1 10", "output": "mine"}])) == len(lib.load())


def test_local_steps_get_a_bash_on_windows(monkeypatch):
    import os
    import shutil
    from skills.network_config_test import jar_executor
    if os.name != "nt" or not shutil.which("git"):
        return
    env = dict(os.environ)
    env["PATH"] = r"C:\Windows\System32;C:\Windows"
    path = jar_executor._local_step_env(env)["PATH"]
    bash = shutil.which("bash", path=path)
    assert bash and "system32" not in bash.lower()


def test_developer_notes_reach_the_step6_prompt():
    from skills.network_config_gen.skill import NetworkConfigGenSkill, SkillInput
    seen: list[str] = []

    async def llm(system: str, user: str) -> str:
        seen.append(system + user)
        return "nodes: {}\nphases: {}\n"

    notes = "Either IMSI or MSISDN is given; resolve IMSI via MSISDN"
    asyncio.run(NetworkConfigGenSkill(llm).generate(SkillInput(uc_name="UC", requirements={"developer_notes": notes})))
    assert notes in seen[0] and "ONE OF several CIQ columns" in seen[0]


def test_jar_success_status_is_a_passing_run():
    from skills.network_config_test.scenarios import run_passed
    for status in ("SUCCESS", "success", "PASS", "COMPLETED"):
        assert run_passed(status)
    for status in ("FAIL", "FAILED", "", None):
        assert not run_passed(status)
