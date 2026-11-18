"""Every folder the workflow uses is a variable at the top of the YAML (user 2026-11-18, as production writes it):
helper scripts in ACTIVITY_PYTHON_SCRIPT_PATH with the UC's prefix, the order's files in LOCAL_PATH, node folders
as <TYPE>_<FOLDER>_PATH. Undone by flatten; steps 8 / 9 see the node folders written out."""
from __future__ import annotations

import copy

from skills.helper_script_gen.skill import check_files_from_flags
from skills.network_config_gen import paths, structure
from skills.network_config_gen.autofix import dump_yaml, parse_yaml

UC = "PGW_RDS_1058_IFC_CAPABILITY_MODIFICATION"
FLAT = """
nodes:
  niam_pgw_server: {type: remote, plugin: ssh, ssh: {host: '${NIAM_IP}', user: '${M2MUSER}', ne_id: '${PGW.niamID-1}'}}
  LOCAL: {type: local, plugin: local}
phases:
  preNodeHealthCheck:
    name: Health check before
    steps:
    - {id: pgw_status, node: niam_pgw_server, send: /opt/provgw/tomcat/bin/run_as_provgw.sh status 2>/dev/null}
  activity_configuration:
    name: Change
    steps:
    - id: build
      node: LOCAL
      send: "python create_xml.py --imsi '${configSequences.0.records.0.data.Test IMSI}' --output_file '${ORDER_NO}.xml'"
    - id: outbox_clear
      node: niam_pgw_server
      send: ls -l /srv/sftp/smc-sftp-DEFAULT/outbox/${ORDER_NO}.xml.res 2>&1 || true
    - id: upload
      node: niam_pgw_server
      sftp: {operation: upload, local_path: '${ORDER_NO}.xml', remote_path: '/srv/sftp/smc-sftp-DEFAULT/inbox/${ORDER_NO}.xml'}
    - id: download
      node: niam_pgw_server
      sftp: {operation: download, local_path: '${ORDER_NO}.xml.res', remote_path: '/srv/sftp/smc-sftp-DEFAULT/outbox/${ORDER_NO}.xml.res'}
    - {id: parse, node: LOCAL, send: "python parse_response.py --response_file '${ORDER_NO}.xml.res'"}
"""


def _applied() -> dict:
    parsed = parse_yaml(FLAT)
    structure.apply(parsed, {"uc_name": UC})
    return parsed


def _sends(parsed: dict) -> dict[str, str]:
    out = {}

    def walk(steps):
        for s in steps or []:
            if isinstance(s, dict):
                if s.get("id"):
                    out[s["id"]] = s.get("send") or s.get("sftp")
                walk(s.get("steps"))
    for body in parsed["phases"].values():
        walk(body["steps"])
    return out


