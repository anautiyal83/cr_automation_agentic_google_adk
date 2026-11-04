# Tasks: Role-Based Login

**Input**: [spec.md](spec.md), [plan.md](plan.md)

Implement step by step; each step is reviewed (and committed) before the next.

## Step 0: Decisions
- [X] T000 Decisions D1–D7 (2026-11-04)
- [X] T000d D9: login with email address
- [X] T000c D8: activity trail of every action, admin-only separate screen
- [X] T000b Q1: only action buttons (start / delete / update) are disabled on View screens; reading buttons stay enabled

## Step 1: Data + service (no UI change) — done 2026-11-04
- [X] T001 Schema (`storage/db.py`): `roles` (levels per screen as JSON), `users` (email login, `must_change_password`), `audit_log` + indexes; migration adds `pipeline_runs.started_by` (+ index) and `users.must_change_password` — checked on a copy of the real DB (10 runs kept)
- [X] T002 `core/auth.py`: screens + admin-only screens, View/Update levels, PBKDF2-SHA256 310k, password policy, email validation, storage secret, `AuthService` (default administrator from `NETCONFIG_ADMIN_EMAIL`/`NETCONFIG_ADMIN_PASSWORD` else `admin@netconfig.local`/`admin` + forced change; lockout; same message for unknown email; users/roles CRUD; default/last administrator and admin-role protection), `CurrentUser.can_view/can_update/owns_run`, CLI `python -m core.auth set-password <email>` (recorded in the trail)
- [X] T003 `tests/test_auth.py` (10 tests)
- [X] T003a `core/audit.py` (append-only `AuditTrail`: record never raises, secrets masked, query/count/distinct filters, CSV export) + `tests/test_audit.py` (3 tests)

## Step 2: Login gate — done 2026-11-04
- [X] T004 Probe NiceGUI 3.17.1 (no upgrade needed): `BaseHTTPMiddleware` reads the signed `app.storage.user`; no session → 307 to `/login`; static assets and websocket stay reachable; a media file is served with a session and redirected (content not leaked) without one
- [X] T005 `ui/pages/login_page.py`: email + password form, forced new-password form (must differ from the current one), `/logout`, safe `redirect_to` (local paths only, never /login or /logout), trail entries login / login.failed / login.locked / logout / session.expired / password.changed
- [X] T006 `ui/app.py`: `storage_secret` in `ui.run`, `AuthMiddleware`, 12 h session expiry, `AuthService` + `AuditTrail` created at start with `ensure_defaults()`; main page requires an active user who has set their password
- [X] T007 Preview (DB copy): `/`, `/?page=settings`, a media URL and `/logout` without session → `/login` (deep link kept); wrong password message; default admin forced to change (main page refused until then); logout; login with the new password (email case-insensitive); all six trail entries recorded; tests for public paths and redirect targets

## Step 3: Screens and levels — done 2026-11-04
- [X] T008 Main page: user per load (disabled/deleted → logout), sidebar only permitted screens (empty groups hidden), panels of other screens not built, `navigate()` refuses them (notice + trail `screen.open` refused), deep link to one refused and recorded, landing on the first permitted screen, cross-screen calls None-safe, artifact cache panel admin only
- [X] T009 `ui/access.py`: `action()` marks 32 action buttons (Live Run, New Run, Run History, Artifact Cleanup, LLM Usage, Settings, Vendor Profiles; table-row Re-run/Delete by class); View panels get `cr-readonly` (CSS disables marked buttons and uploads, "View only" pill); buttons with a screen are disabled outright (dialogs). Server guard `@requires(screen, action)` on 21 handlers, `@requires_admin` on cache clearing; refusals notified and recorded
- [X] T010 Header user menu: name + role, email, Change password dialog (trail), Sign out
- [X] T011 Preview (DB copy, role analyst: Dashboard/Run History/Output Documents View, Live Run Update): admin sees 8 screens + cache; Ana 4 screens, no cache, `/?page=settings` refused (recorded), View pills, Cleanup Old Runs disabled, Re-run greyed; devtools bypass (class removed) → server refuses Re-run, notification + trail `run.rerun` refused; Live Run (Update) interactive. `tests/test_access.py`

