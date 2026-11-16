"""UC export (step 10 / Run Artifacts) and the simulator guide step 8 writes for it."""
import json
import zipfile

import pytest

from skills.network_simulator_setup.guide import GUIDE_NAME, write_guide
from tools.uc_export import ExportError, build_export, export_path

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


RDS_CONFIG = """config: {prompt: 'rdsggn01:~ # ', hostname: rdsggn01}
auth:
  enabled: true
  users:
  - {username: oamsys, password: netconf-sim, prompt: 'rdsggn01:~ # '}
  - {username: sdfrun, password: netconf-sim, prompt: 'rdshyd201$ '}
'regex:^ldapsearch -x -b "imsi=\\S+,dc=IMSI" objectclass=\\*$': {lines: ['# extended LDIF', 'imsi: 1']}
"""

OVERLAY = """nodes:
  RDS: {type: remote, plugin: ssh, ssh: {host: localhost, port: '22200', user: oamsys, ne_id: BH-R-1-CLI}}
  PGW: {type: remote, plugin: ssh, ssh: {host: localhost, port: '22201', user: oamsys,
        ne_id: BH-NOKIA-CFX-Q-BHPPNCFQD1-172.27.11.147-CLI, elevate_to_root: true}}
  LOCAL: {type: local, plugin: local}
phases:
  activity_configuration:
    name: Main activity
    steps:
    - {node: RDS, send: su - sdfrun}
    - {node: RDS, send: 'ldapsearch -x -b "imsi=${rec.data.Test IMSI},dc=IMSI" objectclass=*'}
    - {node: LOCAL, send: "python create_xml.py --imsi '${rec.data.Test IMSI}'", command_description: Build the order XML}
    - {node: PGW, sftp: {operation: upload, local_path: '${ORDERNO}.xml', remote_path: '/srv/inbox/${ORDERNO}.xml'}}
    - {node: PGW, send: sar 1 10}
    - {node: PGW, send: unknown_tool --x}
"""


def _run(tmp_path, with_deliverables=True):
    rd = tmp_path / "3f5d8990-0000-0000-0000-000000000000"
    sim = rd / "simulator"
    for sub in ("config", "outputs/PGW/responses", "vfs/PGW/tmp", "logs"):
        (sim / sub).mkdir(parents=True)
    (sim / "config" / "PGW.yaml").write_text(PGW_CONFIG, encoding="utf-8")
    (sim / "manifest.json").write_text(json.dumps({"sim_dir": str(sim), "instances": [
        {"name": "RDS", "port": 22200, "nodes": ["RDS"], "pid": 1, "status": "stopped"},
        {"name": "PGW", "port": 22201, "nodes": ["PGW"], "pid": 2, "status": "stopped"}]}), encoding="utf-8")
    (sim / f"{UC}.yaml").write_text(OVERLAY, encoding="utf-8")
    (sim / "config" / "RDS.yaml").write_text(RDS_CONFIG, encoding="utf-8")
    (sim / "vfs/PGW/tmp/left_by_step9.xml").write_text("x", encoding="utf-8")
    (sim / "logs/PGW.events.jsonl").write_text("{}\n", encoding="utf-8")
    (sim / "logs/llm_PGW_raw.txt").write_text("raw", encoding="utf-8")
    (rd / "json_data").mkdir()
    (rd / "json_data" / f"{UC}.json").write_text(json.dumps({"crGroups": [{"crGroup": "CR1", "nodeGroups": [
        {"nodeGroup": "NG1", "nodes": [{"node": "BH"}], "configSequences": [{"records": [
            {"data": {"INPUT_FILE": "SD_input.xlsx", "Test IMSI": "404530000000132"}}]}]}]}]}), encoding="utf-8")
    (rd / "scripts").mkdir()
    (rd / "scripts" / "create_xml.py").write_text("print('ok')\n", encoding="utf-8")
    if with_deliverables:
        for name in (f"{UC}_validation-rules.yaml", f"{UC}_json-output.yaml"):
            (rd / name).write_text("x: 1\n", encoding="utf-8")
        (rd / f"{UC}.yaml").write_text(OVERLAY, encoding="utf-8")
    (rd / "scripts" / "SD_input.xlsx").write_bytes(b"xlsx")
    (rd / "scripts" / "postdump_white_20231108.txt").write_text("left by a step 9 run", encoding="utf-8")
    (rd / "scripts" / "old_helper.py").write_text("print('an earlier step 6')\n", encoding="utf-8")
    jar = tmp_path / "mockwire.jar"
    jar.write_bytes(b"PK jar")
    return rd, jar


