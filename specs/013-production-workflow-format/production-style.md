# Production workflow style (from the 24 workflows bundled in the cli-automation JAR)

Counts are over the corpus (2,324 steps; 1 file with a tab character could not be parsed and is excluded).
"Ours" = what step 6 writes today (EIR run 5b516ded).

## File

| Item | Production | Ours |
|------|-----------|------|
| Top-level keys | `version: 1.2`, `name: <kebab-case>`, `globals`, `nodes`, `phases` (+ `notifications` in 3) | `nodes`, `phases`, `globals` |
| `globals.defaults` | in every file: `timeout_sec: 120`, `interaction_timeout_sec: 180`, `log_level: detailed`, `on_failure: stop` | none |
| `globals.vars` | `ROLLBACK_REQUIRED` (24/25), `ROLLBACK_ONLY`, `RETRY_COUNT: 3`, `LOOP_RETRY: [1, 2, 3]` (18), `BACK_UP_LOCATION_REPO` (17), `LOCAL_PATH` (17), `REPO_IP_V4` / `REPO_IP_V6` (15), `ACTIVITY_PYTHON_SCRIPT_PATH` (13), `ACTIVITY_ID`, `NODE_TYPE` | `ROLLBACK_ONLY`, `PRECHECK_FAILED`, `BACKUP_FAILED`, `CHANGES_MADE`, `ROLLBACK_REQUIRED` |
| `send` scalar style | double-quoted 942, single-quoted 654, `>` block 416, `\|` block 58, plain 71 — multi-line commands as `>-` | ruamel picks; multi-line → double-quoted (breaks `\n`, probe P-8) |

## Nodes

| Production | Ours |
|-----------|------|
| `niam_<type>_server` (or `niam_<type>_<n>` per NIAM entry): `type: remote`, `plugin: ssh`, `category: internal`, `prompt_regex` generic (`(?m)\[[^\]\r\n]+\][#$][^\r\n]*$` for Linux shells), `ssh: {host: ${NIAM_IP}, port: ${M2MPORT}, user: ${M2MUSER}, auth: {password: ${M2MPASSWORD}}, ne_id: <NIAM id>, connect_timeout_sec: 300, read_timeout_sec: 300, keepalive_sec: 30, login_retries: 2, login_retry_delay_sec: 15, strict_host_key_checking: false}` (+ `elevate_to_root`, `kex_algorithms`, `host_key_algorithms`, `interactive_prompts` where the node needs them) | one node per prompt in the session log (`BASTION`, `EIR`, `ZT2`) with the log's literal prompt and `${<NODE>.niamID-1}` host |
| `repo_server` (ssh, prompt `(?m)[#$]\s*$`, `${REPO_IP}` / `${REPO_USER}` / `${REPO_PASSWORD}`), `repo_server_sftp` (`type: sftp`, `plugin: sftp-plugin`) | `REPO` |
| `gitlab_api` (`type: rest`, `plugin: rest-plugin`, OAuth client credentials), `mailer` (email) where used | — |
| LOCAL steps use `node: local`, not declared | `LOCAL` declared |
| Nested hosts are sessions on the NIAM node: `oc exec -it ${POD} -n ${NS} -- bash` → steps → `exit`; `ssh -oPort=… -oStrictHostKeyChecking=no -oUserKnownHostsFile=/dev/null user@${IP}` + `expect_reply` password → steps → `exit` | a node switch (new SSH login) |

## Phases

