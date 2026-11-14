"""What the activity loads reaches the helper scripts and the simulator (OB-58, OB-59)."""
from __future__ import annotations

import asyncio

import yaml

import openpyxl
import pytest

from skills.helper_script_gen.context import script_context, smoke_inputs
from skills.helper_script_gen.skill import smoke_run
from skills.network_simulator_setup import state_changes
from skills.network_simulator_setup.topology import NodeSpec

DUMP = "1,sb,eir,white,99002199000000,99002199999999,21-July-2026,,"
WORKFLOW = {
    "nodes": {"local": {"type": "local"}, "EIR": {"type": "remote"}},
    "phases": {
        "backup": {"steps": [{"node": "EIR", "send": "org -c eirWhiteRng >/opt/p/predump_white_${DATE}.txt"}]},
        "activity_configuration": {"steps": [
            {"node": "EIR", "send": "dscli update file /tmp/bulk.json -n EIR", "command_description": "Load"},
            {"node": "EIR", "send": "org -c eirWhiteRng >/opt/p/postdump_white_${DATE}.txt"},
            {"node": "local", "command_description": "Validate the delta",
             "send": 'python3 check.py --delta_file "${LOCAL_PATH}/${rec.data.DELTA_FILE}" '
                     '--postdump_white "postdump_white_${DATE}.txt"',
             "validation": {"description": "All TACs of DELTA_FILE must be present in the post dump.",
                            "success": {"criteria": {"regex": "DELTA_OK"}, "message": "All present."},
                            "failure": {"message": "Some TACs are missing."}}}]}},
}
REQUIREMENTS = {"doc_extracted": {
    "ciq_sheets": {"INPUT": {"sample_rows": [{"DELTA_FILE": "SD_TAC_Addition_150926.xlsx"}]}},
    "command_samples": [{"command": "org -c eirWhiteRng", "output": DUMP}]}}


@pytest.fixture
def delta(tmp_path):
    path = tmp_path / "1a2b3c4d_SD_TAC_Addition_230926.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "White"
    for row in [("TAC", "Action"), ("01695000", "Create"), ("35000178", "Create"), ("35999999", "Delete")]:
        ws.append(row)
    wb.save(path)
    return str(path)


def test_helper_script_learns_the_steps_check_and_the_formats_it_reads(delta):
    """OB-58: validate_tac_delta.py saw 3000 characters of the workflow, inverted the check and compared whole dump
    records with TACs. The prompt now carries the step's check, the dump's real format (its writer's recorded
    output) and the uploaded workbook's layout."""
    samples = REQUIREMENTS["doc_extracted"]["command_samples"]
    text = script_context(WORKFLOW, "check.py", REQUIREMENTS, [delta], samples)
    assert "All TACs of DELTA_FILE must be present in the post dump." in text
    assert "failure means: Some TACs are missing." in text
    assert "`org -c eirWhiteRng >/opt/p/postdump_white_${DATE}.txt`" in text and DUMP in text
    assert 'sheet "White" (3 data rows)' in text and "keep their leading zeros" in text and "TAC | Action" in text and "01695000 | Create" in text
    inputs = smoke_inputs(WORKFLOW, "check.py", REQUIREMENTS, [delta], samples)
    assert set(inputs) == {"${rec.data.DELTA_FILE}", "postdump_white_${DATE}.txt"}
    assert inputs["postdump_white_${DATE}.txt"].decode().strip() == DUMP


def test_smoke_run_feeds_the_files_the_script_reads():
    """A script that cannot parse the real dump format crashes in the smoke run (an input it reads is staged)."""
    code = ("import argparse\np = argparse.ArgumentParser()\np.add_argument('--postdump_white')\n"
            "a, _ = p.parse_known_args()\nrows = open(a.postdump_white).read().split()\n"
            "print(int(rows[0]))\n")
    problems = smoke_run("check.py", code, ['python3 check.py --postdump_white "postdump_white_${DATE}.txt"'],
                         {}, inputs={"postdump_white_${DATE}.txt": (DUMP + "\n").encode()})
    assert problems and "ValueError" in problems[0]


def _node() -> NodeSpec:
    node = NodeSpec(name="EIR", instance="eir", prompt="$ ")
    node.commands = [{"cmd": "org -c eirWhiteRng >/opt/p/predump_white_${DATE}.txt", "phase": "backup"},
                     {"cmd": "dscli update file /tmp/bulk.json -n EIR", "phase": "activity_configuration",
                      "description": "Load"},
                     {"cmd": "org -c eirWhiteRng >/opt/p/postdump_white_${DATE}.txt",
                      "phase": "activity_configuration"},
                     {"cmd": "dscli update file /tmp/revert.json -n EIR", "phase": "rollback_configuration"}]
    return node