def test_folders_are_declared_at_the_top_and_steps_use_their_names():
    wf = _applied()
    vars_ = wf["globals"]["vars"]
    assert list(vars_)[:5] == ["LOCAL_PATH", "ACTIVITY_PYTHON_SCRIPT_PATH", "PGW_BIN_PATH", "PGW_INBOX_PATH",
                               "PGW_OUTBOX_PATH"]
    assert vars_["LOCAL_PATH"] == "/mnt/shared_data/${CHILD_REQ_ID}/${NODE_NAME}"
    assert vars_["ACTIVITY_PYTHON_SCRIPT_PATH"].endswith("/CLICR/ANY/V1/script/python")
    assert vars_["PGW_OUTBOX_PATH"] == "/srv/sftp/smc-sftp-DEFAULT/outbox"
    s = _sends(wf)
    assert s["pgw_status"] == "${PGW_BIN_PATH}/run_as_provgw.sh status 2>/dev/null"  # /dev/null is the system's
    assert s["outbox_clear"].startswith("ls -l ${PGW_OUTBOX_PATH}/${ORDER_NO}.xml.res")
    # one shared script folder: every script carries the UC's prefix; its files are the order's (LOCAL_PATH)
    assert s["build"].startswith("python ${ACTIVITY_PYTHON_SCRIPT_PATH}/pgw_rds_1058_create_xml.py --imsi")
    assert s["build"].endswith("--output_file '${LOCAL_PATH}/${ORDER_NO}.xml'")
    assert s["parse"] == ("python ${ACTIVITY_PYTHON_SCRIPT_PATH}/pgw_rds_1058_parse_response.py "
                          "--response_file '${LOCAL_PATH}/${ORDER_NO}.xml.res'")
    assert s["upload"] == {"operation": "upload", "local_path": "${LOCAL_PATH}/${ORDER_NO}.xml",
                           "remote_path": "${PGW_INBOX_PATH}/${ORDER_NO}.xml"}
    assert s["download"]["local_path"] == "${LOCAL_PATH}/${ORDER_NO}.xml.res"
    # the work folder exists before the first step writes into it
    first = wf["phases"]["activity_configuration"]["steps"][0]
    assert first["id"] == paths.LOCAL_DIR_ID and first["send"] == "mkdir -p ${LOCAL_PATH} && ls -ld ${LOCAL_PATH}"
    assert not any(st.get("id") == paths.LOCAL_DIR_ID for st in wf["phases"]["preNodeHealthCheck"]["steps"])


def test_flatten_gives_the_plain_form_back_and_apply_is_idempotent():
    wf = _applied()
    flat = structure.flattened(wf)
    text = dump_yaml(flat)
    assert "${" + paths.SCRIPT_VAR not in text and "_PATH}" not in text and "pgw_rds_1058_" not in text
    assert "python create_xml.py" in text and "/srv/sftp/smc-sftp-DEFAULT/inbox/${ORDER_NO}.xml" in text
    again = copy.deepcopy(wf)
    structure.apply(again, {"uc_name": UC})
    assert again == wf


def test_simulator_and_script_checks_see_real_folders():
    text = dump_yaml(_applied())
    nodes = paths.expand_nodes_text(text)
    assert "ls -l /srv/sftp/smc-sftp-DEFAULT/outbox/${ORDER_NO}.xml.res" in nodes  # what MockWire receives
    assert "${ACTIVITY_PYTHON_SCRIPT_PATH}/pgw_rds_1058_create_xml.py" in nodes   # the automation server's: kept
    local = paths.local_view(text)
    assert "python pgw_rds_1058_create_xml.py --imsi" in local and "--output_file '${ORDER_NO}.xml'" in local


def test_step9_points_both_folders_at_its_own(tmp_path):
    from skills.network_config_test.jar_executor import _point_local_path
    wf = tmp_path / f"{UC}.yaml"
    wf.write_text(dump_yaml(_applied()), encoding="utf-8")
    _point_local_path(str(wf), str(tmp_path), "")
    vars_ = parse_yaml(wf.read_text(encoding="utf-8"))["globals"]["vars"]
    here = str(tmp_path.resolve()).replace("\\", "/")
    assert vars_["LOCAL_PATH"] == here and vars_["ACTIVITY_PYTHON_SCRIPT_PATH"] == here
    assert vars_["PGW_OUTBOX_PATH"] == "/srv/sftp/smc-sftp-DEFAULT/outbox"


def test_a_script_writes_only_the_files_its_command_names():
    flags = ["--pre", "--output_file"]
    made_up = 'import argparse\nout = f"report_{1}.html"\nwith open(out, "w") as f:\n    f.write("x")\n'
    assert check_files_from_flags(made_up, flags)
    given = ('import argparse\np = argparse.ArgumentParser()\np.add_argument("--output_file")\n'
             'a = p.parse_args()\nwith open(a.output_file, "w") as f:\n    f.write("x")\n')
    assert check_files_from_flags(given, flags) == []
    assert check_files_from_flags(made_up, ["--pre"]) == []  # the command names no file: nothing better
