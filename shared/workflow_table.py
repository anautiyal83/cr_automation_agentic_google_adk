"""The workflow as a table of rows (spec 012, features H1–H4, H8).

A row is a step the AI (or the user) wrote, numbered within its phase. The steps the platform adds
(``nc_*``: backups by naming convention, checksums, record loops) are not rows: they are rebuilt
from the rows by the step 6 structure code after every change, so each phase only reports how many
it holds. Changes work on the flat form (``structure.flattened``) and ``structure.apply`` rebuilds
the file with the run's options (phases, repo route) — the same code step 6 runs.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field

_REC = re.compile(r"\$\{configSequences\.0\.records\.0\.data\.([^}]+)\}")


@dataclass
class Row:
    phase: str
    number: int                 # 1-based within the phase
    node: str = ""
    send: str = ""
    description: str = ""
    success: str = ""           # success regex
    condition: str = ""         # success expr
    captures: list[str] = field(default_factory=list)   # register regexes, one per capture
    when: str = ""
    on_failure: str = ""

    @property
    def captured_names(self) -> list[str]:
        return [n for rx in self.captures for n in re.findall(r"\(\?<(\w+)>", rx)]


@dataclass
class Phase:
    key: str
    name: str
    rows: list[Row]
    automatic: int              # steps the platform adds in this phase (locked)


def show(text: str) -> str:
    """A cell as a reviewer reads it: ``${configSequences.0.records.0.data.X}`` → ``<X>``."""
    return _REC.sub(lambda m: f"<{m.group(1)}>", str(text or ""))


def _criteria(step: dict) -> dict:
    validation = step.get("validation") if isinstance(step.get("validation"), dict) else {}
    success = validation.get("success") if isinstance(validation.get("success"), dict) else {}
    crit = success.get("criteria") if isinstance(success.get("criteria"), dict) else validation.get("criteria")
    return crit if isinstance(crit, dict) else {}


def _row(phase: str, number: int, step: dict) -> Row:
    crit = _criteria(step)
    regs = [r.get("regex", "") for r in step.get("register") or [] if isinstance(r, dict) and r.get("regex")]
    return Row(phase=phase, number=number, node=str(step.get("node") or ""), send=str(step.get("send") or ""),
               description=str(step.get("command_description") or ""), success=str(crit.get("regex") or ""),
               condition=str(crit.get("expr") or ""), captures=regs, when=str(step.get("when") or ""),
               on_failure=str(step.get("on_failure") or ""))


def _bodies(parsed: dict):
    for key, body in (parsed.get("phases") or {}).items():
        if isinstance(body, dict) and isinstance(body.get("steps"), list):
            yield key, body


def _count_auto(steps: list) -> int:
    n = 0
    for s in steps or []:
        if not isinstance(s, dict):
            continue
        if str(s.get("id", "")).startswith("nc_") and s.get("type") != "loop":
            n += 1
        n += _count_auto(s.get("steps"))
    return n


def table(text: str) -> list[Phase]:
    """The phases of a workflow with their rows (raises ValueError for text that is not a workflow)."""
    from skills.network_config_gen import structure
    from skills.network_config_gen.autofix import parse_yaml
    parsed = parse_yaml(text)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("phases"), dict):
        raise ValueError("not a workflow: no phases")
    flat = structure.flattened(parsed)
    auto = {key: _count_auto(body["steps"]) for key, body in _bodies(parsed)}
    return [Phase(key=key, name=str(body.get("name") or key), automatic=auto.get(key, 0),
                  rows=[_row(key, i, s) for i, s in enumerate((s for s in body["steps"] if isinstance(s, dict)), 1)])
            for key, body in _bodies(flat)]


def nodes(text: str) -> list[str]:
    from skills.network_config_gen.autofix import parse_yaml
    parsed = parse_yaml(text)
    return list((parsed.get("nodes") or {}).keys()) if isinstance(parsed, dict) else []


def _step_from(row: Row, base: dict | None) -> dict:
    step = copy.deepcopy(base) if base else {}
    step["node"] = row.node
    step["send"] = row.send
    for key, value in (("command_description", row.description), ("when", row.when), ("on_failure", row.on_failure)):
        if value:
            step[key] = value
        else:
            step.pop(key, None)
    if row.captures:
        step["register"] = [{"regex": rx} for rx in row.captures if rx.strip()]
    else:
        step.pop("register", None)
    if row.success or row.condition:
        validation = step.setdefault("validation", {}) if isinstance(step.get("validation"), dict) else {}
        step["validation"] = validation
        success = validation.setdefault("success", {}) if isinstance(validation.get("success"), dict) else {}
        validation["success"] = success
        crit = {}
        if row.success:
            crit["regex"] = row.success
        if row.condition:
            crit["expr"] = row.condition
        success["criteria"] = crit
        validation.pop("criteria", None)
    elif isinstance(step.get("validation"), dict):
        step["validation"].pop("criteria", None)
        if isinstance(step["validation"].get("success"), dict):
            step["validation"]["success"].pop("criteria", None)
    return step


def change(text: str, action: str, phase: str, number: int, row: Row | None, requirements: dict | None) -> str:
    """The workflow after ``action`` (``edit`` / ``add`` / ``delete``) on row ``number`` of ``phase``.
    ``add`` puts the row at ``number`` (1 = first, len+1 = at the end); the rows after it move down.
    The platform's steps are rebuilt (``structure.apply``) with the run's options."""
    from skills.network_config_gen import structure
    from skills.network_config_gen.autofix import dump_yaml, parse_yaml
    parsed = parse_yaml(text)
    flat = structure.flattened(parsed)
    bodies = dict(_bodies(flat))
    if phase not in bodies:
        raise ValueError(f"no phase {phase!r}")
    steps = [s for s in bodies[phase]["steps"] if isinstance(s, dict)]
    index = number - 1
    if action == "add":
        if not 0 <= index <= len(steps):
            raise ValueError(f"step number {number} is outside 1–{len(steps) + 1}")
        steps.insert(index, _step_from(row, None))
    elif action in ("edit", "delete"):
        if not 0 <= index < len(steps):
            raise ValueError(f"no step {number} in {phase}")
        if action == "edit":
            steps[index] = _step_from(row, steps[index])
        else:
            steps.pop(index)
    else:
        raise ValueError(f"unknown action {action!r}")
    bodies[phase]["steps"] = steps
    structure.apply(flat, requirements or {})
    return dump_yaml(flat)


def rebuild(text: str, requirements: dict | None) -> str:
    """A workflow edited as YAML, with the platform's steps rebuilt from its own (unchanged when the
    text is no workflow)."""
    from skills.network_config_gen import structure
    from skills.network_config_gen.autofix import dump_yaml, parse_yaml
    parsed = parse_yaml(text)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("phases"), dict):
        return text
    structure.apply(parsed, requirements or {})
    return dump_yaml(parsed)