def test_guide_says_how_to_start_log_in_and_run_the_workflow(tmp_path):
    rd, _ = _run(tmp_path)
    written = write_guide(rd / "simulator", UC, run_id=rd.name)
    assert {p.name for p in written} == {GUIDE_NAME, "start_simulator.ps1", "start_simulator.sh",
                                         "stop_simulator.ps1", "stop_simulator.sh"}
    guide = (rd / "simulator" / GUIDE_NAME).read_text(encoding="utf-8")
    # the UC's servers: ports, what they simulate, how to start each
    assert "| PGW | 22201 | PGW | pgwggn01 | oamsys / netconf-sim |" in guide
    assert "| RDS | 22200 | RDS | rdsggn01 |" in guide
    assert "--config config/RDS.yaml" in guide and "--config config/PGW.yaml" in guide
    # how they work together: NIAM login, root, su, nested shell, the file trigger, LOCAL steps
    assert "NIAM asks `neId:` and the workflow answers `BH-NOKIA-CFX-Q-BHPPNCFQD1-172.27.11.147-CLI`" in guide
    assert "Ctrl+P, Ctrl+E" in guide and "pgw:\\w # " in guide and "`su - sdfrun`" in guide
    assert "`oc exec -it <…> -- bash` opens a nested shell" in guide
    assert "`/srv/inbox/<X>.xml` makes the node create `/srv/outbox/${X}.xml.res`" in guide
    assert "Steps on node `LOCAL` run on the machine that runs the workflow" in guide
    # the workflow on each server, with what the simulator answers
    assert ("Flow: **RDS** (2 steps) → **automation server** (1 step) → **PGW** (3 steps, file transfer)"
            in guide)
    assert "`su - sdfrun` → switches user → `rdshyd201$ `" in guide
    assert "objectclass=*` → `# extended LDIF` (+1 lines)" in guide
    assert "SFTP upload `${ORDERNO}.xml` ↔ `/srv/inbox/${ORDERNO}.xml`" in guide
    assert "`unknown_tool --x` → **no answer**" in guide
    # by hand: the login and a command with its answer
    assert "ssh -p 22201" in guide and "# neId: BH-NOKIA-CFX-Q-BHPPNCFQD1-172.27.11.147-CLI" in guide
    assert "sar 1 10\n#   → Average:" in guide
    assert "--port 22201" in guide and "--node BH --cr CR1" in guide
    assert f"--json ../json_data/{UC}.json" in guide
    sh = (rd / "simulator" / "start_simulator.sh").read_bytes()
    assert b"\r\n" not in sh and b"start PGW 22201" in sh and b"start RDS 22200" in sh
    assert '@{ Name = "PGW"; Port = 22201 }' in (rd / "simulator" / "start_simulator.ps1").read_text(encoding="utf-8")


def test_no_guide_without_a_started_simulator(tmp_path):
    assert write_guide(tmp_path, UC) == []


def test_export_holds_the_deliverables_and_a_clean_simulator(tmp_path):
    rd, jar = _run(tmp_path)
    out = build_export(rd, UC, jar=jar)
    assert out == rd / "export" / f"{UC}_export.zip"
    z = zipfile.ZipFile(out)
    names = set(z.namelist())
    for name in (f"{UC}_validation-rules.yaml", f"{UC}_json-output.yaml", f"{UC}.yaml", "USER_GUIDE.md",
                 f"json_data/{UC}.json", "scripts/create_xml.py",
                 "simulator/mockwire.jar", "simulator/config/PGW.yaml", f"simulator/{UC}.yaml",
                 "simulator/start_simulator.ps1", "simulator/start_simulator.sh",
                 "simulator/stop_simulator.ps1", "simulator/stop_simulator.sh",
                 "simulator/outputs/PGW/responses/", "simulator/vfs/PGW/", "simulator/vfs/RDS/", "simulator/logs/"):
        assert f"{UC}/{name}" in names, name
    # the run's logs, events, LLM output, manifest and what step 9 left on the nodes stay out
    assert not [n for n in names if n.endswith((".jsonl", "raw.txt", "manifest.json", "left_by_step9.xml"))]
    assert z.getinfo(f"{UC}/simulator/start_simulator.sh").external_attr >> 16 & 0o111
    # one user guide: every file with what it is, then the simulator part (headings one level down, code intact)
    assert not [n for n in names if n.endswith(("README.md", "SIMULATOR_GUIDE.md"))]
    # from scripts/: the helper the workflow calls and the input the CIQ names — not leftovers
    assert not [n for n in names if n.endswith(("postdump_white_20231108.txt", "old_helper.py"))]
    guide = z.read(f"{UC}/USER_GUIDE.md").decode("utf-8")
    for arc in (f"{UC}_validation-rules.yaml", "json_data/", "simulator/mockwire.jar", "simulator/config/PGW.yaml",
                "simulator/vfs/RDS/", "simulator/start_simulator.sh", "scripts/SD_input.xlsx"):
        assert f"| `{arc}" in guide, arc
    assert '`scripts/create_xml.py` | Helper script (step 6) the workflow runs on the automation server: "Build the order XML"' in guide
    assert "server **PGW** (port 22201, simulates PGW)" in guide
    assert "`scripts/SD_input.xlsx` | Input file the CIQ names in column `INPUT_FILE`" in guide
    assert "\n## Network simulator\n" in guide and "\n### Servers\n" in guide and "\n### How the servers work together\n" in guide
    assert "\n# password: netconf-sim\n" in guide  # a comment in a code block stays as it is


