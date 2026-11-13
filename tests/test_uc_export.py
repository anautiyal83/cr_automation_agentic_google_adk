"""UC export (step 10 / Run Artifacts) and the simulator guide step 8 writes for it."""
import json
import zipfile

import pytest

from skills.network_simulator_setup.guide import GUIDE_NAME, write_guide
from tools.uc_export import ExportError, build_export

UC = "PGW_RDS_1058_IFC_CAPABILITY_MODIFICATION"

PGW_CONFIG = """config: {prompt: '[oamsys@pgwggn01 \\W]$ ', hostname: pgwggn01}
auth:
  enabled: true
  users:
  - {username: oamsys, password: netconf-sim, prompt: '[oamsys@pgwggn01 \\W]$ '}
  - {username: provgw, password: netconf-sim, prompt: '[provgw@pgwggn01 ~]$ '}
ctrl+p+e: {pushPrompt: 'pgw:\\w # '}
niam:
  enabled: true
  users: [{username: oamsys, password: netconf-sim, neIds: [BH-NOKIA-CFX-Q-BHPPNCFQD1-172.27.11.147-CLI]}]
vfs:
  dirs: [/srv/sftp/smc-sftp-DEFAULT/inbox, /tmp]
  triggers: [{onCreate: '/srv/inbox/(?<X>[^/]+)\\.xml', create: '/srv/outbox/${X}.xml.res', lines: [ok]}]
sar 1 10: {lines: ['Average:        all      0.15      0.00      0.11      0.00      0.00     99.74']}
'regex:^oc exec -it \\S+ -- bash$': {pushPrompt: '[root@pod ~]# '}
"""


def _run(tmp_path, with_deliverables=True):
    rd = tmp_path / "3f5d8990-0000-0000-0000-000000000000"
    sim = rd / "simulator"
    for sub in ("config", "outputs/PGW/responses", "vfs/PGW/tmp", "logs"):
        (sim / sub).mkdir(parents=True)
    (sim / "config" / "PGW.yaml").write_text(PGW_CONFIG, encoding="utf-8")
    (sim / "manifest.json").write_text(json.dumps({"sim_dir": str(sim), "instances": [
        {"name": "PGW", "port": 22201, "nodes": ["PGW"], "pid": 1, "status": "stopped"}]}), encoding="utf-8")
    (sim / f"{UC}.yaml").write_text("nodes: {PGW: {ssh: {host: localhost, port: '22201'}}}\n", encoding="utf-8")
    (sim / "vfs/PGW/tmp/left_by_step9.xml").write_text("x", encoding="utf-8")
    (sim / "logs/PGW.events.jsonl").write_text("{}\n", encoding="utf-8")
    (sim / "logs/llm_PGW_raw.txt").write_text("raw", encoding="utf-8")
    (rd / "json_data").mkdir()
    (rd / "json_data" / f"{UC}.json").write_text(json.dumps({"crGroups": [{"crGroup": "CR1", "nodeGroups": [
        {"nodeGroup": "NG1", "nodes": [{"node": "BH"}]}]}]}), encoding="utf-8")
    (rd / "scripts").mkdir()
    (rd / "scripts" / "create_xml.py").write_text("print('ok')\n", encoding="utf-8")
    if with_deliverables:
        for name in (f"{UC}_validation-rules.yaml", f"{UC}_json-output.yaml", f"{UC}.yaml"):
            (rd / name).write_text("x: 1\n", encoding="utf-8")
    jar = tmp_path / "mockwire.jar"
    jar.write_bytes(b"PK jar")
    return rd, jar


def test_guide_says_how_to_start_log_in_and_run_the_workflow(tmp_path):
    rd, _ = _run(tmp_path)
    written = write_guide(rd / "simulator", UC, run_id=rd.name)
    assert {p.name for p in written} == {GUIDE_NAME, "start_simulator.ps1", "start_simulator.sh",
                                         "stop_simulator.ps1", "stop_simulator.sh"}
    guide = (rd / "simulator" / GUIDE_NAME).read_text(encoding="utf-8")
    assert "| PGW | 22201 | PGW | oamsys | netconf-sim |" in guide
    assert "ssh -p 22201" in guide and "BH-NOKIA-CFX-Q-BHPPNCFQD1-172.27.11.147-CLI" in guide
    assert "Ctrl+P" in guide and "pgw:\\w # " in guide and "`su - provgw`" in guide
    assert "`sar 1 10`" in guide and "^oc exec -it \\S+ -- bash$" in guide  # a command / a nested shell
    assert "--port 22201" in guide and "--node BH --cr CR1" in guide
    assert f"--json ../json_data/{UC}.json" in guide
    sh = (rd / "simulator" / "start_simulator.sh").read_bytes()
    assert b"\r\n" not in sh and b"start PGW 22201" in sh
    assert '@{ Name = "PGW"; Port = 22201 }' in (rd / "simulator" / "start_simulator.ps1").read_text(encoding="utf-8")


def test_no_guide_without_a_started_simulator(tmp_path):
    assert write_guide(tmp_path, UC) == []


def test_export_holds_the_deliverables_and_a_clean_simulator(tmp_path):
    rd, jar = _run(tmp_path)
    out = build_export(rd, UC, jar=jar)
    assert out == rd / "export" / f"{UC}_export.zip"
    z = zipfile.ZipFile(out)
    names = set(z.namelist())
    for name in (f"{UC}_validation-rules.yaml", f"{UC}_json-output.yaml", f"{UC}.yaml", "README.md",
                 f"json_data/{UC}.json", "scripts/create_xml.py",
                 "simulator/mockwire.jar", "simulator/config/PGW.yaml", f"simulator/{UC}.yaml",
                 "simulator/SIMULATOR_GUIDE.md", "simulator/start_simulator.ps1", "simulator/start_simulator.sh",
                 "simulator/stop_simulator.ps1", "simulator/stop_simulator.sh",
                 "simulator/outputs/PGW/responses/", "simulator/vfs/PGW/", "simulator/logs/"):
        assert f"{UC}/{name}" in names, name
    # the run's logs, events, LLM output, manifest and what step 9 left on the nodes stay out
    assert not [n for n in names if n.endswith((".jsonl", "raw.txt", "manifest.json", "left_by_step9.xml"))]
    assert z.getinfo(f"{UC}/simulator/start_simulator.sh").external_attr >> 16 & 0o111


def test_export_names_what_the_run_lacks(tmp_path):
    rd, jar = _run(tmp_path, with_deliverables=False)
    with pytest.raises(ExportError, match="validation-rules.yaml"):
        build_export(rd, UC, jar=jar)
