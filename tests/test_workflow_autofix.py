"""Tests for step 6 workflow autofix + validator (spec 004 DEP-1, DEP-3, DEP-4, DEP-6, DEP-7, DEP-8).

    python -m pytest tests/test_workflow_autofix.py -v
"""
from __future__ import annotations

import yaml

from skills.network_config_gen.autofix import fix_placeholders, fix_workflow
from skills.network_config_gen.validator import check_placeholders, validate_workflow

WORKFLOW = r"""
nodes:
  RDS:
    type: remote
    plugin: ssh
    prompt_regex: 'rdsggn01:~ #|rdsggn01:~ \$'
    ssh: {host: '${node_row.RDS.NIAM_NAME1}', ne_id: '${node_row.RDS.NIAM_NAME1}', user: oamsys}
  LOCAL: {type: local, plugin: local}
phases:
  preNodeHealthCheck:
    name: Pre checks
    steps:
    - node: RDS
      send: su - sdfrun
      validation:
        description: prompt changes
        success: {criteria: {regex: 'rdsggn01:~ \$'}, message: switched}
        failure: {message: not switched}
    - node: RDS
      send: exit
      validation:
        success: {criteria: {regex: 'rdsggn01:~ #'}}
  activity_configuration:
    steps:
    - node: RDS
      send: "ldapsearch -b \"imsi=${configData.tables.'Profile Modification'.'Test IMSI'},dc=IMSI\""
      when: "configSequences.0.records.0.data.Test IMSI != ''"
      register:
      - {regex: '(?P<profile_data>[\s\S]+)', name: pre_ldap_result}
      - {regex: 'ORDER=(\d+)', name: order_no}
      validation:
        success: {criteria: {regex: '(?P<attr_name>\w+): (?P<attr_value>.*)'}}
    - node: LOCAL
      send: "python report.py --pre '${register.pre_ldap_result}' --order ${{env.order_no}} --vni '${configData.tables.''Profile Modification''.VNI}'"
      when: {bad: map}
"""


def _fixed() -> dict:
    return yaml.safe_load(fix_workflow(WORKFLOW))


def _steps(wf: dict, phase: str) -> list[dict]:
    return wf["phases"][phase]["steps"]


def test_placeholder_rewrites():
    assert fix_placeholders("${{env.X}}") == "${X}"
    assert fix_placeholders("${configData.tables.IP.Node_Name}") == "${configSequences.0.records.0.data.Node_Name}"
    assert fix_placeholders("'${configData.tables.''Profile Modification''.''Test IMSI''}'") == \
        "'${configSequences.0.records.0.data.Test IMSI}'"
    assert fix_placeholders("${node_row.PGW.NIAM_NAME2}") == "${PGW.niamID-2}"
    assert fix_placeholders("${register.a} ${env.b}") == "${a} ${b}"
    assert fix_placeholders({"k": ["${{x}}"]}, {"x": "y"}) == {"k": ["${y}"]}


def test_nodes_use_niam_id_placeholders():
    ssh = _fixed()["nodes"]["RDS"]["ssh"]
    assert ssh["host"] == "${RDS.niamID-1}" and ssh["ne_id"] == "${RDS.niamID-1}"


def test_su_and_exit_complete_on_step_prompt_regex():
    su, exit_ = _steps(_fixed(), "preNodeHealthCheck")
    assert su["prompt_regex"] == r"rdsggn01:~ \$"
    assert "criteria" not in su["validation"]["success"]
    assert su["validation"]["success"]["message"] == "switched"
    assert exit_["prompt_regex"] == "rdsggn01:~ #"
    assert "criteria" not in exit_["validation"]["success"]


def test_regexes_become_java_and_references_follow_renames():
    ldap, local = _steps(_fixed(), "activity_configuration")
    # `name:` + one named group → the group takes the declared name
    assert ldap["register"][0]["regex"] == r"(?<preLdapResult>[\s\S]+)"
    assert ldap["register"][1]["regex"] == r"ORDER=(?<orderNo>\d+)"
    assert ldap["validation"]["success"]["criteria"]["regex"] == r"(?<attrName>\w+): (?<attrValue>.*)"
    assert ldap["send"] == 'ldapsearch -b "imsi=${configSequences.0.records.0.data.Test IMSI},dc=IMSI"'
    assert ldap["when"] == "configSequences.0.records.0.data.Test IMSI != ''"  # string `when` kept
    assert "when" not in local                                                # map `when` dropped
    assert local["send"] == ("python report.py --pre '${preLdapResult}' --order ${orderNo} "
                             "--vni '${configSequences.0.records.0.data.VNI}'")


def test_validator_reports_only_unresolvable_placeholders():
    assert validate_workflow(fix_workflow(WORKFLOW)) == []
    broken = WORKFLOW.replace("${register.pre_ldap_result}", "${never_registered}")
    assert validate_workflow(fix_workflow(broken)) == [
        "Unresolvable placeholder ${never_registered}: variable is never registered by a step "
        "(add a register with a (?<name>…) group on the step that produces it)"]


def test_check_placeholders_flags_dead_syntax_and_unknown_roots():
    wf = {"nodes": {"RDS": {}}, "phases": {"p": {"steps": [{"node": "RDS", "send":
          "x ${configData.tables.S.C} ${node_row.RDS.NIAM_USER} ${nodeGroups.RDS.host} "
          "${RDS.niamID-1} ${SECRET.pw} ${NODE_TYPE} ${crGroup} ${configSequences.0.records.0.data.VNI}"}]}}}
    problems = "\n".join(check_placeholders(wf))
    assert "${configData.tables.S.C}" in problems
    assert "${node_row.RDS.NIAM_USER}" in problems
    assert "${nodeGroups.RDS.host}" in problems
    for ok in ("${RDS.niamID-1}", "${SECRET.pw}", "${NODE_TYPE}", "${crGroup}", "VNI}"):
        assert ok not in problems
