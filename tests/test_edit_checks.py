"""Spec 012 C1–C3: an edited file is checked like its step checks it, in plain language with lines."""
from __future__ import annotations

from shared import edit_checks
from shared.edit_checks import Problem

WORKFLOW = """\
nodes:
  EIR:
    type: remote
    plugin: ssh
    ssh: {host: '${EIR.niamID-1}', user: oamsys}
phases:
  backup:
    steps:
    - node: EIR
      send: DATE=$(date +%Y%m%d); org -c eirWhiteRng >/opt/sdf/processing/predump_white_${DATE}.txt
    - node: EIR
      send: ss
      validation:
        success:
          criteria: {regex: 'State\\s+Recv-Q'}
"""


def test_invalid_yaml_is_reported_at_its_line():
    [p] = edit_checks.check("workflow", "nodes:\n  EIR: {type: remote\nphases: {}\n")
    assert p.line in (2, 3) and p.plain.startswith("This is not valid YAML here")


def test_shell_variable_in_braces_is_found_and_fixed():
    problems = edit_checks.check("workflow", WORKFLOW, {})
    shell = [p for p in problems if p.fix == "unbrace"]
    assert shell and shell[0].line == 10 and "Write $DATE instead" in shell[0].plain
    fixed = edit_checks.apply_fix(WORKFLOW, shell[0])
    assert "predump_white_$DATE.txt" in fixed and "${DATE}" not in fixed


def test_success_check_taken_from_the_session_log():
    p = Problem(line=12, plain="", technical=(
        "Step 'ss' (backup): validation regex /State\\s+Recv-Q/ does not match the real output recorded for this "
        "command. Real output (relevant lines):\n     LOCAL NODE STATUS:\n   DSA 1 NODE 1 is Primary\nWrite the regex"),
        fix="success_from_log", target="ss")
    fixed = edit_checks.apply_fix(WORKFLOW, p)
    assert "LOCAL\\ NODE\\ STATUS:" in fixed and "Recv-Q" not in fixed


def test_problems_point_at_the_step_they_name():
    lines = WORKFLOW.splitlines()
    assert edit_checks.line_of("Step 'ss' (backup): validation regex /x/ does not match the real output", lines) == 12
    assert edit_checks.line_of("Unresolvable placeholder ${DATE}: variable is never registered", lines) == 10
    assert edit_checks.line_of("CIQ column 'X' (S) is filled for every CIQ row but the workflow never uses it", lines) == 0


def test_script_syntax_and_contract():
    wf = "phases:\n  a:\n    steps:\n    - node: LOCAL\n      send: python compare.py --dump d.txt --tac t.xlsx\n"
    [bad] = edit_checks.check("script", "def f(:\n    pass\n", workflow_text=wf, rel="scripts/compare.py")
    assert bad.line == 1 and bad.plain.startswith("Python error")
    code = "import argparse\np = argparse.ArgumentParser()\np.add_argument('--dump')\nprint('ok')\n"
    [missing] = edit_checks.check("script", code, workflow_text=wf, rel="scripts/compare.py")
    assert "--tac" in missing.plain
