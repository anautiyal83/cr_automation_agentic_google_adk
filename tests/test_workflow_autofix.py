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


def test_condition_with_method_call_or_negation_rejected():
    # G-15: x.endsWith('.tar') and !x.endsWith('.tar') were both skipped by the JAR
    from skills.network_config_gen.validator import check_condition_syntax
    d = "configSequences.0.records.0.data."
    wf = {"phases": {"a": {"steps": [
        {"node": "MRF", "send": "tar -xf f", "when": d + "INPUT_FILE.endsWith('.tar')"},
        {"node": "MRF", "send": "cp f", "when": "!" + d + "INPUT_FILE.endsWith('.tar')"},
        {"node": "MRF", "send": "ok", "when": d + "IFC != 'UNCHANGED' && " + d + "IFC != ''"}]}}}
    assert len(check_condition_syntax(wf)) == 2


def test_record_columns_only():
    from skills.network_config_gen.validator import check_record_columns
    wf = {"phases": {"a": {"steps": [
        {"node": "MRF", "send": "cp /tmp/${configSequences.0.records.0.data.INPUT_FILE} /a/${configSequences.0.records.0.data.Circle}/",
         "when": "configSequences.0.records.0.data.VER == '13'"}]}}}
    errs = check_record_columns(wf, {"sheet": "ANNOUNCEMENT_FILES", "columns": ["GROUP", "INPUT_FILE"]})
    assert sorted(e.split("'")[1] for e in errs) == ["Circle", "VER"]


def test_unattributed_commands_count_for_the_only_node():
    from skills.network_config_gen.skill import _expected_coverage
    req = {"category_1_node_activity": {"nodes": [{"name": "MRF"}]},
           "category_2_commands": {"commands": [{"cmd": "a", "node": "MRF"}, {"cmd": "b", "node": "unknown"}]}}
    assert _expected_coverage(req)["node_commands"] == {"MRF": 2}


def test_method_conditions_become_bash_tests():
    # G-15: method calls / ! / version masks in `when` are always false in the JAR
    from skills.network_config_gen.autofix import condition_to_bash, shell_guard_conditions
    d = "configSequences.0.records.0.data."
    assert condition_to_bash(d + "INPUT_FILE.endsWith('.tar') == false && MRF.VER == '13.X.X.X'") == \
        "[[ ! '${" + d + "INPUT_FILE}' == *'.tar' && '${MRF.VER}' == '13.'*'.'*'.'* ]]"
    assert condition_to_bash(d + "IFC.matches('" + "\\" + "d+')") == "[[ '${" + d + "IFC}' =~ ^([0-9]+)$ ]]"
    assert condition_to_bash("foo(bar) > 3") == ""          # not translatable: left for the validator
    wf = {"phases": {"a": {"steps": [{"node": "MRF", "send": "tar -xf f", "when": d + "INPUT_FILE.endsWith('.tar')",
                                      "validation": {"success": {"criteria": {"regex": "ok"}}}}]}}}
    assert shell_guard_conditions(wf) == 1
    step = wf["phases"]["a"]["steps"][0]
    assert "when" not in step and step["send"].startswith("if [[ '${" + d + "INPUT_FILE}' == *'.tar' ]]; then tar -xf f;")
    assert '"' not in step["send"]                           # LOCAL commands break on double quotes
    assert step["validation"]["success"]["criteria"]["regex"] == "(?:ok)|SKIPPED_BY_CONDITION"


def test_backslash_quote_in_single_quoted_scalar_repaired():
    from skills.network_config_gen.autofix import sanitize_yaml
    bad = "a:\n  regex: 'tar: Removing leading `/" + "\\" + "''\n"
    assert yaml.safe_load(sanitize_yaml(bad))["a"]["regex"] == "tar: Removing leading `/'"


