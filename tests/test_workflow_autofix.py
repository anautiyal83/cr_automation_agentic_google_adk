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
    # DEP-48: subscript form, in placeholders and bare `when` paths
    assert fix_placeholders("echo ${configSequences.0.records.0.data['Test IMSI']}") == \
        "echo ${configSequences.0.records.0.data.Test IMSI}"
    assert fix_placeholders("configSequences.0.records.0.data[\"Test MSISDN\"] != ''") == \
        "configSequences.0.records.0.data.Test MSISDN != ''"


def test_wrapped_plain_value_with_colon_parses():
    # DEP-49: continuation line of a plain scalar holds " : " → unparsable before folding
    text = ("phases:\n  a:\n    steps:\n    - node: LOCAL\n"
            "      send: python create_xml.py --vni '${configSequences.0.records.0.data.VNI}'\n"
            "        --imsi '${resolvedImsi != null ? resolvedImsi : configSequences.0.records.0.data.Test IMSI}'\n"
            "      command_description: 'Activity: build'\n")
    step = yaml.safe_load(fix_workflow(text))["phases"]["a"]["steps"][0]
    assert step["send"].startswith("python create_xml.py --vni '${configSequences.0.records.0.data.VNI}' --imsi '")
    assert step["command_description"] == "Activity: build"


def test_stray_trailing_quote_is_dropped():
    # DEP-50: the shell waited for the closing quote and the step timed out
    from skills.network_config_gen.autofix import drop_stray_quote
    from skills.network_config_gen.validator import check_unbalanced_quotes
    cmd = "ldapsearch -D 'cn=x' -b \"imsi=${resolvedImsi},dc=IMSI\" -s sub objectclass=*"
    assert drop_stray_quote(cmd + '"') == cmd
    assert drop_stray_quote(cmd) == cmd
    assert drop_stray_quote("echo \"a 'b\"") == "echo \"a 'b\""
    bad = {"phases": {"a": {"steps": [{"node": "RDS", "send": "grep \"x y"}, {"node": "RDS", "send": cmd}]}}}
    assert len(check_unbalanced_quotes(bad)) == 1


def test_subscript_column_in_single_quoted_scalar():
    text = ("phases:\n  a:\n    steps:\n    - node: RDS\n"
            "      when: 'configSequences.0.records.0.data[''Test IMSI''] != '''''\n"
            "      send: echo ${configSequences.0.records.0.data['Test IMSI']}\n")
    step = yaml.safe_load(fix_workflow(text))["phases"]["a"]["steps"][0]
    assert step["when"] == "configSequences.0.records.0.data.Test IMSI != ''"
    assert step["send"] == "echo ${configSequences.0.records.0.data.Test IMSI}"


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


def test_node_file_read_from_a_made_up_local_dir_is_downloaded():
    from skills.network_config_gen.autofix import fix_missing_downloads
    wf = {"phases": {"a": {"steps": [
        {"node": "PGW", "send": "ls -l /out/${ORDERNO}.xml.res"},
        {"node": "LOCAL", "send": "python parse_response.py --response_file '/tmp/${ORDERNO}.xml.res'"},
    ]}}}
    assert fix_missing_downloads(wf) == 1
    steps = wf["phases"]["a"]["steps"]
    assert steps[1]["sftp"]["remote_path"] == "/out/${ORDERNO}.xml.res"
    assert steps[2]["send"] == "python parse_response.py --response_file '${ORDERNO}.xml.res'"


def test_exit_without_su_is_dropped():
    from skills.network_config_gen.autofix import drop_session_exits
    wf = {"phases": {"pre": {"steps": [
        {"node": "RDS", "send": "su - sdfrun"}, {"node": "RDS", "send": "ndsLoadStats"},
        {"node": "RDS", "send": "exit"},                                  # leaves su: kept
        {"node": "RDS", "send": "exit", "prompt_regex": "Connection to .* closed."},  # closes the session
        {"node": "PGW", "send": "su - provgw"},
    ]}, "post": {"steps": [{"node": "PGW", "send": "exit"}, {"node": "PGW", "send": "exit"}]}}}
    assert drop_session_exits(wf) == 2
    assert [s["send"] for s in wf["phases"]["pre"]["steps"]] == ["su - sdfrun", "ndsLoadStats", "exit", "su - provgw"]
    assert [s["send"] for s in wf["phases"]["post"]["steps"]] == ["exit"]


