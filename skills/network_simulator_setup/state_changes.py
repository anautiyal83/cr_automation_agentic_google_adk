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
RECORDS_SUFFIX = ":records"  # a read chain that every CIQ record passes through in turn (OB-96)

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
  In `with`, a value the activity takes from the CIQ is written {{ciq:<CIQ column>}} (e.g. 'scscfMandCap:
  {{ciq:Mandatory Cap}}'), never the CIQ cell itself: each test case fills it from its own CIQ record, and a cell
  'UNCHANGED' / blank cells are applied by the simulator (the UC's confirmed cell rules). List a replace for EVERY attribute the activity can set from
  the CIQ (each column the request carries, e.g. VNI → netwListId), also when the record shown below keeps it:
  other records change it.

{reads}

=== INPUT FILES OF THE ACTIVITY (CIQ file columns; the uploaded document of that kind) ===
{documents}

=== CIQ VALUES (first record) ===
{ciq}
"""


_SETUP = {"source", ".", "export", "cd", "set", "unset", "umask"}  # load the environment: neither read nor change

# A post-activity line holding CIQ values (OB-87, run 61561981: one after-profile for every test case, but EAST
# expects Mandatory Cap 10 and NORTH other values; the LLM wrote CIQ cells — `UNCHANGED` — as values):
# `scscfMandCap: {ciq:Mandatory Cap}{ciq-else:scscfMandCap: 1}` — step 9 fills it from the case's CIQ record
# (`resolve_ciq`); a cell `UNCHANGED` keeps the line as it was, a blank cell deletes the attribute
CIQ_TOKEN = re.compile(r"\{ciq:([^{}]+)\}")
CIQ_ELSE = "{{ciq-else:{}}}"
_CIQ_ELSE = re.compile(r"\{ciq-else:(.*)\}$", re.S)
_ATTR_LINE = re.compile(r"^\s*([A-Za-z][\w-]*)\s*:\s")


def attribute_for(column: str, attributes: list[str]) -> str | None:
    """The attribute a CIQ column sets, by name: `Packet Data Network Type` → `pdnType` (initials + last word),
    `Mandatory Cap` → `scscfMandCap` (each word's first letters, in order; a same-named attribute first). None unless exactly one attribute fits."""
    words = [w.lower() for w in re.findall(r"[A-Za-z]+", column)]
    if not words:
        return None
    acronym = "".join(w[0] for w in words[:-1]) + words[-1]
    prefixes = [w[:3] for w in words]
    exact = [a for a in attributes if a.lower() == re.sub(r"[^a-z]", "", column.lower())]
    if exact:
        return exact[0]

    def fits(attr: str) -> bool:
        a = attr.lower()
        if len(words) > 1 and acronym in a:
            return True
        pos = 0
        for pre in prefixes:
            pos = a.find(pre, pos)
            if pos < 0:
                return False
            pos += len(pre)
        return True
    found = [a for a in dict.fromkeys(attributes) if fits(a)]
    return found[0] if len(found) == 1 else None


_COMPARED = re.compile(r"<\$?([^<>]{1,40})>\s*==\s*([A-Za-z][\w-]*)")


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def column_attributes(requirements: dict) -> dict[str, str]:
    """{normalised CIQ column: attribute} from the requirements (documents, answers, MOP pseudo code) comparing the two
    (`IF (<$IFC> == globalFilterIds)`, run 6570694d: no name links IFC to globalFilterIds, OB-96)."""
    out: dict[str, str] = {}

    def walk(value):
        if isinstance(value, str):
            for col, attr in _COMPARED.findall(value):
                out.setdefault(_norm(col), attr)
        elif isinstance(value, dict):
            # step 1's `ciq_field_mappings` pairs (`{ciq_column: APN, node_field: pdnAccPointName}`: APN is compared
            # nowhere in the MOP, OB-101)
            col, attr = value.get("ciq_column"), value.get("node_field")
            if isinstance(col, str) and isinstance(attr, str) and re.fullmatch(r"[A-Za-z][\w-]*", attr.strip()):
                out.setdefault(_norm(col), attr.strip())
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
    walk(requirements or {})  # the MOP's pseudo code sits in generation_hints
    return out


def complete_columns(after: list[str], before: list[str], sent: list[str], read: str,
                     mapping: dict[str, str] | None = None) -> list[str]:
    """The LLM left a column the activity sends out of a cross-node read's after-profile (run 6570694d: no
    `pdnType`, so a blank cell never deleted it): the attribute that column names gets its `{ciq:<column>}` line —
    not a column the read itself is addressed by (the IMSI it looks up), not one already there (OB-95). The
    attribute is the one the documents compare the column with (``column_attributes``), else the one it names.
    A documented attribute the recorded subscriber does not have (no `scscfMandCap` in the sample profile) is added
    after its last attribute line: the activity sets it (run 6570694d, OB-101)."""
    covered = {c for line in after for c in CIQ_TOKEN.findall(line)}
    out = list(after)
    attrs = [m.group(1) for line in before if (m := _ATTR_LINE.match(line))]
    for col in sent:
        if col in covered or f".data.{col}" in read or f"data.{col}}}" in read:
            continue
        named = (mapping or {}).get(_norm(col))
        attr = named if named in attrs else attribute_for(col, attrs)
        if not attr and named:
            last = max((i for i, line in enumerate(out) if _ATTR_LINE.match(line)), default=len(out) - 1)
            out.insert(last + 1, f"{named}: {{ciq:{col}}}")
            continue
        if not attr:
            continue
        for i, line in enumerate(out):  # every line of it (one per PDN context in a full profile)
            m = _ATTR_LINE.match(line)
            if m and m.group(1) == attr and "{ciq" not in line:
                out[i] = f"{attr}: {{ciq:{col}}}" + CIQ_ELSE.format(line)
    return out


def resolve_line(line: str, record: dict, rules: dict | None = None) -> str | None:
    """A post-activity line for one CIQ record; None when it is to be left out. A cell ``UNCHANGED`` / blank follows
    the UC's confirmed cell rules (``shared/ciq_cells``: keep → the line as it was, delete → no line)."""
    from shared import ciq_cells
    if "{ciq" not in line:
        return line
    rules = {**ciq_cells.DEFAULT, **(rules or {})}
    m = _CIQ_ELSE.search(line)
    body, before = (line[:m.start()], m.group(1)) if m else (line, None)
    values = {col: record.get(col, record.get(col.strip())) for col in CIQ_TOKEN.findall(body)}
    kinds = {ciq_cells.kind(v) for v in values.values()}
    for cell in ("unchanged", "blank"):
        if cell in kinds:
            return (before or None) if rules[cell] == "keep" else None
    return CIQ_TOKEN.sub(lambda t: str(values.get(t.group(1)) or "").strip(), body)


def expand_records(config: dict, records: list[dict], rules: dict | None = None) -> bool:
    """A read chain over CIQ record loops (``…:records:<segments>``, one entry per place the read is sent) becomes
    the sequence the run sends it in — each loop's places once per record, a place outside loops once (the last
    record's values) — the after-lines filled with that record's values (in place); True when something changed."""
    changed = False
    marker = re.compile(re.escape(RECORDS_SUFFIX) + r":((?:[LO]\d+\.?)+)$")

    def walk(node):
        nonlocal changed
        if isinstance(node, dict):
            chain = node.get("responses")
            keys = {k for r in chain or [] if isinstance(r, dict) for k in (r.get("whenState") or {})
                    if marker.search(str(k))} if isinstance(chain, list) else set()
            if len(keys) == 1:
                state = keys.pop()
                places = {0: next((r.get("lines") or [] for r in chain if not r.get("whenState")), [])}
                for r in chain:
                    if (r.get("whenState") or {}).get(state) is not None:
                        places[int(r["whenState"][state])] = r.get("lines") or []
                recs, events, base = records or [{}], [], 0
                for seg in marker.search(state).group(1).split("."):
                    n = int(seg[1:])
                    if seg[0] == "L":
                        events += [(base + k, i) for i in range(len(recs)) for k in range(n)]
                    else:
                        events += [(base + k, len(recs) - 1) for k in range(n)]
                    base += n

                def lines(g):
                    place, rec = events[g]
                    filled = [resolve_line(str(x), recs[rec], rules) for x in places.get(place, [])]
                    return [x for x in filled if x is not None]
                out = [{"whenState": {state: str(g)}, "lines": lines(g)}
                       | ({"setState": {state: str(g + 1)}} if g + 1 < len(events) else {})
                       for g in range(1, len(events))]
                out.append({"lines": lines(0), "setState": {state: "1"}})
                node["responses"] = out
                changed = True
                return
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(config)
    return changed


def resolve_ciq(config: dict, record: dict, rules: dict | None = None) -> bool:
    """Fill every CIQ line of an instance config (in place) for one CIQ record; True when something changed."""
    changed = False

    def walk(node):
        nonlocal changed
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "lines" and isinstance(v, list) and any("{ciq" in str(x) for x in v):
                    filled = [resolve_line(str(x), record, rules) for x in v]
                    node[k] = [x for x in filled if x is not None]
                    changed = True
                else:
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(config)
    return changed


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


def _stages(cmd: str) -> list[str]:
    """The pipeline's stages: ``|`` outside quotes, not ``||``."""
    out, cur, quote = [], "", ""
    for i, ch in enumerate(cmd):
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif ch == "|" and "|" not in (cmd[i - 1:i], cmd[i + 1:i + 2]):
            out.append(cur.strip())
            cur = ""
            continue
        cur += ch
    return out + [cur.strip()]


def _unquoted(text: str) -> str:
    return re.sub(r"'[^']*'|\"[^\"]*\"", "", text)


def answering_key(entries: dict, cmd: str, row: dict) -> str | None:
    """The key MockWire answers ``cmd`` by (``CommandRouter.findStrict``): the whole line — unless a regex key's
    match leaves a pipe / redirect / ``;`` / ``&`` after it — else the pipeline's first stage. The full
    ``ldapsearch … objectclass=*`` and the same read ``| egrep '…'`` are ONE read for MockWire: as two reads with a
    chain each, the first key answered all four reads per record and ran ahead (run 6570694d, OB-101)."""
    from .skill import _find_entry, _py_regex, resolve_command
    text = _bare(cmd)
    stages = _stages(text)
    for cand in [text] + (stages[:1] if len(stages) > 1 else []):
        resolved = resolve_command(cand, row)
        key, _ = _find_entry(entries, cand, resolved)
        if key is None:
            continue
        if str(key).startswith("regex:"):
            rx = _py_regex(str(key)[6:])
            m = rx.search(resolved) if rx is not None else None
            if m is not None and re.search(r"[|;>&]", _unquoted(resolved[m.end():])):
                continue
        return key
    return None


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
    answered = [answering_key(entries, str(c["cmd"]).strip(), row) for c in seq]

    def read_of(k: int) -> str:  # what MockWire answers it by (OB-101), else its text
        return answered[k] or _bare(seq[k]["cmd"])

    out, done = [], set()
    for i, c in enumerate(seq):
        bare = _bare(c["cmd"])
        if read_of(i) in done or bare.split()[0] in _SIM_BUILTINS | _SETUP or bare in node.vfs_commands:
            continue
        done.add(read_of(i))
        occ = [k for k in range(len(seq)) if read_of(k) == read_of(i)]
        key, entry = (answered[i], entries[answered[i]]) if answered[i] else \
            _find_entry(entries, bare, resolve_command(bare, row))
        if key is None:  # the LLM keyed it with its redirect
            full = str(c["cmd"]).strip()
            key, entry = _find_entry(entries, full, resolve_command(full, row))
        if (len(occ) < 2 or not isinstance(entry, dict) or not isinstance(entry.get("lines"), list)
                or not any(str(x).strip() for x in entry["lines"])):
            continue
        for a, b in zip(occ, occ[1:]):
            # another node's activity between the two reads applies the change (the order uploaded to PGW, read
            # back on RDS — OB-84): the read itself counts, whatever runs on its own node in between (run
            # 61561981: an `if [[ IFC … ]]` lookup there was taken as the change and never ran). A repository
            # server only stores files: its transfers change nothing
            lo, hi = seq[a].get("order"), seq[b].get("order")
            cross = [o for o in others or [] if lo is not None and hi is not None
                     and "repo" not in str(o.get("node") or "").lower()
                     and _kind(o.get("phase", "")) == "activity" and lo < (o.get("order") or -1) < hi
                     and (o.get("transfer") or str(o.get("cmd") or "").strip())]
            if cross:
                first = min(o["order"] for o in cross)
                # reads in CIQ record loops run once per record (OB-96): `L<n>` = n reads in one record loop
                # (each record passes them in turn), `O<n>` = n reads outside; the read before / after the change
                # by its place
                segments: list[list] = []
                for k in occ:
                    loop = seq[k].get("loop") or ""
                    if segments and segments[-1][0] == loop:
                        segments[-1][1] += 1
                    else:
                        segments.append([loop, 1])
                out.append({"bare": bare, "key": key, "lines": [str(x) for x in entry["lines"]], "setters": [],
                            "dump": bare != str(c["cmd"]).strip(), "cross": cross[:8],
                            "n_pre": sum(1 for k in occ if (seq[k].get("order") or 0) < first),
                            "segments": ".".join(f"{'L' if lp else 'O'}{n}" for lp, n in segments)
                            if any(lp for lp, _ in segments) else ""})
                break
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
        for i in hit:  # a CIQ value: filled per test case, the line as it was when the cell keeps it (OB-87)
            lines[i] = item["with"] + (CIQ_ELSE.format(lines[i]) if CIQ_TOKEN.search(item["with"]) else "")
        if not hit:  # an attribute the activity adds
            added.append(item["with"] + (CIQ_ELSE.format("") if CIQ_TOKEN.search(item["with"]) else ""))
    if not added and lines == pre:
        return None
    return lines + [l for l in added if l not in lines]


async def apply(llm_fn, uc_name: str, node, entries: dict, row: dict, documents: list[str] | None,
                others: list[dict] | None = None, workflow_commands: list[dict] | None = None,
                mapping: dict[str, str] | None = None) -> dict:
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
    # the CIQ columns the activity sends (`${rec.data.X}` in its commands, on any node): each one a cross-node read
    # prints needs its replace — the LLM left `Packet Data Network Type` → pdnType out on one draft (OB-87)
    sent = sorted({m for c in [*node.commands, *(others or []), *(workflow_commands or [])]
                   if _kind(c.get("phase", "")) == "activity"
                   for m in re.findall(r"\$\{(?:rec|configSequences\.\d+\.records\.\d+)\.data\.([^}]+)\}",
                                       str(c.get("cmd") or ""))})
    if sent and any(r.get("cross") for r in reads):
        named = {c: (mapping or {}).get(_norm(c)) for c in sent}
        listing.append("CIQ COLUMNS THE ACTIVITY SENDS (give a replace for EACH one a read above prints — map the "
                       "column to the attribute that shows it): "
                       + ", ".join(f"{c} (→ {named[c]}, as the documents compare them)" if named[c] else c
                                   for c in sent))
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
        if r.get("cross"):
            # the columns the activity sends, also when the LLM called the read unchanged (run 6570694d: the full
            # profile kept `pdnType` after a blank cell deleted it in the filtered read, OB-95)
            completed = complete_columns(list(after if after is not None else r["lines"]), r["lines"], sent, r["bare"],
                                         mapping)
            after = completed if completed != r["lines"] else after
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
            if r.get("segments"):
                # read in CIQ record loops: one entry per place it is read, in order (`…:records:<segments>`:
                # step 9 repeats each loop's places per record, the after-lines filled with that record's values —
                # ``expand_records``); unexpanded, the places come round again
                state += f"{RECORDS_SUFFIX}:{r['segments']}"
                places = sum(int(x[1:]) for x in r["segments"].split("."))
                chain = [{"whenState": {state: str(k)}, "lines": r["lines"] if k < n_pre else after,
                          "setState": {state: str((k + 1) % places)}} for k in range(1, places)]
                chain.append({"lines": r["lines"], "setState": {state: str(1 % places)}})
            else:
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
