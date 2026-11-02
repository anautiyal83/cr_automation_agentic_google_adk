# MockWire Feature Gaps for NetConfig UC Testing

**Created**: 2026-11-02 | **Simulator**: `lib/mockwire/mockwire-simulator/mockwire.jar` (build 2026-08-19)
**Reference UC**: PGW_RDS_1058_IFC_CAPABILITY_MODIFICATION | **Client under test**: `cli-automation-standalone.jar execute`

Features our UCs need that MockWire does not provide today. Each gap is backed by
evidence from the Phase 0 spike (`research.md`) or the 2026-11-02 behaviour probe
(SSH session against a minimal config). The **pipeline workaround** column says what
step 8 does until MockWire is fixed. Per the constitution ("Mock simulator parity"),
each workaround is a tracked known limitation.

Priority: **P1** = blocks realistic testing of the reference UC; **P2** = needed for
fidelity or diagnostics; **P3** = convenience or production parity.

## Summary

| ID | Pri | Feature | Status today | Pipeline workaround |
|----|-----|---------|--------------|---------------------|
| MW-01 | P1 | NIAM root elevation via Ctrl+P, Ctrl+E | Keys swallowed; no prompt change | Login lands directly on the root prompt; overlay sets `elevate_to_root: false`. Elevation is **not tested**. |
| MW-02 | P1 | Per-command exit codes (`$?`) | Always `__CMD_DONE__:0` | None: failure paths and `on_failure` handling can't be tested |
| MW-03 | P1 | Regex capture groups in responses (`$1`, named groups) | Returned literally | Resolve CIQ values at generation time (Phase 3 T031). Runtime values (`${env.ORDERNO}`, `${register.*}`) can't be echoed. |
| MW-04 | P1 | State-driven responses (pre vs post change) | Only call-count cycling (`SEQUENTIAL`, `GLOBAL_CYCLE`) | Cycle by call count; breaks with retries and multiple zones per instance |
| MW-05 | P1 | In-shell `sftp put` plus server-side file reactions | `sftp: command not found` even with a `transfer:` section | Static responses for the `sftp put` and `ls -l …xml.res` strings |
| MW-06 | P2 | Shell builtins on the existing VFS: `ls`, `cat`, `touch`, `pwd`, redirection | All `command not found` (`vfs/LocalSandboxVFS` exists but isn't exposed) | Static responses per exact command |
| MW-07 | P2 | `echo` builtin with variable expansion (`$?`, `$USER`, `$HOME`, env) | `echo: command not found` (only the `__CMD_DONE__` marker is special-cased) | Static responses |
| MW-08 | P2 | Real `su` semantics | No built-in `su`. Built-in `exit` closes the session even inside `su`. | `su - X` → `pushPrompt`; explicit `exit` → `popPrompt` per config |
| MW-09 | P2 | Honour `stty -echo` / `stty echo` at runtime | Echo fixed by `terminalMode`; AUTO echoes and breaks JAR output capture | `terminalMode: PLAIN` on every instance |
| MW-10 | P2 | CLI: `--config`, `--responses`, `--log-file`, `--bind` | Only `--name`, `--port`, `--ttl-minutes`; paths derived from the JAR location | 7.7 MB JAR copied into every `run_dir/simulator/` |
| MW-11 | P2 | Unmatched-command reporting | Unknown commands logged as a normal `Route input` line | None: step 9 can't tell "simulator missing response" from "workflow wrong" |
| MW-12 | P2 | `whoami`, `hostname`, `date` builtins driven by session/user | `command not found` | Static responses |
| MW-13 | P3 | Per-neId profiles on one instance (one NIAM gateway → many nodes) | One prompt and command set per instance | One instance per node (decision D2) |
| MW-14 | P3 | Deterministic host key and known_hosts export | New key per host-key store; JAR rejects it under strict checking | Overlay sets `strict_host_key_checking: "no"` |

## Details

### MW-01 — NIAM root elevation (Ctrl+P, Ctrl+E) · P1
- **UC need**: Requirements specify `root_method: ctrl_p_e`, `root_prompt: rdsggn01:~ #`. Workflow nodes set `elevate_to_root: true`. The JAR (`RemoteCommandExecutor.elevateToRootIfConfigured`) sends the key sequence and waits for the root prompt (`root prompt not reached after elevation`).
- **Evidence**: The probe sent `\x10\x05`: nothing was routed and the prompt stayed `[oamsys@rdsggn01 ~]$`. In the JAR run, every SSH session's first command took about 18 s (the elevation wait) and MockWire logged a stray `e` (`estty -echo`). With `elevate_to_root: false`: 6.6 s total, no stray char, identical results.
- **Proposed**:
  ```yaml
  niam:
    elevation:
      sequence: ctrl+p+e          # bytes 0x10 0x05
      rootPrompt: 'rdsggn01:~ # '
      passwordPrompt: null        # optional: prompt for root password first
  ```
  Behaviour: on the sequence, push `rootPrompt` and print it. `exit` pops back.

### MW-02 — Per-command exit codes · P1
- **UC need**: The JAR parses `__CMD_DONE__:(?<cmdexit>\d+)` to decide success. Workflows use `on_failure: continue` / stop semantics. Negative tests (node down, command rejected) need non-zero codes.
- **Evidence**: A command returning `ERROR: boom` still produced `__CMD_DONE__:0`.
- **Proposed**: `exitCode: <int>` on a command and on each item of `responses:`. The built-in `__CMD_DONE__` marker and `$?` use the last command's code.

### MW-03 — Regex capture substitution · P1
- **UC need**: `ldapsearch … -b "imsi=<IMSI from CIQ>"` must return `dn: imsi=<same IMSI>`. `ls -l …/outbox/${env.ORDERNO}.xml.res` must list the generated order number. Values differ per CIQ row and per run.
- **Evidence**: `regex:ldapsearch .*imsi=(\d+).*` with `lines: ['dn: imsi=$1', 'value: ${1}']` returned the text literally.
- **Proposed**: Substitute `$1..$9` and `${name}` (Java named groups) in `lines` and in file content (opt-in `template: true` for files).

### MW-04 — State-driven responses · P1
- **UC need**: The activity phase reads LDAP before (`scscfMandCap: 15`), applies the change (SPML XML via PGW / `ldapmodify`), then reads it again and must see the new value. The `create_html_report.py` comparison depends on pre ≠ post.
- **Evidence**: Only `responseStrategy: SINGLE | SEQUENTIAL | GLOBAL_CYCLE` exist (`yaml/Command$ResponseStrategy`). Call-count cycling breaks when step 9 retries, or when one instance serves several zones (EAST, NORTH) in one run.
- **Proposed**:
  ```yaml
  "regex:ldapmodify .*imsi=(\\d+).*":
    lines: ['modifying entry ...']
    setState: {key: 'imsi:$1', value: modified}
  "regex:ldapsearch .*imsi=(\\d+).*":
    responses:
    - {whenState: {key: 'imsi:$1', value: modified}, file: post.txt}
    - {file: pre.txt}            # default
  ```
  State is per instance and survives sessions. Optional `--state-file` to inspect or reset it.

### MW-05 — In-shell `sftp put` + server-side file reactions · P1
- **UC need**: The PGW activity is: `sftp put <ORDERNO>.xml /srv/sftp/…/inbox`, then poll `ls -l …/outbox/<ORDERNO>.xml.res`, then parse the response file.
- **Evidence**: `sftp put a.xml /srv/inbox` → `-bash: sftp: command not found` with a `transfer:` section configured. `InShellTransferCommands` exists but didn't match this form. (The workflow's in-shell `sftp put` form itself needs review in step 6.)
- **Proposed**: In-shell `sftp`/`scp` writing into the VFS, plus file triggers:
  ```yaml
  vfs:
    triggers:
    - onCreate: '/srv/sftp/smc-sftp-DEFAULT/inbox/(.+)\.xml'
      create: '/srv/sftp/smc-sftp-DEFAULT/outbox/$1.xml.res'
      contentFile: 'spml_success_response.xml'
      delayMs: 2000
  ```

