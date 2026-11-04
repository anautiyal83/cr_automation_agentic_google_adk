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

## Step 3: Screens and levels
- [ ] T008 Main page: user per load (disabled/deleted → logout), sidebar filter, only permitted panels, guarded navigation and deep links, landing screen
- [ ] T009 `ui/access.py`: mark action buttons in every section, disable them on View panels (after build and after refreshes); `require(update)` in every changing handler
- [ ] T010 Header: user + role, Change password, Logout
- [ ] T011 Check SC-002

## Step 4: Run ownership
- [ ] T012 `started_by` recorded by the runner (UI user / `cli`)
- [ ] T013 Run scope in Dashboard, Live Run, Output Documents, Run History, LLM Usage, Artifact Cleanup; ownership check on run-id actions and file View/Download
- [ ] T014 Check SC-004 with two users

## Step 5: Users & Roles screen
- [ ] T015 Users tab (add, role, enable/disable, reset password, delete)
- [ ] T016 Roles tab (None/View/Update per screen, delete rules)
- [ ] T017 Check SC-003 end to end

## Step 6: Activity trail
- [ ] T019 Recording points: sessions, users/roles, runs, milestone decisions, step 1 answers, documents, settings/vendors/cleanup/cache/usage, refused attempts
- [ ] T020 Pipeline events as `system` (step started/passed/failed, retries, run end)
- [ ] T021 `ui/pages/audit_section.py`: admin-only Audit Trail screen with filters, details, CSV export
- [ ] T022 Check SC-006

## Step 7: Wrap-up
- [ ] T018 CLAUDE.md (login, env vars, secret, set-password CLI), README, cross-links
