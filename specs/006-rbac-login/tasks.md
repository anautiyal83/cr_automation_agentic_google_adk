# Tasks: Role-Based Login

**Input**: [spec.md](spec.md), [plan.md](plan.md)

Implement step by step; each step is reviewed (and committed) before the next.

## Step 0: Decisions
- [X] T000 Decisions D1–D7 (2026-11-04)
- [ ] T000b Q1: reading buttons (View, Download, Refresh) on View screens — enabled or disabled?

## Step 1: Data + service (no UI change)
- [~] T001 Schema: `roles`, `users` tables (drafted before the spec) — add `must_change_password`,
  levels per screen in `roles.permissions`, `pipeline_runs.started_by` migration
- [~] T002 `core/auth.py` (drafted before the spec) — rework for D1/D2/D6: screen levels, only the
  admin role, default `admin` user + forced change, `admin` user protected, `set-password` CLI
- [ ] T003 `tests/test_auth.py`

## Step 2: Login gate
- [ ] T004 Probe NiceGUI 3.17.1: middleware + `app.storage.user` + `storage_secret`; media URL blocked without session
- [ ] T005 `ui/pages/login_page.py`: login form, forced new-password form, safe `redirect_to`
- [ ] T006 `ui/app.py`: storage secret, `AuthMiddleware`, 12 h expiry, `AuthService` singleton + `ensure_defaults()`
- [ ] T007 Preview harness with secret + login; check SC-001

## Step 3: Screens and levels
- [ ] T008 Main page: user per load (disabled/deleted → logout), sidebar filter, only permitted panels, guarded navigation and deep links, landing screen
- [ ] T009 `ui/access.py`: disable all buttons on View panels (after build and after refreshes); `require(update)` in every changing handler of the sections
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

## Step 6: Wrap-up
- [ ] T018 CLAUDE.md (login, env vars, secret, set-password CLI), README, cross-links
