"""Tests for step 8 (network_simulator_setup) Phase 1 plumbing and the step 9 node fix.

Integration tests start real MockWire processes and are skipped when Java or the
JAR is unavailable.

    python -m pytest tests/test_simulator_setup.py -v
"""
from __future__ import annotations

import asyncio
import json
import shutil
import socket
from pathlib import Path

import pytest

from core.config import PROJECT_ROOT
from shared.state import simulator_processes, stop_simulator
from skills.network_config_test.jar_executor import _execution_targets, _find_json_data
from skills.network_config_test.result_parser import parse_execution_results
from skills.network_simulator_setup.autofix import (
    RUNTIME_COMMANDS,
    dump_yaml,
    ensure_runtime_entries,
    extract_workflow_commands,
    extract_workflow_nodes,
    parse_llm_yaml,
    parse_yaml,
)
from skills.network_simulator_setup.simulator_runner import (
    SimulatorInstance,
    SimulatorStartError,
    allocate_ports,
    prepare_simulator_dir,
    read_manifest,
    start_simulator,
    write_instance_files,
)
from skills.network_simulator_setup.validator import validate_mockwire_config

MOCKWIRE_JAR = PROJECT_ROOT / "lib" / "mockwire" / "mockwire-simulator" / "mockwire.jar"
needs_mockwire = pytest.mark.skipif(
    not MOCKWIRE_JAR.exists() or shutil.which("java") is None,
    reason="MockWire JAR or java not available",
)

WORKFLOW = """
nodes:
  RDS:
    type: remote
    plugin: ssh
    ssh: {host: '${node_row.RDS.NIAM_NAME1}', port: '22'}
  PGW:
    type: remote
    plugin: ssh
  LOCAL:
    type: local
    plugin: local
phases:
  preNodeHealthCheck:
    steps:
    - {node: RDS, send: su - sdfrun}
    - {node: RDS, send: sar 1 10, validation: {description: idle}}
    - {node: PGW, send: /opt/provgw/tomcat/bin/run_as_provgw.sh status}
  activity_configuration:
    steps:
    - {node: LOCAL, send: python create_xml.py}
"""

LLM_CONFIG = """
config:
  prompt: 'rdsggn01:~ # '
auth:
  enabled: true
  users:
  - {username: oamsys, password: '*', prompt: 'rdsggn01:~ # '}
"su - sdfrun":
  lines: []
  pushPrompt: 'sdfrun@rdsggn01:~ $ '
"sar 1 10":
  file: sar.txt
"""


# ---------------------------------------------------------------------------
# Workflow parsing (map-style phases, `send:` key, local nodes)
# ---------------------------------------------------------------------------

def test_extract_workflow_nodes_skips_local():
    assert extract_workflow_nodes(WORKFLOW) == ["PGW", "RDS"]


def test_extract_workflow_commands_reads_send_and_phase_map():
    cmds = extract_workflow_commands(WORKFLOW)
    assert [c["cmd"] for c in cmds] == [
        "su - sdfrun", "sar 1 10",
        "/opt/provgw/tomcat/bin/run_as_provgw.sh status", "python create_xml.py",
    ]
    assert cmds[0]["phase"] == "preNodeHealthCheck"


# ---------------------------------------------------------------------------
# Autofix + validator
# ---------------------------------------------------------------------------

def fixed_config(text: str = LLM_CONFIG) -> str:
    parsed = parse_yaml(text)
    ensure_runtime_entries(parsed)
    return dump_yaml(parsed)


def test_ensure_runtime_entries_and_exit_pop():
    parsed = parse_yaml(fixed_config())
    assert parsed["config"]["terminalMode"] == "PLAIN"
    for key in RUNTIME_COMMANDS:
        assert key in parsed
    assert parsed["exit"] == {"lines": [], "popPrompt": True}


def test_validator_accepts_fixed_flat_config(tmp_path):
    fixed = fixed_config()
    (tmp_path / "sar.txt").write_text("Average: all 99.25\n")
    assert validate_mockwire_config(fixed, tmp_path) == []


def test_validator_reports_problems(tmp_path):
    errors = validate_mockwire_config(LLM_CONFIG, tmp_path)
    assert any("terminalMode" in e for e in errors)
    assert any("stty -echo" in e for e in errors)
    assert any("missing file 'sar.txt'" in e for e in errors)
    assert validate_mockwire_config("a: [unclosed") == ["Invalid YAML syntax"]
    assert validate_mockwire_config("- a\n- b\n") == ["Config root must be a map (got a list)"]