### MW-06 — VFS-backed shell builtins · P2
- **Evidence**: `ls`, `ls -l`, `touch`, `cat`, `pwd`, `echo data > f.txt` all return `command not found`, although `vfs/LocalSandboxVFS`, `VfsNode` and `LsOptions` are in the JAR.
- **Proposed**: Expose `ls [-l]`, `cat`, `touch`, `rm`, `mkdir`, `pwd`, `cd`, and `>`/`>>` redirection on the sandbox VFS. Seed files from YAML (`vfs.files: {path: contentFile}`). YAML command entries keep precedence over builtins.

### MW-07 — `echo` with variable expansion · P2
- **Evidence**: `echo hello world`, `echo $?`, `echo $HOME $USER` → `command not found`. The JAR marker works only because `__CMD_DONE__` is special-cased in `CommandRouter`.
- **Proposed**: A built-in `echo` expanding `$?`, `$USER`, `$HOME`, `$HOSTNAME`, and variables from `config.env`.

### MW-08 — `su` semantics · P2
- **Evidence**: Built-in `exit` closes the SSH session even after `su` pushed a prompt (spike F7). An explicit `exit: {popPrompt: true}` fixes it, but then `exit` at the base prompt no longer closes the session.
- **Proposed**: Built-in `su [-] <user>`: switch to that `auth.users` entry's prompt, optional password prompt (`auth.users[].suPassword`), and update `whoami`/`$USER`. `exit` pops when the stack is non-empty and closes the session otherwise.