def test_ciq_path_column_replaces_mop_example_directory():
    # G-15: the CIQ gives the destination per file; the MOP's example directory is swapped for it
    from skills.network_config_gen.autofix import use_path_columns
    base = "/var/opt/swms/clips/persistent/provisioned/audioclips"
    rc = {"filled": ["DEST"], "samples": {"DEST": [base + "/RAJ/POSTPAID", base + "/RAJ/POSTPAID-0"]}}
    wf = {"phases": {"a": {"steps": [
        {"node": "MRF", "send": "cp /tmp/f " + base + "/${MRF.Circle}/PREPAID/"},
        {"node": "MRF", "send": "chmod 777 " + base + "/${MRF.Circle}/PREPAID/${x}"},
        {"node": "MRF", "send": "tar -czf /tmp/b.tgz /var/opt/swms/backup/a/b/c"}]}}}
    assert use_path_columns(wf, rc) == 2
    s = [st["send"] for st in wf["phases"]["a"]["steps"]]
    ref = "${configSequences.0.records.0.data.DEST}"
    assert s == ["cp /tmp/f " + ref + "/", "chmod 777 " + ref + "/${x}", "tar -czf /tmp/b.tgz /var/opt/swms/backup/a/b/c"]


def test_missing_column_is_fixed_by_step_6_not_asked(tmp_path):
    """DEP-53: the error names the node values and the shell derivation; the correction prompt
    carries the CIQ section; a framework error is never a step 1 question."""
    import yaml
    from skills.network_config_gen import prompts
    from skills.network_config_gen.validator import behaviour_issues, check_record_columns, validate_workflow
    d = "configSequences.0.records.0.data."
    rec = {"sheet": "ANNOUNCEMENT_FILES", "columns": ["GROUP", "INPUT_FILE"], "node_fields": {"MRF": ["VER", "Circle"]}}
    wf = {"nodes": {"MRF": {"type": "remote", "plugin": "ssh", "ssh": {"host": "${MRF.niamID-1}"}}},
          "phases": {"a": {"steps": [
              {"node": "MRF", "send": f"if [[ '${{{d}MRF_VERSION}}' == '13' ]]; then cp /tmp/${{{d}INPUT_FILE}} /x/; fi"},
              {"node": "MRF", "send": f"rm -rf /x/${{{d}INPUT_FILE_BASENAME}}*"}]}}}
    errs = check_record_columns(wf, rec)
    assert len(errs) == 2 and all("${MRF.VER}" in e and "$(basename" in e for e in errs)
    text = yaml.safe_dump(wf)
    expected = {"record_columns": rec}
    all_errors = validate_workflow(text, None, None, expected)
    assert any("MRF_VERSION" in e for e in all_errors)
    assert not [e for e in behaviour_issues(text, None, expected) if "MRF_VERSION" in e or "BASENAME" in e]
    filled = prompts.WORKFLOW_REGENERATE.format(current_yaml="x", test_failures="y", reviewer_analysis="", nodes_summary="",
                                                phases="", developer_answers="", developer_notes="", runtime_params="",
                                                ciq="  Node values of the current MRF node: ${MRF.VER}")
    assert "=== CIQ DATA" in filled and "${MRF.VER}" in filled and "${node}" not in filled.replace("Never ${node}", "")


def test_cwd_prompts_match_any_directory():
    """E030: after `cd /var/opt/.../POSTPAID/` the MRF prompt is `[root@rjjvrmr03 POSTPAID]#`; a prompt_regex
    written for `~` never matched and the rollback-only run failed ("prompt not ready")."""
    import re

    from skills.network_config_gen.autofix import generalize_prompt_cwd
    wf = {"nodes": {
        "MRF": {"prompt_regex": r"(?m)\[swmsuser@rjjvrmr03 ~]\$|\[root@rjjvrmr03 ~]#"},
        "X": {"prompt_regex": r"(?m)\[(swmsuser|root)@rjjvrmr03 (.*?)\][#$]\s*$"},
        "PGW": {"prompt_regex": r"pgwggn01:~ #|\[provgw@pgwggn01 ~]\$"},
    }, "phases": {"a": {"steps": [{"node": "MRF", "send": "su - swmsuser",
                                    "prompt_regex": r"\[swmsuser@rjjvrmr03 tmp\]\$"}]}}}
    assert generalize_prompt_cwd(wf) == 4
    mrf = wf["nodes"]["MRF"]["prompt_regex"]
    for prompt in ("[root@rjjvrmr03 POSTPAID]#", "[root@rjjvrmr03 ~]#", "[swmsuser@rjjvrmr03 tmp]$"):
        assert re.search(mrf, prompt), prompt
    assert re.search(wf["nodes"]["X"]["prompt_regex"], "[root@rjjvrmr03 POSTPAID]#")
    pgw = wf["nodes"]["PGW"]["prompt_regex"]
    assert re.search(pgw, "[provgw@pgwggn01 inbox]$") and re.search(pgw, "pgwggn01:~ #")
    assert re.search(wf["phases"]["a"]["steps"][0]["prompt_regex"], "[swmsuser@rjjvrmr03 ~]$")
    assert generalize_prompt_cwd(wf) == 0                                       # idempotent