def test_read_after_the_activity_shows_what_it_loaded(delta):
    """OB-59: the post dump answered like the pre dump. The change command marks the state; the read answers with
    one line per loaded row once it is marked (rows expanded here, not by the LLM)."""
    entries = {"org -c eirWhiteRng": {"lines": [DUMP]},
               "regex:^dscli update file /tmp/bulk\\.json\\s": {"lines": ['{"status": 1}']}}
    reads = state_changes.repeated_reads(_node(), entries, {})
    assert [r["bare"] for r in reads] == ["org -c eirWhiteRng"]
    assert [s["cmd"] for s in reads[0]["setters"]] == ["dscli update file /tmp/bulk.json -n EIR"]

    async def llm(system, user):
        assert "01695000 | Create" in user and DUMP in user
        return ("'1':\n  changed_by: ['dscli update file /tmp/bulk.json -n EIR']\n"
                "  add_rows: {file: 'DELTA_FILE', sheet: 'White', where: {'Action': 'Create'}}\n"
                "  line: '1,sb,eir,white,{TAC}000000,{TAC}999999,15-September-2026,,'\n")
    out = asyncio.run(state_changes.apply(llm, "UC", _node(), entries,
                                          {"DELTA_FILE": "SD_TAC_Addition_150926.xlsx"}, [delta]))
    read = out["org -c eirWhiteRng"]["responses"]
    assert read[0]["whenState"] == {"changed:1": "yes"} and read[1] == {"lines": [DUMP]}
    assert read[0]["lines"] == [DUMP, "1,sb,eir,white,01695000000000,01695000999999,15-September-2026,,",
                                "1,sb,eir,white,35000178000000,35000178999999,15-September-2026,,"]
    assert out["regex:^dscli update file /tmp/bulk\\.json\\s"]["setState"] == {"changed:1": "yes"}


def test_unchanged_read_keeps_its_single_answer():
    entries = {"org -c eirWhiteRng": {"lines": [DUMP]},
               "regex:^dscli update file /tmp/bulk\\.json\\s": {"lines": ['{"status": 1}']}}

    async def llm(system, user):
        return "'1':\n  unchanged: true\n"
    assert asyncio.run(state_changes.apply(llm, "UC", _node(), entries, {}, [])) == entries


def test_a_command_also_sent_before_the_first_read_never_marks_the_change():
    """A setter sent outside the span between the reads (or sharing a key with such a command) would change the
    state before the first read."""
    node = _node()
    node.commands.insert(0, {"cmd": "dscli update file /tmp/bulk.json -n EIR", "phase": "preNodeHealthCheck"})
    entries = {"org -c eirWhiteRng": {"lines": [DUMP]},
               "regex:^dscli update file /tmp/bulk\\.json\\s": {"lines": ['{"status": 1}']}}
    assert state_changes.repeated_reads(node, entries, {}) == []


SEQ = "export USER=sdfrun; source /opt/sdf/.bash_profile; org -c eirWhiteRng >/opt/p/predump_white_${DATE}.txt"


def test_a_sequence_writing_a_file_is_answered_part_by_part():
    """OB-60 (run b81305ff): MockWire runs `export …; source …; org -c X >file` part by part; the whole-line key
    without the redirect never matched, so `source` and `org -c X` were unmatched (exit 127, dump step failed).
    Loops and `bash -c '…'` stay whole."""
    from skills.network_simulator_setup.vfs_commands import sequence_parts, source_answers, split_redirect_sequences
    assert sequence_parts(SEQ) == ["export USER=sdfrun", "source /opt/sdf/.bash_profile",
                                   "org -c eirWhiteRng >/opt/p/predump_white_${DATE}.txt"]
    assert sequence_parts("dsClassInfo | grep -i rng >/p/d.txt || true") == ["dsClassInfo | grep -i rng >/p/d.txt",
                                                                            "true"]
    for whole in ("for i in `a; b`; do x >/p/f; done", "bash -c 'a; b' >/p/f", "a; b"):
        assert sequence_parts(whole) == [whole] or ">" not in whole
    parts = split_redirect_sequences([{"cmd": SEQ, "phase": "backup", "validation": {"x": 1}},
                                      {"cmd": "export A=1; uptime", "phase": "backup"}])
    assert [p["cmd"] for p in parts] == ["source /opt/sdf/.bash_profile",
                                         "org -c eirWhiteRng >/opt/p/predump_white_${DATE}.txt",
                                         "export A=1; uptime"]  # no file written: the whole-line key works
    assert parts[1]["validation"] == {} and parts[1]["phase"] == "backup"
    assert source_answers(parts) == {"source /opt/sdf/.bash_profile": {"lines": []}}


