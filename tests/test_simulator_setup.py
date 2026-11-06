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
    extract_workflow_commands,
    extract_workflow_nodes,
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

MOCKWIRE_JAR = PROJECT_ROOT / "lib" / "mockwire.jar"
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
    return text


def test_validator_accepts_flat_config(tmp_path):
    (tmp_path / "sar.txt").write_text("Average: all 99.25\n")
    assert validate_mockwire_config(LLM_CONFIG, tmp_path) == []


def test_validator_reports_problems(tmp_path):
    errors = validate_mockwire_config(LLM_CONFIG, tmp_path)
    assert any("missing file 'sar.txt'" in e for e in errors)
    bad_elevation = LLM_CONFIG + "'ctrl+p+e': {lines: []}\n"
    assert any("ctrl+p+e" in e for e in validate_mockwire_config(bad_elevation))
    only_elevation = "config: {prompt: 'x # '}\n'ctrl+p+e': {pushPrompt: 'y # '}\n"
    assert validate_mockwire_config(only_elevation) == ["No UC command responses defined"]
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
        sim = await start_simulator(sim_dir, [inst], str(MOCKWIRE_JAR), ttl_minutes=5)
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
                await start_simulator(sim_dir, [SimulatorInstance(name="RDS", port=busy)], str(MOCKWIRE_JAR),
                                      timeout=20)
        msg = str(exc.value)
        assert "RDS.log" in msg and "MockWire" in msg  # log path + tail included
        assert read_manifest(str(tmp_path))["instances"][0]["status"] == "failed"

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Step 9 overlay prompt_regex + event-log classification
# ---------------------------------------------------------------------------

def test_extend_prompt_regex_adds_only_uncovered_prompts():
    import re
    from skills.network_simulator_setup.overlay import extend_prompt_regex
    regex = extend_prompt_regex(r"rdsggn01:~ #|rdsggn01:~ \$",
                                ["[oamsys@rdsggn01 ~]$ ", "rdsggn01:~ # ", "rdsggn01:~ $ "])
    assert regex.count("|") == 2  # only the login prompt was added
    for prompt in ("[oamsys@rdsggn01 ~]$", "rdsggn01:~ #", "rdsggn01:~ $"):
        assert re.search(regex, prompt)


def test_classify_failures_uses_simulator_events():
    from skills.network_config_test.result_parser import CommandResult, classify_failures
    failed = [CommandResult(command="sar 1 10", node="RDS", success=True, validation_status="failed")]
    gap = [{"source": "unmatched", "instance": "RDS_22200", "input": "ndsLoadStats -v"}]
    broken = [{"source": "unmatched", "instance": "RDS_22200",
               "input": 'ldapsearch -b "imsi=,dc=IMSI,dc=C-NTDB" x'}]

    # a missing simulator response is fixed first, in step 8
    c = classify_failures(failed, gap)
    assert c["classification"] == "simulator" and c["retry_target"] == 8
    assert c["unmatched_commands"] == ["ndsLoadStats -v"]
    # a command sent with an empty value is a workflow problem, like the validation failure
    c = classify_failures(failed, broken)
    assert c["classification"] == "workflow" and c["retry_target"] == 6
    assert any("empty/unresolved" in w for w in c["workflow_issues"])
    # pieces of an if/then/else the shell could not parse (no `;` before else, run 46e5fee1): step 6
    pieces = [{"source": "unmatched", "instance": "MRF_22200", "input": x} for x in (
        "if [[ -s /var/x/f.tar ]]", "then tar -czvf /tmp/b.tar.gz -C /var/x f.tar else echo NONE", "fi")]
    c = classify_failures(failed, pieces)
    assert c["classification"] == "workflow" and c["retry_target"] == 6
    assert sum("broken shell syntax" in w for w in c["workflow_issues"]) == 3
    ok = [{"source": "unmatched", "instance": "MRF_22200", "input": "if [[ -s /x ]]; then fooctl; fi"}]
    assert classify_failures(failed, ok)["classification"] == "simulator"


@needs_mockwire
def test_mockwire_starts_with_relative_run_dir(tmp_path, monkeypatch):
    # The pipeline's run_dir is relative ("./data/runs/<id>") while MockWire runs with
    # cwd=<run_dir>/simulator — paths passed to it must not be resolved twice.
    monkeypatch.chdir(tmp_path)

    async def scenario():
        sim_dir = prepare_simulator_dir("data/runs/rel", str(MOCKWIRE_JAR))
        write_instance_files(sim_dir, "RDS", fixed_config(), {"sar.txt": "x\n"})
        sim = await start_simulator(sim_dir, [SimulatorInstance("RDS", allocate_ports(1)[0])],
                                    str(MOCKWIRE_JAR), ttl_minutes=2)
        try:
            assert sim.is_running
            assert Path(sim.instances[0].events_file).is_absolute()
        finally:
            await sim.stop()

    asyncio.run(scenario())