def test_tar_backup_success_regex_matches_gnu_tar():
    """DEP-58: GNU tar prints "tar: Removing leading `/' from member names", never the quoted form
    the LLM wrote; such a regex becomes the archive's `ls -1` line, a matching one is kept."""
    import re
    from skills.network_config_gen.autofix import tar_create_success

    def step(send, regex):
        return {"node": "MRF", "send": send, "validation": {"success": {"criteria": {"regex": regex}}}}

    wrong = step("tar -czvf /tmp/announcement_backup.tar.gz ${rec.data.DEST}/", "tar: Removing '.*'")
    dated = step("tar -czf /tmp/bk_$(date +%F).tgz /etc/x", "Removing '/'")
    good = step("tar -czvf /tmp/b.tar.gz /d/", "tar: Removing leading")
    local = {"node": "LOCAL", "send": "tar -czf /tmp/l.tgz /x", "validation": {"success": {"criteria": {"regex": "nope"}}}}
    wf = {"nodes": {"MRF": {"type": "remote"}, "LOCAL": {"type": "local"}},
          "phases": {"backup": {"steps": [wrong, dated, good, local]}}}
    # run e47b5409: tar -v never prints the archive name, so this regex can only match an `ls` of it
    e47 = step("tar -czvf /tmp/announcement_backup_$(date +%Y%m%d%H%M%S).tar.gz ${rec.data.DEST}/", r".*\.tar\.gz")
    wf["phases"]["backup"]["steps"].append(e47)
    assert tar_create_success(wf) == 3
    assert e47["send"].endswith(" && ls -1 /tmp/announcement_backup*.tar.gz")   # a glob: date runs once
    assert re.search(e47["validation"]["success"]["criteria"]["regex"], "/tmp/announcement_backup_20261104230136.tar.gz")
    assert wrong["send"].endswith(" && ls -1 /tmp/announcement_backup.tar.gz")
    regex = wrong["validation"]["success"]["criteria"]["regex"]
    assert re.search(regex, "tar: Removing leading `/' from member names\n/tmp/announcement_backup.tar.gz", re.M)
    regex = dated["validation"]["success"]["criteria"]["regex"]
    assert re.search(regex, "/tmp/bk_2026-11-04.tgz", re.M) and re.search(regex, "/tmp/bk.tgz", re.M)
    assert good["validation"]["success"]["criteria"]["regex"] == "tar: Removing leading" and "ls -1" not in good["send"]
    assert local["validation"]["success"]["criteria"]["regex"] == "nope"
    assert tar_create_success(wf) == 0  # idempotent


