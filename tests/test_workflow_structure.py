"""Step 6 structure (all CIQ records, repo backups, transfer checksums) and what steps 1/8/9
need from it (DEP-9, G-17, G-18)."""
from __future__ import annotations

import json

from skills.network_config_gen import structure
from skills.network_config_gen.autofix import dump_yaml, parse_yaml
from skills.network_config_gen.validator import check_placeholders, validate_workflow

REC = "configSequences.0.records.0.data."

WORKFLOW = f"""
nodes:
  MRF:
    type: remote
    plugin: ssh
    prompt_regex: 'mrf:~ #'
    ssh: {{host: '${{MRF.niamID-1}}', port: '22', user: oamsys, auth: {{password: '${{SECRET.password}}'}}}}
phases:
  backup:
    name: Backup existing files
    steps:
    - node: MRF
      send: ls -ltrh ${{{REC}DEST}}/
      command_description: list
      register:
      - regex: total\\s(?<totalFiles>\\d+)
    - node: MRF
      when: totalFiles != '0'
      send: tar -czvf /tmp/clips_backup_$(date +%Y%m%d%H%M%S).tar.gz ${{{REC}DEST}}/
      command_description: backup
      register:
      - regex: (?<backupFileName>clips_backup_\\S+)
  activity_configuration:
    name: Load
    steps:
    - node: MRF
      sftp: {{operation: upload, local_path: '${{{REC}FILE}}', remote_path: '/tmp/${{{REC}FILE}}'}}
      command_description: upload
    - node: MRF
      send: cp /tmp/${{{REC}FILE}} ${{{REC}DEST}}/
      command_description: copy
  rollback:
    name: Rollback
    steps:
    - node: MRF
      when: backupFileName != ''
      send: tar -xzvf /tmp/${{backupFileName}} -C ${{{REC}DEST}}/
      command_description: restore
"""


VIA_REPO = {"run_options": {"backup_via_repo": True},
            "category_1_node_activity": {"nodes": [{"name": "MRF", "ssh": {"file_transfer": "scp"}}]}}


def _applied(requirements: dict | None = None) -> dict:
    parsed = parse_yaml(WORKFLOW)
    structure.apply(parsed, requirements)
    return parsed


def _leaves(parsed: dict, phase: str) -> list[dict]:
    return list(structure._all_steps(parsed["phases"][phase]["steps"]))


def test_record_steps_loop_over_all_records():
    parsed = _applied()
    loop = parsed["phases"]["activityConfiguration"]["steps"][0]
    assert loop["type"] == "loop" and loop["for_each"] == "${configSequences.0.records}"
    assert loop["item_var"] == "rec" and loop["reverse_order"] is False  # null NPEs in the JAR
    sends = [s.get("send", "") for s in loop["steps"]]
    assert "cp /tmp/${rec.data.FILE} ${rec.data.DEST}/" in sends
    assert not any(REC in s for s in sends)


def test_every_transfer_is_checksummed_on_both_ends():
    steps = _leaves(_applied(), "activityConfiguration")
    i = next(k for k, s in enumerate(steps) if (s.get("sftp") or {}).get("local_path") == "${rec.data.FILE}")
    before, after = steps[i - 1], steps[i + 1]
    assert before["node"] == "LOCAL" and before["send"] == "sha256sum ${rec.data.FILE}"
    assert after["node"] == "MRF" and after["send"] == "sha256sum /tmp/${rec.data.FILE}"
    src = before["register"][0]["regex"].split("<")[1].split(">")[0]
    dst = after["register"][0]["regex"].split("<")[1].split(">")[0]
    assert after["validation"]["success"]["criteria"]["expr"] == f"{dst} == {src}"


