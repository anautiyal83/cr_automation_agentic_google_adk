# PGW_RDS_1058_IFC_CAPABILITY_MODIFICATION — generated workflow vs production

**Compared** (2026-11-04): pipeline `C:\Users\DELL\Desktop\CR-Config\PGW\local\…yaml` (= run 6db86ff8, 2026-11-03 13:02,
449 lines — **before** DEP-9 record loops, `structure.py` and the condition autofixes) with production `…\production\…yaml`
(1728 lines). **Inputs the pipeline had**: MOP (4 phases, 38 lines: health checks, two `ldapsearch` dumps, `org -c
hssGlobalFilterCriteria`, "copy this XML in /srv/sftp/smc-sftp-DEFAULT/inbox"), CIQ sheets IP (ZONE, Node_Name,
NIAM_NAME1/2/3) / Index / Profile Modification (Zone, Test IMSI, Test MSISDN, Mandatory Cap, Optional Cap, IFC, VNI,
refinmocName, refinmtcName, APN, Packet Data Network Type), XML samples (hss_IFC, hss_vni, hlr_csi, hss_pdn), three
`ldapsearch` transcripts, **149 developer answers**, no developer notes.
Causes: R requirement · C conflict · P pipeline · F framework knowledge · E convention (see [spec.md](spec.md)).

## What each workflow does

| Concept | Production | Pipeline |
|---------|------------|----------|
| Node access | Three NIAM entries per node type (`niamID-1/2/3`); a login probe picks the first that answers (**NIAM failover**); `su - root` + `whoami` = root check | One entry per node type (`niamID-1`), `elevate_to_root` |
| Pre-health | Per node: sar (≥ 8 of 10 samples idle > 90 %), `ss` (**every** remote node Link UP: count == total), `ndsLoadStats` (Total operations rejected = 0), ProvGW (apache **and** instances running); any failure sets `PRECHECK_FAILED` → a gate **stops before the activity** | Same commands, weaker criteria (sar average, one "Link UP", one data line), `on_failure: continue` — **the activity runs even when a health check failed** |
| Test-subscriber guard | Every Test IMSI (or MSISDN-resolved IMSI) must be listed for its zone in `TESTIMSIFILELOCATION` (`zone|imsi`); otherwise stop | None — any IMSI in the CIQ is modified |
| Records | Loops over node groups → config sequences → records | `records.0` only (fixed since by DEP-9) |
| Change detection | Reads the current LDAP values per IMSI and decides per field (UNCHANGED = skip, blank = remove, value = compare); only IMSIs that need a change go into the request; nothing to change → no request | Always builds and sends a request |
| Requests | **Two** requests per config sequence × node group: HSS IFC (capabilities, IFC, VNI, APN/pdnType) and **HLR** (TCSI/OCSI = refinmocName / refinmtcName), each with its own `.res` and per-IMSI result check | One request (`${ORDERNO}.xml`) from `create_xml.py`, one `.res`, batch-level `result="success"` |
| APN | Verifies the CIQ APN exists in the subscriber's LDAP (and its pdnType) before building the request; aborts otherwise | Not checked |
| IFC | Numeric → `org` lookup, **several comma-separated IFCs** (count + names); name → used directly | Single numeric IFC → 5th field of `org` output |
| Delivery | XML written into the inbox as provgw (`base64 -d >`), duplicate guard: `.res` already in outbox → abort | Framework SFTP upload; the "absent before" check only continues; ownership `smcuser:smcgroup` (answer orch_q29) not applied |
| Evidence | Pre and post values per IMSI saved under `/mnt/shared_data/${CHILD_REQ_ID}/${NODE}/{precheck,postcheck}`; per-field post-check flags (VNIOK, REFINMOCOK, …) fail the step; comparison report by a central script into `REPORT_PATH` | Pre/post `ldapsearch` registered in memory; generated `create_html_report.py` decides |
| Scripts | Pre-installed central scripts (`ACTIVITY_PYTHON_SCRIPT_PATH`) | Generated per run in the working directory |
| Naming | `hss_IFC_${CHILD_REQ_ID}_<configSeq>_<nodeGroup>.xml` | `${ORDERNO}.xml` |
| Backup / rollback | None (rollback manual) | None — consistent with answers na_q2/orch_q45 |

## Gaps

