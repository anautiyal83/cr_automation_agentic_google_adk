"""Step 8: commands MockWire answers from its VFS get no response key (bugs.md DEP-59).

    python -m pytest tests/test_vfs_commands.py -v
"""
from __future__ import annotations

import json
import tarfile

from skills.network_simulator_setup.topology import NodeSpec
from skills.network_simulator_setup.vfs_commands import resolved_dirs, vfs_handled, workflow_prefixes

DEST = "${rec.data.MRF_DESTINATION_PATH}"
FILE = "${rec.data.INPUT_FILE}"
GUARD = "if [[ '${MRF.VER}' == '13.'* ]]; then %s; else echo SKIPPED_BY_CONDITION; fi"
# the MRF announcement load as step 6 writes it (run 3d75c561)
MRF = [
    "systemctl status swms --no-pager",                                         # health check
    f"ls -ltrh {DEST}/ | grep \"{FILE}\" || true",                             # pre-check
    f"tar -czvf /tmp/announcement_backup.tar.gz {DEST}/ && ls -1 /tmp/announcement_backup.tar.gz",
    f"scp backup@repo-server:/backup/netconfig/${{crGroup}}/files/{FILE} /tmp/{FILE}",
    f"sha256sum /tmp/{FILE}",
    GUARD % f"cp /tmp/{FILE} {DEST}/",
    GUARD % f"chmod 777 {DEST}/{FILE}",
    "if [[ ! '${MRF.VER}' == '13.'* ]]; then tar -xf /tmp/" + FILE + f" -C {DEST}/; else echo SKIPPED_BY_CONDITION; fi",
    "if [[ ! '${MRF.VER}' == '13.'* ]]; then chmod 777 " + DEST + "/*; else echo SKIPPED_BY_CONDITION; fi",
    f"ls -ltrh {DEST}/ | grep \"$(basename {FILE} .tar)\"",                    # post-check
    "rm -rf " + DEST + "/*",                                                    # rollback
    f"tar -xzvf /tmp/announcement_backup.tar.gz -C /",
    "ls -l /opt/swms/conf/swms.cfg",                                            # a real node file
    "cat /var/log/messages | grep -i error",
]


def _prefixes():
    return workflow_prefixes([{"cmd": c} for c in MRF], [], None)


def test_file_commands_on_workflow_paths_run_on_the_vfs():
    prefixes = _prefixes()
    assert {"/tmp", DEST} <= prefixes and "/" not in prefixes   # tar -x -C / never means "every path"
    handled = {c for c in MRF if vfs_handled(c, prefixes)}
    assert handled == set(MRF[1:3] + MRF[4:12])
    for real in ("systemctl status swms --no-pager", "ls -l /opt/swms/conf/swms.cfg",
                 "cat /var/log/messages | grep -i error"):
        assert real not in handled                                # keeps its response key
    assert MRF[3] not in handled                                  # the repo copy: a peer builtin


def test_copy_targets_count_only_once_their_source_is_known():
    cmds = [{"cmd": "cp /opt/app/conf.xml /opt/app/conf.xml.bak"}, {"cmd": "ls -l /opt/app/conf.xml.bak"}]
    prefixes = workflow_prefixes(cmds, [], None)
    assert "/opt/app" not in prefixes and not vfs_handled(cmds[1]["cmd"], prefixes)
    steps = [{"node": "MRF", "sftp": {"operation": "upload", "local_path": "a.xml", "remote_path": "/srv/inbox/a.xml"}}]
    prefixes = workflow_prefixes([{"cmd": "cp /srv/inbox/a.xml /srv/work/"}, {"cmd": "ls /srv/work/a.xml"}], steps, None)
    assert {"/srv/inbox", "/srv/work"} <= prefixes and vfs_handled("ls /srv/work/a.xml", prefixes)


def test_destination_folders_exist_per_record(tmp_path):
    doc = {"crGroups": [{"crGroup": "CR1", "nodeGroups": [{"nodeGroup": "G", "nodes": [{"node": "MRF1"}],
           "configSequences": [{"records": [{"data": {"MRF_DESTINATION_PATH": "/clips/RAJ/POSTPAID"}},
                                            {"data": {"MRF_DESTINATION_PATH": "/clips/RAJ/PREPAID"}}]}]}]}]}
    jf = tmp_path / "ciq.json"
    jf.write_text(json.dumps(doc), encoding="utf-8")
    dirs = resolved_dirs(_prefixes(), [str(jf)])
    assert {"/tmp", "/clips/RAJ/POSTPAID", "/clips/RAJ/PREPAID"} <= set(dirs)


def test_keys_answering_vfs_commands_are_dropped():
    from skills.network_simulator_setup.skill import drop_vfs_answers
    node = NodeSpec(name="MRF", instance="MRF", prompt="# ", commands=[{"cmd": c} for c in MRF])
    node.vfs_commands = {c for c in MRF if vfs_handled(c, _prefixes())}
    entries = {
        r'regex:ls -ltrh \S+/ \| grep ".+?"': {"lines": [""]},                 # the LLM's post-check answer
        "regex:if \\[\\[ '\\S+' == '13\\.'\\* \\]\\]; then cp /tmp/\\S+ \\S+/; else echo SKIPPED_BY_CONDITION; fi":
            {"lines": [""]},
        "systemctl status swms --no-pager": {"lines": ["active (running)"]},
        "regex:ls -l /opt/\\S+": {"lines": ["-rw-r--r-- 1 root root 10 swms.cfg"]},
    }
    kept = drop_vfs_answers(node, entries, {})
    assert set(kept) == {"systemctl status swms --no-pager", "regex:ls -l /opt/\\S+"}
    # a key that also answers a command on a real node file stays (with a warning)
    shared = {"regex:ls .*": {"lines": ["x"]}}
    assert drop_vfs_answers(node, shared, {}) == shared and node.warnings