def test_dump_parts_of_a_sequence_get_the_recorded_dump_and_the_change():
    """The split parts reach the recorded dump (redirect_contents) and OB-59's read → change → read again;
    `source` is neither a read nor the change."""
    from skills.network_simulator_setup.vfs_commands import redirect_contents, split_redirect_sequences
    node = NodeSpec(name="EIR", instance="eir", prompt="$ ")
    node.commands = split_redirect_sequences([
        {"cmd": SEQ, "phase": "backup"},
        {"cmd": "dscli update file /tmp/bulk.json -n EIR", "phase": "activity_configuration"},
        {"cmd": SEQ.replace("predump", "postdump"), "phase": "activity_configuration"}])
    entries = {**redirect_contents(node.commands, [{"command": "org -c eirWhiteRng", "output": DUMP}]),
               "source /opt/sdf/.bash_profile": {"lines": []},
               "regex:^dscli update file /tmp/bulk\.json\s": {"lines": ['{"status": 1}']}}
    assert entries["org -c eirWhiteRng"] == {"lines": [DUMP]}
    reads = state_changes.repeated_reads(node, entries, {})
    assert [r["bare"] for r in reads] == ["org -c eirWhiteRng"]
    assert [s["cmd"] for s in reads[0]["setters"]] == ["dscli update file /tmp/bulk.json -n EIR"]



def test_registered_text_argument_is_described_as_text():
    """OB-62 (run b81305ff): `--postdump_white "${DIFFWHITE}"` carries the diff output (registered `[\s\S]+`); the
    script opened it as a file. The prompt says what the argument holds."""
    wf = {"phases": {"p": {"steps": [
        {"node": "EIR", "send": "diff predump_white.txt postdump_white.txt",
         "register": [{"regex": r"(?<DIFFWHITE>[\s\S]+)"}]},
        {"node": "local", "send": 'python3 v.py --postdump_white "${DIFFWHITE}"', "command_description": "Check",
         "validation": {"description": "the delta TACs are added"}}]}}}
    wf["phases"]["p"]["steps"].insert(0, {"node": "EIR", "send": "org -c eirWhiteRng >/opt/p/postdump_white.txt"})
    text = script_context(wf, "v.py", {}, [], [{"command": "org -c eirWhiteRng", "output": DUMP}])
    assert "--postdump_white ${DIFFWHITE}: NOT a file name" in text
    assert "`diff predump_white.txt postdump_white.txt`" in text and "many lines" in text
    # what the diffed files hold: the dump's records (the TAC is a field inside them, not the first 8 characters)
    assert "`diff` reads postdump_white.txt, the output of `org -c eirWhiteRng` on EIR" in text and DUMP in text



def test_a_change_command_the_rollback_also_sends_never_marks_the_change(delta):
    """OB-63 (run b81305ff): the rollback sends the same `dscli update file /tmp/bulk.json`; marking the change on it
    showed the loaded TACs in a rollback-only run's post-rollback dump. An activity-only command between the reads
    marks it instead."""
    node = _node()
    node.commands[3] = {"cmd": "dscli update file /tmp/bulk.json -n EIR", "phase": "rollback_configuration"}
    node.commands.insert(1, {"cmd": "oc cp bulk.json pod:/tmp", "phase": "activity_configuration"})
    entries = {"org -c eirWhiteRng": {"lines": [DUMP]},
               "regex:^dscli update file /tmp/bulk\\.json\\s": {"lines": ['{"status": 1}']},
               "oc cp bulk.json pod:/tmp": {"lines": []}}

    async def llm(system, user):
        return ("'1':\n  changed_by: ['dscli update file /tmp/bulk.json -n EIR']\n"
                "  add_lines: ['1,sb,eir,white,01695000000000,01695000999999,15-September-2026,,']\n")
    out = asyncio.run(state_changes.apply(llm, "UC", node, entries, {}, []))
    assert "setState" not in out["regex:^dscli update file /tmp/bulk\\.json\\s"]
    assert out["oc cp bulk.json pod:/tmp"]["setState"] == {"changed:1": "yes"}


def test_the_part_of_a_sequence_that_writes_the_file_gives_its_format():
    """OB-63: the dump writer `export …; source …; org -c eirWhiteRng >file` hid the recorded format (`;`)."""
    wf = {"phases": {"p": {"steps": [
        {"node": "EIR", "send": "export U=s; source /opt/p/.profile; org -c eirWhiteRng >/opt/p/postdump_white.txt"},
        {"node": "local", "send": 'python3 v.py --postdump_white "postdump_white.txt"', "command_description": "C",
         "validation": {"description": "check"}}]}}}
    text = script_context(wf, "v.py", {}, [], [{"command": "org -c eirWhiteRng", "output": DUMP}])
    assert "by `org -c eirWhiteRng >/opt/p/postdump_white.txt`" in text and DUMP in text



