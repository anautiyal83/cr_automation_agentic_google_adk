# Research: Step 8 Simulator — Phase 0 Spike Results (2026-11-02)

Setup: two hand-written MockWire instances (RDS with NIAM on 22301, PGW without on 22302),
started from a **copy** of `lib/mockwire/mockwire-simulator/mockwire.jar` in a scratch folder.
Workflow overlay = the `preNodeHealthCheck` phase of run `b61b977c` with `nodes.<N>.ssh`
pointed at localhost. Driven by `lib/cli-automation-standalone.jar execute`.

**Result: the end-to-end path works.** 3/7 steps pass. The 4 failures are workflow (Step 6) defects, not simulator defects.

## Confirmed facts

| # | Finding | Impact on design |
|---|---------|------------------|
| F1 | Running MockWire from a per-run JAR copy resolves `config/<name>.yaml` and `outputs/<name>/responses/` from that copy's folder. CLI: `--name --port --ttl-minutes`. | D1 works with no JAR change (T002 ✔). |
| F2 | The JAR honours the overlay: it connects to `nodes.<N>.ssh.host/port`. | The overlay workflow design (FR-S8-11) is valid. |
| F3 | The JAR uses JSch with strict host-key checking → `reject HostKey: localhost`. Fix: `ssh.strict_host_key_checking: "no"` (a field in `SshDefinition`). | Overlay MUST set it. |
| F4 | NIAM: the JAR authenticates with keyboard-interactive (password round, then neId round). MockWire `niam:` with `users[].neIds` accepts it (`KBI auth complete`). | Per-node NIAM instances work (D2). neIds come from CIQ JSON `niamID-1..3`. |
| F5 | The JAR sends `stty -echo`, then each command as `<cmd>; echo __CMD_DONE__:$?`, and parses `__CMD_DONE__:(\d+)`. MockWire splits on `;`, but needs explicit entries for `stty -echo` (`lines: []`) and `echo __CMD_DONE__:$?` (`lines: ['__CMD_DONE__:0']`). | Builtin catalogue (T032) MUST add both to every instance. |
| F6 | Default `terminalMode` (AUTO) echoes input, and the echoed `__CMD_DONE__` text breaks output capture. `config.terminalMode: PLAIN` fixes it. | Every generated config MUST set `terminalMode: PLAIN`. |
| F7 | Built-in `exit` closes the session even inside `su`. A YAML entry `"exit": {lines: [], popPrompt: true}` returns to the previous prompt. `su - X` uses `pushPrompt`. | Builtin catalogue: `su - <user>` → pushPrompt and `exit` → popPrompt, per instance. |
| F8 | `execute --node` must be the JSON node-group key (zone, e.g. `EAST`), plus `--cr CR1`. The current code passes `PGW_RDS` → `node not found: PGW_RDS`, so **Step 9 has never executed a command**. It must loop over every zone and CR in the JSON. | DEP-2 confirmed (critical). |
| F9 | The first command of each SSH session waits about 18 s before completing (but captures correctly). MockWire logs the JAR's first line as `estty -echo` (a stray leading char). | Performance only. Investigate in Phase 1 (T017). |
| F10 | paramiko 5.0.0 is already installed: password + NIAM neId prompt, pushPrompt, sentinel all verified. | D3 adds no new dependency. |

## Workflow (Step 6) defects exposed by the spike

| ID | Defect | Evidence |
|----|--------|----------|
| DEP-4 | `su - <user>` / `exit` steps validate the prompt (`regex: rdsggn01:~ \$`), but the JAR strips prompts from the output (output = `''`). They can never pass, on real nodes too. | report: `su - sdfrun` output `''` |
| DEP-5 | The `sar 1 10` validation regex expects 5 numeric columns after `all`, but real `sar` prints 6 (`%user %nice %system %iowait %steal %idle`). It can never match a real node. | report: output correct, validation failed |

These are exactly what Step 9 should flag and route to Step 6. The simulator must return **realistic** output (from SSH logs where available), not output tailored to a wrong regex. This refines FR-S8-04/06: the offline check reports mismatches but does not bend responses to fit invalid regexes.

## Minimal working instance template (verified)

```yaml
config:
  prompt: 'rdsggn01:~ # '
  delayMs: 50
  jitterMs: 10
  idleTimeoutSec: 300
  terminalMode: PLAIN
auth:
  enabled: true
  users:
  - {username: oamsys, password: test, prompt: 'rdsggn01:~ # '}
niam:                         # only when workflow node has ssh.ne_id
  enabled: true
  prompts: {passwordPrompt: "Password: ", neIdPrompt: "neId: ", banner: "Nokia Proxy (simulated)"}
  mfa: {enabled: false}
  users:
  - {username: oamsys, password: test, neIds: [<niamID-1>, <niamID-2>, <niamID-3>]}
"stty -echo": {lines: []}
"echo __CMD_DONE__:$?": {lines: ['__CMD_DONE__:0']}
"su - sdfrun": {lines: [], pushPrompt: 'sdfrun@rdsggn01:~ $ '}
"exit": {lines: [], popPrompt: true}
"sar 1 10": {file: "sar.txt"}          # outputs/<instance>/responses/sar.txt
```

Overlay node (verified):

```yaml
RDS:
  ssh: {host: localhost, port: '22301', user: oamsys, auth: {password: test},
        ne_id: <niamID-1>, strict_host_key_checking: 'no', ...rest unchanged}
```
