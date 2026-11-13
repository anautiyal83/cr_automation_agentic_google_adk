"""Read → change → read again (OB-59): a read the workflow repeats after the activity prints the changed state.

Run 6110bc53: ``org -c eirWhiteRng >…/predump_white_….txt`` (backup) and ``org -c eirWhiteRng >…/postdump_white_….txt``
(after ``dscli update file …``) got the same recorded answer, so the post dump never held the TACs the activity
loads from DELTA_TAC_FILE, and a check that they are there could not pass (and a check of the opposite did).

Deterministic: which reads repeat around the activity's commands on a node, and which of those commands may mark
the change (MockWire ``setState`` on its key; the read's key answers by ``whenState``). The LLM only says how the
output changes — unchanged, or one line per row of an input document (a template; the rows are expanded here, so
600 TACs never pass through the LLM) or a few literal lines.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_READS = 10       # per node, in the prompt
MAX_LINES = 5000     # added lines per read
STATE_PREFIX = "changed:"

SYSTEM = """\
You decide how the output of read-only commands on a simulated telecom node changes once the activity has run,
so a check after the activity sees the change. Output ONLY a YAML map, no markdown fences, no explanations.
Use single quotes for every string (double any single quote inside).
"""

USER = """\
UC: {uc_name}
Node: {node}

Each READ below is sent before the activity and again after it (the later output is what the post checks see).
For each, answer with its number as the key:

'<n>':
  unchanged: true                        # the activity does not change what this read prints
or
'<n>':
  changed_by: ['<the command from its list that applies the change>']
  add_rows: {{file: '<CIQ column>', sheet: '<sheet>', where: {{'<header>': '<value>'}}}}  # rows the activity loads
  line: '<ONE output line exactly in the read's format, with {{<header>}} for each value from the row>'
  remove_matching: '<python regex of lines the activity removes>'    # optional
or, when no input document lists what changes:
'<n>':
  changed_by: ['<command>']
  add_lines: ['<output line>', ...]       # at most 10
or, when the activity modifies values the read prints (a profile / attributes, values from the CIQ):
'<n>':
  changed_by: ['<command>']               # [] when the change is applied on another node
  replace:
  - {{match: '<python regex of the line before>', with: '<the line after the activity>'}}   # one per changed line

{reads}

=== INPUT FILES OF THE ACTIVITY (CIQ file columns; the uploaded document of that kind) ===
{documents}

