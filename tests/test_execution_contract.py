"""Execution contract (spec 011): default failure, abort and rollback rules added by step 6."""
from __future__ import annotations

import copy

import pytest

from skills.network_config_gen import contract
from skills.network_config_gen.contract import PHASE_WHEN, changes_node, phase_kind

LOCAL = {"LOCAL"}


@pytest.mark.parametrize("key,name,kind", [
    ("preNodeHealthCheck", "Pre-activity health checks", "pre_health"),
    ("PRE_NODE_HEALTH_CHECK", "", "pre_health"),
    ("postNodeHealthCheck", "Post-activity health checks", "post_health"),
    ("backup", "Backup existing announcement files", "backup"),
    ("activity_configuration", "Announcement file loading", "activity"),
    ("activityPrecheck", "", "activity"),
    ("activity_postcheck", "Verify loaded files", "activity"),
    ("rollback", "Rollback announcement files", "rollback"),
    ("rollbackPostCheck", "", "rollback"),
    ("restore", "Restore previous state", "rollback"),
])
def test_phase_kinds(key, name, kind):
    assert phase_kind(key, {"name": name}) == kind


@pytest.mark.parametrize("step,expected", [
    # E-D3: real directories and configuration are changes; staging in /tmp is not
    ({"node": "MRF", "send": "cp /tmp/a.tar /var/opt/swms/clips/RAJ/"}, True),
    ({"node": "MRF", "send": "\\cp -p /tmp/${rec.data.INPUT_FILE} ${rec.data.MRF_DESTINATION_PATH}"}, True),
    ({"node": "MRF", "send": "tar -xf /tmp/a.tar -C /var/opt/swms/clips/RAJ/"}, True),
    ({"node": "MRF", "send": "tar -xf /tmp/a.tar -C /tmp/work"}, False),
    ({"node": "MRF", "send": "tar -czvf /tmp/backup.tar.gz /var/opt/swms/clips/RAJ/"}, False),  # a backup
    ({"node": "MRF", "send": "chmod 777 /var/opt/swms/clips/RAJ/a.wav"}, True),
    ({"node": "MRF", "send": "rm -f /tmp/old.tar"}, False),
    ({"node": "MRF", "send": "rm -f ${ANNOUNCEMENTFILES}"}, True),                        # unknown → change
    ({"node": "MRF", "send": "ls -ltrh /var/opt/swms/clips/RAJ/ | grep x | wc -l"}, False),
    ({"node": "MRF", "send": "sha256sum /tmp/a.tar > /tmp/a.sum"}, False),
    ({"node": "MRF", "send": "echo x > /etc/app.conf"}, True),
    ({"node": "MRF", "send": "ls /x 2>&1 || true"}, False),
    ({"node": "MRF", "send": "systemctl restart swms"}, True),
    ({"node": "MRF", "send": "systemctl status swms --no-pager"}, False),
    ({"node": "RDS", "send": "ldapmodify -x -h localhost -f /tmp/m.ldif"}, True),
    ({"node": "RDS", "send": "ldapsearch -x -h localhost -b imsi=1"}, False),
    ({"node": "MRF", "send": "sed -i 's/a/b/' /opt/app/conf"}, True),
    ({"node": "PGW", "sftp": {"operation": "upload", "local_path": "a.xml",
                              "remote_path": "/srv/sftp/smc-sftp-DEFAULT/inbox/a.xml"}}, True),
    ({"node": "MRF", "sftp": {"operation": "upload", "local_path": "a.tar", "remote_path": "/tmp/a.tar"}}, False),
    ({"node": "PGW", "sftp": {"operation": "download", "remote_path": "/srv/out/a.res", "local_path": "a.res"}}, False),
    ({"node": "LOCAL", "send": "python create_xml.py --out /data/a.xml"}, False),         # the local server
])
def test_change_on_the_node(step, expected):
    assert changes_node(step, LOCAL) is expected


