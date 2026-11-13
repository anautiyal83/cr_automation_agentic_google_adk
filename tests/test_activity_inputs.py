"""What the activity loads reaches the helper scripts and the simulator (OB-58, OB-59)."""
from __future__ import annotations

import asyncio

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
