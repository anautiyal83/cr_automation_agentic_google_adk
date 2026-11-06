"""PipelineAgent — 8-agent orchestrator with built-in milestones and retry routing.

Executes 8 agents sequentially:
1. RequirementsGatherer — parse docs, questionnaire, compile requirements
2. CiqValidationJsonGen — generate validation-rules.yaml + json-output.yaml
3. CiqValidationJsonQA — test generated YAMLs via JAR
4. NetworkConfigGen — generate workflow YAML
5. NetworkConfigMopQA — MOP generation test (no simulator needed)
6. NetworkSimulatorSetup — generate simulator config, start MockWire, sanity check
7. NetworkConfigExecQA — execution test against simulator
8. ReportGenerator — final HTML + Excel report

After each agent (except requirements and report), the pipeline:
- Publishes step events for the UI progress tracker
- Pauses for milestone review (built-in, not a separate agent)
- Handles user decisions (continue/rerun/cancel)
- On QA failure, reads _retry_target from state and jumps back
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any
from uuid import UUID

from google.adk.agents import BaseAgent

from core.events import PipelineEvent, event_bus
from core.logging_config import get_flow_logger
from shared.state import (
    pipeline_actions,
    milestone_events,
    milestone_decisions,
    milestone_waiting,
)

logger = logging.getLogger(__name__)
flow = get_flow_logger(__name__)

MAX_RETRIES = 3

# Maps agent name → display step numbers for the UI (10-step tracker)
_AGENT_DISPLAY_STEPS: dict[str, list[int]] = {
    "requirements_gatherer": [1, 2],
    "ciq_validation_json_gen": [3, 4],
    "ciq_validation_json_qa": [5],
    "network_config_gen": [6],
    "helper_script_gen": [6],       # shares step 6 with config gen
    "network_config_mop_qa": [7],
    "network_simulator_setup": [8],
    "network_config_exec_qa": [9],
    "report_generator": [10],
}

# Maps agent name → milestone config (step_num, name) for pause after execution
_AGENT_MILESTONES: dict[str, tuple[int, str]] = {
    "ciq_validation_json_gen": (4, "Steps 3–4 · Validation Rules & CIQ JSON"),
    "ciq_validation_json_qa": (5, "QA-Validation Results"),
    "helper_script_gen": (6, "Execution Workflow & Scripts"),
    "network_config_mop_qa": (7, "MOP Generation QA"),
    "network_simulator_setup": (8, "Simulator Setup"),
    "network_config_exec_qa": (9, "Execution QA"),
}

# Cached only once this step has executed their artifacts and passed: step 6 passing its
# validator is not proof — a workflow that failed step 9 stayed cached and the next run reused it (DEP-55)
_CACHE_AFTER_STEP: dict[str, int] = {"network_config_gen": 9, "helper_script_gen": 9}


def _step_ok(state: dict, run_dir: str, step: int) -> bool:
    """A display step passed: not failed in the state and no error file on disk (the disk is
    authoritative — ADK session state written by an agent is not always visible here)."""
    if state.get(f"step_{step}_status") == "failed":
        return False
    return not (run_dir and (Path(run_dir) / f"step_{step}_error.json").exists())


def _settle_deferred_cache(cache, uc_name: str, pending: dict, display_steps: list[int], state: dict,
                           run_dir: str) -> None:
    """The step that executes deferred artifacts (step 9) ended: cache what it proved; when it
    failed, drop them — also a cached copy this run reused — so the next run generates again."""
    for proof in sorted(set(_CACHE_AFTER_STEP.values()) & set(display_steps)):
        proven = [a for a, s in _CACHE_AFTER_STEP.items() if s == proof]
        if _step_ok(state, run_dir, proof):
            for name in proven:
                if name in pending:
                    data, inputs, *rest = pending.pop(name)
                    cache.save_cache(uc_name, name, data, inputs=inputs, options=rest[0] if rest else "")
        else:
            for name in proven:
                pending.pop(name, None)
                cache.clear_cache(uc_name, name)
            flow.flow("CACHE DROPPED %s — step %d failed with them", ", ".join(proven), proof)


# Maps agent name → index for retry routing
_AGENT_NAMES = [
    "requirements_gatherer",       # 0
    "ciq_validation_json_gen",     # 1
    "ciq_validation_json_qa",      # 2
    "network_config_gen",          # 3
    "helper_script_gen",           # 4
    "network_config_mop_qa",       # 5
    "network_simulator_setup",     # 6
    "network_config_exec_qa",      # 7
    "report_generator",            # 8
]

def _first_agent_of_step(step_num: int) -> str:
    """First agent (in execution order) that reports into a display step."""
    return next((a for a in _AGENT_NAMES if step_num in _AGENT_DISPLAY_STEPS.get(a, [])), "")


def clear_step_statuses(state, from_index: int, keep: tuple[int, ...] = ()) -> None:
    """Forget the results of the display steps the run does again (agents ``from_index`` on, steps
    in ``keep`` excepted): the tracker shows them pending, not their last result (OB-16)."""
    for name in _AGENT_NAMES[from_index:]:
        for sn in _AGENT_DISPLAY_STEPS.get(name, []):
            if sn not in keep:
                state.pop(f"step_{sn}_status", None)


async def _checkpoint(run_id: str, index: int, agent_name: str, kind: str, state) -> None:
    """Save the run's restart checkpoint (session state + requirements, spec 007)."""
    from agents.requirements_agent import requirements_store
    from orchestrator import checkpoints
    await checkpoints.save(run_id, index, agent_name, kind, dict(state), requirements_store.get(run_id))


