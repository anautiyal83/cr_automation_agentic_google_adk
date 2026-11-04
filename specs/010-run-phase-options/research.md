# Research: run phase options — probes (2026-11-04)

| Probe | Setup | Result |
|-------|-------|--------|
| T001a node-side `scp` with `expect_reply` | MockWire MRF instance with an `interactive` entry: `scp … backup@repo-server:…` asks `backup@repo-server's password: ` and answers `netconf-sim`; workflow step `send: scp /tmp/a.txt backup@repo-server:/backup/a.txt`, `expect_reply: [{expect: 'password:', reply: "netconf-sim\n", timeout_sec: 10}]` | The JAR answered the prompt, the output shows the transfer (100 %), validation passed ✔ |
| T001b the same without `expect_reply` | step `timeout_sec: 15` | The step hung at the password prompt and failed ✘ — **every node-side transfer to / from the repo needs `expect_reply`** |
| T002 MockWire in-shell transfers | `Builtins.transfer` (lib/mockwire) | `scp <file> <user@host:remote>` / `sftp put` only copy **into the same instance's** VFS; `scp user@host:path local` (download) is not supported; no password prompt. A node ↔ repo copy in the simulator needs: peer instances (host → that instance's VFS), download direction, a password prompt answered before the copy |

## Consequences

- Step 6 (repo route) adds `expect_reply: [{expect: 'password:', reply: '${SECRET.<repo password secret>}\n'}]` to
  every node-side transfer command.
- Step 8 / MockWire: in-shell `scp` / `sftp` with a `user@host:` side resolve `host` to a peer instance's VFS
  (`transfer.peers` in the instance config, written by step 8 from the topology), copy in both directions, and ask
  for the password first when the instance config says so.

## Repo route probes (2026-11-04, O-D7 corrected)

| Probe | Setup | Result |
|-------|-------|--------|
| T002b MockWire peers | REPO + MRF instances, REPO `transfer.peers: {rjjvrmr03: {root: <MRF vfs>, password}}`; JAR steps on REPO: `scp f user@rjjvrmr03:/tmp/`, `scp user@rjjvrmr03:/tmp/bk.tar.gz /repo/`, `sftp user@rjjvrmr03:/opt <<< 'put f'`, each with `expect_reply` | All copies land in the other instance's VFS (seen with `ls` on MRF / `cat` on REPO) ✔; wrong password → `Permission denied`, nothing copied ✔ |
| T009a unknown step key | a step with an extra key `nc_origin` | **Rejected** by the JAR (`Unable to find property 'nc_origin' on class StepDefinition`) — `flatten` rebuilds the staged step from the `nc_` checksum step instead |
| T011a steps 8 + 9 via repo | run 3d75c561 (MRF) copied, `run_options.backup_via_repo`, node type `file_transfer` scp / sftp, current `structure.apply` (test_step `--agents 8,9 --refix-workflow`, DB copy) | SCP 4/4, SFTP 4/4 (TC-P01..03, TC-R01): backups node → repo and inputs automation server → repo → node, every checksum comparison ✔. The first attempt failed on the LLM's backup regex (DEP-58, not the route) — patched in the copy |
| T013a steps 8 + 9, node runs the copies (O-D7 final) | same run 3d75c561 copy, `structure.apply` with the copy on MRF (`scp` / `sftp … backup@REPO:…`, repo password by `expect_reply`), MRF instance with `transfer.peers: {REPO}` (`via_repo_89.py`, DB copy) | SCP and SFTP each: every node ↔ repo copy prompted for the password and copied (100 %), all checksum comparisons ✔, TC-R01 rollback ✔; TC-P01..03 failed only at the last post-check `ls … \| grep "$(basename …)"` — step 8's LLM answered it `''` this time (DEP-59, not the route) |