def test_staged_archive_inputs_are_real_archives(tmp_path):
    from skills.network_config_test.jar_executor import _write_test_input
    for name in ("PROMPT_5003.tar", "b.tar.gz", "c.txt"):
        _write_test_input(tmp_path / name, name)
    with tarfile.open(tmp_path / "PROMPT_5003.tar") as t:
        assert t.getnames() == ["PROMPT_5003.dat"]
    with tarfile.open(tmp_path / "b.tar.gz") as t:
        assert t.getnames() == ["b.dat"]
    assert (tmp_path / "c.txt").read_text(encoding="utf-8").startswith("test input staged")


def test_invented_response_repaired_from_any_regex():
    """A synthetic answer the LLM could not fix for a non-literal regex gets a generated line."""
    import re
    from skills.network_simulator_setup.skill import repair_literal_mismatches
    regex = r"\w{3} \w{3} \s?\d{1,2} \d{2}:\d{2}:\d{2} \w{3} \d{4}"
    node = NodeSpec(name="MRF", instance="MRF", prompt="# ",
                    commands=[{"cmd": "date", "validation": {"success": {"criteria": {"regex": regex}}}}])
    fixed = repair_literal_mismatches(node, {"date": {"lines": ["Tue Oct 26 10:30:00 CEST 2023"]}}, set(), {})
    assert re.search(regex, "\n".join(fixed["date"]["lines"]))


def test_if_else_member_check_runs_on_the_vfs():
    """Run 9de5fcb8: `if …; then ls -ld $(tar -tf …); else ls … | grep …; fi` got a canned key."""
    check = (f"if [[ ! '${{MRF.VER}}' == '13.'* ]]; then ls -ld $(tar -tf /tmp/{FILE} | sed 's#^#{DEST}/#'); "
             f"else ls -ltrh {DEST}/ | grep \"{FILE}\"; fi")
    assert vfs_handled(check, _prefixes())
    assert not vfs_handled("if [[ 'a' == 'a' ]]; then systemctl status x; else echo no; fi", _prefixes())


def test_loop_pass_aligns_with_the_nearest_preceding_step():
    """Rollback-only via repo (run 9de5fcb8): the 2nd record pass of the rollback's sha256sum matched the
    backup's identical command and was reported as "outside the rollback phase"."""
    from types import SimpleNamespace
    from skills.network_config_test.scenarios import WorkflowStep, align

    def step(i, kind, node, send):
        return WorkflowStep(index=i, phase=kind, phase_kind=kind, node=node, send=send, regex="", on_failure="stop")
    steps = [step(0, "backup", "REPO", "sha256sum /r/x"), step(1, "rollback", "REPO", "sha256sum /r/x"),
             step(2, "rollback", "MRF", "tar -xzvf /t/x")]
    cmds = [SimpleNamespace(node="REPO", command="sha256sum /r/x"), SimpleNamespace(node="MRF", command="tar -xzvf /t/x")]
    # a loop's 2nd pass goes to the nearest preceding match, not the backup's identical command
    assert align(cmds * 2, steps)[2:] == [1, 2]
    # rollback-only: matched against the rollback steps, indexes stay the workflow's
    assert align(cmds * 2, [s for s in steps if s.phase_kind == "rollback"]) == [1, 2, 1, 2]


def test_rollback_result_is_compared_with_the_node_before(tmp_path):
    """Rollback-only "passed" twice while the original file was gone: the node's files are compared."""
    from skills.network_config_test.scenarios import node_files_diff
    before, after = tmp_path / "b", tmp_path / "a"
    for root, files in ((before, {"MRF/d/a.wav": "old", "MRF/d/x": "x", "MRF/tmp/l": "1", "REPO/bk": "1"}),
                        (after, {"MRF/d/x": "x", "MRF/d/new.wav": "n", "MRF/tmp/l": "2"})):
        for rel, text in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text, encoding="utf-8")
    assert node_files_diff(before, after) == ["missing: /d/a.wav", "left behind: /d/new.wav"]
    (after / "MRF/d/a.wav").write_text("old", encoding="utf-8")
    (after / "MRF/d/new.wav").unlink()
    assert node_files_diff(before, after) == []                      # /tmp and the repo server don't count


def test_staged_archive_members_carry_the_mode_the_workflow_checks(tmp_path):
    """Run 46e5fee1: the untar keeps the members' permissions; the check wants 777 — the test archive
    stored 0644, so the check failed on the simulator only."""
    from skills.network_config_test.jar_executor import _write_test_input, extracted_member_mode
    wf = {"phases": {"a": {"steps": [{"node": "MRF", "send": "ls -ld $(tar -tf /tmp/a.tar | sed 's#^#/d/#')",
                                      "register": [{"regex": "^\-rwxrwxrwx.*", "loop": True, "count_var": "ok"}]}]}}}
    assert extracted_member_mode(wf) == 0o777 and extracted_member_mode({"phases": {}}) == 0o644
    _write_test_input(tmp_path / "a.tar", "a.tar", extracted_member_mode(wf))
    with tarfile.open(tmp_path / "a.tar") as t:
        assert t.getmembers()[0].mode == 0o777


def test_staged_inputs_are_made_again_real_ones_kept(tmp_path):
    from skills.network_config_test.jar_executor import _staged_by_step9, _write_test_input
    _write_test_input(tmp_path / "a.tar", "a.tar")
    _write_test_input(tmp_path / "b.txt", "b.txt")
    (tmp_path / "real.tar").write_bytes(b"not ours")
    assert _staged_by_step9(tmp_path / "a.tar") and _staged_by_step9(tmp_path / "b.txt")
    assert not _staged_by_step9(tmp_path / "real.tar")
