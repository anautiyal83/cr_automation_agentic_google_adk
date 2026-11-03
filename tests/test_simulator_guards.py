"""Step 8: keys/responses built from the workflow (S8-26..29)."""
from __future__ import annotations

import json
import re

from skills.network_simulator_setup.guards import eval_test, guarded_responses
from skills.network_simulator_setup.skill import _regex_literal, command_key_regex


def test_command_key_regex_handles_shell_substitution():
    cmd = "tar -czvf /tmp/b_$(date +%Y%m%d%H%M%S).tar.gz ${configSequences.0.records.0.data.DEST}/"
    assert re.fullmatch(command_key_regex(cmd), "tar -czvf /tmp/b_$(date +%Y%m%d%H%M%S).tar.gz /var/x/")


def test_plain_text_regex_literal():
    assert _regex_literal("tar: Removing leading `/\' from member names") == "tar: Removing leading `/' from member names"
    assert _regex_literal(r"total \d+") is None


def test_bash_tests_evaluated():
    assert eval_test("[[ '13.0.3.7' == '13.'* ]]") and not eval_test("[[ '17.0.3.7' == '13.'* ]]")
    assert eval_test("[[ ! '17.0.3.7' == '13.'* && 'a.tar' == *'.tar' ]]")
    assert eval_test("[[ '40453' =~ ^([0-9]+)$ ]]")


def test_guarded_command_answered_per_target(tmp_path):
    doc = {"crGroups": [{"crGroup": "CR1", "nodeGroups": [{"nodeGroup": "G", "nodes": [
        {"node": "MRF1", "MRF": {"VER": "13.0.3.7"}}, {"node": "MRF3", "MRF": {"VER": "17.0.3.7"}}],
        "configSequences": [{"records": [{"data": {"F": "a.tar"}}]}]}]}]}
    jf = tmp_path / "ciq.json"
    jf.write_text(json.dumps(doc), encoding="utf-8")
    cmd = "if [[ '${MRF.VER}' == '13.'* ]]; then cp /tmp/${configSequences.0.records.0.data.F} /d/; else echo SKIPPED_BY_CONDITION; fi"
    out = guarded_responses([{"cmd": cmd}], {"regex:cp \S+ \S+": {"lines": []}}, [str(jf)])
    by_ver = {(r"13\.0\.3\.7" in k): v["lines"] for k, v in out.items()}   # keys are regex-escaped
    assert by_ver == {True: [], False: ["SKIPPED_BY_CONDITION"]}
