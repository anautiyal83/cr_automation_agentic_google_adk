"""Tests for step 8 Phase 2: per-node topology and instance configs (spec 004).

    python -m pytest tests/test_simulator_topology.py -v
"""
from __future__ import annotations

import asyncio
import json
import shutil
import time

import pytest

from core.config import PROJECT_ROOT
from shared.state import simulator_processes, stop_simulator
from skills.network_simulator_setup import NetworkSimulatorSetupSkill, SkillInput
from skills.network_simulator_setup.autofix import parse_llm_yaml, parse_yaml
from skills.network_simulator_setup.topology import build_instance_config, build_topology
from skills.network_simulator_setup.validator import validate_mockwire_config

MOCKWIRE_JAR = PROJECT_ROOT / "lib" / "mockwire" / "mockwire-simulator" / "mockwire.jar"
needs_mockwire = pytest.mark.skipif(
    not MOCKWIRE_JAR.exists() or shutil.which("java") is None,
    reason="MockWire JAR or java not available",
)

WORKFLOW = r"""
nodes:
  RDS:
    type: remote
    plugin: ssh
    prompt_regex: 'rdsggn01:~ #|rdsggn01:~ \$'
    ssh: {host: '${node_row.RDS.NIAM_NAME1}', user: '${node_row.RDS.NIAM_USER}',
          ne_id: '${node_row.RDS.NIAM_NAME1}', elevate_to_root: true}
  PGW:
    type: remote
    plugin: ssh
    prompt_regex: 'pgwggn01:~ #|\[provgw@pgwggn01 ~]\$'
    ssh: {host: pgw.example, user: oamsys, elevate_to_root: true}
  LOCAL: {type: local, plugin: local}
phases:
  preNodeHealthCheck:
    steps:
    - node: RDS
      send: su - sdfrun
      validation: {success: {criteria: {regex: 'rdsggn01:~ \$'}}}
    - {node: RDS, send: ndsLoadStats}
    - {node: RDS, send: exit}
    - {node: PGW, send: su - provgw}
    - {node: PGW, send: 'sftp put ${env.ORDERNO}.xml /srv/inbox'}
  activity_configuration:
    steps:
    - {node: LOCAL, send: python create_xml.py}
"""

REQUIREMENTS = {"category_1_node_activity": {"nodes": [
    {"name": "RDS", "ssh": {"root_prompt": "rdsggn01:~ #", "prompt_pattern": "[oamsys@rdsggn01 ~]$"}},
    {"name": "PGW", "target_user_after_root": "provgw",
     "target_user_after_root_prompt": "[provgw@pgwggn01 ~]$",
     "ssh": {"root_prompt": "pgwggn01:~ #"}},
]}}


@pytest.fixture
def ciq_json(tmp_path):
    path = tmp_path / "UC_CR1.json"
    path.write_text(json.dumps({"data": {"nodeGroups": [
        {"nodeGroup": z, "crGroup": "CR1", "nodes": [{"node": z,
            "RDS": {"niamID-1": f"{z}-RD1-CLI", "niamID-2": f"{z}-RD2-CLI"},
            "PGW": {"niamID-1": f"{z}-PG1-CLI"}}]}
        for z in ("EAST", "NORTH")]}}), encoding="utf-8")
    return [str(path)]


def test_topology_one_spec_per_remote_node(ciq_json):
    specs = {s.name: s for s in build_topology(WORKFLOW, REQUIREMENTS, ciq_json)}
    assert set(specs) == {"RDS", "PGW"}  # LOCAL is not simulated

    rds, pgw = specs["RDS"], specs["PGW"]
    # Elevated nodes land on the root prompt, which must satisfy prompt_regex
    assert rds.prompt == "rdsggn01:~ # " and pgw.prompt == "pgwggn01:~ # "
    # su pushes the prompt the workflow validates / requirements describe
    assert rds.escalations == {"su - sdfrun": "rdsggn01:~ $ "}
    assert pgw.escalations == {"su - provgw": "[provgw@pgwggn01 ~]$ "}
    # NIAM only where the workflow uses ne_id; IDs from every zone
    assert rds.ne_ids == ["EAST-RD1-CLI", "EAST-RD2-CLI", "NORTH-RD1-CLI", "NORTH-RD2-CLI"]
    assert pgw.ne_ids == []
    assert pgw.transfer and not rds.transfer
    assert pgw.user == "oamsys" and rds.user == "oamsys"