def test_a_pod_exec_working_on_files_is_answered_by_its_script():
    """OB-66 (run b81305ff): step 6 wrote the dumps inside the pod, `kubectl exec -it P -n NS -- bash -c '…; org -c X >
    /p/f.txt'`. The key on the line without its redirect never matched (unanswered), and the in-pod diff got a
    canned answer. The script's commands are what MockWire runs; a pod exec reading the node is left alone."""
    from skills.network_simulator_setup.vfs_commands import pod_script, split_redirect_sequences
    dump = ("kubectl exec -it ${EIRPOD} -n ${NS} -- bash -c 'export USER=sdfrun; source /opt/sdf/.bash_profile; "
            "org -c eirWhiteRng > /opt/sdf/processing/predump_white_${DATE}.txt'")
    diff = "kubectl exec -it ${EIRPOD} -n ${NS} -- bash -c 'diff /opt/p/predump_white.txt /opt/p/postdump_white.txt'"
    ls = "oc exec P -- sh -c \"ls -lrth /opt/p/a.txt /opt/p/b.txt\""
    read = "kubectl exec -it ${EIRPOD} -n ${NS} -- bash -c 'export USER=sdfrun; source /opt/sdf/.bash_profile; ss'"
    assert pod_script(read) == "export USER=sdfrun; source /opt/sdf/.bash_profile; ss"
    assert pod_script("kubectl get pods -n x") == ""
    out = split_redirect_sequences([{"cmd": c, "phase": "backup", "validation": {"x": 1}} for c in (dump, diff, ls, read)])
    assert [o["cmd"] for o in out] == [
        "source /opt/sdf/.bash_profile", "org -c eirWhiteRng > /opt/sdf/processing/predump_white_${DATE}.txt",
        "diff /opt/p/predump_white.txt /opt/p/postdump_white.txt", "ls -lrth /opt/p/a.txt /opt/p/b.txt", read]
    assert out[1]["sequence_of"] == dump and out[1]["validation"] == {} and out[-1]["validation"] == {"x": 1}
    # OB-70: `|| true` after the pod exec (grep / diff autofixes) — the script is still opened
    out = split_redirect_sequences([{"cmd": dump.replace("org -c eirWhiteRng", "dsClassInfo | grep -i rng") + " || true"}])
    assert [o["cmd"] for o in out] == ["source /opt/sdf/.bash_profile",
                                       "dsClassInfo | grep -i rng > /opt/sdf/processing/predump_white_${DATE}.txt"]



def test_a_read_changes_after_the_activity_on_another_node():
    """OB-84 (run 61561981): RDS reads the subscriber profile, the order XML is uploaded to PGW, RDS reads it again —
    no command on RDS changes it, so the post read repeated the pre read and the comparison report failed
    (`Mandatory Cap: expected 10, post 1`). The read itself switches to the changed profile after its pre reads."""
    from types import SimpleNamespace
    read = 'ldapsearch -x -b "imsi=${rec.data.Test IMSI}" objectclass=*'
    rds = SimpleNamespace(name="RDS", vfs_commands=set(), commands=[
        {"cmd": read, "phase": "activity_configuration", "order": 3},
        {"cmd": read, "phase": "activity_configuration", "order": 4},
        {"cmd": read, "phase": "activity_configuration", "order": 12}])
    pgw = [{"cmd": "", "phase": "activity_configuration", "order": 8, "transfer": True, "node": "PGW",
            "description": "Upload the order XML"},
           {"cmd": "ls -l /srv/outbox/X.xml.res", "phase": "activity_configuration", "order": 9, "node": "PGW"}]
    entries = {read: {"lines": ["uid: 1", "scscfMandCap: 1", "pdnType: IPv6"]}}
    prompts = []

    async def llm(system, user):
        prompts.append(user)
        return ("'1':\n  changed_by: []\n  replace:\n  - {match: '^scscfMandCap:', with: 'scscfMandCap: 10'}\n"
                "  - {match: '^pdnType:', with: 'pdnType: IPv4'}\n")
    out = asyncio.run(state_changes.apply(llm, "UC", rds, entries, {}, [], others=pgw))
    assert "between the two reads the activity runs on OTHER nodes" in prompts[0] and "PGW: Upload the order XML" in prompts[0]
    chain = out[read]["responses"]
    state = chain[0]["whenState"]
    assert list(state.values()) == ["2"]  # read twice before the upload
    assert chain[0]["lines"] == ["uid: 1", "scscfMandCap: 10", "pdnType: IPv4"]
    assert chain[1]["setState"] == {list(state)[0]: "2"} and chain[-1]["setState"] == {list(state)[0]: "1"}
    assert chain[-1]["lines"] == entries[read]["lines"]
    # nothing on another node between the reads: no change
    assert asyncio.run(state_changes.apply(llm, "UC", rds, entries, {}, [], others=[])) == entries
    # a lookup on RDS between the reads does not take the change over (run 61561981: a skipped `if [[ IFC … ]]`)
    rds.commands.insert(2, {"cmd": "org -c hssGlobalFilterCriteria", "phase": "activity_configuration", "order": 5})
    entries2 = {**entries, "org -c hssGlobalFilterCriteria": {"lines": ["x"]}}
    out = asyncio.run(state_changes.apply(llm, "UC", rds, entries2, {}, [], others=pgw))
    assert "setState" not in out["org -c hssGlobalFilterCriteria"] and out[read]["responses"][0]["lines"][1] == "scscfMandCap: 10"
    # a repository server's transfers are no change
    repo = [{**o, "node": "repo_server"} for o in pgw]
    assert asyncio.run(state_changes.apply(llm, "UC", rds, entries, {}, [], others=repo)) == entries



