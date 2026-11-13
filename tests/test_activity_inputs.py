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
    assert 'sheet "White" (3 data rows)' in text and "TAC | Action" in text and "01695000 | Create" in text
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