def test_backup_goes_to_repo_server_and_rollback_fetches_it():
    """O-D7: the node runs the copies to / from the repo server (scp per the node type; the
    framework logs in to the node, through NIAM for Nokia), checked with sha256sum on both ends,
    the repo password answered by expect_reply."""
    parsed = _applied(VIA_REPO)
    assert parsed["nodes"]["REPO"]["ssh"]["auth"]["password"] == "${SECRET.repo_password}"
    backup = _leaves(parsed, "backup")
    tar = next(s for s in backup if s.get("send", "").startswith("tar"))
    # no $(date): the archive is fetched again; its name registered under the LLM's variable
    assert tar["send"] == "tar -czvf /tmp/clips_backup.tar.gz ${rec.data.DEST}/ && ls -1 /tmp/clips_backup.tar.gz"
    assert tar["register"] == [{"regex": "(?<backupFileName>clips_backup\.tar\.gz)"}]
    ids = [s.get("id") for s in backup]
    assert ids.index("nc_bk1_sum_node") < ids.index("nc_bk1_copy") < ids.index("nc_bk1_sum_repo")
    copy_ = backup[ids.index("nc_bk1_copy")]
    repo_file = "/backup/netconfig/${crGroup}/${bkHost1}/${rec.data.DEST}/clips_backup.tar.gz"
    assert copy_["node"] == "MRF" and copy_["when"] == "totalFiles != '0'"
    assert copy_["send"] == f"scp /tmp/clips_backup.tar.gz backup@repo-server:{repo_file}"
    assert copy_["expect_reply"] == [{"expect": "assword:", "reply": "${SECRET.repo_password}\n", "timeout_sec": 30}]
    assert '"CHANGES_MADE": "true"' not in json.dumps(copy_)  # nothing changes on the node
    check = backup[ids.index("nc_bk1_sum_repo")]
    assert check["send"] == f"sha256sum {repo_file}" and check["validation"]["success"]["criteria"]["expr"] == "bk1Dst == bk1Src"
    assert not any("sftp" in s for s in backup)  # nothing through the automation server
    assert not any("${node}" in json.dumps(s) for s in backup)  # empties multi-node MOPs
    rollback = _leaves(parsed, "rollbackConfiguration")
    ids = [s.get("id") for s in rollback]
    assert ids.index("nc_rs1_sum_repo") < ids.index("nc_rs1_copy") < ids.index("nc_rs1_sum_node") < ids.index(None)
    assert rollback[ids.index("nc_rs1_copy")]["send"] == \
        "scp backup@repo-server:/backup/netconfig/${crGroup}/${rsHost1}/${rec.data.DEST}/clips_backup.tar.gz /tmp/clips_backup.tar.gz"
    restore = rollback[ids.index(None)]
    assert restore["send"] == "tar -xzvf /tmp/clips_backup.tar.gz -C /" and restore["when"] == "backupFileName != ''"
    loop = parsed["phases"]["rollbackConfiguration"]["steps"]
    assert len(loop) == 1 and restore in loop[0]["steps"]  # extracted in the same record pass


def test_input_files_go_through_the_repo_server():
    """O-D2 "via repo server" moves every file: automation server → repo (framework sftp) → node (the
    node copies it from the repo); the copy into the node's /tmp changes nothing on it (E-D3)."""
    steps = _leaves(_applied(VIA_REPO), "activityConfiguration")
    ids = [s.get("id") for s in steps]
    stage = steps[ids.index("nc_xr1_stage")]
    assert stage["node"] == "REPO"
    assert stage["sftp"]["remote_path"] == "/backup/netconfig/${crGroup}/files/${rec.data.FILE}"
    copy_ = steps[ids.index("nc_xr1_copy")]
    assert copy_["node"] == "MRF"
    assert copy_["send"] == "scp backup@repo-server:/backup/netconfig/${crGroup}/files/${rec.data.FILE} /tmp/${rec.data.FILE}"
    assert '"CHANGES_MADE": "true"' not in json.dumps(copy_)  # into /tmp
    assert ids.index("nc_xr1_stage") < ids.index("nc_xr1_copy") < ids.index("nc_xr1_sum_node")
    assert steps[ids.index("nc_xr1_sum_node")]["send"] == "sha256sum /tmp/${rec.data.FILE}"
    assert not any(s.get("node") == "MRF" and "sftp" in s for s in steps)


def test_sftp_node_types_and_node_changes_through_the_repo():
    from skills.network_config_gen.contract import changes_node
    sftp_reqs = {**VIA_REPO, "category_1_node_activity": {"nodes": [{"name": "MRF", "ssh": {"file_transfer": "sftp"}}]}}
    backup = _leaves(_applied(sftp_reqs), "backup")
    assert next(s for s in backup if s.get("id") == "nc_bk1_copy")["send"] == (
        "sftp backup@repo-server:/backup/netconfig/${crGroup}/${bkHost1}/${rec.data.DEST} "
        "<<< 'put /tmp/clips_backup.tar.gz clips_backup.tar.gz'")
    assert structure.repo_copy("sftp", "u", "repo", "/opt/a/f.wav", "/r/f.wav", False) == "sftp u@repo:/r/f.wav /opt/a/f.wav"
    assert structure.repo_copy("scp", "u", "fd00::1", "/tmp/f", "/r/f", True) == "scp /tmp/f u@[fd00::1]:/r/f"
    # a copy from the repo onto the node outside /tmp changes it; a copy to the repo and the repo's own
    # steps do not
    assert changes_node({"node": "MRF", "send": "sftp u@repo:/r/f.wav /opt/a/f.wav"}, {"LOCAL"}, {"REPO"})
    assert changes_node({"node": "MRF", "send": "scp u@[fd00::1]:/r/f /opt/a/"}, {"LOCAL"}, {"REPO"})
    assert not changes_node({"node": "MRF", "send": "scp u@repo:/r/f /tmp/f"}, {"LOCAL"}, {"REPO"})
    assert not changes_node({"node": "MRF", "send": "scp /opt/f u@repo:/r/f"}, {"LOCAL"}, {"REPO"})
    assert not changes_node({"node": "MRF", "send": "sftp u@repo:/r <<< 'put /opt/f f'"}, {"LOCAL"}, {"REPO"})
    assert not changes_node({"node": "REPO", "send": "mkdir -p /backup/x"}, {"LOCAL"}, {"REPO"})