def test_post_activity_ciq_values_are_filled_per_test_case(tmp_path):
    """OB-87 (run 61561981): one after-profile for every case, but EAST expects Mandatory Cap 10 and NORTH keeps it
    (`UNCHANGED`); the LLM had written CIQ cells as values. The line holds the column; each case fills its record."""
    import json
    from skills.network_config_test.scenarios import case_record, fill_ciq
    from skills.network_simulator_setup.state_changes import expand
    after = expand({"replace": [{"match": "^scscfMandCap:", "with": "scscfMandCap: {ciq:Mandatory Cap}"},
                                {"match": "^pdnType:", "with": "pdnType: {ciq:PDN Type}"}]},
                   ["uid: 1", "scscfMandCap: 1", "pdnType: IPv6"], {})
    cfg = yaml.safe_dump({"regex:^ldapsearch": {"responses": [{"whenState": {"s": "1"}, "lines": after},
                                                             {"lines": ["uid: 1", "scscfMandCap: 1"]}]}})
    ciq = tmp_path / "ciq.json"
    ciq.write_text(json.dumps({"crGroups": [{"crGroup": "CR1", "nodeGroups": [
        {"nodeGroup": "EAST", "nodes": [{"node": "EAST"}], "configSequences": [{"records": [
            {"data": {"Mandatory Cap": "10", "PDN Type": "IPv4"}}]}]},
        {"nodeGroup": "NORTH", "nodes": [{"node": "NORTH"}], "configSequences": [{"records": [
            {"data": {"Mandatory Cap": "UNCHANGED", "PDN Type": ""}}]}]}]}]}), encoding="utf-8")
    rules = {"unchanged": "keep", "blank": "delete"}  # the UC's confirmed cell rules (shared/ciq_cells)
    east = yaml.safe_load(fill_ciq(cfg, case_record((str(ciq), "EAST", "CR1")), rules))
    north = yaml.safe_load(fill_ciq(cfg, case_record((str(ciq), "NORTH", "CR1")), rules))
    # without a blank rule, a blank cell keeps the line
    kept = yaml.safe_load(fill_ciq(cfg, case_record((str(ciq), "NORTH", "CR1"))))
    assert kept["regex:^ldapsearch"]["responses"][0]["lines"] == ["uid: 1", "scscfMandCap: 1", "pdnType: IPv6"]
    assert east["regex:^ldapsearch"]["responses"][0]["lines"] == ["uid: 1", "scscfMandCap: 10", "pdnType: IPv4"]
    # UNCHANGED keeps the line, a blank cell deletes the attribute (the UC's answer: "empty tag … to delete")
    assert north["regex:^ldapsearch"]["responses"][0]["lines"] == ["uid: 1", "scscfMandCap: 1"]
    assert east["regex:^ldapsearch"]["responses"][1]["lines"] == ["uid: 1", "scscfMandCap: 1"]  # before: untouched