def test_topology_warns_when_niam_ids_missing():
    rds = next(s for s in build_topology(WORKFLOW, REQUIREMENTS, []) if s.name == "RDS")
    assert rds.ne_ids == [] and any("niamID" in w for w in rds.warnings)


def test_instance_config_deterministic_parts_win(ciq_json):
    rds = next(s for s in build_topology(WORKFLOW, REQUIREMENTS, ciq_json) if s.name == "RDS")
    llm = {"ndsLoadStats": {"lines": ["Updates Rejected: 0"]},
           "su - sdfrun": {"lines": ["hijack"]}, "exit": {"lines": ["bye"]},
           "config": {"prompt": "wrong"}}
    cfg = parse_yaml(build_instance_config(rds, llm))
    assert cfg["config"]["prompt"] == "rdsggn01:~ # "
    assert cfg["su - sdfrun"] == {"lines": [], "pushPrompt": "rdsggn01:~ $ "}
    assert cfg["exit"] == {"lines": [], "popPrompt": True}
    assert cfg["ndsLoadStats"] == {"lines": ["Updates Rejected: 0"]}
    assert cfg["niam"]["users"][0]["neIds"][0] == "EAST-RD1-CLI"
    assert validate_mockwire_config(build_instance_config(rds, llm)) == []


def test_parse_llm_yaml_merges_documents_and_strips_fences():
    raw = "Here you go:\n```yaml\n'ss':\n  lines: ['Link UP']\n---\n'sar 1 10':\n  lines: ['x']\n```"
    assert set(parse_llm_yaml(raw)) == {"ss", "sar 1 10"}


def _fake_llm(calls: list):
    async def llm(system: str, user: str) -> str:
        calls.append(user)
        if "Node: RDS" in user:
            return "'ndsLoadStats':\n  lines:\n  - 'Updates Rejected:        0'\n"
        return "'regex:sftp put \\S+\\.xml /srv/inbox':\n  lines: []\n"
    return llm


def test_setup_without_start_builds_one_config_per_node(tmp_path, ciq_json):
    calls: list = []
    skill = NetworkSimulatorSetupSkill(llm_fn=_fake_llm(calls))
    out = asyncio.run(skill.setup(SkillInput(
        uc_name="UC", workflow_yaml=WORKFLOW, requirements=REQUIREMENTS,
        ciq_json_files=ciq_json, run_dir=str(tmp_path), mockwire_jar=str(MOCKWIRE_JAR),
        auto_start=False)))
    assert out.status == "passed", out.validation_errors
    assert set(out.instance_configs) == {"RDS", "PGW"}
    assert len(calls) == 2  # one LLM call per node
    assert (tmp_path / "simulator" / "config" / "RDS.yaml").exists()
    assert (tmp_path / "simulator" / "config" / "PGW.yaml").exists()
    assert out.simulator_config_yaml.count("# ===== Instance") == 2