# ---------------------------------------------------------------------------
# Ports
# ---------------------------------------------------------------------------

def test_allocate_ports_skips_busy_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("0.0.0.0", 0))
        busy = s.getsockname()[1]
        s.listen()
        ports = allocate_ports(2, base=busy)
    assert busy not in ports and len(set(ports)) == 2


# ---------------------------------------------------------------------------
# Step 9 node fix (DEP-2)
# ---------------------------------------------------------------------------

def _write_ciq_json(path: Path, cr: str, zones: list[str]) -> None:
    path.write_text(json.dumps({"data": {"schemaVersion": "2", "nodeGroups": [
        {"nodeGroup": z, "crGroup": cr, "nodes": [{"node": z, "RDS": {"niamID-1": f"{z}-rds"}}]}
        for z in zones
    ]}}), encoding="utf-8")


def test_execution_targets_one_per_zone_and_cr(tmp_path):
    json_dir = tmp_path / "json_data"
    json_dir.mkdir()
    _write_ciq_json(json_dir / "UC_CR1.json", "CR1", ["EAST", "NORTH"])
    _write_ciq_json(json_dir / "UC_CR2.json", "CR2", ["EAST"])
    (json_dir / "UC_VALIDATION_REPORT.json").write_text("{}")

    files = _find_json_data(json_dir)
    assert len(files) == 2
    assert [(n, c) for _, n, c in _execution_targets(files)] == [
        ("EAST", "CR1"), ("NORTH", "CR1"), ("EAST", "CR2")]


def test_parse_execution_results_reads_per_cr_reports(tmp_path):
    for cr, zone in (("CR1", "EAST"), ("CR1", "NORTH")):
        d = tmp_path / "exec_output" / cr
        d.mkdir(parents=True, exist_ok=True)
        (d / f"UC_EXECUTION_REPORT_{zone}.json").write_text(json.dumps({"data": {
            "ss": {"phase": "pre", "target": "RDS", "success": True, "output": "Link UP",
                   "validation_status": "success"}}}))
    results = parse_execution_results(str(tmp_path))
    assert len(results) == 2
    assert {r.node for r in results} == {"RDS"}


# ---------------------------------------------------------------------------
# Integration: real MockWire lifecycle
# ---------------------------------------------------------------------------

@needs_mockwire
def test_mockwire_start_manifest_stop(tmp_path):
    async def scenario():
        sim_dir = prepare_simulator_dir(str(tmp_path), str(MOCKWIRE_JAR))
        write_instance_files(sim_dir, "RDS", fixed_config(),
                             {"sar.txt": "Average: all 99.25\n"})
        inst = SimulatorInstance(name="RDS", port=allocate_ports(1)[0], nodes=["RDS"])
        sim = await start_simulator(sim_dir, [inst], ttl_minutes=5)
        try:
            assert sim.is_running
            assert sim.port_for("rds") == inst.port
            manifest = read_manifest(str(tmp_path))
            assert manifest["instances"][0]["status"] == "running"
            # Nothing is written into the shared MockWire folder
            assert not (MOCKWIRE_JAR.parent / "config" / "RDS.yaml").exists()

            simulator_processes["test-run"] = sim
            await stop_simulator("test-run")
            assert not sim.is_running and "test-run" not in simulator_processes
            assert read_manifest(str(tmp_path))["instances"][0]["status"] == "stopped"
        finally:
            await sim.stop()

    asyncio.run(scenario())


@needs_mockwire
def test_mockwire_port_conflict_reports_log_tail(tmp_path):
    async def scenario():
        sim_dir = prepare_simulator_dir(str(tmp_path), str(MOCKWIRE_JAR))
        write_instance_files(sim_dir, "RDS", fixed_config())
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("0.0.0.0", 0))
            s.listen()
            busy = s.getsockname()[1]
            with pytest.raises(SimulatorStartError) as exc:
                await start_simulator(sim_dir, [SimulatorInstance(name="RDS", port=busy)], timeout=20)
        msg = str(exc.value)
        assert "RDS.log" in msg and "MockWire" in msg  # log path + tail included
        assert read_manifest(str(tmp_path))["instances"][0]["status"] == "failed"

    asyncio.run(scenario())