def test_classify_local_failure_is_workflow_when_events_present():
    from skills.network_config_test.result_parser import CommandResult, classify_failures
    local = [CommandResult(command="python create_xml.py --imsi 1", node="LOCAL", success=False,
                           output="python.exe: can't open file 'create_xml.py': [Errno 2] No such file")]
    assert classify_failures(local, [])["classification"] == "workflow"


def test_real_output_wins_over_invented_responses():
    from skills.network_simulator_setup.skill import merge_responses
    dump = "ldapsearch -b \"imsi=${resolvedImsi},dc=IMSI\" objectclass=*"
    lookup = "ldapsearch -b \"msisdn=${configSequences.0.records.0.data.Test MSISDN},dc=MSISDN\" objectclass=*"
    commands = [{"cmd": lookup}, {"cmd": dump}]
    recorded = {r'regex:^ldapsearch -b "imsi=(?<resolvedImsi>.+?),dc=IMSI" objectclass=\*$': {"lines": ["dn: uid=1"]}}
    synthetic = {r'regex:ldapsearch -b "imsi=\S+,dc=IMSI"': {"lines": ["dn: imsi=invented"]},   # only the dump
                 r'regex:ldapsearch -b "msisdn=\S+': {"lines": ["provisionedIMSI: 1"]},          # the lookup
                 "uptime": {"lines": ["up"]}}
    merged = merge_responses(recorded, synthetic, commands, {dump}, {"Test MSISDN": "9810217635"})
    keys = list(merged)
    assert keys[0].startswith(r'regex:^ldapsearch -b "imsi=')                     # real output first
    assert r'regex:ldapsearch -b "imsi=\S+,dc=IMSI"' not in merged                # invented duplicate dropped
    assert r'regex:ldapsearch -b "msisdn=\S+' in merged and "uptime" in merged


def test_invented_exact_key_never_hides_real_output():
    """DEP-54: MockWire tries exact keys before regex keys. An invented exact answer for an IMSI
    the simulator made up hid the real-output template (made-up format → the workflow's regex,
    written from the real output, failed)."""
    from skills.network_simulator_setup.skill import merge_responses
    dump = "ldapsearch -b \"imsi=${resolvedImsi},dc=IMSI\" objectclass=*"
    lookup = "ldapsearch -b \"msisdn=${configSequences.0.records.0.data.Test MSISDN},dc=MSISDN\" objectclass=*"
    recorded = {r'regex:^ldapsearch -b "imsi=(?<resolvedImsi>.+?),dc=IMSI" objectclass=\*$': {"lines": ["dn: uid=1"]}}
    synthetic = {'ldapsearch -b "msisdn=1234567890,dc=MSISDN" objectclass=*': {"lines": ["provisionedIMSI: 123"]},
                 'ldapsearch -b "imsi=123,dc=IMSI" objectclass=*': {"lines": ["dn: imsi=123,dc=IMSI"]}}
    merged = merge_responses(recorded, synthetic, [{"cmd": lookup}, {"cmd": dump}], {dump},
                             {"Test MSISDN": "1234567890"})
    assert 'ldapsearch -b "imsi=123,dc=IMSI" objectclass=*' not in merged          # real output answers it
    assert 'ldapsearch -b "msisdn=1234567890,dc=MSISDN" objectclass=*' in merged   # nothing real covers it


def test_shell_pipes_in_regex_keys_never_make_a_catch_all():
    """E030 run 2: the step 8 LLM keyed a response `regex:ls -ltrh \\S+/announcements/ | grep "\\S+" || true`;
    as a regex `||` has an empty alternative, so it answered EVERY command of MRF — the backup `tar` got the
    ls output, no archive was created and the transfer failed."""
    import re

    from skills.network_simulator_setup.autofix import safe_regex_key
    key = safe_regex_key(r'regex:ls -ltrh \S+/announcements/ | grep "\S+" || true')
    pattern = re.compile(key[len("regex:"):])
    assert not pattern.search("tar -czvf /tmp/announcement_backup.tar.gz /var/opt/x/")
    assert pattern.search('ls -ltrh /var/opt/announcements/ | grep "DOC_5003" || true')
    assert safe_regex_key(r"regex:(start|stop) swms") == r"regex:(start|stop) swms"   # a real alternation stays
    assert safe_regex_key("regex:.*") is None                                          # matches anything
    assert safe_regex_key("regex:(unclosed") is None                                    # invalid
    assert safe_regex_key("ls -l | wc -l") == "ls -l | wc -l"                          # exact keys untouched