@needs_mockwire
def test_setup_starts_one_instance_per_node_and_ssh_flow_works(tmp_path, ciq_json):
    paramiko = pytest.importorskip("paramiko")

    def read_until(ch, expected: str, timeout: float = 6.0) -> str:
        end, out = time.time() + timeout, ""
        while time.time() < end and expected not in out:
            if ch.recv_ready():
                out += ch.recv(65535).decode(errors="replace")
            else:
                time.sleep(0.05)
        return out

    async def scenario():
        skill = NetworkSimulatorSetupSkill(llm_fn=_fake_llm([]))
        out = await skill.setup(SkillInput(
            uc_name="UC", workflow_yaml=WORKFLOW, requirements=REQUIREMENTS,
            ciq_json_files=ciq_json, run_dir=str(tmp_path), mockwire_jar=str(MOCKWIRE_JAR),
            ttl_minutes=3))
        sim = out.simulator
        assert sim and sim.is_running and len(sim.instances) == 2
        # Phase 4: real SSH sanity passes for both nodes and the report says so
        assert out.status == "passed", out.validation_errors
        assert out.sanity.nodes_passed == 2
        rds = next(nr for nr in out.sanity.node_results if nr.node == "RDS")
        assert (rds.niam, rds.prompt_match, rds.basic_command, rds.escalation, rds.uc_command) ==             ("PASS", "PASS", "PASS", "PASS", "PASS")
        assert "Sanity Checks" in out.report_html and ">PASS<" in out.report_html
        simulator_processes["topology-test"] = sim
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect("localhost", port=sim.port_for("RDS"), username="oamsys",
                           password="netconf-sim", look_for_keys=False, allow_agent=False)
            ch = client.invoke_shell()
            assert "neId" in read_until(ch, "neId")        # NIAM banner after ~2 s login delay
            ch.send("NORTH-RD2-CLI\n")
            assert "rdsggn01:~ #" in read_until(ch, "rdsggn01:~ #")  # root prompt after NIAM login
            ch.send("su - sdfrun; echo __CMD_DONE__:$?\n")
            assert "rdsggn01:~ $" in read_until(ch, "rdsggn01:~ $")  # escalated prompt pushed
            ch.send("ndsLoadStats; echo __CMD_DONE__:$?\n")
            got = read_until(ch, "rdsggn01:~ $")
            assert "Updates Rejected" in got and "__CMD_DONE__:0" in got
            ch.send("exit\n")
            assert "rdsggn01:~ #" in read_until(ch, "rdsggn01:~ #")  # popped back to root
            client.close()
        finally:
            await stop_simulator("topology-test")
        assert not sim.is_running

    asyncio.run(scenario())


def test_parse_llm_yaml_repairs_double_quoted_regex_keys_and_truncation():
    # Real failure from gemini-2.5-flash: "\S" is an invalid double-quoted YAML escape,
    # and the answer was cut off inside a padded line.
    raw = ("\"sar 1 10\":\n  lines:\n  - 'Average: all 99.95'\n"
           + r'"regex:ls -l /srv/outbox/\S+\.xml\.res":' + "\n  lines:\n  - 'ok'\n"
           + r'"regex:sftp put \S+\.xml /srv/inbox":' + "\n  lines:\n  - 'sftp> put X.xml'\n"
           + "  - 'X.xml" + " " * 40)
    parsed = parse_llm_yaml(raw)
    assert parsed[r"regex:ls -l /srv/outbox/\S+\.xml\.res"] == {"lines": ["ok"]}
    assert r"regex:sftp put \S+\.xml /srv/inbox" in parsed


def test_salvage_keeps_valid_entries_when_one_is_broken():
    from skills.network_simulator_setup.autofix import salvage_yaml_entries
    raw = "'ss':\n  lines: ['Link UP']\n'bad': [unclosed\n'sar 1 10':\n  lines: ['x']\n"
    assert parse_llm_yaml(raw) is None
    assert set(salvage_yaml_entries(raw)) == {"ss", "sar 1 10"}


def test_node_responses_retry_after_unparseable_answer(ciq_json):
    answers = iter(["this is not yaml at all", "'ndsLoadStats':\n  lines: ['ok']\n"])

    async def llm(system: str, user: str) -> str:
        return next(answers)

    rds = next(s for s in build_topology(WORKFLOW, REQUIREMENTS, ciq_json) if s.name == "RDS")
    skill = NetworkSimulatorSetupSkill(llm_fn=llm)
    entries = asyncio.run(skill._generate_node_responses("UC", rds, "", ""))
    assert entries == {"ndsLoadStats": {"lines": ["ok"]}}