| # | Gap | Severity | Cause | Why we missed it |
|---|-----|----------|-------|------------------|
| G-1 | **Pre-health failure does not stop the activity** | High | R, P, F | The MOP never says a failed health check blocks the activity; step 6 marks health checks `on_failure: continue` and has no gate pattern (flag + stop) |
| G-2 | **No NIAM failover** (only `niamID-1`) | High | P, F | Requirements had `niam_failover: true` and the CIQ has NIAM_NAME1/2/3; step 6 has no failover pattern (probe each entry, pick the first that answers) |
| G-3 | **No test-subscriber guard** (any IMSI in the CIQ is modified) | High | R | The whitelist file and the rule "only listed test IMSIs per zone" are in no document |
| G-4 | **HLR changes not provisioned separately**: refinmocName / refinmtcName (TCSI/OCSI) need their own HLR request and response | High | P, C | Answers cmd_q28/29 point to `hlr_csi.xml` as a separate sample; the pipeline merged everything into one request. The MOP says only "copy this XML" |
| G-5 | **No change detection**: requests are sent even when LDAP already has the target values; blank vs UNCHANGED vs value decided only in the script | Medium | C, P | orch_q32 defines UNCHANGED / blank / value; cmd_q11 says "always modify existing entries" — contradictory; compare-before-apply was never asked |
| G-6 | **Health-check scope**: "every remote node Link UP", "all CPU samples" checked as "one line matches" | Medium | P | Answers say "for all" (orch_q47, orch_q4); step 6 wrote "exists" regexes (same pattern as MRF M-6) |
| G-7 | **APN not validated** before the request (and pdnType per APN) | Medium | R, P | cmd_q33 says pdnType is modified for a particular APN; nothing says the APN must exist first |
| G-8 | **Multiple IFCs** per row not handled | Medium | R | No CIQ example or rule for comma-separated IFCs |
| G-9 | **Inbox file ownership/permissions** (`smcuser:smcgroup`, rw) not applied after the upload | Medium | P | Answer orch_q29 was captured (orchestration `file_permissions`) but not used by step 6 |
| G-10 | **Duplicate guard** only reports: a pre-existing `.res` should abort the request | Low | P | The check exists but with `on_failure: continue` |
| G-11 | **Per-IMSI results** not checked in the `.res` (batch result only) | Low | R | Answer cmd_q13 defines the batch rule only |
| G-12 | **Evidence and naming**: pre/post values not saved per IMSI to the shared path; files named `${ORDERNO}.xml` instead of `<type>_${CHILD_REQ_ID}_<configSeq>_<nodeGroup>.xml`; scripts generated instead of the central ones | Low | R, E | Shared-path layout, file naming and the runtime parameter name (`CHILD_REQ_ID`) are platform conventions in no document |
| G-13 | Records beyond the first not processed | — | P | **Fixed since** (DEP-9 record loops) — verify on the re-run |
| — | `su - root` + `whoami` vs `elevate_to_root` | — | E | Framework elevation; equivalent |

## Requirement-process findings (PGW)

| # | Finding | Cause |
|---|---------|-------|
| Q-1 | **149 questions answered**; the same question repeated up to 7 times (sar regex: orch_q75/78/96/104/120/123/126; Link UP ×6; `.res` regex ×6) | P — step 1 does not de-duplicate questions across rounds |
| Q-2 | **The user was asked for regexes** and answered in Python syntax (`(?P<idle_cpu>…)`) or with patterns that do not match the real output (`Updates Rejected:\s*0\s*Queries Rejected:\s*0` — the DEP-54 regex) | P — regexes must come from transcripts, never from the user |
| Q-3 | Answers mix test and production environment (`localhost`, `password123`, "local simulator for now") | R — no environment sheet; secrets asked as values |
| Q-4 | Answers carry template syntax the framework does not support (`${{CIQ.Profile Modification.Mandatory Cap}}`, `${{node_row.RDS.NIAM_NAME1}}`) | P — step 1's suggested defaults leak internal syntax |

## Not to copy from production

`node_row.*` / `${nodeGroups}` / `ng_row.configSequences` (older data model), `${base64:VAR}` interpolation, register
entries with `name/value/when` and `loop/count_var`, `retry_on`, step `node: "${TargetNode}"` (dynamic node), password
in clear text (`reply: "siemens"`) — check each against our JAR before using; several are not in our v1.2 schema.

## Proposed fixes

See [tasks.md](tasks.md) (P009-1x): health-check gate (G-1), NIAM failover pattern (G-2), test-subscriber guard as a
step 1 question (G-3), one request per XML sample type (G-4), compare-before-apply (G-5), "every item" checks (G-6,
shared with MRF P009-06), APN pre-validation (G-7), applying captured file ownership (G-9), duplicate guard stops (G-10),
step 1 question de-duplication and no regex questions (Q-1, Q-2).