def test_no_output_check_passes_on_empty_output():
    """The JAR rejects any regex with ^ on empty output (probed, run c89f65c2): a silent cp failed
    `(?:^$)|SKIPPED_BY_CONDITION`. Empty-only alternatives become `.*`; others stay."""
    from skills.network_config_gen.autofix import empty_output_success
    from skills.network_config_test.scenarios import java_regex_matches

    def step(rx):
        return {"node": "MRF", "send": "cp a b", "validation": {"success": {"criteria": {"regex": rx}}}}
    steps = [step(r"(?:^$)|SKIPPED_BY_CONDITION"), step(r"^\s*$"), step(r"(?:^$|SKIPPED_BY_CONDITION)"),
             step(r"^(?!.*error).*$"), step(r"(?:.*)|SKIPPED_BY_CONDITION"), step(r"^total \d+")]
    wf = {"phases": {"activity": {"steps": steps}}}
    assert empty_output_success(wf) == 3
    rx = [s["validation"]["success"]["criteria"]["regex"] for s in steps]
    assert rx[:3] == [r".*|SKIPPED_BY_CONDITION", ".*", r"(?:.*|SKIPPED_BY_CONDITION)"]
    assert rx[3:] == [r"^(?!.*error).*$", r"(?:.*)|SKIPPED_BY_CONDITION", r"^total \d+"]   # kept
    assert all(java_regex_matches(r, "") for r in rx[:3]) and java_regex_matches(rx[0], "SKIPPED_BY_CONDITION")
    assert not java_regex_matches(r"^$", "") and java_regex_matches(r"$", "")       # the JAR's rule
    assert java_regex_matches(r"[^x]*", "")                                          # a class, no anchor
    assert empty_output_success(wf) == 0


def test_chmod_check_reads_a_listing_and_a_skip_passes():
    """DEP-63 (run c89f65c2): chmod prints nothing, its count check had nothing to count; the JAR
    counts `.*` twice per line, fails on any parenthesis in expr, reads unregistered vars as ''."""
    from skills.network_config_gen.autofix import (expr_without_parentheses, guard_skip_expr,
                                                    list_after_silent, loop_count_nonempty)
    step = {"node": "MRF",
            "send": "if [[ ! '${MRF.VER}' == '13.'* ]]; then chmod 777 ${rec.data.DEST}/*; "
                    "else echo SKIPPED_BY_CONDITION; fi",
            "register": [{"regex": "(?<fileLine>.*)", "loop": True, "count_var": "TOTAL_FILES"},
                         {"regex": "^-rwxrwxrwx.*", "loop": True, "count_var": "OK_PERMISSIONS"}],
            "validation": {"success": {"criteria": {"expr": "(OK_PERMISSIONS == TOTAL_FILES && TOTAL_FILES > 0)"}}}}
    plain_cp = {"node": "MRF", "send": "cp /tmp/a /opt/x/", "validation": {"success": {"criteria": {"regex": ".*"}}}}
    wf = {"phases": {"activity": {"steps": [step, plain_cp]}}}
    for rule in (list_after_silent, loop_count_nonempty, expr_without_parentheses, guard_skip_expr):
        assert rule(wf) == 1, rule.__name__
        assert rule(wf) == 0, rule.__name__ + " is idempotent"
    assert step["send"] == ("if [[ ! '${MRF.VER}' == '13.'* ]]; then chmod 777 ${rec.data.DEST}/* && "
                            "ls -l ${rec.data.DEST}/*; else echo SKIPPED_BY_CONDITION; fi")
    assert step["register"][0]["regex"] == "(?<fileLine>.+)"
    assert step["register"][-1] == {"regex": "(?<ncSkipped>SKIPPED_BY_CONDITION)"}
    assert step["validation"]["success"]["criteria"]["expr"] == \
        "OK_PERMISSIONS == TOTAL_FILES && TOTAL_FILES > 0 || ncSkipped == 'SKIPPED_BY_CONDITION'"
    assert plain_cp["send"] == "cp /tmp/a /opt/x/"     # its check needs no output