def test_a_column_the_llm_left_out_gets_its_attribute_line():
    """OB-95 (run 6570694d): the after-profile had no `pdnType`, so the blank Packet Data Network Type never deleted it."""
    from skills.network_simulator_setup.state_changes import CIQ_ELSE, attribute_for, complete_columns
    attrs = ["dn", "uid", "profileName", "globalFilterIds", "scscfMandCap", "scscfOptCap", "netwListId",
             "pdnAccPointName", "pdnType"]
    assert attribute_for("Packet Data Network Type", attrs) == "pdnType"
    assert attribute_for("Mandatory Cap", attrs) == "scscfMandCap"
    assert attribute_for("Optional Cap", attrs) == "scscfOptCap"
    assert attribute_for("Test IMSI", attrs) is None and attribute_for("IFC", attrs) is None
    assert attribute_for("refinmocName", attrs + ["refinmocName", "refinmtcName"]) == "refinmocName"
    before = ["uid: 1", "scscfMandCap: 1", "pdnType: IPv4"]
    after = ["uid: 1", "scscfMandCap: {ciq:Mandatory Cap}" + CIQ_ELSE.format("scscfMandCap: 1"), "pdnType: IPv4"]
    done = complete_columns(after, before, ["Mandatory Cap", "Packet Data Network Type", "Test IMSI"],
                            'ldapsearch -b "imsi=${rec.data.Test IMSI}"')
    assert done[2] == "pdnType: {ciq:Packet Data Network Type}" + CIQ_ELSE.format("pdnType: IPv4")
    assert done[:2] == after[:2]



def test_a_read_called_unchanged_still_gets_the_columns_the_activity_sends():
    """OB-95: the full profile (two `pdnType` lines, one per PDN context) was answered "unchanged" by the LLM."""
    from types import SimpleNamespace
    read = 'ldapsearch -b "imsi=${rec.data.Test IMSI}" objectclass=*'
    rds = SimpleNamespace(name="RDS", vfs_commands=set(), commands=[
        {"cmd": read, "phase": "activity_configuration", "order": 3},
        {"cmd": read, "phase": "activity_configuration", "order": 12}])
    others = [{"cmd": "", "phase": "activity_configuration", "order": 8, "transfer": True, "node": "PGW"},
              {"cmd": "python create_xml.py --pdn '${rec.data.Packet Data Network Type}'",
               "phase": "activity_configuration", "order": 6, "node": "LOCAL"}]
    entries = {read: {"lines": ["uid: 1", "pdnType: IPv4v6", "apn: a", "pdnType: IPv4v6"]}}

    async def llm(system, user):
        return "'1':\n  unchanged: true\n"
    out = asyncio.run(state_changes.apply(llm, "UC", rds, entries, {}, [], others=others[:1], workflow_commands=others))
    after = out[read]["responses"][0]["lines"]
    assert [l.split("{ciq-else")[0] for l in after] == ["uid: 1", "pdnType: {ciq:Packet Data Network Type}", "apn: a",
                                                        "pdnType: {ciq:Packet Data Network Type}"]


def test_a_value_registered_per_record_reaches_the_later_record_loop_per_record():
    """OB-96 (run 6570694d): the post reads and the report read the IMSI / pre-read registered in the first record
    loop outside it — the last record's only. They loop over the records too and read each record's value back."""
    from skills.network_config_gen import structure
    wf = {"nodes": {"RDS": {"type": "ssh"}, "PGW": {"type": "ssh"}, "local": {"type": "local"}}, "phases": {
        "activity_configuration": {"steps": [
            {"node": "RDS", "send": "echo ${configSequences.0.records.0.data.Test IMSI}",
             "register": [{"regex": r"(?<imsi>\d+)"}]},
            {"node": "RDS", "send": "ldapsearch imsi=${imsi}", "register": [{"regex": r"(?<pre>[\s\S]+)"}]},
            {"node": "local", "send": "python create_xml.py --cap '${configSequences.0.records.0.data.Cap}'"},
            {"node": "PGW", "send": "ls -l /srv/inbox"},
            {"node": "RDS", "send": "ldapsearch imsi=${imsi}", "register": [{"regex": r"(?<post>[\s\S]+)"}]},
            {"node": "local", "send": "python report.py --pre '${pre}' --post '${post}'"},
            {"node": "PGW", "send": "df -h"}]}}}
    structure.loop_record_steps(wf)
    assert structure.keep_record_values(wf) == 2
    steps = wf["phases"]["activity_configuration"]["steps"]
    assert [s.get("type") for s in steps] == ["loop", None, "loop", None]
    assert [s.get("id", s.get("send")) for s in steps[0]["steps"]] == [
        "echo ${rec.data.Test IMSI}", "nc_keep_imsi", "ldapsearch imsi=${imsi}", "nc_keep_pre",
        "python create_xml.py --cap '${rec.data.Cap}'"]
    assert [s.get("id", s.get("send")) for s in steps[2]["steps"]] == [
        "nc_load_imsi", "ldapsearch imsi=${imsi}", "nc_load_pre", "python report.py --pre '${pre}' --post '${post}'"]
    key = "$(printf '%s' '${rec.data.Cap}|${rec.data.Test IMSI}' | sha1sum | cut -c1-12)"
    assert steps[2]["steps"][0]["send"] == f"cat nc_records/imsi_{key}.txt 2>/dev/null || true"
    before = structure.flattened(wf)
    structure.loop_record_steps(before)
    structure.keep_record_values(before)
    assert before == wf  # idempotent


