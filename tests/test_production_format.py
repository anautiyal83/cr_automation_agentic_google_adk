"""Spec 013: workflows in production format (``structure.apply`` → ``production.apply``).

    python -m pytest tests/test_production_format.py -v
"""
from __future__ import annotations

from skills.network_config_gen import production, structure
from skills.network_config_gen.autofix import dump_yaml, parse_yaml

WORKFLOW = r"""
nodes:
  EIR:
    type: remote
    plugin: ssh
    prompt_regex: '\$ $'
    ssh: {host: '${EIR1.niamID-1}', user: oamsys}
phases:
  preNodeHealthCheck:
    name: Pre-activity health checks
    steps:
    - node: EIR
      send: oc get ns | grep -i ei1
      command_description: Get the EIR namespace
      register:
      - regex: (?<EIRNAMESPACE>\S+)
      validation:
        success:
          criteria:
            expr: EIRNAMESPACE != ''
  activityConfiguration:
    name: Main TAC Loading Activity
    steps:
    - node: EIR
      send: dscli update file /tmp/bulkProvisioning.json -n EIR
      command_description: Update TAC
      when: EIRNAMESPACE != '' && configSequences.0.records.0.data.Test IMSI == ''
  rollbackConfiguration:
    name: Rollback
    steps:
    - node: EIR
      send: dscli update file /tmp/bulkProvisioning.json_backup -n EIR
      command_description: Restore TAC
"""


def _applied() -> dict:
    parsed = parse_yaml(WORKFLOW)
    structure.apply(parsed, {"uc_name": "EIR_1029_TAC_LOADING_EIR"})
    return parsed


def test_file_and_phases_as_production():
    parsed = _applied()
    assert list(parsed)[:5] == ["version", "name", "globals", "nodes", "phases"]
    assert parsed["version"] == 1.2 and parsed["name"] == "eir-1029-tac-loading-eir"
    assert parsed["globals"]["defaults"] == production.DEFAULTS
    phases = parsed["phases"]
    assert list(phases) == ["preNodeHealthCheck", "activity_configuration", "rollback_configuration"]
    assert [p["name"] for p in phases.values()] == ["PRE_NODE_HEALTH_CHECK", "ACTIVITY_CONFIGURATION",
                                                    "ROLLBACK_CONFIGURATION"]
    assert phases["activity_configuration"]["description"] == "Main TAC Loading Activity"
    assert phases["preNodeHealthCheck"]["when"] == '${ROLLBACK_ONLY != "true"}'
    assert phases["rollback_configuration"]["when"] == '${ROLLBACK_REQUIRED == "true" || ROLLBACK_ONLY == "true"}'


def test_expressions_and_validations_as_production():
    parsed = _applied()
    pre = parsed["phases"]["preNodeHealthCheck"]["steps"][0]
    assert pre["validation"]["enabled"] is True and pre["validation"]["description"] == "Get the EIR namespace"
    assert pre["validation"]["success"]["criteria"]["expr"] == '${EIRNAMESPACE != ""}'
    loop = parsed["phases"]["activity_configuration"]["steps"][0]  # the step reads a CIQ column: record loop
    act = loop["steps"][0]
    assert act["when"] == '${EIRNAMESPACE != "" && rec.data.Test IMSI == ""}'
    assert list(act)[:4] == ["node", "command_description", "send", "when"]


def test_apply_is_idempotent_and_flat_form_is_plain():
    parsed = _applied()
    once = dump_yaml(parsed)
    structure.apply(parsed, {"uc_name": "EIR_1029_TAC_LOADING_EIR"})
    assert dump_yaml(parsed) == once
    flat = structure.flattened(parsed)
    text = dump_yaml(flat)
    assert "${ROLLBACK" not in text and "PRECHECK_FAILED" not in text      # contract removed again
    act = flat["phases"]["activity_configuration"]["steps"][0]
    assert act["when"] == "EIRNAMESPACE != '' && configSequences.0.records.0.data.Test IMSI == ''"


def test_wrap_unwrap():
    assert production.wrap("A != '' && B == 'x'") == '${A != "" && B == "x"}'
    assert production.wrap('${A != ""}') == '${A != ""}'
    assert production.wrap("${x} > 0") == "${x} > 0"          # a placeholder inside: left for autofix
    assert production.unwrap('${A != "" && B == "x"}') == "A != '' && B == 'x'"
    assert production.unwrap("${CHANGES_MADE}") == "${CHANGES_MADE}"  # a lone placeholder stays
    assert production.unwrap("${a} > ${b}") == "${a} > ${b}"


def test_local_steps_on_undeclared_local_node():
    """Spec 013 B-R4: production runs automation-server steps on `node: local` and declares no LOCAL node;
    loops carry no node."""
    parsed = parse_yaml(WORKFLOW)
    parsed["phases"]["preNodeHealthCheck"]["steps"].append({"node": "LOCAL", "send": "python check.py --a 'x'"})
    structure.apply(parsed, {"uc_name": "UC"})
    assert "LOCAL" not in parsed["nodes"] and "local" not in parsed["nodes"]
    assert parsed["phases"]["preNodeHealthCheck"]["steps"][-1]["node"] == "local"
    loop = parsed["phases"]["activity_configuration"]["steps"][0]
    assert loop["type"] == "loop" and "node" not in loop
    from skills.network_config_gen.validator import validate_workflow
    assert not [e for e in validate_workflow(dump_yaml(parsed)) if "unknown node" in e]
