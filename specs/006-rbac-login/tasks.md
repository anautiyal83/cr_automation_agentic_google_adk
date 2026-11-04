# Tasks: Role-Based Login

**Input**: [spec.md](spec.md), [plan.md](plan.md)

Implement step by step; each step is reviewed (and committed) before the next.

## Step 0: Decisions
- [ ] T000 Confirm open questions Q1–Q5 in spec.md

## Step 1: Data + service (no UI change)
- [~] T001 Schema: `roles`, `users` tables in `storage/db.py` (drafted 2026-11-04, before the spec — review)
- [~] T002 `core/auth.py`: permission registry, hashing, policy, storage secret, `AuthService`
  (users/roles CRUD, lockout, last-admin and admin-role rules) (drafted 2026-11-04 — review)
- [ ] T003 `tests/test_auth.py` (plan C9)

## Step 2: Login gate
- [ ] T004 Probe NiceGUI 3.17.1: middleware + `app.storage.user` + `storage_secret`, media URL blocked without session
- [ ] T005 `ui/pages/login_page.py`: login form, first-admin setup form, safe `redirect_to`
- [ ] T006 `ui/app.py`: `storage_secret` in `ui.run`, `AuthMiddleware`, session expiry (12 h), `AuthService` singleton + `ensure_defaults()` at start
- [ ] T007 Preview harness passes the storage secret; check SC-001 (every URL → /login)

## Step 3: Screens by permission
- [ ] T008 Main page: resolve user per load (disabled/deleted → logout); filter sidebar; build only permitted panels; guard `navigate()` and deep links; land on first permitted screen
- [ ] T009 Header: user + role, Change password dialog, Logout
- [ ] T010 Check SC-002 with a `viewer` user (sidebar, deep links, in-page jumps)

## Step 4: Users & Roles screen
- [ ] T011 `ui/pages/users_section.py` — Users tab (add, role, enable/disable, reset password, delete)
- [ ] T012 Roles tab (add, edit screens by checkbox, delete with rules)
- [ ] T013 Check SC-003 end to end in the UI

## Step 5: Accountability (if Q5 = yes)
- [ ] T014 `pipeline_runs.started_by` (migration), set by the runner from the logged-in user, shown in Run History

## Step 6: Wrap-up
- [ ] T015 CLAUDE.md (login, env vars, secret), README run instructions, bugs/backlog cross-links
