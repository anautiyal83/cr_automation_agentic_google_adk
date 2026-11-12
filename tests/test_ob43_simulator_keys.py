"""OB-43: EIR step 9 — loops over a registered list, LLM keys for entering a pod shell, unanchored lookup keys,
lone quotes in the LLM's single-quoted YAML."""
from __future__ import annotations

import yaml

from skills.network_config_gen.autofix import loop_over_lookup
from skills.network_simulator_setup.autofix import parse_llm_yaml
from skills.network_simulator_setup.topology import _anchored_start, _enters_session


def test_loop_over_registered_list_runs_the_lookup():
    """`for i in `echo "${PODS}"`` sent the registered lines inside the command: `-bash: for: command not found`."""
    lookup = "kubectl get pods -n ${NS} | grep eirapp | awk '{print $1}'"
    parsed = {"phases": {"a": {"steps": [
        {"node": "n", "send": lookup, "register": [{"regex": r"(?<PODS>[\s\S]+)"}]},
        {"node": "n", "send": 'for i in `echo "${PODS}"`; do echo $i; kubectl exec -it $i -n ${NS} -- bash -c \'ss\'; done'},
        {"node": "n", "send": "for p in $(echo ${PODS}); do echo $p; done"},
        {"node": "n", "send": 'for i in `echo "${OTHER}"`; do echo $i; done'},
    ]}}}
    assert loop_over_lookup(parsed) == 2
    sends = [s["send"] for s in parsed["phases"]["a"]["steps"]]
    assert sends[1].startswith(f"for i in `{lookup}`; do echo $i;")
    assert sends[2] == f"for p in `{lookup}`; do echo $p; done"
    assert sends[3] == 'for i in `echo "${OTHER}"`; do echo $i; done'  # not a registered list: unchanged


def test_llm_keys_for_entering_a_shell_are_left_to_the_session_key():
    """MockWire tries exact keys first: the LLM's `kubectl exec -it eirapp-… -- bash` with plain lines pushed no
    prompt, and the next `exit` ended the session."""
    assert _enters_session("kubectl exec -it eirapp-1 -n nk -- bash")
    assert _enters_session(r"regex:kubectl exec -it \S+ -n \S+ -- bash")
    assert _enters_session(r"regex:^kubectl\s+exec\s+-it\s+\S+\s+-n\s+\S+\s+--\s+bash\s*$")
    assert _enters_session(r"regex:ssh -oPort=8099 dsadmin@\S+")
    assert not _enters_session(r"regex:^for\s+i\s+in\s+`kubectl\s+get\s+pods`;\s*do\s+kubectl\s+exec\s+-it\s+\$i\s+--\s+bash\s+-c")
    assert not _enters_session("ss")


def test_regex_keys_match_from_the_start_of_the_command():
    """The lookup's key answered the loop that runs the lookup in its backticks."""
    assert _anchored_start(r"regex:kubectl get pods -n \S+") == r"regex:^kubectl get pods -n \S+"
    assert _anchored_start(r"regex:(?i)ls -l \S+") == r"regex:(?i)^ls -l \S+"
    assert _anchored_start(r"regex:^df -h\s*$") == r"regex:^df -h\s*$"
    assert _anchored_start("ls -l") == "ls -l"


def test_loop_key_broken_by_an_unescaped_question_mark_is_rebuilt():
    """OB-44: the TAC loop's key left the ternary's `?` unescaped (`\\) ? "` = optional space) and matched nothing;
    the lookup's key, still unanchored during the checks, "answered" the loop, so the key was never rebuilt."""
    from skills.network_simulator_setup.skill import _find_entry, rekey_unanswered
    from skills.network_simulator_setup.topology import NodeSpec
    lookup = "kubectl get pods -n ${NS} | grep eirapp | awk '{print $1}'"
    loop = (f"for i in `{lookup}`; do echo $i; kubectl exec -it $i -n ${{NS}} -- bash -c 'dsClassInfo'; echo ; done"
            " | sort -u | wc -l | awk '{print ($1 == 1) ? \"VALUES_MATCH\" : \"VALUES_MISMATCH\"}'")
    broken = (r"regex:for i in `kubectl get pods -n \S+ \| grep eirapp \| awk '\{print \$1\}'`; do echo \$i; "
              r"kubectl exec -it \$i -n \S+ -- bash -c 'dsClassInfo'; echo ; done \| sort -u \| wc -l \| "
              r"awk '\{print \(\$1 == 1\) ? " + '"VALUES_MATCH" : "VALUES_MISMATCH"' + r"\}'")
    entries = {_anchored_start(r"regex:kubectl get pods -n \S+ \| grep eirapp \| awk '\{print \$1\}'"): {"lines": ["p1"]},
               _anchored_start(broken): {"lines": ["VALUES_MATCH"]}}
    resolved = loop.replace("${NS}", "1")
    assert _find_entry(entries, loop, resolved)[1] is None  # what MockWire sees: nothing answers the loop
    node = NodeSpec(name="eir", instance="eir", prompt="$ ", commands=[{"cmd": lookup}, {"cmd": loop}])
    fixed = rekey_unanswered(node, entries, set(), {})
    assert _find_entry(fixed, loop, resolved)[1] == {"lines": ["VALUES_MATCH"]}


def test_lone_quote_inside_single_quoted_item_is_repaired():
    raw = ("```yaml\n'oc cp a b':\n  lines:\n  - 'pod-1'\n"
           "  - 'tar: Removing leading `/' from member names'\n'ss':\n  lines:\n  - 'ok'\n```")
    parsed = parse_llm_yaml(raw)
    assert parsed["oc cp a b"]["lines"][1] == "tar: Removing leading `/' from member names"
    assert parsed["ss"] == {"lines": ["ok"]}
    assert yaml.safe_load("a: ''''") == {"a": "'"}  # a doubled quote stays one quote