def test_a_read_chain_over_record_loops_gives_each_record_its_own_reads():
    from types import SimpleNamespace
    from skills.network_simulator_setup.autofix import extract_workflow_commands
    from skills.network_config_test.scenarios import fill_ciq
    loop = {"type": "loop", "for_each": "${configSequences.0.records}", "item_var": "rec"}
    wf = yaml.safe_dump({"phases": {"activity_configuration": {"steps": [
        {**loop, "id": "nc_records_a_1", "steps": [
            {"node": "RDS", "send": "ldapsearch x"},
            {"node": "local", "send": "python create_xml.py --pdn '${rec.data.PDN Type}'"}]},
        {"node": "PGW", "sftp": {"operation": "upload", "local_path": "a.xml", "remote_path": "/in/"}},
        {**loop, "id": "nc_records_a_2", "steps": [{"node": "RDS", "send": "ldapsearch x"}]}]}}})
    cmds = extract_workflow_commands(wf)
    assert [c["loop"] for c in cmds] == ["nc_records_a_1", "nc_records_a_1", "", "nc_records_a_2"]
    rds = SimpleNamespace(name="RDS", vfs_commands=set(), commands=[c for c in cmds if c["node"] == "RDS"])
    others = [c for c in cmds if c["node"] != "RDS"]
    entries = {"ldapsearch x": {"lines": ["uid: 1", "pdnType: IPv4"]}}

    async def llm(system, user):
        return "'1':\n  unchanged: true\n"
    out = asyncio.run(state_changes.apply(llm, "UC", rds, entries, {}, [], others=others, workflow_commands=others))
    chain = out["ldapsearch x"]["responses"]
    assert {k for r in chain for k in (r.get("whenState") or {})} == {"changed:read1:records:L1.L1"}
    rules = {"unchanged": "keep", "blank": "delete"}
    cfg = yaml.safe_load(fill_ciq(yaml.safe_dump(out), [{"PDN Type": None}, {"PDN Type": "ipv6"}], rules))
    sent = {int(next(iter(r["whenState"].values()))) if r.get("whenState") else 0: r["lines"]
            for r in cfg["ldapsearch x"]["responses"]}
    # pre reads of both records, then each record's own post read
    assert sent == {0: ["uid: 1", "pdnType: IPv4"], 1: ["uid: 1", "pdnType: IPv4"],
                    2: ["uid: 1"], 3: ["uid: 1", "pdnType: ipv6"]}


def test_documents_comparing_a_column_with_an_attribute_map_it():
    """OB-96: `IF (<$IFC> == globalFilterIds)` in the MOP links IFC to globalFilterIds — no name does."""
    from skills.network_simulator_setup.state_changes import CIQ_ELSE, column_attributes, complete_columns
    req = {"generation_hints": ["IF (<$IFC> == globalFilterIds)\n ->Success", "IF (<$Optional Cap> == scscfOptCap)"]}
    mapping = column_attributes(req)
    assert mapping == {"ifc": "globalFilterIds", "optionalcap": "scscfOptCap"}
    done = complete_columns(["globalFilterIds: 100"], ["globalFilterIds: 100"], ["IFC"], "ldapsearch x", mapping)
    assert done == ["globalFilterIds: {ciq:IFC}" + CIQ_ELSE.format("globalFilterIds: 100")]


def test_a_number_registered_from_a_block_ends_its_line():
    r"""OB-97: the prompt after a multi-line block is in the output — `(?<x>\d+)` took `rdsggn01`'s 01."""
    from skills.network_config_gen.autofix import block_numbers_end_their_line
    wf = {"phases": {"a": {"steps": [
        {"send": "if [[ x ]]; then\n  a\nelse\n  echo 1\nfi", "register": [{"regex": r"(?<imsi>\d+)"}]},
        {"send": "echo 1", "register": [{"regex": r"(?<y>\d+)"}]}]}}}
    assert block_numbers_end_their_line(wf) == 1 and block_numbers_end_their_line(wf) == 0
    assert wf["phases"]["a"]["steps"][0]["register"][0]["regex"] == r"(?<imsi>\d+)(?=\s*$)"
    assert wf["phases"]["a"]["steps"][1]["register"][0]["regex"] == r"(?<y>\d+)"


