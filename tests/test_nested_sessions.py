"""Spec 013 Stage B: one NIAM node per CIQ node type, hosts reached from it as nested sessions (generic rules).

    python -m pytest tests/test_nested_sessions.py -v
"""
from __future__ import annotations

import pytest

from skills.network_config_gen import sessions


@pytest.mark.parametrize("send,kind", [
    ("oc exec -it ${POD} -n ${NS} -- bash", "exec"),
    ("oc exec -it ${POD} -n ${NS} bash", "exec"),
    ("kubectl exec -ti app-1 -- /bin/sh", "exec"),
    ("oc rsh app-1", "exec"),
    ("ssh -oPort=8099 dsadmin@${IP}", "ssh"),
    ("ssh -p 2222 -o StrictHostKeyChecking=no admin@10.0.0.1", "ssh"),
    ("ssh admin@10.0.0.1 StrictHostKeyChecking=no", "ssh"),
    ("telnet 10.0.0.2 23", "telnet"),
    ("su - sdfrun", "su"),
    ("sudo -i", "su"),
    # one-shot: no session
    ("kubectl exec -it ${POD} -n ns -- bash -c 'netstat -an'", ""),
    ("oc exec ${POD} -n ns -- ip a", ""),
    ("ssh admin@host uptime", ""),
    ("su - rtp99 -c \"df -kh\"", ""),
    ("echo ssh", ""),
])
def test_enter_kind(send, kind):
    assert sessions.enter_kind(send) == kind


def _wf() -> dict:
    """A jump host (logs in to CIQ node type APP1), a pod and a server the LLM made nodes of."""
    return {
        "nodes": {
            "JUMP": {"type": "remote", "plugin": "ssh", "prompt_regex": r"\[ops@jump [^\]]*]\$",
                     "ssh": {"host": "${JUMP.niamID-1}", "ne_id": "${JUMP.niamID-1}", "user": "niam"}},
            "POD": {"type": "remote", "plugin": "ssh", "prompt_regex": r"\[app@app-7f9c-x2 [^\]]*]\$",
                    "ssh": {"host": "${POD.niamID-1}"}},
            "SRV": {"type": "remote", "plugin": "ssh", "prompt_regex": r"\[admin@10.0.0.9 ~]\$",
                    "ssh": {"host": "${SRV.niamID-1}"}},
            "LOCAL": {"type": "local", "plugin": "local"},
        },
        "phases": {
            "preNodeHealthCheck": {"steps": [
                {"node": "JUMP", "send": "kubectl get pods -n ns"},
                {"node": "JUMP", "send": "oc exec -it ${POD} -n ns bash"},
                {"node": "POD", "send": "ps -ef"},
                {"node": "POD", "send": "ls -l", "prompt_regex": r"\[app@app-7f9c-x2 [^\]]*]\$"},
                {"node": "JUMP", "send": "kubectl get svc"},              # back on the jump host
                {"node": "LOCAL", "send": "echo local"},
                {"node": "JUMP", "send": "ssh -oPort=22 admin@${IP} StrictHostKeyChecking=no"},
                {"node": "SRV", "send": "df -h"},                        # phase ends inside the server
            ]},
        },
    }


def test_hosts_reached_from_the_login_become_nested_sessions():
    wf = _wf()
    assert sessions.nested_sessions(wf, {"APP1": 1})
    assert list(wf["nodes"]) == ["LOCAL", "niam_app_server"]
    node = wf["nodes"]["niam_app_server"]
    assert node["ssh"]["ne_id"] == node["ssh"]["host"] == "${APP1.niamID-1}"
    assert node["prompt_regex"] == sessions.NODE_PROMPT
    steps = wf["phases"]["preNodeHealthCheck"]["steps"]
    assert [s["send"] for s in steps] == [
        "kubectl get pods -n ns", "oc exec -it ${POD} -n ns -- bash", "ps -ef", "ls -l", "exit", "kubectl get svc",
        "echo local", "ssh -oPort=22 -oStrictHostKeyChecking=no admin@${IP}", "df -h", "exit"]
    assert {s["node"] for s in steps} == {"niam_app_server", "LOCAL"}
    enter, inner, leave = steps[1], steps[3], steps[4]
    assert enter["prompt_regex"] == inner["prompt_regex"] == leave["prompt_regex"] == sessions.STEP_PROMPT
    assert steps[7]["expect_reply"] == [{"expect": "assword:", "reply": "${SECRET.admin_password}"}]


