"""Per-UC baseline (user, 2026-11-13): a run whose step 9 passed is what the next runs of its UC start from."""
import asyncio
import json

import yaml

from skills.network_config_gen.skill import NetworkConfigGenSkill, SkillInput
from storage import uc_baseline

UC = "EIR_1029_TAC_LOADING_EIR"
WORKFLOW = yaml.safe_dump({
    "nodes": {"N1": {"type": "remote", "plugin": "ssh", "prompt_regex": "# ", "ssh": {"host": "h", "user": "u"}}},
    "phases": {"activity_configuration": {"steps": [{
        "node": "N1", "send": "uptime", "command_description": "Uptime",
        "validation": {"success": {"criteria": {"regex": "load average"}}}}]}}})


def _run(tmp_path, name="b81305ff-0000"):
    rd = tmp_path / "runs" / name
    (rd / "scripts").mkdir(parents=True)
    (rd / f"{UC}.yaml").write_text(WORKFLOW, encoding="utf-8")
    (rd / "scripts" / "validate_tac_diff.py").write_text("print('ok')\n", encoding="utf-8")
    (rd / "scripts" / "SD_TAC.xlsx").write_bytes(b"staged input")  # a test input, not a script
    sim = rd / "simulator"
    (sim / "config").mkdir(parents=True)
    (sim / "outputs" / "niam_eir_server" / "responses").mkdir(parents=True)
    (sim / "outputs" / "niam_eir_server" / "responses" / "dump.txt").write_text("1,white\n", encoding="utf-8")
    (sim / "config" / "niam_eir_server.yaml").write_text("config: {prompt: '# '}\n", encoding="utf-8")
    (sim / "synthetic_keys.json").write_text(json.dumps({"niam_eir_server": ["x"]}), encoding="utf-8")
    (rd / "json_data").mkdir()
    (rd / "json_data" / f"{UC}.json").write_text('{"crGroups": []}', encoding="utf-8")
    return rd


def test_save_load_and_discard(tmp_path):
    rd = _run(tmp_path)
    base = tmp_path / "knowledge"
    req = {"developer_notes": "n", "run_options": {"backup": True}}
    assert uc_baseline.save(rd, UC, req, base=base) == base / UC / "baseline"
    b = uc_baseline.load(UC, base=base)
    assert b.run_id == "b81305ff-0000" and b.workflow_yaml == WORKFLOW
    assert list(b.scripts) == ["validate_tac_diff.py"]  # staged inputs are not part of it
    assert b.simulator_configs == {"niam_eir_server": "config: {prompt: '# '}\n"}
    assert b.synthetic_keys == {"niam_eir_server": ["x"]}
    assert b.requirements_fp == uc_baseline.requirements_fingerprint(req)
    assert b.requirements_fp != uc_baseline.requirements_fingerprint({**req, "developer_notes": "changed"})
    assert b.simulator_fp == uc_baseline.simulator_fingerprint(WORKFLOW, uc_baseline.run_ciq_json_files(rd))
    assert b.simulator_fp != uc_baseline.simulator_fingerprint(WORKFLOW + "# x\n", uc_baseline.run_ciq_json_files(rd))
    assert [x["uc_name"] for x in uc_baseline.list_baselines(base)] == [UC]
    # a newer passing run replaces it
    rd2 = _run(tmp_path, "c0ffee00-1111")
    uc_baseline.save(rd2, UC, req, base=base)
    assert uc_baseline.load(UC, base=base).run_id == "c0ffee00-1111"
    assert uc_baseline.discard(UC, base=base) and uc_baseline.load(UC, base=base) is None


def test_step6_reuses_the_proven_workflow_without_the_llm():
    async def llm(system, user):
        raise AssertionError("the LLM must not be called")
    out = asyncio.run(NetworkConfigGenSkill(llm_fn=llm).generate(SkillInput(
        uc_name=UC, generation_hints={"step_6": "x"},
        baseline_yaml=WORKFLOW, baseline_run="b81305ff-0000", baseline_same_inputs=True)))
    assert out.status == "passed" and "load average" in out.workflow_yaml
    assert out.origin.startswith("Reused the workflow proven in run b81305ff")


def test_step6_adapts_the_proven_workflow_when_the_requirements_changed():
    prompts = []

    async def llm(system, user):
        prompts.append(user)
        return WORKFLOW
    out = asyncio.run(NetworkConfigGenSkill(llm_fn=llm).generate(SkillInput(
        uc_name=UC, generation_hints={"step_6": "x"},
        baseline_yaml=WORKFLOW, baseline_run="b81305ff-0000", baseline_same_inputs=False)))
    assert "PROVEN WORKFLOW FOR THIS UC (passed QA-Execution in run b81305ff-0000)" in prompts[0]
    assert "uptime" in prompts[0].split("PROVEN WORKFLOW")[1]
    assert out.origin.startswith("Adapted by the AI from the workflow proven in run b81305ff")


def test_step6_corrects_a_proven_workflow_that_fails_a_newer_check():
    """Same requirements, but a check added since fails (OB-65: inline python on a node): the correction rounds
    start from the proven workflow."""
    bad = yaml.safe_load(WORKFLOW)
    bad["phases"]["activity_configuration"]["steps"].append(
        {"node": "N1", "send": "python3 -c \"import pandas\"", "command_description": "Check"})
    seen = []

    async def llm(system, user):
        seen.append(user)
        return WORKFLOW
    out = asyncio.run(NetworkConfigGenSkill(llm_fn=llm).generate(SkillInput(
        uc_name=UC, generation_hints={"step_6": "x"},
        baseline_yaml=yaml.safe_dump(bad), baseline_run="b81305ff-0000", baseline_same_inputs=True)))
    assert seen and "inline Python program" in seen[0] and "python3 -c" in seen[0]
    assert out.status == "passed" and out.origin.startswith("The workflow proven in run b81305ff, corrected")


def test_pipeline_saves_the_baseline_only_when_step_9_passed(tmp_path, monkeypatch):
    from orchestrator import pipeline_agent
    rd = _run(tmp_path)
    monkeypatch.setattr(uc_baseline, "_BASE", tmp_path / "knowledge")
    state = {"run_id": "r1", "document_set": {"run_options": {}}, "step_9_status": "failed"}
    pipeline_agent._save_baseline("network_config_exec_qa", [9], state, str(rd), UC)
    assert uc_baseline.load(UC) is None
    state["step_9_status"] = "passed"
    (rd / "step_9_error.json").write_text("{}", encoding="utf-8")  # the disk says it failed
    pipeline_agent._save_baseline("network_config_exec_qa", [9], state, str(rd), UC)
    assert uc_baseline.load(UC) is None
    (rd / "step_9_error.json").unlink()
    pipeline_agent._save_baseline("network_simulator_setup", [8], state, str(rd), UC)  # not step 9
    assert uc_baseline.load(UC) is None
    pipeline_agent._save_baseline("network_config_exec_qa", [9], state, str(rd), UC)
    assert uc_baseline.load(UC).run_id == rd.name
