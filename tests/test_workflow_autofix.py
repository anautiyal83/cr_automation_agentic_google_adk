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


def test_local_step_reading_a_node_file_by_name_gets_a_download():
    from skills.network_config_gen.autofix import fix_missing_downloads
    from skills.network_config_gen.validator import check_file_flows
    wf = {"phases": {"activity": {"steps": [
        {"node": "LOCAL", "send": "python create_xml.py --out ${ORDERNO}.xml"},
        {"node": "PGW", "sftp": {"operation": "upload", "local_path": "${ORDERNO}.xml",
                                 "remote_path": "/in/${ORDERNO}.xml"}},
        {"node": "PGW", "send": "ls -l /out/${ORDERNO}.xml.res"},
        {"node": "LOCAL", "send": "python parse_response.py --response_file '${ORDERNO}.xml.res'"},
    ]}}}
    assert len(check_file_flows(wf)) == 1  # the uploaded ${ORDERNO}.xml is local: not flagged
    assert fix_missing_downloads(wf) == 1
    steps = wf["phases"]["activity"]["steps"]
    assert steps[3]["sftp"] == {"operation": "download", "remote_path": "/out/${ORDERNO}.xml.res",
                                "local_path": "${ORDERNO}.xml.res", "overwrite": True}
    assert check_file_flows(wf) == []


def test_always_empty_register_used_in_a_command_is_rejected():
    from skills.network_config_gen.validator import check_empty_registers
    wf = {"phases": {"a": {"steps": [
        {"node": "RDS", "send": "echo none", "register": [{"regex": "(?<resolvedImsi>)"}]},
        {"node": "RDS", "send": "ldapsearch -b \"imsi=${resolvedImsi},dc=IMSI\""},
    ]}}}
    assert len(check_empty_registers(wf)) == 1
    wf["phases"]["a"]["steps"][1]["send"] = "uptime"
    assert check_empty_registers(wf) == []


def test_downloads_overwrite_the_previous_node_groups_file():
    from skills.network_config_gen.autofix import fix_workflow
    import yaml
    wf = yaml.safe_load(fix_workflow(
        "nodes: {PGW: {type: remote, plugin: ssh}}\nphases:\n  a:\n    steps:\n"
        "    - {node: PGW, sftp: {operation: download, remote_path: /o/x.res, local_path: x.res}}\n"))
    assert wf["phases"]["a"]["steps"][0]["sftp"]["overwrite"] is True


def test_ls_of_one_file_never_prints_total():
    from skills.network_config_gen.validator import check_file_listing_regexes

    def wf(send, regex):
        return {"phases": {"a": {"steps": [{"node": "PGW", "send": send,
                                            "validation": {"success": {"criteria": {"regex": regex}}}}]}}}
    assert len(check_file_listing_regexes(wf("ls -l /o/${ORDERNO}.xml.res", r"total\s+\d+\s+.+?\.xml\.res"))) == 1
    assert check_file_listing_regexes(wf("ls -l /o/${ORDERNO}.xml.res", r"^-rw\S+\s+\d+.+\.xml\.res$")) == []
    assert check_file_listing_regexes(wf("ls -l /o/", r"total\s+\d+")) == []  # a directory has a total


def test_overlay_shortens_polls_for_the_simulator():
    from skills.network_simulator_setup.overlay import _shorten_polls
    phases = {"a": {"steps": [{"send": "ls", "retries": 30, "retry_delay_sec": 10}, {"send": "x", "retries": 2}]}}
    _shorten_polls(phases)
    assert phases["a"]["steps"][0]["retries"] == 3 and phases["a"]["steps"][0]["retry_delay_sec"] == 2
    assert phases["a"]["steps"][1]["retries"] == 2