=== CIQ VALUES (first record) ===
{ciq}
"""


_SETUP = {"source", ".", "export", "cd", "set", "unset", "umask"}  # load the environment: neither read nor change


def _kind(phase: str) -> str:
    p = str(phase or "").lower()
    if "rollback" in p:
        return "rollback"
    return "activity" if "configuration" in p else "other"


def _bare(cmd: str) -> str:
    """The read without its ``>file`` (``org -c eirWhiteRng >/p/predump.txt`` → ``org -c eirWhiteRng``)."""
    from .vfs_commands import redirect_targets
    cmd = str(cmd or "").strip()
    if redirect_targets(cmd) and not re.search(r";|&&|\|\|", cmd):
        return re.split(r"(?<![\d&>])>", cmd, maxsplit=1)[0].strip()
    return cmd


def repeated_reads(node, entries: dict, row: dict, others: list[dict] | None = None) -> list[dict]:
    """Reads sent again after activity commands on ``node`` (rollback phases left out: neither run in a sunny
    day): {bare, key, lines, setters: [{cmd, key, description}]}. A setter runs only between the two reads and
    has a key of its own (no command outside that span answers by it), so the state cannot change early.

    With no setter on ``node``, a read repeated around another node's activity (``others``, by workflow
    position: RDS read → order uploaded to PGW → RDS read again, OB-84) gets ``cross`` (those commands) and
    ``n_pre`` (how often it is read before them)."""
    from .skill import _SIM_BUILTINS, _find_entry, resolve_command
    from .topology import _enters_session
    seq = [c for c in node.commands if _kind(c.get("phase", "")) != "rollback" and str(c.get("cmd") or "").strip()]
    keys = [_find_entry(entries, str(c["cmd"]).strip(), resolve_command(str(c["cmd"]).strip(), row))[0]
            for c in seq]
    # a command the rollback sends too (the same `dscli update file …`, run b81305ff) would mark the change in a
    # rollback-only run, where the activity never ran (OB-63)
    rollback = [str(c["cmd"]).strip() for c in node.commands
                if _kind(c.get("phase", "")) == "rollback" and str(c.get("cmd") or "").strip()]
    rollback_keys = {_find_entry(entries, cmd, resolve_command(cmd, row))[0] for cmd in rollback}
    out, done = [], set()
    for i, c in enumerate(seq):
        bare = _bare(c["cmd"])
        if bare in done or bare.split()[0] in _SIM_BUILTINS | _SETUP or bare in node.vfs_commands:
            continue
        done.add(bare)
        occ = [k for k, d in enumerate(seq) if _bare(d["cmd"]) == bare]
        key, entry = _find_entry(entries, bare, resolve_command(bare, row))
        if key is None:  # the LLM keyed it with its redirect
            full = str(c["cmd"]).strip()
            key, entry = _find_entry(entries, full, resolve_command(full, row))
        if (len(occ) < 2 or not isinstance(entry, dict) or not isinstance(entry.get("lines"), list)
                or not any(str(x).strip() for x in entry["lines"])):
            continue
        for a, b in zip(occ, occ[1:]):
            setters = []
            for t in range(a + 1, b):
                cmd = str(seq[t]["cmd"]).strip()
                k = keys[t]
                if (k is None or _kind(seq[t].get("phase", "")) != "activity" or cmd in node.vfs_commands
                        or _enters_session(cmd) or cmd.split()[0] in _SIM_BUILTINS | _SETUP
                        or any(keys[o] == k for o in range(len(seq)) if not a < o < b)
                        or any(s["key"] == k for s in setters)):
                    continue
                setters.append({"cmd": cmd, "key": k, "description": seq[t].get("description", ""),
                                "rollback": k in rollback_keys})
            if setters:
                out.append({"bare": bare, "key": key, "lines": [str(x) for x in entry["lines"]], "setters": setters,
                            "dump": bare != str(c["cmd"]).strip()})
                break
            lo, hi = seq[a].get("order"), seq[b].get("order")
            cross = [o for o in others or [] if lo is not None and hi is not None
                     and _kind(o.get("phase", "")) == "activity" and lo < (o.get("order") or -1) < hi
                     and (o.get("transfer") or str(o.get("cmd") or "").strip())]
            if cross:
                first = min(o["order"] for o in cross)
                out.append({"bare": bare, "key": key, "lines": [str(x) for x in entry["lines"]], "setters": [],
                            "dump": bare != str(c["cmd"]).strip(), "cross": cross[:8],
                            "n_pre": sum(1 for k in occ if (seq[k].get("order") or 0) < first)})
                break
    out.sort(key=lambda r: not r["dump"])  # reads saved to a file (pre / post dumps) first
    return out[:MAX_READS]


def _documents(row: dict, documents: list[str] | None) -> dict[str, str]:
    """{CIQ column: uploaded document} for the CIQ file columns."""
    from tools.input_documents import sample_for
    out = {}
    for col, value in row.items():
        v = str(value or "").strip()
        if re.fullmatch(r"[\w.\-]+\.[A-Za-z][A-Za-z0-9]{1,5}", v):
            doc = sample_for(v, documents)
            if doc:
                out[str(col)] = doc
    return out


def expand(answer: dict, pre: list[str], docs: dict[str, str]) -> list[str] | None:
    """The read's output after the activity; None when unchanged (or the answer is unusable)."""
    from tools.input_documents import read_tables
    if not isinstance(answer, dict) or answer.get("unchanged") is True:
        return None
    lines = list(pre)
    pattern = answer.get("remove_matching")
    if isinstance(pattern, str) and pattern:
        try:
            rx = re.compile(pattern)
            lines = [l for l in lines if not rx.search(l)]
        except re.error:
            pass
    added: list[str] = []
    rows, template = answer.get("add_rows"), answer.get("line")
    if isinstance(rows, dict) and isinstance(template, str) and template.strip():
        doc = docs.get(str(rows.get("file") or "").strip())
        tables = read_tables(doc) if doc else {}
        sheet = str(rows.get("sheet") or "")
        table = tables.get(sheet) or next((t for s, t in tables.items() if s.lower() == sheet.lower()), None) \
            or (next(iter(tables.values())) if len(tables) == 1 else None)
        if table and len(table) > 1:
            header = [h.strip() for h in table[0]]
            where = rows.get("where") if isinstance(rows.get("where"), dict) else {}
            for r in table[1:]:
                values = dict(zip(header, r))
                if all(str(values.get(str(k).strip(), "")).strip().lower() == str(v).strip().lower()
                       for k, v in where.items()):
                    added.append(re.sub(r"\{([^{}]+)\}", lambda m: values.get(m.group(1).strip(), m.group(0)),
                                        template))
                if len(added) >= MAX_LINES:
                    break
    literal = answer.get("add_lines")
    if isinstance(literal, list):
        added += [str(l) for l in literal[:10]]
    for item in answer.get("replace") or [] if isinstance(answer.get("replace"), list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("with"), str):
            continue
        try:
            rx = re.compile(str(item.get("match") or "^$(?!)"))
        except re.error:
            continue
        hit = [i for i, l in enumerate(lines) if rx.search(l)]
        for i in hit:
            lines[i] = item["with"]
        if not hit:  # an attribute the activity adds
            added.append(item["with"])
    if not added and lines == pre:
        return None
    return lines + [l for l in added if l not in lines]