def test_apply_is_idempotent_and_flatten_gives_the_llm_form():
    for reqs in (None, VIA_REPO):
        once = dump_yaml(_applied(reqs))
        again = parse_yaml(once)
        structure.apply(again, reqs)
        assert dump_yaml(again) == once
        flat = structure.flattened(parse_yaml(once))
        assert "REPO" not in flat["nodes"]
        text = dump_yaml(flat)
        assert "nc_" not in text and "rec.data" not in text and REC + "FILE" in text
        upload = flat["phases"]["activityConfiguration"]["steps"][0]  # back on its node and path
        assert upload["node"] == "MRF" and upload["sftp"]["remote_path"] == f"/tmp/${{{REC}FILE}}"


def test_validator_checks_the_flat_form_and_rejects_node_placeholder():
    for reqs in (None, VIA_REPO):
        errors = validate_workflow(dump_yaml(_applied(reqs)))
        assert not [e for e in errors if "rec." in e or "bkHost" in e or "xfer" in e or "xr1" in e], errors
    assert check_placeholders({"phases": {"p": {"steps": [{"node": "X", "send": "echo ${node}"}]}}})


def test_upload_inputs_staged_for_every_record(tmp_path):
    from skills.network_config_test.jar_executor import stage_upload_inputs
    doc = {"crGroups": [{"crGroup": "CR1", "nodeGroups": [{"nodeGroup": "G", "nodes": [{"node": "MRF1"}],
           "configSequences": [{"records": [{"data": {"FILE": "a.tar"}}, {"data": {"FILE": "b.tar"}}]}]}]}]}
    jf = tmp_path / "ciq.json"
    jf.write_text(json.dumps(doc), encoding="utf-8")
    created = stage_upload_inputs(_applied(), str(jf), "MRF1", "CR1", str(tmp_path / "cwd"))
    assert sorted(p.replace("\\", "/").rsplit("/", 1)[-1] for p in created) == ["a.tar", "b.tar"]


def test_step9_sees_loop_steps():
    from skills.network_config_test.scenarios import workflow_steps
    sends = [s.send for s in workflow_steps(_applied())]
    assert "cp /tmp/${rec.data.FILE} ${rec.data.DEST}/" in sends


def test_step8_leaves_file_commands_to_builtins_and_seeds_backup_sources(tmp_path):
    from skills.network_simulator_setup.guards import backup_seed_files, targets
    from skills.network_simulator_setup.topology import _is_builtin_key, file_builtin
    assert file_builtin("sha256sum /tmp/x") and file_builtin("tar -czvf /tmp/b.tgz /d/")
    assert not file_builtin("tar -xzvf /tmp/b.tgz -C /") and _is_builtin_key("regex:sha256sum\\ \\S+")
    doc = {"crGroups": [{"crGroup": "CR1", "nodeGroups": [{"nodeGroup": "G", "nodes": [{"node": "MRF1"}],
           "configSequences": [{"records": [{"data": {"DEST": "/clips/a"}}, {"data": {"DEST": "/clips/b"}}]}]}]}]}
    jf = tmp_path / "ciq.json"
    jf.write_text(json.dumps(doc), encoding="utf-8")
    assert len(targets([str(jf)])) == 2  # one per record
    seeds = backup_seed_files([{"cmd": "tar -czvf /tmp/clips_backup.tar.gz ${rec.data.DEST}/"}], [str(jf)])
    assert sorted(seeds) == ["/clips/a/existing_content.dat", "/clips/b/existing_content.dat"]


def test_repo_server_answer_parsed():
    from skills.requirement_gathering.questionnaire import parse_repo_server, repo_server_question
    assert parse_repo_server("backup@10.1.2.3:2222 /srv/backups/") == {
        "backup_dir": "/srv/backups", "host": "10.1.2.3", "user": "backup", "port": "2222"}
    assert parse_repo_server("repo.example.net") == {"host": "repo.example.net"}
    assert parse_repo_server("repo01") == {"host": "repo01"}
    # DEP-57: a sentence is no address (the last word "rollback." became the repo host)
    assert parse_repo_server("repo server is an intermittent server which will just keep the backup files "
                             "in case we need to perform a rollback.") == {}
    assert repo_server_question([{"phase": "Backup", "command": "take a backup of the clips"}])
    assert not repo_server_question([{"phase": "Activity", "command": "copy the file"}])