def _write_requirements_summary(run_dir: str, uc_name: str, requirements: dict) -> None:
    """Steps 1-2 from cache: the requirements document as the agent would have written it (OB-16)."""
    try:
        from skills.requirement_gathering.html_report import summary_html_from_requirements
        (Path(run_dir) / f"{uc_name}_requirements-summary.html").write_text(
            summary_html_from_requirements(uc_name, requirements), encoding="utf-8")
    except Exception as e:
        logger.warning("Requirements summary not written from cache: %s", e)


# Retry target step → agent index
_RETRY_STEP_TO_INDEX: dict[int, int] = {
    3: 1,   # retry CiqValidationJsonGen
    4: 1,   # retry CiqValidationJsonGen
    6: 3,   # retry NetworkConfigGen (also re-runs helper_script_gen)
    8: 6,   # retry NetworkSimulatorSetup
}


class PipelineAgent(BaseAgent):
    """8-agent orchestrator with built-in milestone pauses and retry routing."""

    name: str = "netconfig_pipeline"
    description: str = "NetConfig pipeline: 7 agents with milestone review and retry loops"

    _agents_list: list[BaseAgent] = []

    model_config = {"arbitrary_types_allowed": True}

    def _configure(self, agents_list: list[BaseAgent]):
        """Configure after construction (Pydantic model limitation)."""
        self._agents_list = agents_list
        self.sub_agents = list(agents_list)

    async def _run_async_impl(self, ctx):
        # Restart recovery (spec 007): a resumed run starts at its checkpoint's agent; at a
        # milestone checkpoint the agent is not re-run, its review pause is shown again
        index = int(ctx.session.state.pop("_resume_index", 0) or 0)
        resume_pause_at = ctx.session.state.pop("_resume_milestone_at", None)
        pending_cache: dict[str, tuple] = {}  # (artifacts, inputs, options)  # agent → artifacts waiting for step 9 (DEP-55)
        total = len(self._agents_list)
        run_id_str = ctx.session.state.get("run_id", "")

        flow.flow("PIPELINE START — %d agents, run_id=%s", total, run_id_str[:8] if run_id_str else "?")
        pipeline_t0 = time.monotonic()

        while index < total:
            agent = self._agents_list[index]

            # ----- CHECK PIPELINE ACTIONS -----
            action = pipeline_actions.pop(run_id_str, None)
            if action:
                result = await self._handle_action(action, ctx, index, run_id_str)
                if result == "break":
                    break
                elif isinstance(result, int):
                    index = result
                    continue

            # ----- CHECK TERMINATION -----
            if ctx.session.state.get("pipeline_cancelled") or ctx.session.state.get("pipeline_failed"):
                break

            # ----- CLEAR old error files before re-run -----
            # Only the first agent of a shared display step clears it: helper_script_gen runs
            # after network_config_gen (both step 6) and must not wipe that agent's error
            run_dir = ctx.session.state.get("run_dir", "")
            for step_num in _AGENT_DISPLAY_STEPS.get(agent.name, []):
                if run_dir and _first_agent_of_step(step_num) == agent.name:
                    err = Path(run_dir) / f"step_{step_num}_error.json"
                    if err.exists():
                        err.unlink()

            # ----- RESUMED AT THIS AGENT'S MILESTONE: no cache check, no run -----
            resume_pause = resume_pause_at is not None and resume_pause_at == index
            resume_pause_at = None if resume_pause else resume_pause_at

            # ----- CHECK ARTIFACT CACHE -----
            display_steps = _AGENT_DISPLAY_STEPS.get(agent.name, [])
            uc_name = ctx.session.state.get("uc_name", "")
            cache_hit = False
            try:
                from storage.artifact_cache import ArtifactCache, CACHEABLE_AGENTS, inputs_fingerprint, options_fingerprint
                cache = ArtifactCache()
                # A step an earlier agent of the same display step failed is not "passed" from
                # cache: the cache hit hid step 6's failure and skipped its review (OB-14)
                step_failed = any(ctx.session.state.get(f"step_{sn}_status") == "failed" for sn in display_steps)
                if agent.name in CACHEABLE_AGENTS and not step_failed and not resume_pause:
                    has = cache.has_cache(uc_name, agent.name,
                                          inputs_fingerprint(ctx.session.state.get("document_set")),
                                          options_fingerprint(ctx.session.state.get("document_set")))
                    flow.flow("CACHE CHECK %s: has_cache=%s", agent.name, has)
                    if has:
                        cached = cache.load_cache(uc_name, agent.name)
                    else:
                        cached = None
                    if cached:
                        # Inject cached artifacts into session state
                        artifacts = ctx.session.state.get("artifacts", {})
                        for key, value in cached.items():
                            if key == "requirements_json":
                                # Special: load into requirements_store
                                import json as _json
                                from agents.requirements_agent import requirements_store
                                requirements_store[run_id_str] = _json.loads(value) if isinstance(value, str) else value
                                # Also write to disk
                                if run_dir:
                                    (Path(run_dir) / "requirements.json").write_text(
                                        value if isinstance(value, str) else _json.dumps(value, indent=2),
                                        encoding="utf-8")
                                    _write_requirements_summary(run_dir, uc_name, requirements_store[run_id_str])
                            elif key == "helper_scripts" and isinstance(value, dict):
                                # Write scripts to run dir
                                if run_dir:
                                    scripts_dir = Path(run_dir) / "scripts"
                                    scripts_dir.mkdir(parents=True, exist_ok=True)
                                    for fname, content in value.items():
                                        (scripts_dir / fname).write_text(content, encoding="utf-8")
                                artifacts[key] = {fname: str(scripts_dir / fname) for fname in value}
                            else:
                                artifacts[key] = value
                                # Also write YAML artifacts to disk
                                if run_dir and isinstance(value, str) and key.endswith("_yaml"):
                                    fname_map = {
                                        "validation_rules_yaml": f"{uc_name}_validation-rules.yaml",
                                        "json_template_yaml": f"{uc_name}_json-output.yaml",
                                        "workflow_yaml": f"{uc_name}.yaml",
                                    }
                                    if key in fname_map:
                                        (Path(run_dir) / fname_map[key]).write_text(value, encoding="utf-8")
                        ctx.session.state["artifacts"] = artifacts
                        # Mark steps as passed
                        for sn in display_steps:
                            ctx.session.state[f"step_{sn}_status"] = "passed"
                            await self._publish(run_id_str, "step_started", sn)
                            await self._publish(run_id_str, "step_completed", sn, {"status": "passed"})
                        flow.flow("CACHE HIT [%d/%d] %s — skipped (artifacts loaded from cache)",
                                   index + 1, total, agent.name)
                        cache_hit = True
            except Exception as e:
                logger.error("Cache check failed for %s: %s", agent.name, e, exc_info=True)

            if cache_hit:
                index += 1
                continue

            # ----- SET LLM CONTEXT for usage tracking -----
            from shared.llm import set_llm_context
            set_llm_context(run_id=run_id_str, uc_name=uc_name, step=agent.name)
            agent_t0 = time.monotonic()

            if resume_pause:
                flow.flow("RESUME [%d/%d] %s — back at its milestone (not re-run)", index + 1, total, agent.name)
            else:
                try:
                    from storage.artifact_cache import ArtifactCache
                    ArtifactCache().invalidate_after(uc_name, agent.name)
                except Exception as e:
                    logger.debug("Cache invalidation failed for %s: %s", agent.name, e)

                # ----- CHECKPOINT: a restart re-runs this agent from here (spec 007) -----
                await _checkpoint(run_id_str, index, agent.name, "agent", ctx.session.state)

                # ----- PUBLISH step_started -----
                if display_steps:
                    await self._publish(run_id_str, "step_started", display_steps[0])

                # ----- EXECUTE AGENT -----
                flow.flow("AGENT START [%d/%d] %s", index + 1, total, agent.name)

                try:
                    async for evt in agent.run_async(ctx):
                        yield evt
                except Exception as e:
                    elapsed = time.monotonic() - agent_t0
                    logger.error("Agent %s failed after %.1fs: %s", agent.name, elapsed, e, exc_info=True)
                    flow.flow("AGENT FAILED [%d/%d] %s — %.1fs", index + 1, total, agent.name, elapsed)
                    ctx.session.state["pipeline_failed"] = True
                    ctx.session.state["pipeline_error"] = str(e)
                    for sn in display_steps:
                        await self._publish(run_id_str, "step_failed", sn, {"error": str(e)})
                    break

            elapsed = time.monotonic() - agent_t0
            flow.flow("AGENT DONE [%d/%d] %s — %.1fs", index + 1, total, agent.name, elapsed)

            # ----- CANCELLED inside the agent (Cancel at a step 1 question round): nothing of it is
            # cached or reported as completed (OB-19)
            if (pipeline_actions.get(run_id_str) or {}).get("action") == "cancel":
                pipeline_actions.pop(run_id_str, None)
                flow.flow("PIPELINE CANCELLED during %s", agent.name)
                ctx.session.state["pipeline_cancelled"] = True
                break

            # ----- SAVE TO CACHE (on success) -----
            try:
                from storage.artifact_cache import ArtifactCache, CACHEABLE_AGENTS, inputs_fingerprint, options_fingerprint
                run_dir = ctx.session.state.get("run_dir", "")
                if agent.name in CACHEABLE_AGENTS:
                    all_passed = all(_step_ok(ctx.session.state, run_dir, sn) for sn in display_steps)
                    if all_passed:
                        cache = ArtifactCache()
                        artifacts = ctx.session.state.get("artifacts", {})
                        cache_data = dict(artifacts)
                        # Special: save requirements_json from store
                        if agent.name == "requirements_gatherer":
                            from agents.requirements_agent import requirements_store
                            import json as _json
                            req = requirements_store.get(run_id_str)
                            if req:
                                cache_data["requirements_json"] = _json.dumps(req, indent=2)
                        inputs = inputs_fingerprint(ctx.session.state.get("document_set"))
                        options = options_fingerprint(ctx.session.state.get("document_set"))
                        proof = _CACHE_AFTER_STEP.get(agent.name)
                        if proof:  # kept until the step that executes them passes (DEP-55)
                            pending_cache[agent.name] = (cache_data, inputs, options)
                            flow.flow("CACHE DEFERRED %s — saved when step %d passes", agent.name, proof)
                        else:
                            cache.save_cache(uc_name, agent.name, cache_data, inputs=inputs, options=options)
                _settle_deferred_cache(ArtifactCache(), uc_name, pending_cache, display_steps,
                                       ctx.session.state, run_dir)
            except Exception as e:
                logger.error("Cache save failed for %s: %s", agent.name, e)

            # ----- PUBLISH step_completed -----
            for sn in display_steps:
                status = ctx.session.state.get(f"step_{sn}_status", "passed")
                evt_type = "step_completed" if status != "failed" else "step_failed"
                await self._publish(run_id_str, evt_type, sn, {"status": status})

            # ----- CHECK post-agent actions (set during agent execution) -----
            action = pipeline_actions.pop(run_id_str, None)
            if action:
                result = await self._handle_action(action, ctx, index, run_id_str)
                if result == "break":
                    break
                elif isinstance(result, int):
                    index = result
                    continue

            # ----- MILESTONE PAUSE (built-in) -----
            milestone_cfg = _AGENT_MILESTONES.get(agent.name)
            if milestone_cfg:
                ms_step, ms_name = milestone_cfg
                # ----- CHECKPOINT: a restart shows this review again (spec 007) -----
                await _checkpoint(run_id_str, index, agent.name, "milestone", ctx.session.state)
                decision = await self._milestone_pause(ctx, ms_step, ms_name, run_id_str)

                if decision == "cancel":
                    ctx.session.state["pipeline_cancelled"] = True
                    pipeline_actions[run_id_str] = {"action": "cancel"}
                    break
                elif decision == "clarify":
                    # The user answers this step's open questions in step 1: requirements are
                    # gathered again (clarifications.json is asked there) and every later step
                    # regenerates from them
                    try:
                        from storage.artifact_cache import ArtifactCache
                        ArtifactCache().clear_from_step(uc_name, self._agents_list[0].name)
                    except Exception as e:
                        logger.debug("Cache clear failed on clarify: %s", e)
                    flow.flow("CLARIFY — step %d sent the run back to step 1", ms_step)
                    await self._publish(run_id_str, "retry_started", 1)
                    index = 0
                    continue
                elif decision == "rerun":
                    # Determine retry target from the agent's output
                    retry_target = ctx.session.state.pop("_retry_target", None)
                    if retry_target and retry_target in _RETRY_STEP_TO_INDEX:
                        target_index = _RETRY_STEP_TO_INDEX[retry_target]
                    else:
                        # From the earliest agent of this step that failed: a step 6 rerun after a
                        # workflow error must regenerate the workflow, not only the scripts (OB-12)
                        target_index = self._rerun_index(run_id_str, ctx, ms_step, index)
                    if await self._retries_exhausted(ctx, run_id_str, target_index, ms_step):
                        break
                    # Clear cache from rerun target onward
                    try:
                        from storage.artifact_cache import ArtifactCache
                        rerun_agent = self._agents_list[target_index].name
                        ArtifactCache().clear_from_step(uc_name, rerun_agent)
                        flow.flow("RERUN — cleared cache from %s onward", rerun_agent)
                    except Exception:
                        pass
                    flow.flow("RERUN — jumping to agent index %d (retry_target=%s)", target_index, retry_target)
                    clear_step_statuses(ctx.session.state, target_index)
                    await self._publish(run_id_str, "retry_started", retry_target or display_steps[0] if display_steps else 0)
                    index = target_index
                    continue
                else:
                    # "Continue" on a failed step goes on with what it produced: the QA agent's
                    # retry request is dropped (OB-5)
                    dropped = ctx.session.state.pop("_retry_target", None)
                    if dropped is not None:
                        flow.flow("CONTINUE — step %d accepted by the reviewer, no retry of step %s", ms_step, dropped)

            # ----- CHECK _retry_target from QA agents (auto-retry without milestone) -----
            retry_target = ctx.session.state.pop("_retry_target", None)
            if retry_target is not None and retry_target in _RETRY_STEP_TO_INDEX:
                target_index = _RETRY_STEP_TO_INDEX[retry_target]
                if await self._retries_exhausted(ctx, run_id_str, target_index, display_steps[-1] if display_steps else 0):
                    break
                # The retried agent must regenerate, not reload what just failed (OB-11)
                try:
                    from storage.artifact_cache import ArtifactCache
                    ArtifactCache().clear_from_step(uc_name, self._agents_list[target_index].name)
                except Exception as e:
                    logger.debug("Cache clear failed on auto-retry: %s", e)
                flow.flow("AUTO-RETRY — step %d -> agent index %d", retry_target, target_index)
                await self._publish(run_id_str, "retry_started", retry_target)
                index = target_index
                continue

            index += 1

        pipeline_elapsed = time.monotonic() - pipeline_t0
        final = "cancelled" if ctx.session.state.get("pipeline_cancelled") else \
                "failed" if ctx.session.state.get("pipeline_failed") else "completed"
        flow.flow("PIPELINE END — %s, %.1fs", final, pipeline_elapsed)
        return
        yield  # async generator

    # ------------------------------------------------------------------
    # Built-in milestone pause
    # ------------------------------------------------------------------

    async def _retries_exhausted(self, ctx, run_id_str: str, target_index: int, failed_step: int) -> bool:
        """Count a retry of the agent at ``target_index`` (review rerun or auto-retry); past
        ``MAX_RETRIES`` the run fails with the failed step's error instead of going on with failed
        artifacts or looping (OB-4)."""
        key = f"retry_count_agent_{target_index}"
        count = ctx.session.state.get(key, 0) + 1
        ctx.session.state[key] = count
        if count <= MAX_RETRIES:
            flow.flow("RETRY %d/%d of %s", count, MAX_RETRIES, self._agents_list[target_index].name)
            return False
        detail = ""
        run_dir = ctx.session.state.get("run_dir", "")
        err = Path(run_dir) / f"step_{failed_step}_error.json" if run_dir and failed_step else None
        try:
            if err and err.exists():
                detail = str(json.loads(err.read_text(encoding="utf-8")).get("error") or "").strip().splitlines()[0][:300]
        except (OSError, ValueError, IndexError):
            pass
        message = (f"Step {failed_step} still failing after {MAX_RETRIES} retries of "
                   f"{self._agents_list[target_index].name}" + (f": {detail}" if detail else ""))
        flow.flow("MAX RETRIES — %s", message)
        ctx.session.state["pipeline_failed"] = True
        ctx.session.state["pipeline_error"] = message
        await self._publish(run_id_str, "step_failed", failed_step, {"error": message})
        return True

    def _rerun_index(self, run_id_str: str, ctx, step_num: int, index: int) -> int:
        """Agent index a milestone rerun starts from: the earliest agent the step's error file
        names in ``failed_agents``, else the FIRST agent of the display step — "Re-run step" at the
        step 6 review re-ran only helper_script_gen and kept the old workflow (run 9de5fcb8)."""
        run_dir = ctx.session.state.get("run_dir", "")
        err = Path(run_dir) / f"step_{step_num}_error.json" if run_dir else None
        try:
            failed = json.loads(err.read_text(encoding="utf-8")).get("failed_agents") or [] if err and err.exists() else []
        except (OSError, ValueError):
            failed = []
        names = [a.name for a in self._agents_list]
        indices = [names.index(a) for a in failed if a in names]
        if indices:
            return min(indices)
        first = _first_agent_of_step(step_num)
        return min(index, names.index(first)) if first in names else index

    async def _milestone_pause(self, ctx, step_num: int, name: str, run_id_str: str) -> str:
        """Pause for user review. Returns 'continue', 'rerun', or 'cancel'."""
        run_dir = ctx.session.state.get("run_dir", "")

        # Determine status: check error files AND session state for ALL display steps
        # belonging to the current agent (e.g. step 4 milestone checks steps 3+4)
        status = "passed"
        # Find which agent owns this milestone step
        agent_name = None
        for aname, (ms_step, _) in _AGENT_MILESTONES.items():
            if ms_step == step_num:
                agent_name = aname
                break
        check_steps = _AGENT_DISPLAY_STEPS.get(agent_name, [step_num]) if agent_name else [step_num]

        for sn in check_steps:
            # Check error file on disk
            if run_dir:
                err_file = Path(run_dir) / f"step_{sn}_error.json"
                if err_file.exists():
                    status = "failed"
                    break
            # Check session state
            if ctx.session.state.get(f"step_{sn}_status") == "failed":
                status = "failed"
                break

        if status == "passed" and not ctx.session.state.get("review_milestones", True):
            flow.flow("MILESTONE Step %d (%s): passed — review off, continuing", step_num, name)
            return "continue"

        flow.flow("MILESTONE Step %d (%s): status=%s — awaiting review", step_num, name, status)

        # Signal to UI
        milestone_waiting[step_num] = {"waiting": True, "name": name, "status": status}
        ctx.session.state[f"milestone_{step_num}_waiting"] = True
        ctx.session.state[f"milestone_{step_num}_step_name"] = name
        ctx.session.state[f"milestone_{step_num}_step_status"] = status

        # Publish milestone_reached event for the UI
        artifact_file = ""
        extra_files = []
        if run_dir:
            artifact_file, extra_files = self._resolve_artifacts(
                ctx.session.state, step_num
            )
        await self._publish(run_id_str, "milestone_reached", step_num, {
            "milestone": name,
            "artifact_file": artifact_file,
            "test_status": status,
            "extra_artifact_files": extra_files,
        })

        # Wait for user decision via asyncio.Event
        evt = milestone_events.get(step_num)
        if evt:
            await evt.wait()
            evt.clear()
        else:
            while step_num not in milestone_decisions:
                await asyncio.sleep(0.5)

        decision = milestone_decisions.pop(step_num, "continue")
        milestone_waiting.pop(step_num, None)
        flow.flow("MILESTONE Step %d: decision=%s", step_num, decision)
        return decision

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _handle_action(self, action: dict, ctx, index: int, run_id_str: str):
        """Handle a pipeline action. Returns 'break', target_index (int), or None."""
        act = action.get("action")
        if act == "cancel":
            flow.flow("PIPELINE CANCELLED at index %d", index)
            ctx.session.state["pipeline_cancelled"] = True
            return "break"
        elif act == "rerun":
            target = action.get("target_step")
            if target and target in _RETRY_STEP_TO_INDEX:
                target_index = _RETRY_STEP_TO_INDEX[target]
                flow.flow("RERUN — jumping to step %d (index %d)", target, target_index)
                clear_step_statuses(ctx.session.state, target_index)
                await self._publish(run_id_str, "retry_started", target)
                return target_index
        return None

    async def _publish(self, run_id_str: str, event_type: str, step_num: int = 0, payload: dict | None = None):
        """Publish a pipeline event to the event bus."""
        if not run_id_str:
            return
        try:
            await event_bus.publish(PipelineEvent(
                run_id=UUID(run_id_str),
                event_type=event_type,
                step_number=step_num,
                payload=payload or {},
            ))
        except Exception:
            pass

    @staticmethod
    def _resolve_artifacts(state: dict, step_num: int) -> tuple[str, list[str]]:
        """Find the primary artifact file and extra files for milestone review."""
        run_dir = state.get("run_dir", "")
        uc_name = state.get("uc_name", "")
        if not run_dir or not uc_name:
            return "", []

        _ARTIFACT_MAP = {
            4: (f"{uc_name}_validation-rules.yaml", [f"{uc_name}_json-output.yaml"]),
            5: (f"{uc_name}_ciq-qa-report.html", []),
            6: (f"{uc_name}.yaml", []),
            7: (f"{uc_name}_mop-qa-report.html", []),
            8: (f"{uc_name}_simulator-report.html", [f"{uc_name}_simulator-config.yaml"]),
            9: (f"{uc_name}_network-qa-report.html", []),
        }

        primary, extras = _ARTIFACT_MAP.get(step_num, ("", []))
        rd = Path(run_dir)
        primary_path = str(rd / primary) if primary and (rd / primary).exists() else ""
        extra_paths = [str(rd / e) for e in extras if (rd / e).exists()]

        # For step 6: include generated helper scripts
        if step_num == 6:
            scripts_dir = rd / "scripts"
            if scripts_dir.exists():
                for f in scripts_dir.glob("*.py"):
                    extra_paths.append(str(f))

        # For step 5: the CIQ JSON the json-output template produced
        if step_num == 5:
            extra_paths += [str(f) for f in sorted((rd / "json_data").glob("*.json"))]

        # For step 9: the execution-report-generator HTML of every executed case
        if step_num == 9:
            extra_paths += [str(f) for f in sorted((rd / "exec_report").glob("*.html"))]

        # For step 7: also include MOP output HTML from mop_output/ subdir
        if step_num == 7:
            mop_dir = rd / "mop_output"
            if mop_dir.exists():
                for f in mop_dir.glob("*.html"):
                    extra_paths.append(str(f))

        # Fallback: if primary not found, search for any matching file
        if not primary_path and primary:
            candidates = list(rd.glob(f"*{primary.split('_', 1)[-1] if '_' in primary else primary}"))
            if candidates:
                primary_path = str(candidates[0])

        return primary_path, extra_paths


