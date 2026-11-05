"""Step 8 / overlay output read by YAML 1.1 parsers (MockWire, cli-automation) — run c89f65c2.

    python -m pytest tests/test_step8_yaml11.py -v
"""
from __future__ import annotations

import re

import yaml  # PyYAML resolves plain scalars like SnakeYAML (YAML 1.1)

from skills.network_simulator_setup.autofix import dump_yaml, parse_yaml


def test_strings_stay_strings_for_a_yaml11_reader():
    lines = ["10:00:00", "no", "on", "~", "0755", "1_000", "12", "x: y", "Tue Oct 26 10:30:00 UTC 2023"]
    text = dump_yaml({"time": {"lines": list(lines)}, "10:00": {"lines": []}, "regex:a": {"exitCode": 1}})
    back = yaml.safe_load(text)
    assert back["time"]["lines"] == lines          # `time` was answered 36000 (base-60 int)
    assert "10:00" in back and back["regex:a"]["exitCode"] == 1
    assert list(back) == ["time", "10:00", "regex:a"]  # MockWire tries keys in file order


def test_step_prompt_regex_gets_the_simulator_prompts():
    from skills.network_simulator_setup.overlay import build_overlay
    from skills.network_simulator_setup.topology import NodeSpec
    wf = """
nodes:
  MRF: {type: remote, plugin: ssh, prompt_regex: 'x', ssh: {host: h, user: u}}
phases:
  rollback:
    steps:
    - node: MRF
      send: cd /var/opt/clips/
      prompt_regex: '[\\r\\n][a-zA-Z0-9._-]+@[a-zA-Z0-9._-]+:.*[#$>] *$'
"""
    spec = NodeSpec(name="MRF", instance="MRF", prompt="[swmsuser@rjjvrmr03 ~]$ ", login_prompt="[swmsuser@rjjvrmr03 ~]$ ",
                    root_prompt="mrf:~ # ")
    step = parse_yaml(build_overlay(wf, [spec], {"MRF": 22200}))["phases"]["rollback"]["steps"][0]
    assert re.search(step["prompt_regex"], "mrf:~ # ")
