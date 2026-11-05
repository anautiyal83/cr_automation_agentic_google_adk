"""PipelineAgent retry limits (bugs.md OB-4, OB-5).

    python -m pytest tests/test_pipeline_retries.py -v
"""
from __future__ import annotations

import asyncio
import inspect
import json
from types import SimpleNamespace

from orchestrator import pipeline_agent
from orchestrator.pipeline_agent import MAX_RETRIES, PipelineAgent


def _agent(agents: list[str]) -> PipelineAgent:
    pipeline = PipelineAgent()
    pipeline._agents_list = [SimpleNamespace(name=n) for n in agents]
    return pipeline


def test_retries_fail_the_run_when_exhausted(tmp_path):
    """OB-4: after MAX_RETRIES retries of the same agent the run fails with the step's error
    instead of going on with failed artifacts (or rerunning forever from the CLI)."""
    (tmp_path / "step_9_error.json").write_text(json.dumps({"error": "TC-P01 failed: backup\nmore"}), encoding="utf-8")
    pipeline = _agent(["req", "gen", "qa", "config_gen"])
    ctx = SimpleNamespace(session=SimpleNamespace(state={"run_dir": str(tmp_path)}))
    for _ in range(MAX_RETRIES):
        assert asyncio.run(pipeline._retries_exhausted(ctx, "", 3, 9)) is False
    assert not ctx.session.state.get("pipeline_failed")
    assert asyncio.run(pipeline._retries_exhausted(ctx, "", 3, 9)) is True
    assert ctx.session.state["pipeline_failed"] is True
    assert ctx.session.state["pipeline_error"] == \
        f"Step 9 still failing after {MAX_RETRIES} retries of config_gen: TC-P01 failed: backup"
    # another agent has its own count
    assert asyncio.run(pipeline._retries_exhausted(ctx, "", 1, 5)) is False


def test_continue_on_a_failed_step_drops_the_retry():
    """OB-5: "Continue" at a review drops the QA agent's retry request before the auto-retry
    block reads it; both retry paths go through the retry limit."""
    source = inspect.getsource(pipeline_agent.PipelineAgent._run_async_impl)
    rerun = source.index('elif decision == "rerun"')
    dropped = source.index('dropped = ctx.session.state.pop("_retry_target", None)')
    auto = source.index("CHECK _retry_target from QA agents")
    assert rerun < dropped < auto
    assert source.count("_retries_exhausted(") == 2
    assert "continuing pipeline" not in source


def test_rerun_of_a_passed_step_starts_at_its_first_agent(tmp_path):
    """Run 9de5fcb8: "Re-run step" at the step 6 review (no failure) re-ran only helper_script_gen —
    the workflow stayed the one generated before the fixes."""
    from orchestrator.pipeline_agent import _AGENT_NAMES, _first_agent_of_step
    pipeline = _agent(list(_AGENT_NAMES))
    ctx = SimpleNamespace(session=SimpleNamespace(state={"run_dir": str(tmp_path)}))
    helper = _AGENT_NAMES.index("helper_script_gen")
    assert pipeline._rerun_index("", ctx, 6, helper) == _AGENT_NAMES.index(_first_agent_of_step(6)) < helper
    (tmp_path / "step_6_error.json").write_text(json.dumps({"failed_agents": ["helper_script_gen"]}), encoding="utf-8")
    assert pipeline._rerun_index("", ctx, 6, helper) == helper        # a recorded failure still decides
