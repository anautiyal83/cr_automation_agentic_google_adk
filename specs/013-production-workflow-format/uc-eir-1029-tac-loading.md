# EIR_1029_TAC_LOADING_EIR — local vs production (2026-11-11)

Local: `CR-Config/EIR/local` (step 6 of run 5b516ded, 1,058 lines, 64 steps). Production: `CR-Config/EIR/production`
(= JAR `templates/yaml`, 3,223 lines, 140 steps). Gap causes as in spec 009 (R requirement, C conflict, P pipeline,
F framework knowledge, E environment).

## Breaks on a real node

| # | Local | Production | Cause |
|---|-------|-----------|-------|
| 1 | `oc exec … bash` on BASTION, then `ss` / `DSstatus` / `org -c` on node `EIR` = a new SSH login, not the pod | one NIAM node, nested sessions, `exit` | P / F (PF-D2) |
| 2 | Prompts copied from the log (pod name, DS server IP) | generic prompt | P |
| 3 | `for_each:` on plain steps (not a framework key; name mismatch `EIR_POD_NAMES` vs `EIRPODNAMES`) | shell `for` loop over pods in one send | P (validator should reject) |
| 4 | TAC check `expr: eirBlackRngCount == eirBlackRngCount` (always true) | collect all pods → LOCAL `sort -u \| wc -l` → `TAC_MATCH` | P (validator: self-compare) |
| 5 | `diff predump… postdump…` on BASTION — the dumps are inside the pod | zip in pod → `oc cp` → repo → local → Python compare + HTML report | P / R |
| 6 | `ssh -oPort=8099 dsadmin@IP StrictHostKeyChecking=no …`, no password reply | `-oStrictHostKeyChecking=no …` + `expect_reply ${DSADMIN_PWD}` | P |
| 7 | Envoy `ip a` on node `EIR` (envoy is ZT2) | `oc exec ${ENVOYPOD} -n ${ZT2NAMESPACE} -- ip a` | P |
| 8 | Rollback uses `${ZT2NAMESPACE}`, `${DSSERVERIP}`, `${HOSTNAME}`, `${DATE}` of skipped phases (rollback-only: empty) | rollback looks them up again | P (rollback naming rule) |
| 9 | `unzip` in Backup, relative path, no `-o` | `unzip -o … -d /tmp`, JSON name detected | P |

## Wrong values

- `dscli` output: `Removing file: (?<FILEPATH>…)` vs JSON `"path"` / `"status"` — R (no sample output in the log)
- HOSTNAME: first helm release vs release without `-eir` — R/C
- DS server IP: `fd02:` + `/64` hard-coded vs `eth1` global inet6 — P
- netstat: any ESTABLISHED vs any connection not ESTABLISHED/LISTEN (+ 3 retries) — P
- N17: any pod 0 vs any non-zero `[1-9]\d*` — P

## Missing

Input-file route (LOCAL_PATH → GitLab → repo → node, checksums + retries) — R/F; `DELTA_TAC_FILE` unused — P;
TAC propagation wait + retry — R; repo backup of `bulkProvisioning.json_backup` — F (route option); rollback
verification (diff pre vs post-rollback + report) — P; evidence zips — R/F; `globals.defaults`, retries, timeouts,
`use_exit_code`, `expect_reply`, `warning` vars, computed registers — F.

## Production weaknesses (do not copy)

Rollback-only on a later day: `${DATE}` regenerated → `predump_${DATE}` not found (our naming rule avoids it);
pre-check N17 regex reads the first pod only.