## Step 4: Run ownership — done 2026-11-04
- [X] T012 `started_by` recorded by the runner (`runner.start(..., started_by=)`: the logged-in user from New Run and Run History → Re-run; `cli` from main.py / test_run.py); `runner.active_run()`
- [X] T013 `ui/access.py` `RunScope` (SQL filter, `owns`, `check` with notice + trail) used in Dashboard (runs + LLM totals), Live Run (restore of the active run, DB fallback, opening a run, restart/stop/cancel/milestone/questionnaire via `@_own_run`), Output Documents (run list, selection, jump from other screens), Run History (list with status filter, details, open, outputs, re-run, cleanup), LLM Usage (view and clear only own runs: `load_usage_stats/clear_usage_stats(owner=)`), Artifact Cleanup (list, delete, bulk). One pipeline at a time: `may_start_run` refuses a start while another user's run is active (spec)
- [X] T014 Preview (DB copy, 10 ownerless runs; Ana owns 2, Bob 1): admin sees 10 on Dashboard/Run History/Output Documents/Artifact Cleanup, Ana 2, Bob 1. `tests/test_access.py`: scope filter/owns/check (refusal recorded), ownerless runs admin-only, usage by owner, one pipeline at a time

## Step 5: Users & Roles screen — done 2026-11-04
- [X] T015 `ui/pages/users_section.py` Users tab: table (email, name, role, Active/Disabled, "Must set password", last login); Add user (email login, name, role, initial password with Generate; the user sets their own at first login), Edit (name, role, active; default administrator locked), Reset password (forced change at next login), Delete (not self, not the default administrator); every change and failure in the trail with before/after
- [X] T016 Roles tab: table (role, description, screens summary, users); Add/Edit role with None/View/Update per screen grouped like the sidebar; Delete (not admin, not in use); built-in admin row read-only. Admin-only screen in the sidebar (Admin group), all handlers `@requires_admin`
- [X] T017 Preview (DB copy): admin creates role "auditor" (Run History View, Output Documents Update) and user carol@example.com (weak password refused); Carol forced to set her password, sees Output Documents + Run History only, Users & Roles link refused; admin adds Dashboard → Carol has it on reload; admin disables Carol → her session ends; default admin Delete disabled; trail: role.create, user.create (failed + done), screen.open refused, role.update (before/after), user.update (before/after)

## Step 6: Activity trail — done 2026-11-04
- [X] T019 Recording points (after success, with details): run.start (UC, documents, review flag), run.rerun (from run), run.restart / stop / cancel, milestone.decision (step, decision), questionnaire.submit (answers / defaults), run.delete, runs.bulk_cleanup, runs.cleanup, usage.clear, cache.clear(_all), settings.save (values; API key only "changed"/"set"), vendor / node type / activity save + delete, document.upload, document.view / document.download (run + file inside the run) — besides step 1–5 entries (logins, users, roles, refusals). Sync callers take the user at once (`record_later`): a scheduled task has no page context
- [X] T020 Pipeline events as `system` from the runner's own subscriber (`PipelineRunner._trail_event`): step.started, step.passed / step.failed, step.retry (target step), milestone.reached, run.completed / failed / cancelled, with run and use case
- [X] T021 `ui/pages/audit_section.py`: admin-only Audit Trail (Admin group) — filters date range (last 7 days by default), user, action, outcome, run id / use case, text; newest first, 50 per page; outcome pills; row → details dialog (JSON); Export CSV of the filter (recorded as audit.export)
- [X] T022 Preview (DB copy): Ana views and downloads documents of her run and follows two refused links → admin's Audit Trail filtered by Ana shows login, document.view, document.download (run aac54518, file), screen.open refused ×2; details dialog; CSV export (5 rows); Ana cannot open the screen. `tests/test_audit.py` (system events), `tests/test_access.py` (document entries)

## Follow-up (2026-11-04)
- [X] T023 D10: runs from before the login belong to the default administrator — `AuthService.ensure_defaults` assigns ownerless runs to it at every start (CLI runs keep `cli`); test

## Follow-up (2026-11-04)
- [X] T024 D11: the trail holds user actions only — the runner records no system entries (steps, retries, queue dispatch, restart recovery; the flow log has them) and reads hide older `system` entries

## Step 7: Wrap-up — done 2026-11-04
- [X] T018 README "Login & Access" (first start, env vars, set-password, roles/levels, own runs, one pipeline at a time, audit trail, secret, HTTPS, CLI); CLAUDE.md "Login & Access" (how to add action buttons, run scope, trail) + structure