async def apply(llm_fn, uc_name: str, node, entries: dict, row: dict, documents: list[str] | None,
                others: list[dict] | None = None) -> dict:
    """``entries`` with each repeated read answering its changed output once the activity has run (``others``:
    the other nodes' commands, for a change applied there)."""
    import yaml
    from .autofix import parse_llm_yaml
    reads = repeated_reads(node, entries, row, others)
    if not reads:
        return entries
    docs = _documents(row, documents)
    logger.info("Phase 2: %s — %d reads repeated after activity commands; input documents: %s", node.name,
                len(reads), ", ".join(f"{c}={Path(d).name}" for c, d in docs.items()) or "none")
    from tools.input_documents import preview
    listing = []
    for n, r in enumerate(reads, 1):
        listing.append(f"READ {n}: {r['bare']}\n  before the activity it prints ({len(r['lines'])} lines; first 8):\n"
                       + "\n".join(f"    {l}" for l in r["lines"][:8])
                       + ("\n  activity commands on this node between the two reads (changed_by is one of these):\n"
                          + "\n".join(f"    - {s['cmd']}" + (f"   ({s['description']})" if s["description"] else "")
                                      for s in r["setters"]) if r["setters"] else
                          "\n  between the two reads the activity runs on OTHER nodes (changed_by: []):\n"
                          + "\n".join(f"    - {o.get('node')}: {o.get('description') or o.get('cmd') or 'file transfer'}"
                                      for o in r.get("cross") or [])))
    user = USER.format(uc_name=uc_name, node=node.name, reads="\n\n".join(listing),
                       documents="\n".join(f"{col} = {Path(d).name}:\n{preview(d)}" for col, d in docs.items())
                       or "(none uploaded)",
                       ciq=yaml.safe_dump(row, sort_keys=False, allow_unicode=True)[:1500])
    try:
        answers = parse_llm_yaml(await llm_fn(SYSTEM, user))
    except Exception as e:  # noqa: BLE001 — the reads keep their single answer
        logger.warning("Phase 2: %s — changed-state answers failed: %s", node.name, e)
        return entries
    if not isinstance(answers, dict):
        logger.warning("Phase 2: %s — changed-state answer is no YAML map; %d repeated reads keep one answer",
                       node.name, len(reads))
        return entries
    entries = dict(entries)
    for n, r in enumerate(reads, 1):
        answer = answers.get(str(n), answers.get(n))
        after = expand(answer, r["lines"], docs)
        if after is None:
            logger.info("Phase 2: %s — %r is unchanged by the activity (%s)", node.name, r["bare"][:60],
                        "as answered" if isinstance(answer, dict) and answer.get("unchanged") is True
                        else f"unusable answer {str(answer)[:200]!r}")
            continue
        if r.get("cross"):
            # no command here marks the change: the read itself counts — before for its n_pre reads before the other
            # nodes' activity, after from then on (MockWire applies setState of the answer it picks)
            state, n_pre = f"{STATE_PREFIX}read{n}", max(1, int(r.get("n_pre") or 1))
            rest = {k: v for k, v in entries[r["key"]].items() if k != "lines"}
            chain = [{"whenState": {state: str(n_pre)}, "lines": after}]
            chain += [{"whenState": {state: str(k)}, "lines": r["lines"], "setState": {state: str(k + 1)}}
                      for k in range(1, n_pre)]
            chain.append({"lines": r["lines"], "setState": {state: "1"}})
            entries[r["key"]] = {**rest, "responses": chain}
            logger.info("Phase 2: %s — %r changes after the activity on %s (read %d time(s) before)", node.name,
                        r["bare"][:60], ", ".join(sorted({str(o.get('node')) for o in r["cross"]})), n_pre)
            continue
        named = [s for s in r["setters"] if s["cmd"] in [str(c).strip() for c in (answer.get("changed_by") or [])]]
        # the named command, unless the rollback sends it too; then every activity-only command between the reads
        chosen = [s for s in named if not s["rollback"]] or [s for s in r["setters"] if not s["rollback"]]
        if not chosen:
            logger.warning("Phase 2: %s — %r: every command between the reads is also sent by the rollback; "
                           "it keeps one answer", node.name, r["bare"][:60])
            continue
        state = f"{STATE_PREFIX}{n}"
        for s in chosen:
            entry = dict(entries[s["key"]])
            entry["setState"] = {**(entry.get("setState") or {}), state: "yes"}
            entries[s["key"]] = entry
        rest = {k: v for k, v in entries[r["key"]].items() if k != "lines"}
        entries[r["key"]] = {**rest, "responses": [{"whenState": {state: "yes"}, "lines": after},
                                                   {"lines": r["lines"]}]}
        logger.info("Phase 2: %s — %r prints %d more lines after %s", node.name, r["bare"],
                    len(after) - len(r["lines"]), ", ".join(s["cmd"][:40] for s in chosen))
    return entries