def test_export_names_what_the_run_lacks(tmp_path):
    rd, jar = _run(tmp_path, with_deliverables=False)
    with pytest.raises(ExportError, match="validation-rules.yaml"):
        build_export(rd, UC, jar=jar)



def test_a_gateway_run_guide_and_export(tmp_path):
    """MW-13: the NIAM nodes share one gateway server and port, the neId picks the node — the gateway YAML step 8
    writes, the guide's routing / ports, and the export's per-node folders."""
    from types import SimpleNamespace

    import yaml as _yaml

    from skills.network_simulator_setup.gateway import gateway_config, gateway_name
    rd, jar = _run(tmp_path)
    sim = rd / "simulator"
    rds = SimpleNamespace(instance="RDS", ne_ids=["R-1", "R-2"], user="oamsys", password="netconf-sim")
    pgw = SimpleNamespace(instance="PGW", ne_ids=["Q-1"], user="oamsys", password="netconf-sim")
    req = {"category_1_node_activity": {"access_gateway": {"enabled": True, "name": "NIAM"}}}
    assert gateway_name(req) == "niam_gateway"
    gw = _yaml.safe_load(gateway_config([rds, pgw], req, {}))
    assert gw["niam"]["users"][0]["neIds"] == ["R-1", "R-2", "Q-1"]
    assert gw["niam"]["profiles"][1] == {"name": "PGW", "config": "PGW.yaml", "responses": "../outputs/PGW/responses",
                                        "vfsRoot": "../vfs/PGW", "eventsFile": "../logs/PGW.events.jsonl",
                                        "neIds": ["Q-1"]}
    (sim / "config" / "niam_gateway.yaml").write_text(gateway_config([rds, pgw], req, {}), encoding="utf-8")
    (sim / "manifest.json").write_text(json.dumps({"instances": [
        {"name": "niam_gateway", "port": 22200, "nodes": ["RDS", "PGW"],
         "profiles": [{"name": "RDS", "nodes": ["RDS"]}, {"name": "PGW", "nodes": ["PGW"]}]}]}), encoding="utf-8")
    write_guide(sim, UC, run_id=rd.name)
    guide = (sim / GUIDE_NAME).read_text(encoding="utf-8")
    assert "| niam_gateway | 22200 | (gateway) |" in guide and "| PGW | 22200 (via niam_gateway) | PGW |" in guide
    assert "`R-1`, `R-2` → **RDS** (2 neIds)" in guide and "`Q-1` → **PGW** (1 neId)" in guide
    assert "through gateway **niam_gateway**" in guide
    assert "--config config/niam_gateway.yaml" in guide and "--config config/PGW.yaml" not in guide  # one process
    assert "sar 1 10\n#   → Average:" in guide  # the node's own answers
    sh = (sim / "start_simulator.sh").read_text(encoding="utf-8")
    assert "start niam_gateway 22200" in sh and "start PGW" not in sh
    names = set(zipfile.ZipFile(build_export(rd, UC, jar=jar)).namelist())
    assert {f"{UC}/simulator/vfs/RDS/", f"{UC}/simulator/vfs/PGW/", f"{UC}/simulator/config/niam_gateway.yaml"} <= names
    user_guide = zipfile.ZipFile(export_path(rd, UC)).read(f"{UC}/USER_GUIDE.md").decode("utf-8")
    assert "Access gateway **niam_gateway** (port 22200)" in user_guide and "(port 22200 via niam_gateway" in user_guide