def test_exit_leaving_su_waits_for_the_node_prompt_not_connection_closed():
    from skills.network_config_gen.autofix import drop_session_exits
    wf = {"nodes": {"RDS": {"prompt_regex": "rdsggn01:~ #"}}, "phases": {"a": {"steps": [
        {"node": "RDS", "send": "su - sdfrun", "prompt_regex": r"rdshyd201\$"},
        {"node": "RDS", "send": "exit", "prompt_regex": "Connection to .* closed.",
         "validation": {"success": {"criteria": {"regex": "Connection to .* closed."}}}},
    ]}}}
    assert drop_session_exits(wf) == 0
    step = wf["phases"]["a"]["steps"][1]
    assert step["prompt_regex"] == "rdsggn01:~ #" and "regex" not in step["validation"]["success"]["criteria"]


def test_when_word_operators_become_symbols_outside_quotes():
    from skills.network_config_gen.autofix import fix_condition_operators
    c = "configSequences.0.records.0.data.Test IMSI == '' and configSequences.0.records.0.data.Test MSISDN != ''"
    assert fix_condition_operators(c) == c.replace(" and ", " && ")
    assert fix_condition_operators("a == 'x or y' OR b != ''") == "a == 'x or y' || b != ''"
    assert fix_condition_operators("brand != ''") == "brand != ''"


def test_local_arguments_are_single_quoted():
    from skills.network_config_gen.autofix import single_quote_arguments
    cmd = 'python report.py --pre "${pre}" --name "it\'s" --out "r_${ORDERNO}.html"'
    assert single_quote_arguments(cmd) == r"python report.py --pre '${pre}' --name 'it'\''s' --out 'r_${ORDERNO}.html'"
    assert single_quote_arguments("python x.py --a 'y'") == "python x.py --a 'y'"


def test_whole_ciq_record_placeholder_is_rejected():
    from skills.network_config_gen.validator import check_placeholders
    wf = {"nodes": {"LOCAL": {"type": "local"}}, "phases": {"a": {"steps": [
        {"node": "LOCAL", "send": "python r.py --ciq '${configSequences.0.records.0.data}'"}]}}}
    assert any("object Object" in e for e in check_placeholders(wf))


def test_single_file_listing_regex_never_expects_total():
    import re
    from skills.network_config_gen.autofix import fix_single_file_listings
    step = {"node": "PGW", "send": "ls -l /srv/out/${ORDERNO}.xml.res 2>&1 || true",
            "validation": {"description": "Response file exists and is not empty",
                           "success": {"criteria": {"regex": r"total\s+\d+\s+.*\s+.+?.xml.res"}}}}
    wf = {"phases": {"a": {"steps": [step]}}}
    assert fix_single_file_listings(wf) == 1
    regex = step["validation"]["success"]["criteria"]["regex"]
    line = "-rw-r--r-- 1 oamsys oamsys     1647 Nov 03 09:17 /srv/out/TEST_ORDERNO.xml.res"
    assert re.search(regex, line, re.M) and not re.search(regex, line.replace(" 1647 ", " 0 "), re.M)
    assert fix_single_file_listings(wf) == 0


def test_helper_script_called_on_a_node_runs_on_local_with_a_download():
    import yaml
    from skills.network_config_gen.autofix import fix_workflow
    wf = yaml.safe_load(fix_workflow(
        "nodes: {PGW: {type: remote, plugin: ssh}}\nphases:\n  a:\n    steps:\n"
        "    - {node: PGW, send: 'ls -l /out/${ORDERNO}.xml.res'}\n"
        "    - {node: PGW, send: \"python parse_response.py --response_file '/out/${ORDERNO}.xml.res'\"}\n"
        "    - {node: PGW, send: 'python /opt/tool/check.py'}\n"))
    steps = wf["phases"]["a"]["steps"]
    assert steps[1]["sftp"]["remote_path"] == "/out/${ORDERNO}.xml.res"
    assert steps[2]["node"] == "LOCAL" and steps[2]["send"] == "python parse_response.py --response_file '${ORDERNO}.xml.res'"
    assert steps[3]["node"] == "PGW"  # the node's own tool stays