def _workflow() -> dict:
    return {
        "nodes": {"MRF": {"type": "remote", "plugin": "ssh"}},
        "phases": {
            "preNodeHealthCheck": {"name": "Pre-activity health checks", "on_failure": "continue", "steps": [
                {"node": "MRF", "send": "su - swmsuser", "prompt_regex": "x"},
                {"node": "MRF", "send": "systemctl status swms --no-pager",
                 "validation": {"success": {"criteria": {"regex": "active \\(running\\)"}, "message": "ok"},
                                "failure": {"message": "down"}}},
                {"node": "MRF", "send": "sar 1 10", "register": [{"regex": "(?<idle>\\d+)"}]},
                {"node": "MRF", "send": "echo ${idle}"}]},
            "backup": {"name": "Backup", "steps": [
                {"node": "MRF", "send": "tar -czvf /tmp/b.tar.gz /var/opt/x/"}]},
            "activity_configuration": {"name": "Load files", "steps": [
                {"node": "MRF", "send": "ls /tmp/a.tar"},
                {"node": "MRF", "send": "cp /tmp/a.tar /var/opt/x/"}]},
            "postNodeHealthCheck": {"name": "Post-activity health checks", "steps": [
                {"node": "MRF", "send": "systemctl status swms --no-pager"}]},
            "rollback": {"name": "Restore previous files", "when": "backupFileName != ''", "steps": [
                {"node": "MRF", "send": "tar -xzvf /tmp/b.tar.gz -C /"}]},
        },
    }


def test_rules_are_applied():
    wf = _workflow()
    assert contract.apply(wf) == 5
    assert wf["globals"]["vars"] == {f: "false" for f in contract.FLAGS}
    ph = wf["phases"]
    # rule 1: health checks run completely; a user switch / a value used later stops the phase
    pre = ph["preNodeHealthCheck"]["steps"]
    assert [s["on_failure"] for s in pre] == ["stop", "continue", "stop", "continue"]
    # rule 2: any pre-health failure aborts the next phases
    assert all(s["validation"]["failure"]["vars"] == {"PRECHECK_FAILED": "true"} for s in pre)
    assert pre[1]["validation"]["success"]["criteria"]["regex"]          # the LLM's check is kept
    assert ph["preNodeHealthCheck"]["when"] == PHASE_WHEN["pre_health"]
    # rule 3: backup failure aborts the next phases
    bk = ph["backup"]
    assert bk["on_failure"] == "stop" and bk["steps"][0]["validation"]["failure"]["vars"] == {"BACKUP_FAILED": "true"}
    assert "PRECHECK_FAILED != 'true'" in bk["when"]
    # rule 4: the activity stops on a failure; rollback only after a change
    act = ph["activity_configuration"]
    assert act["on_failure"] == "stop" and act["when"] == PHASE_WHEN["activity"]
    read, change = act["steps"]
    assert read["validation"]["failure"]["vars"] == {"ROLLBACK_REQUIRED": "${CHANGES_MADE}"}
    assert change["validation"]["success"]["vars"] == {"CHANGES_MADE": "true"}
    assert change["validation"]["failure"]["vars"] == {"ROLLBACK_REQUIRED": "true"}
    # post-health: reported only (E-D1), skipped after a rollback
    post = ph["postNodeHealthCheck"]
    assert "ROLLBACK_REQUIRED != 'true'" in post["when"] and "validation" not in post["steps"][0]
    # rule 5: rollback on the flag or ROLLBACK_ONLY; the name carries "Rollback" (JAR, after a stop)
    rb = ph["rollback"]
    # the LLM's own condition joins without parentheses (the JAR ignores phases with them)
    assert rb["when"] == ("ROLLBACK_REQUIRED == 'true' && backupFileName != '' || "
                          "ROLLBACK_ONLY == 'true' && backupFileName != ''")
    assert rb["name"].startswith("Rollback")
    assert all("ROLLBACK_ONLY != 'true'" in ph[k]["when"] for k in ph if k != "rollback")


def test_llm_when_is_kept_and_strip_restores_the_llm_form():
    wf = _workflow()
    wf["phases"]["activity_configuration"]["when"] = "configSequences.0.records.0.data.X != ''"
    original = copy.deepcopy(wf)
    contract.apply(wf)
    assert wf["phases"]["activity_configuration"]["when"] == (
        PHASE_WHEN["activity"] + " && configSequences.0.records.0.data.X != ''")
    once = copy.deepcopy(wf)
    contract.apply(wf)                                    # idempotent
    assert wf == once
    contract.strip(wf)
    for key, body in original["phases"].items():
        assert body.get("when") == wf["phases"][key].get("when")
        for a, b in zip(body["steps"], wf["phases"][key]["steps"]):
            assert a.get("validation") == b.get("validation")
    assert "globals" not in wf


def test_combine_and_split_round_trip():
    from skills.network_config_gen.contract import combine, split_own
    for kind, expr in PHASE_WHEN.items():
        for own in ("", "x != ''", "x != '' || y == 'a'", "x != '' && z == '1'"):
            assert split_own(combine(expr, own)) == (expr, own), (kind, own)
    assert split_own("x != ''") is None                     # not a contract condition