def test_untar_branch_verifies_the_archive_members():
    """Run 9de5fcb8, user decision A: after `tar -xf A -C D`, D holds A's members — the check
    `ls D/ | grep <A's name>` becomes a member listing on the extract branch, counted for 777."""
    import re
    from skills.network_config_gen.autofix import untar_check_issues, untar_member_check
    from skills.network_config_test.scenarios import java_regex_matches
    REC = "${configSequences.0.records.0.data."
    dest, file = f"{REC}DEST}}", f"{REC}INPUT_FILE}}"
    extract = {"node": "MRF", "send": f"if [[ ! '${{MRF.VER}}' == '13.'* ]]; then tar -xf /tmp/{file} -C {dest}/; "
                                      "else echo SKIPPED_BY_CONDITION; fi"}
    precheck = {"node": "MRF", "send": f'ls -ltrh {dest}/ | grep "{file}" || true'}
    check = {"node": "MRF", "send": f'ls -ltrh {dest}/ | grep "{file}"',
             "validation": {"success": {"criteria": {"regex": "-rwxrwxrwx.*.+?"}}}}
    wf = {"phases": {"activity": {"steps": [precheck, extract, check]}}}
    assert untar_check_issues(wf) and untar_member_check(wf) == 1 and untar_member_check(wf) == 0
    assert not untar_check_issues(wf)
    assert precheck["send"].endswith("|| true")                       # before the extract: unchanged
    assert check["send"] == (f"if [[ ! '${{MRF.VER}}' == '13.'* ]]; then ls -ld $(tar -tf /tmp/{file} | "
                             f"sed 's#^#{dest}/#'); else ls -ltrh {dest}/ | grep \"{file}\"; fi")
    assert check["validation"]["success"]["criteria"] == {"expr": "ncFilesOk == ncFilesListed && ncFilesListed > 0"}
    ok = next(r["regex"] for r in check["register"] if r["count_var"] == "ncFilesOk")
    lines = ["-rwxrwxrwx 1 root root 1 Nov 4 a.wav", "-rw-r--r-- 1 root root 1 Nov 4 b.wav"]
    assert java_regex_matches(ok, lines[0]) and not java_regex_matches(ok, lines[1])


def test_llm_member_listing_counts_every_line():
    """Run 9de5fcb8: the LLM listed the members itself with `^-rwxrwxrwx.*`, which passes when ONE line
    matches — every listed line must have the mode."""
    from skills.network_config_gen.autofix import untar_member_check
    step = {"node": "MRF",
            "send": "if [[ 'x' == '13.'* ]]; then ls -ltrh /d/ | grep \"a\"; else ls -ld $(tar -tf /tmp/a.tar "
                    "2>/dev/null | sed 's#^#/d/#') 2>/dev/null; fi",
            "validation": {"success": {"criteria": {"regex": "^-rwxrwxrwx.*.+?"}}}}
    extract = {"node": "MRF", "send": "tar -xf /tmp/a.tar -C /d/"}
    wf = {"phases": {"a": {"steps": [extract, step]}}}
    assert untar_member_check(wf) == 1 and untar_member_check(wf) == 0
    assert step["validation"]["success"]["criteria"] == {"expr": "ncFilesOk == ncFilesListed && ncFilesListed > 0"}


def test_when_uses_bare_names():
    """Probed 2026-11-05: `when: ${fileCount} > 0` is always false, `fileCount > 0` works."""
    from skills.network_config_gen.autofix import when_bare_operands
    steps = [{"node": "MRF", "send": "x", "when": "${fileCount} > 0"},
             {"node": "MRF", "send": "y", "when": "${configSequences.0.records.0.data.A} != '' && ${MRF.VER} != ''"}]
    wf = {"phases": {"p": {"when": "${x} == 'y'", "steps": steps}}}
    assert when_bare_operands(wf) == 3 and when_bare_operands(wf) == 0
    assert steps[0]["when"] == "fileCount > 0" and wf["phases"]["p"]["when"] == "x == 'y'"
    assert steps[1]["when"] == "configSequences.0.records.0.data.A != '' && ${MRF.VER} != ''"