| Key | `name` | `when` | Notes |
|-----|--------|--------|-------|
| `preNodeHealthCheck` | `PRE_NODE_HEALTH_CHECK` | `${ROLLBACK_ONLY != "true"}` | `on_failure: stop` (18/20) |
| `backup` | `BACKUP` | `${ROLLBACK_ONLY != "true"}` | |
| `activity_configuration` | `ACTIVITY_CONFIGURATION` | `${ROLLBACK_ONLY != "true"}` | |
| `activity_postcheck` | `ACTIVITY_POSTCHECK` | `${ROLLBACK_ONLY != "true"}` | MRF |
| `postNodeHealthCheck` | `POST_NODE_HEALTH_CHECK` | `${ROLLBACK_ONLY != "true"}` | |
| `rollback_configuration` | `ROLLBACK_CONFIGURATION` | `${ROLLBACK_REQUIRED == "true"}` | |
| `rollback_postcheck` | `ROLLBACK_POSTCHECK` | `${ROLLBACK_REQUIRED == "true"}` | MRF |

Every phase has `name`, `description`, `steps`; `when` in 100/115. Ours: camelCase activity / rollback keys,
human names ("Main TAC Loading Activity"), bare-name `when`, phase flags `PRECHECK_FAILED` / `BACKUP_FAILED`.

## Steps

- Field order: `node`, `command_description`, `send`, `when`, `prompt_regex`, `on_failure`, `use_exit_code`,
  `timeout_sec`, `retries` / `retry_delay_sec` / `retry_on`, `expect_reply`, `register`, `validation`.
- `when` / `expr` always `${…}` around the whole expression (870/870): `${A == "x"}`, `${X != ""}`,
  `${BULK_FILE_FOUND==false}`.
- `validation`: `enabled: true`, `description`, `success: {criteria: {expr: …}, message, vars?}`,
  `failure: {message, vars?}`, `warning: {message, vars}` for "retry" outcomes (522). Criteria: `expr` 1453,
  `regex` 212, none 597, `all`/`any`, `exit_code`, `http_status`.
- `register`: capture regexes (`(?<NAME>…)`) + an error capture `(?<CREATEERROR>ERROR:.*|error:.*)`; computed values
  `- name / when / value` (718).
- Per-item work over pods: a shell `for i in \`…\`; do …; done` in one `send`, then a LOCAL step that aggregates
  (`sort -u | wc -l` → `TAC_MATCH` / `TAC_MISMATCH`).
- Waits: `echo 'Waiting ${DELAY} seconds…'; sleep ${DELAY}; echo 'WAIT_DONE'` with `timeout_sec` above the delay.

## Patterns

- **Transfer + checksum + retry** (300 `sha256sum` steps): checksum at the source (register), transfer, checksum at
  the destination with `success.criteria.expr: ${SRC == DST}` + `vars: {STATUS: SUCCESS}` and `warning.vars:
  {STATUS: FAILED}`; then `type: loop` `for_each: "${LOOP_RETRY}"`, `item_var: ATTEMPT`, `when: ${STATUS !=
  "SUCCESS"}`, `break_when: ${STATUS == "SUCCESS"}`, `hide_when_skipped: true` repeating transfer + checksum; then a
  guard `node: local`, `send: echo checksum-guard`, `when: ${STATUS != "SUCCESS"}`, `on_failure: stop`.
- **Node ↔ repo**: the node runs `printf "put <src> <dst>\nbye\n" | sftp -o StrictHostKeyChecking=no
  ${REPO_USER}@${REPO_IP}` (or `get`) with `expect_reply: [{expect: 'password:', reply: ${REPO_PASSWORD}}]`;
  automation server ↔ repo via `repo_server_sftp` steps `sftp: {operation, local_path, remote_path, overwrite,
  create_dirs}`; repo IP chosen by `who am i` IPv4/IPv6 + computed register.
- **Rollback-only**: rollback looks up again what it needs (`when: ${ROLLBACK_ONLY == "true"}` on namespace / IP
  lookups), takes the backup from the node's `/tmp` or else from the repo.
- **Evidence**: pre/post dumps zipped on the node → repo → automation server; a Python script
  (`${ACTIVITY_PYTHON_SCRIPT_PATH}/…`) compares them and writes an HTML report (`OVERALL RESULT: PASS|FAIL`).