def test_a_file_every_record_pass_writes_is_one_file_for_all():
    """OB-96: create_xml.py ran once per record with the same --output_file and overwrote it — only the last record
    reached the order. The workflow starts the file before the loop; the script contract says each pass adds."""
    from skills.helper_script_gen.context import script_context, shared_outputs
    from skills.network_config_gen import structure
    send = "python create_xml.py --cap '${rec.data.Cap}' --output_file '${ORDER_NO}.xml'"
    assert shared_outputs(send) == ["'${ORDER_NO}.xml'"] or shared_outputs(send) == ["${ORDER_NO}.xml"]
    assert shared_outputs("python r.py --output_file 'r_${rec.data.Cap}.html' --cap '${rec.data.Cap}'") == []
    wf = {"nodes": {"local": {"type": "local"}}, "phases": {"a": {"steps": [
        {"node": "local", "send": send.replace("${rec.data.", "${configSequences.0.records.0.data.")}]}}}
    structure.loop_record_steps(wf)
    assert structure.fresh_shared_outputs(wf) == 1
    fresh = wf["phases"]["a"]["steps"][0]
    assert fresh["id"] == "nc_fresh_1" and fresh["send"].startswith("rm -f ") and "ORDER_NO" in fresh["send"]
    text = script_context(wf, "create_xml.py", {})
    assert "ONE file for all the records" in text


_OVERWRITES = '''import argparse
p = argparse.ArgumentParser(); p.add_argument("--imsi"); p.add_argument("--cap"); p.add_argument("--output_file")
a = p.parse_args()
open(a.output_file, "w").write("<batch>\\n<req imsi='%s' cap='%s'/>\\n</batch>\\n" % (a.imsi, a.cap))
print("XML file generated successfully")
'''

_ADDS = '''import argparse, os
p = argparse.ArgumentParser(); p.add_argument("--imsi"); p.add_argument("--cap"); p.add_argument("--output_file")
a = p.parse_args()
parts = []
if os.path.exists(a.output_file):
    parts = [l for l in open(a.output_file).read().splitlines() if l.startswith("<req")]
parts.append("<req imsi='%s' cap='%s'/>" % (a.imsi, a.cap))
open(a.output_file, "w").write("<batch>\\n" + "\\n".join(parts) + "\\n</batch>\\n")
print("XML file generated successfully")
'''


_KEEPS_ITS_OWN_KEY = '''import argparse, os
p = argparse.ArgumentParser(); p.add_argument("--imsi"); p.add_argument("--cap"); p.add_argument("--output_file")
a = p.parse_args()
parts = []
if os.path.exists(a.output_file) and ("imsi='%s'" % a.imsi) in open(a.output_file).read():
    parts = [l for l in open(a.output_file).read().splitlines() if l.startswith("<req")]
parts.append("<req imsi='%s' cap='%s'/>" % (a.imsi, a.cap))
open(a.output_file, "w").write("<batch>\\n" + "\\n".join(parts) + "\\n</batch>\\n")
print("XML file generated successfully")
'''


def test_a_registered_value_differs_between_the_record_passes():
    """OB-98: create_xml.py kept the earlier part only for the same IMSI; ${resolvedImsi} (registered, no test value)
    got one default in both passes, so the check passed and NORTH's order held its last record only."""
    from skills.helper_script_gen.skill import check_accumulates
    cmd = ["python create_xml.py --imsi '${resolvedImsi}' --cap '${configSequences.0.records.0.data.Cap}' "
           "--output_file '${ORDER_NO}.xml'"]
    values = {"configSequences.0.records.0.data.Cap": "10", "ORDER_NO": "T1"}
    problems = check_accumulates("create_xml.py", _KEEPS_ITS_OWN_KEY, cmd, values)
    assert problems and "ADD this record's part" in problems[0]
    assert check_accumulates("create_xml.py", _ADDS, cmd, values) == []


def test_a_script_every_record_pass_runs_must_add_to_its_file():
    """OB-96: create_xml.py rewrote ${ORDER_NO}.xml in every record's pass — only the last record reached the order."""
    from skills.helper_script_gen.skill import check_accumulates
    cmd = ["python create_xml.py --imsi '${resolvedImsi}' --cap '${configSequences.0.records.0.data.Cap}' "
           "--output_file '${ORDER_NO}.xml'"]
    values = {"resolvedImsi": "404530000000132", "configSequences.0.records.0.data.Cap": "10", "ORDER_NO": "T1"}
    problems = check_accumulates("create_xml.py", _OVERWRITES, cmd, values)
    assert problems and "ADD this record's part" in problems[0]
    assert check_accumulates("create_xml.py", _ADDS, cmd, values) == []
    # a script whose file is the record's own is not asked to
    own = [cmd[0].replace("'${ORDER_NO}.xml'", "'${configSequences.0.records.0.data.Cap}.xml'")]
    assert check_accumulates("create_xml.py", _OVERWRITES, own, values) == []