def test_rows_without_a_column_use_the_looked_up_value():
    from skills.network_config_gen.autofix import unify_alternative_columns
    imsi, msisdn = "configSequences.0.records.0.data.Test IMSI", "configSequences.0.records.0.data.Test MSISDN"
    steps = [
        {"node": "RDS", "send": "ldapsearch imsi=${" + imsi + "}", "when": f"{imsi} != ''",
         "register": [{"regex": "(?<pre>dn: .+)"}]},
        {"node": "RDS", "send": "ldapsearch msisdn=${" + msisdn + "} | grep provisionedIMSI",
         "when": f"{imsi} == '' && {msisdn} != ''", "register": [{"regex": r"provisionedIMSI: (?<resolvedImsi>\d+)"}]},
        {"node": "RDS", "send": "ldapsearch post imsi=${" + imsi + "}", "when": f"{imsi} != ''"},
    ]
    wf = {"nodes": {"RDS": {}, "LOCAL": {}}, "phases": {"a": {"steps": steps}}}
    assert unify_alternative_columns(wf) == 2
    sends = [s["send"] for s in wf["phases"]["a"]["steps"]]
    assert sends[0] == "echo ${" + imsi + "}" and "msisdn=" in sends[1]           # lookup moved first
    assert sends[2] == "ldapsearch imsi=${resolvedImsi}" and "when" not in wf["phases"]["a"]["steps"][2]
    assert sends[3] == "ldapsearch post imsi=${resolvedImsi}"
    assert unify_alternative_columns(wf) == 0


def test_dict_argument_becomes_one_flag_per_field():
    from skills.network_config_gen.autofix import split_dict_arguments
    cmd = ("python r.py --ciq_data '{'Mandatory Cap': '${configSequences.0.records.0.data.Mandatory Cap}', "
           "'Packet Data Network Type': '${configSequences.0.records.0.data.Packet Data Network Type}'}' --out 'r.html'")
    assert split_dict_arguments(cmd) == (
        "python r.py --ciq_data_mandatory_cap '${configSequences.0.records.0.data.Mandatory Cap}' "
        "--ciq_data_packet_data_network_type '${configSequences.0.records.0.data.Packet Data Network Type}' --out 'r.html'")
    assert split_dict_arguments("python r.py --a 'x' --b 'y'") == "python r.py --a 'x' --b 'y'"


def test_double_quotes_in_local_commands_are_rejected():
    from skills.network_config_gen.validator import check_script_arguments
    wf = {"phases": {"a": {"steps": [{"node": "LOCAL", "send": "python r.py --a '{\"k\": \"v\"}'"}]}}}
    assert any("double quote" in e for e in check_script_arguments(wf))


def test_absence_check_runs_before_the_upload_that_produces_the_file():
    from skills.network_config_gen.autofix import order_absence_checks
    absent = {"node": "PGW", "send": "ls -l /out/${ORDERNO}.xml.res 2>&1 || true",
              "validation": {"success": {"criteria": {"regex": "No such file or directory"}}}}
    upload = {"node": "PGW", "sftp": {"operation": "upload", "local_path": "${ORDERNO}.xml",
                                      "remote_path": "/in/${ORDERNO}.xml"}}
    present = {"node": "PGW", "send": "ls -l /out/${ORDERNO}.xml.res"}
    wf = {"phases": {"a": {"steps": [upload, absent, present]}}}
    assert order_absence_checks(wf) == 1
    assert wf["phases"]["a"]["steps"] == [absent, upload, present]
    assert order_absence_checks(wf) == 0
