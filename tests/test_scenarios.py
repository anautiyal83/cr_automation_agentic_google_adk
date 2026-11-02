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


def test_developer_notes_reach_the_step6_prompt():
    from skills.network_config_gen.skill import NetworkConfigGenSkill, SkillInput
    seen: list[str] = []

    async def llm(system: str, user: str) -> str:
        seen.append(system + user)
        return "nodes: {}\nphases: {}\n"

    notes = "Either IMSI or MSISDN is given; resolve IMSI via MSISDN"
    asyncio.run(NetworkConfigGenSkill(llm).generate(SkillInput(uc_name="UC", requirements={"developer_notes": notes})))
    assert notes in seen[0] and "ONE OF several CIQ columns" in seen[0]