def build_pipeline(config: dict | None = None) -> PipelineAgent:
    """Build the 9-agent pipeline.

    Returns a configured PipelineAgent ready for execution.
    """
    import os

    config = config or {}
    api_key = config.get("openrouter_api_key", os.environ.get("OPENROUTER_API_KEY", ""))
    if api_key:
        os.environ["OPENROUTER_API_KEY"] = api_key

    from agents.requirements_agent import RequirementsGathererNode
    from agents.ciq_validation_json_gen_agent import CiqValidationJsonGenAgent
    from agents.ciq_validation_json_qa_agent import CiqValidationJsonQAAgent
    from agents.network_config_gen_agent import NetworkConfigGenAgent
    from agents.helper_script_gen_agent import HelperScriptGenAgent
    from agents.network_config_mop_qa_agent import NetworkConfigMopQAAgent
    from agents.network_simulator_setup_agent import NetworkSimulatorSetupAgent
    from agents.network_config_qa_agent import NetworkConfigQAAgent
    from agents.report_agent import ReportAgent

    agents = [
        RequirementsGathererNode(),       # 0 - Steps 1+2
        CiqValidationJsonGenAgent(),      # 1 - Steps 3+4
        CiqValidationJsonQAAgent(),       # 2 - Step 5
        NetworkConfigGenAgent(),          # 3 - Step 6 (workflow YAML)
        HelperScriptGenAgent(),           # 4 - Step 6 (helper scripts)
        NetworkConfigMopQAAgent(),        # 5 - Step 7 (MOP QA)
        NetworkSimulatorSetupAgent(),     # 6 - Step 8
        NetworkConfigQAAgent(),           # 7 - Step 9 (Execution QA)
        ReportAgent(),                    # 8 - Step 10
    ]

    pipeline = PipelineAgent()
    pipeline._configure(agents)

    logger.info("Pipeline built: %d agents", len(agents))
    return pipeline