def test_grep_without_match_is_an_answer_when_the_check_allows_it():
    """Run 9de5fcb8: `ls <dir>/ | grep "<file>"` (check `.*`) failed on a fresh node — grep exits 1."""
    from skills.network_config_gen.autofix import grep_no_match_ok

    def step(send, regex=None):
        s = {"node": "MRF", "send": send}
        if regex is not None:
            s["validation"] = {"success": {"criteria": {"regex": regex}}}
        return s
    probe = step('ls -ltrh /d/ | grep "a.tar"', ".*")
    needs = step('ls -ltrh /d/ | grep "a.tar"', "^-rwx.*")        # a missing file must still fail
    nocheck = step("cat /x | grep -i err")
    counted = step('ls /d | grep -c "a"', ".*")
    wf = {"phases": {"p": {"steps": [probe, needs, nocheck, counted]}}}
    assert grep_no_match_ok(wf) == 2 and grep_no_match_ok(wf) == 0
    assert probe["send"].endswith("|| true") and nocheck["send"].endswith("|| true")
    assert needs["send"] == 'ls -ltrh /d/ | grep "a.tar"' and "|| true" not in counted["send"]


def test_silent_command_with_substituted_paths_gets_its_listing():
    """Run 9de5fcb8: `chmod 777 $(tar -tf A | grep -v '/$' | sed …)` counted lines it never printed."""
    from skills.network_config_gen.autofix import list_after_silent
    members = "$(tar -tf /tmp/a.tar | grep -v '/$' | sed 's#^#/d/#')"
    step = {"node": "MRF", "send": f"if [[ ! 'x' == '13.'* ]]; then chmod 777 {members}; else echo SKIPPED_BY_CONDITION; fi",
            "register": [{"regex": "(?<l>.+)", "loop": True, "count_var": "TOTAL"}],
            "validation": {"success": {"criteria": {"expr": "TOTAL > 0"}}}}
    recursive = {"node": "MRF", "send": "chmod -R 755 /opt/x", "validation": {"success": {"criteria": {"expr": "a > 0"}}}}
    wf = {"phases": {"p": {"steps": [step, recursive]}}}
    assert list_after_silent(wf) == 2 and list_after_silent(wf) == 0
    assert step["send"] == (f"if [[ ! 'x' == '13.'* ]]; then chmod 777 {members} && ls -l {members}; "
                            "else echo SKIPPED_BY_CONDITION; fi")
    assert recursive["send"] == "chmod -R 755 /opt/x && ls -l /opt/x"


def test_untar_check_by_basename_is_rewritten_too():
    """Run 3f5d8990: `ls <dest>/ | grep "$(basename <archive> .tar)"` after the untar was not rewritten,
    the validator then (rightly) refused the workflow and step 6 failed."""
    from skills.network_config_gen.autofix import untar_check_issues, untar_member_check
    REC = "${configSequences.0.records.0.data."
    dest, file = f"{REC}DEST}}", f"{REC}INPUT_FILE}}"
    extract = {"node": "MRF", "send": f"if [[ ! 'x' == '13.'* ]]; then tar -xf /tmp/{file} -C {dest}/; "
                                      "else echo SKIPPED_BY_CONDITION; fi"}
    for term in (f"$(basename {file} .tar)", f"$(basename /tmp/{file})", file):
        check = {"node": "MRF", "send": f'ls -ltrh {dest}/ | grep "{term}"',
                 "validation": {"success": {"criteria": {"regex": "^-rwxrwxrwx.*"}}}}
        wf = {"phases": {"a": {"steps": [extract, check]}}}
        assert untar_member_check(wf) == 1, term
        assert check["send"].startswith("if [[ ! 'x' == '13.'* ]]; then ls -ld $(tar -tf /tmp/")
        assert not untar_check_issues(wf)


def test_when_drops_register_prefixes():
    """Run 3f5d8990: `when: registered.backupTarName != ''` is no variable — always false."""
    from skills.network_config_gen.autofix import when_bare_operands
    step = {"node": "MRF", "send": "x", "when": "registered.backupTarName != null && register.y != '' && vars.z == 'a'"}
    wf = {"phases": {"p": {"steps": [step]}}}
    assert when_bare_operands(wf) == 1 and when_bare_operands(wf) == 0
    assert step["when"] == "backupTarName != null && y != '' && z == 'a'"
