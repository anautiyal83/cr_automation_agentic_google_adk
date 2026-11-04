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
