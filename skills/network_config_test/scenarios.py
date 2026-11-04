"""Scenario-based execution testing for step 9 (spec 005).

Test cases are derived from the workflow and the CIQ JSON, not invented:

- positive: one per CR group × node group — every check passes on a healthy node
- rollback only (spec 011): one run with ``ROLLBACK_ONLY=true`` on a node group whose positive
  passed — only the rollback phase runs, and its steps pass
- negative (on one representative node group whose positive passed):
  pre-health-check failure, activity failure, post-health-check failure, node login failure

A negative injects one fault into the simulator (the target command answers with a failing
output, or the node rejects the login), the simulator is restarted with that config, and the
workflow runs once. The expected outcome follows the workflow's own failure policy and the
cli-automation JAR's semantics (probed 2026-11-02):

- ``on_failure: stop`` (default) — every later step, in every phase, is skipped; run FAILs
- ``continue`` / ``warning`` — later steps still run; the run still FAILs
- skipped steps are absent from the JAR's execution report

Workflows with the execution contract (spec 011, flags in ``globals.vars``) are judged by it: a
failed health check lets the rest of its phase run, a pre-health failure skips every later phase,
an activity failure stops the activity and runs the rollback only when the node was changed.

Evidence per case: the JAR report (which steps ran, validation status) and MockWire's event
log (which commands each node actually received).
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import time
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
from typing import Any, Awaitable, Callable

from .jar_executor import execute_target, generate_execution_report, stage_upload_inputs
from .result_parser import CommandResult, classify_failures

logger = logging.getLogger(__name__)

LlmFn = Callable[[str, str], Awaitable[str]]

_PROMPT_STEP = re.compile(r"^(su(\s+-)?\s+\S+|exit|logout)$")
_PLACEHOLDER = re.compile(r"\$\{[^{}]*\}")
_RESERVED_KEYS = ("config", "auth", "niam", "transfer", "vfs", "state")
WRONG_PASSWORD = "netconfig-negative-test"

NEGATIVE_KINDS = [
    ("pre_check", "Pre-health check failure", "pre"),
    ("activity", "Activity failure", "activity"),
    ("post_check", "Post-health check failure", "post"),
]


# ---------------------------------------------------------------------------
# Workflow model
# ---------------------------------------------------------------------------

@dataclass
class WorkflowStep:
    index: int
    phase: str
    phase_kind: str          # pre | backup | activity | post | rollback
    node: str
    send: str
    regex: str
    on_failure: str          # stop | continue | warning | run
    description: str = ""
    has_when: bool = False
    changes: bool = False    # the contract marks it as changing the node (CHANGES_MADE)

    @property
    def is_prompt_step(self) -> bool:
        return bool(_PROMPT_STEP.match(self.send))


def phase_kind(name: str, body: dict | None = None) -> str:
    """pre | backup | activity | post | rollback from the phase key or its ``name`` (the
    execution contract's classification, spec 011)."""
    from skills.network_config_gen.contract import phase_kind as contract_kind
    return {"pre_health": "pre", "post_health": "post"}.get(
        k := contract_kind(str(name), body if isinstance(body, dict) else {}), k)


def has_contract(workflow: dict) -> bool:
    """The workflow carries the execution contract's flags (spec 011)."""
    flags = ((workflow.get("globals") or {}).get("vars") or {}) if isinstance(workflow, dict) else {}
    return isinstance(flags, dict) and "ROLLBACK_REQUIRED" in flags and "ROLLBACK_ONLY" in flags


def workflow_steps(workflow: dict) -> list[WorkflowStep]:
    """Steps in execution order with their effective failure policy; the steps of a loop
    (``type: loop``, e.g. one pass per CIQ record) stand for each of its passes."""
    defaults = (workflow.get("defaults") or (workflow.get("globals") or {}).get("defaults") or {})
    default_policy = _policy(defaults.get("on_failure")) or "stop"
    steps: list[WorkflowStep] = []
    phases = workflow.get("phases") or {}
    items = phases.items() if isinstance(phases, dict) else enumerate(phases)

    def leaves(raw_steps):
        for raw in raw_steps or []:
            if not isinstance(raw, dict):
                continue
            if isinstance(raw.get("steps"), list):
                yield from leaves(raw["steps"])
            else:
                yield raw

    for name, body in items:
        body = body if isinstance(body, dict) else {"steps": body}
        phase_policy = _policy(body.get("on_failure")) or default_policy
        kind = phase_kind(str(name), body)
        for raw in leaves(body.get("steps")):
            send = str(raw.get("send") or raw.get("cmd") or raw.get("command") or "").strip()
            steps.append(WorkflowStep(
                index=len(steps), phase=str(name), phase_kind=kind,
                node=str(raw.get("node") or ""), send=send, regex=_success_regex(raw),
                on_failure=_policy(raw.get("on_failure")) or phase_policy,
                description=str(raw.get("command_description") or ""),
                has_when="when" in raw,
                changes=_sets_flag(raw, "success", "CHANGES_MADE"),
            ))
    return steps


def _sets_flag(step: dict, branch: str, flag: str) -> bool:
    validation = step.get("validation") if isinstance(step.get("validation"), dict) else {}
    b = validation.get(branch) if isinstance(validation.get(branch), dict) else {}
    return str((b.get("vars") or {}).get(flag, "")).lower() == "true"


def _policy(value: Any) -> str:
    if isinstance(value, dict):
        return "run" if "run" in value else ""
    value = str(value or "").strip().lower()
    return "run" if value.startswith("run") else value


def _success_regex(step: dict) -> str:
    validation = step.get("validation") if isinstance(step.get("validation"), dict) else {}
    success = validation.get("success") if isinstance(validation.get("success"), dict) else {}
    for criteria in (success.get("criteria"), validation.get("criteria")):
        if isinstance(criteria, dict) and isinstance(criteria.get("regex"), str):
            return criteria["regex"]
    return ""


def matches_command(send: str, executed: str) -> bool:
    """Workflow ``send`` (``${…}`` = any value) vs the resolved command the JAR ran."""
    executed = re.sub(r" \[\d+\]$", "", executed.strip())  # the JAR suffixes repeats: "cmd [2]"
    norm = lambda s: re.sub(r"\s+", " ", s.strip())  # noqa: E731
    send, executed = norm(send), norm(executed)
    if "${" not in send:
        return send == executed
    pattern = ".*?".join(re.escape(p) for p in _PLACEHOLDER.split(send))
    return re.fullmatch(pattern, executed) is not None


_PASSING_RUN = {"PASS", "PASSED", "SUCCESS", "SUCCEEDED", "COMPLETED", "OK"}


def run_passed(overall_status: str) -> bool:
    """The JAR reports a clean run as overall_status SUCCESS (FAIL otherwise)."""
    return str(overall_status or "").strip().upper() in _PASSING_RUN


def java_regex_matches(regex: str, text: str) -> bool:
    """The JAR's validation semantics: find(), ^/$ per line, '.' does not cross lines."""
    py = re.sub(r"\(\?<([A-Za-z][A-Za-z0-9]*)>", r"(?P<\1>", regex)
    try:
        return re.search(py, text.replace("\r\n", "\n"), re.MULTILINE) is not None
    except re.error:
        return False


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

@dataclass
class Fault:
    kind: str                 # validation | login
    node: str
    step_index: int = -1
    send: str = ""
    lines: list[str] = field(default_factory=list)
    description: str = ""


@dataclass
class Scenario:
    id: str
    title: str
    kind: str                 # positive | rollback | negative
    category: str             # happy_path | rollback_only | pre_check | activity | post_check | login
    target: tuple[str, str, str]  # (json_file, node_group, cr_group)
    fault: Fault | None = None
    expected: str = ""
    policy: str = ""          # failure policy of the faulted step
    runtime_vars: dict[str, str] = field(default_factory=dict)  # extra --var (ROLLBACK_ONLY)
    contract: bool = False    # judged by the execution contract (spec 011)

    @property
    def node_group(self) -> str:
        return self.target[1]

    @property
    def cr_group(self) -> str:
        return self.target[2]


@dataclass
class Check:
    description: str
    ok: bool
    detail: str = ""


@dataclass
class ScenarioResult:
    scenario: Scenario
    status: str = "FAIL"      # PASS | FAIL
    actual: str = ""
    checks: list[Check] = field(default_factory=list)
    commands: list[CommandResult] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    overall_status: str = ""  # the JAR's overall_status
    duration_sec: float = 0.0
    out_dir: str = ""
    error: str = ""
    classification: str = ""  # workflow | simulator (when FAIL)
    html_reports: list[str] = field(default_factory=list)  # execution-report-generator output

    def to_dict(self) -> dict:
        s = self.scenario
        return {
            "id": s.id, "title": s.title, "kind": s.kind, "category": s.category,
            "cr_group": s.cr_group, "node_group": s.node_group, "expected": s.expected,
            "policy": s.policy,
            "fault": None if not s.fault else {
                "kind": s.fault.kind, "node": s.fault.node, "command": s.fault.send,
                "description": s.fault.description, "output": "\n".join(s.fault.lines)},
            "status": self.status, "actual": self.actual, "overall_status": self.overall_status,
            "classification": self.classification, "error": self.error,
            "duration_sec": round(self.duration_sec, 1), "out_dir": self.out_dir,
            "html_reports": self.html_reports,
            "checks": [{"check": c.description, "ok": c.ok, "detail": c.detail} for c in self.checks],
            "commands": [{"node": c.node, "phase": c.phase, "command": c.command, "success": c.success,
                          "validation": c.validation_status, "output": c.output[:300]}
                         for c in self.commands],
            "simulator_commands": [{"instance": e.get("instance"), "source": e.get("source"),
                                    "input": e.get("input")} for e in self.events
                                   if not str(e.get("source", "")).startswith(("builtin:stty", "ctrl"))],
        }


def positive_scenarios(targets: list[tuple[str, str, str]]) -> list[Scenario]:
    return [Scenario(
        id=f"TC-P{i:02d}", kind="positive", category="happy_path", target=t,
        title=f"Sunny day — {t[2] or 'CR'} / {t[1]}",
        expected="Every step runs on a healthy node and passes its validation; the run PASSes.",
    ) for i, t in enumerate(targets, 1)]


def rollback_only_scenario(steps: list[WorkflowStep], target: tuple[str, str, str]) -> Scenario | None:
    """One run with ``ROLLBACK_ONLY=true`` (spec 011 rule 5) when the workflow has a rollback."""
    if not any(s.phase_kind == "rollback" for s in steps):
        return None
    return Scenario(
        id="TC-R01", kind="rollback", category="rollback_only", target=target,
        title=f"Rollback only — {target[2] or 'CR'} / {target[1]}",
        expected="With ROLLBACK_ONLY=true only the rollback phase runs; its steps pass; the run PASSes.",
        runtime_vars={"ROLLBACK_ONLY": "true"}, contract=True)


def negative_scenarios(steps: list[WorkflowStep], target: tuple[str, str, str],
                       simulated_nodes: set[str], contract: bool = False) -> tuple[list[Scenario], list[str]]:
    """One fault per phase kind plus a login failure; also returns notes for kinds with no
    injectable step (only commands answered by a simulated node can be faulted)."""
    scenarios: list[Scenario] = []
    notes: list[str] = []
    n = 0
    for category, title, kind in NEGATIVE_KINDS:
        candidates = [s for s in steps if s.phase_kind == kind and s.node in simulated_nodes
                      and s.send and not s.is_prompt_step and s.regex]
        if not candidates:
            notes.append(f"{title}: no validated command on a simulated node in the {kind} phase — "
                         "nothing to inject a fault into")
            continue
        # Prefer a gating check (stop policy): that is what must protect the activity
        step = next((s for s in candidates if s.on_failure in ("stop", "run")), candidates[0])
        n += 1
        scenarios.append(Scenario(
            id=f"TC-N{n:02d}", kind="negative", category=category, target=target,
            title=f"{title} — {step.node}: {step.send[:60]}",
            fault=Fault(kind="validation", node=step.node, step_index=step.index, send=step.send,
                        description=f"'{step.send}' on {step.node} returns a failing output"),
            policy=step.on_failure,
            expected=_expected_text(step, steps, contract),
            contract=contract,
        ))
    remote = next((s for s in steps if s.node in simulated_nodes), None)
    if remote:
        n += 1
        scenarios.append(Scenario(
            id=f"TC-N{n:02d}", kind="negative", category="login", target=target,
            title=f"Node login failure — {remote.node}",
            fault=Fault(kind="login", node=remote.node, step_index=remote.index,
                        description=f"{remote.node} rejects the login (wrong password)"),
            policy=remote.on_failure,
            expected=(f"No command reaches {remote.node}; its first step fails and the run FAILs"
                      + (" — nothing after it runs." if remote.on_failure in ("stop", "run") else ".")),
        ))
    return scenarios, notes


def contract_expectation(step: WorkflowStep, steps: list[WorkflowStep]) -> dict:
    """What the execution contract (spec 011) makes happen after ``step`` fails."""
    if step.phase_kind in ("pre", "post"):
        same = [s for s in steps if s.phase == step.phase and s.index > step.index]
        stops = step.on_failure in ("stop", "run")
        return {"phase_continues": bool(same) and not stops,
                "later_phases_run": step.phase_kind == "post",  # pre-health: every next phase skipped
                "rollback": False}
    if step.phase_kind == "activity":
        changed = step.changes or any(s.changes for s in steps if s.phase_kind == "activity" and s.index < step.index)
        return {"phase_continues": False, "later_phases_run": False, "rollback": changed}
    return {"phase_continues": False, "later_phases_run": False, "rollback": False}


def _expected_text(step: WorkflowStep, steps: list[WorkflowStep], contract: bool = False) -> str:
    if contract:
        exp = contract_expectation(step, steps)
        text = f"The validation of '{step.send[:60]}' detects the fault and the run FAILs"
        if step.phase_kind == "pre":
            return text + ("; the remaining health checks still run" if exp["phase_continues"] else "") + \
                "; every later phase is skipped (pre-health failure)."
        if step.phase_kind == "post":
            return text + ("; the remaining health checks still run" if exp["phase_continues"] else "") + \
                "; it is reported, no rollback."
        return text + "; the activity stops there; " + (
            "the rollback runs (the node was changed)." if exp["rollback"]
            else "no rollback (nothing was changed yet).")
    later = [s for s in steps if s.index > step.index]
    text = f"The validation of '{step.send[:60]}' detects the fault and the run FAILs"
    if step.on_failure in ("stop", "run"):
        kinds = sorted({s.phase_kind for s in later}, key=["pre", "activity", "post"].index)
        return text + (f"; on_failure={step.on_failure}: no later step runs"
                       + (f" (no {', '.join(kinds)} steps)" if kinds else "") + ".")
    return text + f"; on_failure={step.on_failure}: the following steps still run."


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------

class SimulatorControl:
    """How the suite stops/registers MockWire (the agent ties it to shared.state)."""

    async def stop(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def register(self, sim) -> None:  # pragma: no cover - interface
        raise NotImplementedError


@dataclass
class SuiteResult:
    results: list[ScenarioResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    classification: dict = field(default_factory=dict)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.status == "PASS")

    @property
    def failed(self) -> int:
        return len(self.results) - self.passed


class ScenarioSuite:
    """Builds the catalogue, injects faults, runs each case on the simulator, evaluates."""

    def __init__(self, *, run_dir: str, uc_name: str, workflow_path: str, plan: dict,
                 cli_jar: str, mockwire_jar: str, control: SimulatorControl,
                 llm_fn: LlmFn | None = None, java_home: str | None = None,
                 samples: list[dict] | None = None, runtime_vars: dict[str, str] | None = None,
                 host: str = "localhost", include_negatives: bool = False, report_jar: str = ""):
        self.run_dir = Path(run_dir)
        self.sim_dir = self.run_dir / "simulator"
        self.uc_name = uc_name
        self.workflow_path = workflow_path
        self.plan = plan
        self.cli_jar = cli_jar
        self.mockwire_jar = mockwire_jar
        self.control = control
        self.llm_fn = llm_fn
        self.java_home = java_home
        self.samples = samples or []
        self.runtime_vars = runtime_vars
        self.host = host
        self.out_root = self.run_dir / "exec_output" / "scenarios"
        # One HTML report per executed case, by the configured report JAR
        self.report_jar = report_jar
        self.report_dir = self.run_dir / "exec_report"
        # Sunny day only for now; negative cases (spec 005) are switched on later
        self.include_negatives = include_negatives

    async def run(self) -> SuiteResult:
        from ruamel.yaml import YAML
        workflow = YAML(typ="safe").load(Path(self.workflow_path).read_text(encoding="utf-8")) or {}
        steps = workflow_steps(workflow)
        manifest = _read_json(self.sim_dir / "manifest.json")
        instances = [i for i in manifest.get("instances", []) if i.get("name")]
        if not instances:
            return SuiteResult(notes=["No simulator manifest — step 8 did not start a simulator"],
                               classification={"classification": "simulator", "retry_target": 8,
                                               "simulator_issues": ["no simulator instances"],
                                               "workflow_issues": [], "unmatched_commands": []})
        node_instance = {n: i["name"] for i in instances for n in (i.get("nodes") or [i["name"]])}
        base = {i["name"]: (self.sim_dir / "config" / f"{i['name']}.yaml").read_text(encoding="utf-8")
                for i in instances}
        for old in (self.out_root, self.report_dir):
            if old.exists():
                shutil.rmtree(old, ignore_errors=True)

        suite = SuiteResult()
        contract = has_contract(workflow)
        try:
            for sc in positive_scenarios(self.plan["targets"]):
                suite.results.append(await self._run(sc, steps, instances, base, node_instance))
            passed = [r.scenario.target for r in suite.results if r.status == "PASS"]
            representative = passed[0] if passed else self.plan["targets"][0]
            # One rollback-only execution (spec 011 rule 5) on a clean simulator
            rollback = rollback_only_scenario(steps, representative) if contract else None
            if rollback:
                suite.results.append(await self._run(rollback, steps, instances, base, node_instance))
            elif contract:
                suite.notes.append("No rollback phase: the rollback-only execution was not tested")
            if not self.include_negatives:
                suite.notes.append("Negative scenarios are not enabled yet (sunny day only)")
                return suite
            negatives, notes = negative_scenarios(steps, representative, set(node_instance), contract)
            suite.notes += notes
            if not passed:
                suite.notes.append("No happy path passed: negative cases ran on "
                                   f"{representative[2]}/{representative[1]} anyway")
            for sc in negatives:
                await self._prepare_fault(sc, steps, base, node_instance)
                suite.results.append(await self._run(sc, steps, instances, base, node_instance))
        finally:
            # Leave the simulator as step 8 built it
            for name, text in base.items():
                (self.sim_dir / "config" / f"{name}.yaml").write_text(text, encoding="utf-8")
            suite.classification = _classify(suite.results)
        return suite

    # -- faults ---------------------------------------------------------------

    async def _prepare_fault(self, sc: Scenario, steps: list[WorkflowStep], base: dict[str, str],
                             node_instance: dict[str, str]) -> None:
        fault = sc.fault
        if not fault or fault.kind != "validation":
            return
        step = steps[fault.step_index]
        normal = self._normal_output(step, base.get(node_instance.get(step.node, ""), ""))
        fault.lines = await failing_output(step, normal, self.llm_fn)

    def _normal_output(self, step: WorkflowStep, config_text: str) -> str:
        entry = _config_entry(config_text, step.send)
        if isinstance(entry, dict) and isinstance(entry.get("lines"), list):
            return "\n".join(str(x) for x in entry["lines"])
        for s in self.samples:
            if matches_command(step.send, str(s.get("command", ""))):
                return str(s.get("output", ""))
        return ""

    def _configs_for(self, sc: Scenario, base: dict[str, str], node_instance: dict[str, str]) -> dict[str, str]:
        configs = dict(base)
        if sc.fault:
            inst = node_instance.get(sc.fault.node)
            if inst:
                configs[inst] = inject_fault(base[inst], sc.fault)
        return configs

    # -- one case -------------------------------------------------------------

    async def _run(self, sc: Scenario, steps: list[WorkflowStep], instances: list[dict],
                   base: dict[str, str], node_instance: dict[str, str]) -> ScenarioResult:
        t0 = time.monotonic()
        out_dir = self.out_root / sc.id
        result = ScenarioResult(scenario=sc, out_dir=str(out_dir))
        try:
            await self._restart(instances, self._configs_for(sc, base, node_instance))
            if self.plan.get("local_cwd"):
                from ruamel.yaml import YAML
                staged = stage_upload_inputs(
                    YAML(typ="safe").load(Path(self.workflow_path).read_text(encoding="utf-8")) or {},
                    sc.target[0], sc.target[1], sc.target[2], self.plan["local_cwd"])
                if staged:
                    logger.info("Scenario %s: staged %d upload input(s): %s", sc.id, len(staged),
                                ", ".join(Path(s).name for s in staged))
            runtime_vars = self.runtime_vars
            if sc.runtime_vars:
                from core.config import runtime_params
                runtime_vars = {**(runtime_params() if runtime_vars is None else runtime_vars), **sc.runtime_vars}
            exec_result = await execute_target(self.plan, self.cli_jar, self.workflow_path, sc.target,
                                               out_dir, self.java_home, runtime_vars=runtime_vars)
            report = _read_report(out_dir)
            result.overall_status = str(((report.get("metadata") or {}).get("execution_summary") or {})
                                        .get("overall_status") or ("PASS" if exec_result["success"] else "FAIL"))
            result.commands = _commands(report)
            result.events = self._collect_events(out_dir)
            evaluate(result, steps, node_instance)
            if not result.commands and exec_result.get("stdout"):
                result.error = exec_result["stdout"][-600:]
            if report and self.report_jar:
                await self._html_report(sc, out_dir, result)
        except Exception as e:
            logger.error("Scenario %s failed to run: %s", sc.id, e, exc_info=True)
            result.status, result.error, result.classification = "FAIL", str(e), "simulator"
            result.actual = f"Could not run: {e}"
        result.duration_sec = time.monotonic() - t0
        logger.info("Scenario %s %s — %s", sc.id, result.status, result.actual)
        return result

    async def _html_report(self, sc: Scenario, out_dir: Path, result: ScenarioResult) -> None:
        """exec_report/<uc>_<case>_<node>_summary.html + _detail.html (every command)."""
        json_file = next(iter(sorted(Path(out_dir).glob("*.json"))), None)
        if not json_file:
            return
        name = f"{self.uc_name}_{sc.id}_{sc.node_group}"
        rep = await generate_execution_report(self.report_jar, json_file, self.report_dir, name, self.plan,
                                              cr_group=sc.cr_group, request_id=self.run_dir.name[:8],
                                              java_home=self.java_home)
        result.html_reports = rep["files"]
        if rep["error"]:
            logger.warning("Execution report for %s not generated: %s", sc.id, rep["error"])

    async def _restart(self, instances: list[dict], configs: dict[str, str]) -> None:
        from skills.network_simulator_setup.simulator_runner import SimulatorInstance, start_simulator
        await self.control.stop()
        for name, text in configs.items():
            (self.sim_dir / "config" / f"{name}.yaml").write_text(text, encoding="utf-8")
        for f in (self.sim_dir / "logs").glob("*.events.jsonl"):
            f.unlink()
        # Every case starts from a clean node: files uploaded or produced by the previous
        # case (inbox/<ORDERNO>.xml, outbox/….res) must not exist yet
        shutil.rmtree(self.sim_dir / "vfs", ignore_errors=True)
        (self.sim_dir / "vfs").mkdir(parents=True, exist_ok=True)
        sim = await start_simulator(
            self.sim_dir,
            [SimulatorInstance(name=i["name"], port=int(i["port"]), nodes=list(i.get("nodes") or []))
             for i in instances],
            mockwire_jar=self.mockwire_jar, java_home=self.java_home, host=self.host)
        self.control.register(sim)

    def _collect_events(self, out_dir: Path) -> list[dict]:
        events: list[dict] = []
        target = out_dir / "simulator_events"
        target.mkdir(parents=True, exist_ok=True)
        for f in sorted((self.sim_dir / "logs").glob("*.events.jsonl")):
            shutil.copy2(f, target / f.name)
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
        return events


# ---------------------------------------------------------------------------
# Fault injection
# ---------------------------------------------------------------------------

FAULT_SYSTEM = (
    "You write realistic terminal output for negative testing of network node commands. "
    "Output ONLY the raw output the command prints — no explanations, no code fences.")

FAULT_USER = """Command: {command}
What the step checks: {description}
On a healthy node the output matches this success regex (Java): {regex}
Healthy output:
{normal}

Write the output of the same command on a node where this check FAILS (degraded or faulty
node, error state, service down, threshold exceeded — whatever fits the command). Keep the
same format and layout as the healthy output so it looks real, but it must NOT match the
success regex."""


async def failing_output(step: WorkflowStep, normal: str, llm_fn: LlmFn | None) -> list[str]:
    """Realistic failing output for ``step`` that its success regex does not match."""
    if llm_fn:
        prompt = FAULT_USER.format(command=step.send, description=step.description or "(none)",
                                   regex=step.regex, normal=normal[:3000] or "(not recorded)")
        for _ in range(2):
            try:
                text = _strip_fences(await llm_fn(FAULT_SYSTEM, prompt))
            except Exception as e:
                logger.warning("Fault output for %r via LLM failed: %s", step.send, e)
                break
            if text.strip() and not java_regex_matches(step.regex, text):
                return text.splitlines()
            prompt += "\n\nYour previous answer still matched the success regex. Change the part it matches."
    return deterministic_failing_output(step, normal)


def deterministic_failing_output(step: WorkflowStep, normal: str) -> list[str]:
    """Fallback: the healthy output without the lines the regex matches, plus an error line."""
    kept = [line for line in normal.splitlines() if not java_regex_matches(step.regex, line)]
    lines = kept + [f"ERROR: {step.send.split()[0] if step.send else 'command'}: check failed (simulated fault)"]
    if java_regex_matches(step.regex, "\n".join(lines)):
        lines = [f"ERROR: {step.send[:80]}: operation failed (simulated fault)"]
    return lines


def inject_fault(config_text: str, fault: Fault) -> str:
    """MockWire instance config with the fault applied (round-trip, other keys untouched)."""
    from ruamel.yaml import YAML
    from ruamel.yaml.comments import CommentedMap
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 1_000_000  # never fold: a folded long regex KEY is invalid YAML and MockWire exits
    cfg = yaml.load(StringIO(config_text)) or CommentedMap()

    if fault.kind == "login":
        for user in ((cfg.get("auth") or {}).get("users") or []):
            user["password"] = WRONG_PASSWORD
        for user in ((cfg.get("niam") or {}).get("users") or []):
            user["password"] = WRONG_PASSWORD
    else:
        key = command_key(fault.send)
        key_rx = None if key == fault.send else re.compile(key[len("regex:"):])
        for existing in list(cfg.keys()):
            name = str(existing)
            if name == fault.send or (key_rx and not name.startswith("regex:") and key_rx.fullmatch(name)) \
                    or name == key:
                del cfg[existing]
        position = sum(1 for k in cfg.keys() if str(k) in _RESERVED_KEYS or str(k).startswith("ctrl+"))
        cfg.insert(position, key, {"lines": list(fault.lines)})

    out = StringIO()
    yaml.dump(cfg, out)
    return out.getvalue()


def command_key(send: str) -> str:
    """MockWire key for a workflow command: exact text, or ``regex:`` when it has ``${…}``."""
    if "${" not in send:
        return send
    return "regex:^" + ".+?".join(re.escape(p) for p in _PLACEHOLDER.split(send.strip())) + "$"


def _config_entry(config_text: str, send: str) -> Any:
    from ruamel.yaml import YAML
    try:
        cfg = YAML(typ="safe").load(StringIO(config_text)) or {}
    except Exception:
        return None
    if send in cfg:
        return cfg[send]
    for key, value in cfg.items():
        if str(key).startswith("regex:"):
            try:
                if re.search(str(key)[6:], send):
                    return value
            except re.error:
                continue
    return None


def _strip_fences(text: str) -> str:
    text = re.sub(r"^```[a-zA-Z]*\s*\n?", "", text.strip())
    return re.sub(r"\n?```\s*$", "", text).strip("\n")


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def _read_report(out_dir: Path) -> dict:
    for f in sorted(Path(out_dir).glob("*.json")):
        data = _read_json(f)
        if "data" in data:
            return data
    return {}


def _read_json(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _commands(report: dict) -> list[CommandResult]:
    return [CommandResult(
        command=info.get("command", key), phase=info.get("phase", ""),
        node=info.get("target", info.get("node", "")), success=bool(info.get("success", False)),
        output=str(info.get("output", ""))[:2000],
        validation_status=info.get("validation_status", "skipped"),
        validation_criteria=info.get("validation_criteria", ""),
        validation_conclusion=info.get("validation_conclusion", ""),
        failure_reason=info.get("failure") or info.get("reason") or "",
    ) for key, info in (report.get("data") or {}).items() if isinstance(info, dict)]


def align(commands: list[CommandResult], steps: list[WorkflowStep]) -> list[int | None]:
    """Workflow step index of each executed command (report order = execution order)."""
    indexes: list[int | None] = []
    cursor = 0
    for cmd in commands:
        found = None
        for j in range(cursor, len(steps)):
            if steps[j].node.lower() == cmd.node.lower() and matches_command(steps[j].send, cmd.command):
                found = j
                break
        if found is None:  # repeated/looping steps: search from the start
            found = next((s.index for s in steps if s.node.lower() == cmd.node.lower()
                          and matches_command(s.send, cmd.command)), None)
        else:
            cursor = found + 1
        indexes.append(found)
    return indexes


def _failed(cmd: CommandResult) -> bool:
    return not cmd.success or cmd.validation_status == "failed"


def evaluate(result: ScenarioResult, steps: list[WorkflowStep], node_instance: dict[str, str]) -> None:
    """Fill status, checks, actual and classification of one scenario."""
    sc = result.scenario
    cmds = result.commands
    idx = align(cmds, steps)
    unmatched = [e for e in result.events if e.get("source") == "unmatched"]
    failed = [c for c in cmds if _failed(c)]
    checks: list[Check] = []

    if sc.kind == "positive":
        checks.append(Check("Workflow executed commands", bool(cmds), f"{len(cmds)} commands"))
        checks.append(Check("Every executed step passed", not failed,
                            "; ".join(f"{c.node}: {c.command[:60]} — {c.validation_conclusion or c.failure_reason or c.validation_status}"
                                      for c in failed[:5])))
        checks.append(Check("Simulator answered every command", not unmatched,
                            "; ".join(str(e.get("input", ""))[:80] for e in unmatched[:5])))
        checks.append(Check("Run reported PASS", run_passed(result.overall_status), result.overall_status))
        result.actual = (f"{len(cmds)} steps ran, {len(failed)} failed; run {result.overall_status}")
    elif sc.kind == "rollback":
        outside = [c for c, i in zip(cmds, idx) if i is None or steps[i].phase_kind != "rollback"]
        checks.append(Check("Rollback steps ran", bool(cmds),
                            f"{len(cmds)} commands" if cmds else
                            "nothing ran: the rollback steps are skipped without values from earlier phases"))
        checks.append(Check("Only the rollback phase ran", not outside,
                            "; ".join(f"{c.node}: {c.command[:60]}" for c in outside[:5])))
        checks.append(Check("Every rollback step passed", not failed,
                            "; ".join(f"{c.node}: {c.command[:60]} — {c.validation_conclusion or c.failure_reason or c.validation_status}"
                                      for c in failed[:5])))
        checks.append(Check("Simulator answered every command", not unmatched,
                            "; ".join(str(e.get("input", ""))[:80] for e in unmatched[:5])))
        checks.append(Check("Run reported PASS", run_passed(result.overall_status), result.overall_status))
        result.actual = f"{len(cmds)} rollback steps ran, {len(failed)} failed; run {result.overall_status}"
    elif sc.fault and sc.fault.kind == "login":
        inst = node_instance.get(sc.fault.node, "")
        received = [e for e in result.events if e.get("instance") == inst
                    and not str(e.get("source", "")).startswith(("builtin:stty", "ctrl"))]
        node_ok = [c for c in cmds if c.node.lower() == sc.fault.node.lower() and not _failed(c)]
        checks.append(Check(f"{sc.fault.node} received no command", not received,
                            f"{len(received)} commands reached it"))
        checks.append(Check(f"No step on {sc.fault.node} succeeded", not node_ok,
                            "; ".join(c.command[:60] for c in node_ok[:3])))
        checks.append(Check("Run reported FAIL", not run_passed(result.overall_status), result.overall_status))
        if sc.policy in ("stop", "run"):
            later = [c for c, i in zip(cmds, idx) if i is not None and i > sc.fault.step_index]
            checks.append(Check("Nothing ran after the failed login", not later,
                                "; ".join(c.command[:60] for c in later[:3])))
        result.actual = f"{len(received)} commands reached {sc.fault.node}; run {result.overall_status}"
    else:
        fault_i = sc.fault.step_index
        at_fault = [c for c, i in zip(cmds, idx) if i == fault_i]
        later = [c for c, i in zip(cmds, idx) if i is not None and i > fault_i]
        reached = bool(at_fault)
        detected = reached and any(_failed(c) for c in at_fault)
        before_failed = [c for c, i in zip(cmds, idx) if i is not None and i < fault_i and _failed(c)]
        checks.append(Check("Faulted step was reached", reached,
                            "" if reached else "an earlier step failed first: "
                            + "; ".join(c.command[:60] for c in before_failed[:3])))
        checks.append(Check("Validation detected the fault", detected,
                            "" if detected or not reached else
                            f"the success regex accepted the failing output: {at_fault[0].output[:150]!r}"))
        if sc.contract and 0 <= fault_i < len(steps):
            checks += _contract_checks(steps[fault_i], steps, cmds, idx)
        elif sc.policy in ("stop", "run"):
            checks.append(Check(f"on_failure={sc.policy}: no later step ran", not later,
                                "; ".join(f"{c.node}: {c.command[:60]}" for c in later[:5])))
        else:
            has_later = any(s.index > fault_i for s in steps)
            checks.append(Check(f"on_failure={sc.policy}: later steps still ran", bool(later) or not has_later,
                                f"{len(later)} later steps ran"))
        checks.append(Check("Run reported FAIL", not run_passed(result.overall_status), result.overall_status))
        result.actual = (("fault detected" if detected else "fault NOT detected" if reached else "fault not reached")
                         + f"; {len(later)} later steps ran; run {result.overall_status}")

    result.checks = checks
    result.status = "PASS" if all(c.ok for c in checks) else "FAIL"
    if result.status == "FAIL":
        if sc.kind in ("positive", "rollback") or (checks and not checks[0].ok):
            verdict = classify_failures(cmds, result.events)
            result.classification = verdict.get("classification", "workflow")
        else:
            # The fault was delivered: a missed detection or wrong flow is the workflow's
            result.classification = "workflow"


def _contract_checks(step: WorkflowStep, steps: list[WorkflowStep], cmds: list[CommandResult],
                     idx: list[int | None]) -> list[Check]:
    """After a fault in ``step``: what the execution contract (spec 011) must make happen."""
    exp = contract_expectation(step, steps)
    ran = [(c, steps[i]) for c, i in zip(cmds, idx) if i is not None and i > step.index]
    same = [c for c, s in ran if s.phase == step.phase]
    rollback = [c for c, s in ran if s.phase_kind == "rollback"]
    other = [c for c, s in ran if s.phase != step.phase and s.phase_kind != "rollback"]
    checks = []
    if exp["phase_continues"]:
        checks.append(Check("The remaining health checks still ran", bool(same), f"{len(same)} ran"))
    elif step.phase_kind != "post":
        checks.append(Check("The phase stopped at the failure", not same,
                            "; ".join(f"{c.node}: {c.command[:60]}" for c in same[:3])))
    if not exp["later_phases_run"]:
        checks.append(Check("No later phase ran", not other,
                            "; ".join(f"{c.node}: {c.command[:60]}" for c in other[:3])))
    if exp["rollback"]:
        checks.append(Check("The rollback ran (the node was changed)", bool(rollback), f"{len(rollback)} rollback steps"))
    else:
        checks.append(Check("No rollback ran", not rollback,
                            "; ".join(f"{c.node}: {c.command[:60]}" for c in rollback[:3])))
    return checks


def _classify(results: list[ScenarioResult]) -> dict:
    failing = [r for r in results if r.status == "FAIL"]
    if not failing:
        return {}
    workflow = [f"{r.scenario.id} {r.scenario.title}: " + "; ".join(c.description for c in r.checks if not c.ok)
                for r in failing if r.classification != "simulator"]
    simulator = [f"{r.scenario.id} {r.scenario.title}: " + (r.error[:200] or "; ".join(
        c.description for c in r.checks if not c.ok)) for r in failing if r.classification == "simulator"]
    unmatched = sorted({str(e.get("input", "")) for r in failing for e in r.events
                        if e.get("source") == "unmatched"})
    return {
        "classification": "workflow" if workflow else "simulator",
        "retry_target": 6 if workflow else 8,
        "workflow_issues": workflow,
        "simulator_issues": simulator,
        "unmatched_commands": unmatched,
    }