def test_guard_that_holds_for_a_later_record_is_answered():
    from skills.network_simulator_setup.guards import _inner_response
    step = {"validation": {"success": {"criteria": {"regex": "(?:^[^,]+,[^,]+,.*)|SKIPPED_BY_CONDITION"}}},
            "register": [{"regex": "^[^,]+,(?<trigger>[^,]+),.*"}]}
    listing = {"org -c x": {"lines": ["a,TRG1,40453", "a,TRG2,111"]}}
    assert _inner_response("org -c x | grep -i 40453", listing, step) == ["a,TRG1,40453"]
    built = _inner_response("org -c x | grep -i 40453", {}, step)[0]
    assert "40453" in built and built.count(",") >= 2


def test_simulator_prompt_follows_cd():
    from skills.network_simulator_setup.overlay import extend_prompt_regex
    from skills.network_simulator_setup.topology import NodeSpec, _set_prompts, build_instance_config, cwd_prompt
    assert cwd_prompt("[root@rjjvrmr03 ]# ") == ("[root@rjjvrmr03 ~]# ", "[root@rjjvrmr03 \W]# ")
    assert cwd_prompt("rds:/tmp # ") == ("rds:~ # ", "rds:\w # ")
    spec = NodeSpec(name="MRF", instance="MRF", prompt="", user="swmsuser", elevate_to_root=True,
                    prompt_regex=r"(?m)\[(swmsuser|root)@rjjvrmr03 (.*?)\][#$]\s*$")
    _set_prompts(spec, {"root_prompt": "[root@rjjvrmr03 ]#", "prompt_pattern": "[swmsuser@rjjvrmr03 ~]$"})
    assert spec.root_prompt == "[root@rjjvrmr03 ~]# "  # what the sanity check sees after login
    assert "pushPrompt: '[root@rjjvrmr03 \W]# '" in build_instance_config(spec)
    import re
    alt = extend_prompt_regex("x", ["[root@h ~]# "], {"[root@h ~]# ": "[root@h \W]# "})
    assert re.search(alt, "[root@h tmp]#")


def test_condition_variables_register_one_token():
    from skills.network_config_gen.autofix import single_token_condition_vars
    parsed = {"phases": {"b": {"steps": [
        {"node": "MRF", "send": "ls", "register": [{"regex": "(?<files>.*)"}, {"regex": "x(?<other>.*)"}]},
        {"node": "MRF", "send": "tar", "when": "files != ''"}]}}}
    assert single_token_condition_vars(parsed) == 1  # "total 16K" in a when is always false (JAR)
    regs = parsed["phases"]["b"]["steps"][0]["register"]
    assert regs == [{"regex": "(?<files>\S+)"}, {"regex": "x(?<other>.*)"}]


def test_step8_repo_copies_reach_the_node_instance():
    """Spec 010 O-D7 in the simulator: the node's copies are MockWire builtins (peer transfer), the
    node instance lists the repo instance as a peer, the overlay names the repo and answers the
    simulator password."""
    from skills.network_simulator_setup.overlay import build_overlay
    from skills.network_simulator_setup.topology import NodeSpec, add_peers, file_builtin
    assert file_builtin("scp /r/a.tgz u@[${MRF.niamID-1}]:/tmp/a.tgz")
    assert file_builtin(r"regex:^scp\ u@\[x\]:/tmp/a\.tgz\ /r/") and not file_builtin("scp a.txt host:/tmp/")
    assert file_builtin(r"regex:^scp\ /tmp/a\.tgz\ backup@repo\-server:/r/") and file_builtin("scp b@repo:/r/a /tmp/")
    cfg = parse_yaml(add_peers("config: {prompt: '$ '}\n", {"MRF": {"root": "/sim/vfs/MRF", "password": "pw"}}))
    assert cfg["transfer"]["peers"] == {"MRF": {"root": "/sim/vfs/MRF", "password": "pw"}}
    specs = [NodeSpec(name="MRF", instance="MRF", prompt="mrf:~ # "), NodeSpec(name="REPO", instance="REPO", prompt="$ ")]
    overlay = parse_yaml(build_overlay(dump_yaml(_applied(VIA_REPO)), specs, {"MRF": 22200, "REPO": 22201}))
    copy_ = next(s for s in _leaves(overlay, "backup") if s.get("id") == "nc_bk1_copy")
    assert copy_["send"].startswith("scp /tmp/clips_backup.tar.gz backup@REPO:/backup/")
    assert copy_["expect_reply"][0]["reply"] == "netconf-sim\n"
