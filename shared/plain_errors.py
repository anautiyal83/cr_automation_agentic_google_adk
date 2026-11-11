"""Why a step failed, said in terms of the user's own inputs, for the review screen.

The error files hold messages written for the LLM's correction rounds (regexes, placeholders,
registers). A reviewer needs to know what is missing or unclear in what they provided — the CIQ,
the MOP, the session logs (command outputs), or where a value comes from — and which problems are
the AI's own, fixed by a re-run. The technical text stays available under "Technical details".
"""
from __future__ import annotations

import re

CIQ = "CIQ"
MOP = "MOP"
LOGS = "Session logs (command outputs)"
SOURCE = "Where a value comes from"
AI = "Nothing to change in your documents (Re-run fixes it)"
OTHER = "Other"
SECTION_ORDER = (CIQ, MOP, LOGS, SOURCE, AI, OTHER)

_STEP_TITLES = {
    1: "Step 1 could not read the documents",
    2: "Step 2 could not complete the requirements",
    3: "Step 3 could not write the CIQ validation rules",
    4: "Step 4 could not build the CIQ JSON",
    5: "QA-Validation found problems",
    6: "Step 6 could not build a working workflow",
    7: "QA-MOP found problems",
    8: "Step 8 could not set up the simulator",
    9: "QA-Execution: the workflow did not run cleanly",
}


def split_reasons(error: str) -> list[str]:
    """The separate problems in an error text: joined with '; ' before a new sentence, or bullets."""
    parts = re.split(r"\n\s*•\s*|;\s+(?=[A-Z])", error or "")
    return [p.strip() for p in parts if p and p.strip()]


def _cmd(text: str, limit: int = 45) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _names(names) -> str:
    seen = list(dict.fromkeys(names))
    return ", ".join(seen[:6]) + (f" and {len(seen) - 6} more" if len(seen) > 6 else "")


def _real_output(text: str) -> str:
    """The first line of the recorded output quoted in a message, short."""
    m = re.search(r"Real output[^:]*:\s*\n(.*)", text, re.S)
    lines = [ln.strip() for ln in (m.group(1).splitlines() if m else []) if ln.strip()]
    lines = [ln for ln in lines if not ln.lower().startswith("write the regex")]
    # a line with words, not JSON punctuation ("}," / "\"size\": 1")
    worded = [ln for ln in lines if len(re.findall(r"[A-Za-z]", ln)) >= 4 and not ln.startswith(('"', "{", "["))]
    return _cmd((worded or lines)[0], 60) if lines else ""


def summarize(step: int, reasons: list[str], unmatched_commands: list[str] | None = None
              ) -> tuple[str, list[tuple[str, list[str]]], str]:
    """(headline, [(section, lines)], what the user can do) for the technical ``reasons`` of ``step``."""
    found: dict[str, list[str]] = {s: [] for s in SECTION_ORDER}
    no_source: list[str] = []
    arrays: list[str] = []
    rows_without: list[str] = []
    code_checks = 0
    unread_values: list[str] = []
    seen_checks: set[str] = set()

    for raw in reasons:
        text = " ".join(raw.split())
        if m := re.match(r"Unresolvable placeholder \$\{([A-Za-z_]\w*)(\[)?", text):
            (arrays if m.group(2) else no_source).append(m.group(1))
        elif m := re.search(r"uses \$\{([A-Za-z_]\w*)\} but no step that runs for that row registers it", text):
            rows_without.append(m.group(1))
        elif m := re.match(r"Step '(.+?)' \([^)]*\): validation regex .* does not match the real output", text):
            cmd = _cmd(m.group(1))
            if cmd in seen_checks:
                continue
            seen_checks.add(cmd)
            shown = _real_output(raw)
            found[MOP].append(f"`{cmd}`: what does a successful result look like? "
                              + (f"Your session log shows “{shown}”, which does not match the expected result."
                                 if shown else "Its expected result does not match your session log."))
        elif m := re.match(r"Step '(.+?)' \([^)]*\): register regex .*?\(\?<(\w+)>.* captures nothing", text):
            found[LOGS].append(f"`{_cmd(m.group(1))}`: the workflow needs {m.group(2)} from this command, but its "
                               f"recorded output has no such value. Add an output that shows it, or say where "
                               f"{m.group(2)} comes from.")
        elif m := re.search(r"uses CIQ column '([^']+)', which is empty in (\d+) of (\d+)", text):
            found[CIQ].append(f"Column {m.group(1)} is empty in {m.group(2)} of {m.group(3)} rows. Fill it in, or "
                              "say when it may be left empty.")
        elif m := re.search(r"uses CIQ column '([^']+)', which is empty", text):
            found[CIQ].append(f"Column {m.group(1)} is empty in some rows. Fill it in, or say when it may be left empty.")
        elif "is not something criteria.expr can evaluate" in text:
            code_checks += 1
        elif m := re.search(r"expr '.*' uses (.+?), which no step registers", text):
            unread_values += [v.strip() for v in m.group(1).split(",")]
        elif m := re.match(r"(TC-[A-Z]\d+)\s+(.+?):\s+(.+)", text):
            first = re.split(r";\s+", m.group(3))[0]
            found[AI].append(f"Test “{m.group(2)}” failed: {_cmd(first, 110)}")
        elif re.match(r"\d+/\d+ tests? failed", text):
            continue  # the headline says it
        else:
            found[OTHER].append(_cmd(re.split(r"(?<=[.!?])\s", text)[0], 160))

    if no_source:
        found[SOURCE].append(f"{_names(no_source)}: say where each value comes from — a CIQ column, a command's "
                             "output, or a fixed value.")
    if rows_without:
        found[SOURCE].append(f"{_names(rows_without)}: for some CIQ rows nothing provides this value — say where it "
                             "comes from for every row.")
    if unread_values:
        found[SOURCE].append(f"{_names(unread_values)}: a check needs these values — say which command's output "
                             "shows them.")
    for cmd in unmatched_commands or []:
        found[LOGS].append(f"`{_cmd(cmd)}`: there is no recorded output for this command. Add it to the session "
                           "logs (a node transcript showing the command and its output).")
    if arrays:
        found[AI].insert(0, f"The AI used shell arrays the framework cannot run ({_names(arrays)}).")
    if code_checks:
        found[AI].insert(0, f"The AI wrote {code_checks} check(s) as program code the framework cannot run.")

    sections = [(s, list(dict.fromkeys(found[s]))) for s in SECTION_ORDER if found[s]]
    to_check = sum(len(lines) for s, lines in sections if s not in (AI, OTHER))
    title = _STEP_TITLES.get(step, f"Step {step} failed")
    if to_check:
        headline = (f"{title} — {to_check} thing{'s' if to_check != 1 else ''} to check in your documents")
        action = "Edit requirements to add what is missing (or upload corrected documents), or Re-run step to " \
                 "let the AI try again."
    else:
        headline = f"{title} — nothing missing in your documents"
        action = "Re-run step: the AI tries again with these problems." + (
            " Or Skip & continue to accept the findings." if step in (5, 7, 9) else "")
    return headline, sections, action