def test_idempotent():
    wf = _wf()
    sessions.nested_sessions(wf, {"APP1": 1})
    import copy
    again = copy.deepcopy(wf)
    sessions.nested_sessions(again, {"APP1": 1})
    assert again == wf


def test_sessions_of_each_login_stay_open_across_the_other_login():
    """Two CIQ node types, each its own NIAM connection: a `su` on one stays open while steps run on the other."""
    wf = {"nodes": {"A": {"type": "remote", "ssh": {"ne_id": "${A.niamID-1}"}},
                    "B": {"type": "remote", "ssh": {"ne_id": "${B.niamID-1}"}}},
          "phases": {"act": {"steps": [
              {"node": "A", "send": "su - svc", "prompt_regex": r"svc\$ $"},
              {"node": "A", "send": "cmd1"},
              {"node": "B", "send": "cmd2"},
              {"node": "A", "send": "cmd3"},
              {"node": "A", "send": "exit"},
          ]}}}
    sessions.nested_sessions(wf, {"A": 1, "B": 3})
    steps = wf["phases"]["act"]["steps"]
    assert [s["send"] for s in steps] == ["su - svc", "cmd1", "cmd2", "cmd3", "exit"]
    assert [s["node"] for s in steps] == ["niam_a_server", "niam_a_server", "niam_b_server", "niam_a_server",
                                          "niam_a_server"]
    assert steps[0]["prompt_regex"] == r"svc\$ $"           # su keeps its recorded prompt


def test_unknown_ciq_types_leave_the_workflow_alone():
    wf = _wf()
    assert sessions.nested_sessions(wf, {}) == 0 and "JUMP" in wf["nodes"]


def test_simulator_answers_enter_commands_with_the_nested_prompt(tmp_path):
    """Step 8: the NIAM node's instance gets its NIAM IDs by CIQ type; `oc exec … -- bash` pushes a prompt the
    step's prompt_regex accepts, `ssh user@host` asks for a password first; `exit` stays MockWire's builtin."""
    import json
    import re

    import yaml

    from skills.network_simulator_setup.topology import build_instance_config, build_topology
    wf = _wf()
    sessions.nested_sessions(wf, {"APP1": 1})
    ciq = tmp_path / "ciq.json"
    ciq.write_text(json.dumps({"schemaVersion": "2", "crGroups": [{"crGroup": "CR1", "nodeGroups": [
        {"nodeGroup": "NG1", "nodes": [{"node": "NG1", "APP1": {"niamID-1": "gw-1-CLI"}}]}]}]}), encoding="utf-8")
    reqs = {"category_1_node_activity": {"nodes": [{"name": "JUMP", "type": "remote",
                                                    "ssh": {"prompt_pattern": "[ops@jump ~]$"}}]}}
    spec = next(s for s in build_topology(yaml.safe_dump(wf), reqs, [str(ciq)]) if s.name == "niam_app_server")
    assert spec.ne_ids == ["gw-1-CLI"] and spec.login_prompt.startswith("[ops@jump")
    cfg = yaml.safe_load(build_instance_config(spec, {}))
    exec_key = next(k for k in cfg if str(k).startswith("regex:oc exec"))
    ssh_key = next(k for k in cfg if str(k).startswith("regex:ssh"))
    pushed = cfg[exec_key]["pushPrompt"]
    assert re.search(sessions.STEP_PROMPT, pushed) and pushed != spec.login_prompt
    assert cfg[ssh_key]["interactive"][0]["question"] == "Password: "
    assert "exit" not in cfg