### MW-09 — Runtime `stty -echo` · P2
- **Evidence**: Under `terminalMode: AUTO`, input is echoed and the echoed `…; echo __CMD_DONE__:$?` text broke the JAR's output capture (spike F6). The JAR sends `stty -echo` first and expects echo off.
- **Proposed**: Treat `stty -echo` / `stty echo` as built-ins that toggle echo for the session. Then AUTO can stay the default for interactive users.

### MW-10 — CLI flags for run isolation · P2
- **Evidence**: The only flags are `--name`, `--port`, `--ttl-minutes` (`Usage: java -jar mockwire.jar --name <server_name> --port <port>`), and paths are derived from the JAR location. The reference `ssh-simulator` already supports `--config`, `--responses` and `--instance`.
- **Proposed**: `--config <file>`, `--responses <dir>`, `--log-file <file>`, `--bind <host>`, `--state-file <file>` (MW-04), `--recording-dir <dir>`. Then step 8 can stop copying the JAR per run.

### MW-11 — Unmatched-command reporting · P2
- **Evidence**: `unknown_cmd_xyz` is logged as `INFO … Route input: [unknown_cmd_xyz]`, identical to matched commands. The client sees `-bash: unknown_cmd_xyz: command not found`.
- **Proposed**: `WARN UNMATCHED <instance> <user> <cmd>`, and a JSON-lines session log (`--recording-dir`, or extend the existing `recording:`) with `{ts, session, input, matchedKey, strategy, responseSource, exitCode}`. Step 9 would read it to classify failures: missing simulator response → retry step 8; wrong workflow command → retry step 6.

### MW-12 — Identity builtins · P2
- **Evidence**: `whoami`, `hostname`, `date` → `command not found`.
- **Proposed**: `whoami` from the current (su-switched) user; `hostname` from `config.hostname`; `date` with optional `config.fixedTime` for reproducible output.

### MW-13 — Per-neId profiles on one instance · P3
- **Evidence**: One `config.prompt` and one command map per instance. NIAM `neIds` only gate login. Production is one NIAM gateway in front of many nodes, selected by neId.
- **Proposed**:
  ```yaml
  niam:
    targets:
    - neIds: ['BH-NOKIA-CFX-R-*']
      profile: RDS            # profiles/RDS.yaml: prompt, auth prompts, commands
    - neIds: ['BH-NOKIA-CFX-Q-*']
      profile: PGW
  ```

### MW-14 — Deterministic host keys · P3
- **Evidence**: The JAR uses strict host-key checking (`reject HostKey: localhost`). MockWire keys live in `~/.mockwire/keys/`.
- **Proposed**: `--host-key <file>` and `--export-known-hosts <file>`, so tests can keep strict checking enabled.

## Already supported (verified; no work needed)

| Capability | Evidence |
|------------|----------|
| NIAM keyboard-interactive login (password, then neId) with `niam.users[].neIds` | Spike F4: `KBI auth complete` from the JAR |
| `__CMD_DONE__` end-of-command marker after `;` | Probe: `ss; echo __CMD_DONE__:$?` → `Link UP` / `__CMD_DONE__:0` with no YAML entry |
| `;`-separated commands; pipelines with `grep`/`awk`/`sed`/`head`/`tail`/`wc`/`sort`/`uniq`/`tr`/`paste` | Probe: `ss \| grep Link` |
| Exact and `regex:` command keys; `file:` responses from `outputs/<name>/responses/` | Spike; Phase 1 tests |
| `pushPrompt` / `popPrompt` prompt stack | Spike F7 |
| `responses:` with `SEQUENTIAL` / `GLOBAL_CYCLE`, per-command `delayMs`/`jitterMs`, `stream`, `interactive` Q&A | `yaml/Command` fields |
| `--ttl-minutes` self-shutdown | Phase 1 runner |
| Plugins: curl, REST, NETCONF, email; session `recording` | Classes present (not yet exercised by our UCs) |

## Pipeline follow-ups

- Remove the redundant `echo __CMD_DONE__:$?` entry from `autofix.RUNTIME_COMMANDS`, since MockWire handles the marker natively. Keeping it would mask real exit codes once MW-02 lands. *(Done 2026-11-02.)*
- When a gap is fixed upstream, drop its workaround here and in `plan.md`. Order of value for the reference UC: MW-01, MW-03, MW-04, MW-02, MW-05, then MW-11.
