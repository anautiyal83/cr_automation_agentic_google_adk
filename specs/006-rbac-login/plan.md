# Implementation Plan: Role-Based Login

**Spec**: [spec.md](spec.md) | **Date**: 2026-11-04

## Target Design

```
Browser ──► FastAPI/NiceGUI (port 8000)
              │
              ├─ AuthMiddleware (every request)
              │     no session / expired (12 h) ─► 302 /login?redirect_to=…
              │     allowed without login: /login, /_nicegui/<static assets>
              │     (media files /_nicegui/auto/media/… are NOT exempt)
              │
              ├─ /login  (ui/pages/login_page.py)
              │     username + password → AuthService.authenticate (lockout)
              │     must_change_password → "Set a new password" form first
              │     success → app.storage.user = {user_id, login_at} → redirect (local paths only)
              │
              └─ /  main page (ui/app.py)
                    user = AuthService.current_user(user_id)        ← DB, every page load
                    user.levels = {screen: "view" | "update"}       (admin: update everywhere)
                    sidebar + panels: only screens in user.levels
                    View screens: action buttons disabled (Access.apply)
                    changing handlers: Access.require(screen, "update") + RunScope.check(run_id)
                    run lists/queries: RunScope.filter(...)  (admin: all; others: started_by = user)
                    header: user · role · Change password · Logout
                    admin → Users & Roles panel (ui/pages/users_section.py)

core/auth.py ── AuthService(db), CurrentUser(levels, can_view, can_update, owns_run)
storage/db.py ── tables roles, users; pipeline_runs.started_by (migration)
```

## Components

| # | Component | File | Notes |
|---|-----------|------|-------|
| C1 | Schema | `storage/db.py` | `roles(id, name UNIQUE, description, permissions JSON {screen: level}, is_system, created_at)`; `users(id, username UNIQUE NOCASE, display_name, password_hash, role_id, active, must_change_password, created_at, last_login_at, password_changed_at)`; `pipeline_runs.started_by` added by migration |
| C2 | Auth service | `core/auth.py` | Screen registry `SCREENS` (key → label, group) shared by sidebar, role editor and checks; levels `view`/`update`; PBKDF2-SHA256 310k hashing; password policy; `storage_secret()`; `AuthService`: `ensure_defaults` (admin role + `admin` user, password from `NETCONFIG_ADMIN_PASSWORD` else `admin` + must-change), `authenticate` (lockout, dummy-hash timing), `current_user`, users CRUD, roles CRUD, admin/last-admin rules; `CurrentUser.can_view/can_update/owns_run`; CLI `python -m core.auth set-password <user>` |
| C3 | Session + middleware | `ui/app.py` | `ui.run(storage_secret=…)`; Starlette `BaseHTTPMiddleware` on `app.storage.user` |
| C4 | Login page | `ui/pages/login_page.py` | Login form; forced new-password form (FR-013); theme of the app |
| C5 | Screen gating | `ui/app.py` | Filter `menu_groups`; build only permitted panels; guard `navigate()`, deep links, in-page jumps; first permitted screen as landing page |
| C6 | View = read-only | `ui/access.py` (new) | Action buttons are marked where they are created (`action(btn)` → CSS class `cr-action`); `Access(user, screen)` disables every marked button in a View panel after it is built and after refreshes (reading buttons are not marked, stay enabled); `require(screen, "update")` guard in the sections' changing handlers |
| C7 | Run ownership | `orchestrator/runner.py`, sections | Runner stores `started_by`; a `RunScope` (user) gives the SQL filter (`started_by = ?` unless admin) and `check(run_id)`; applied in dashboard queries, Live Run (active-run restore, view_run), Output Documents run list, Run History, LLM Usage (per run), Artifact Cleanup; file View/Download only through owned runs |
| C8 | Header user menu | `ui/app.py` | Display name + role pill; Change password dialog; Logout |
| C9 | Users & Roles screen | `ui/pages/users_section.py` | Users tab (add, role, enable/disable, reset password → must change, delete) and Roles tab (add/edit: None/View/Update per screen grouped like the sidebar, delete); `AuthError` messages as notifications |
| C10 | Tests | `tests/test_auth.py`, `tests/test_access.py` | Service rules; level checks; run scope filter/check; button disabling on a View panel |

## Key Decisions

| ID | Decision | Why |
|----|----------|-----|
| K1 | Permission = (screen, level) with levels View/Update | D6; matches the UI's sections |
| K2 | Levels and ownership re-read from the DB on each page load | Role edits / disabling apply without logout (FR-003) |
| K3 | Screens without permission are not built at all | Hidden NiceGUI elements still exist and their handlers are callable |
| K4 | View enforced twice: action buttons (explicitly marked) disabled (D7) **and** a server-side `require(update)` in changing handlers | Disabled buttons are a UI hint; the server check is the security boundary; explicit marking keeps reading buttons enabled |
| K5 | Stdlib PBKDF2 (310k, OWASP 2023) | No new dependency |
| K6 | NiceGUI `app.storage.user` for the session; lockout in memory | Built in; single server process |
| K7 | Ownership by `started_by` on `pipeline_runs`; ownerless/CLI runs admin-only | D3/D5 without a sharing model |

## Security Notes
- Serve behind HTTPS (reverse proxy) in production.
- `data/.storage_secret` and `data/pipeline.db` must not be shared; `data/` is git-ignored.
- Changing `NETCONFIG_STORAGE_SECRET` logs everyone out.
- Default password `admin` is only usable to set a new one (forced change).

## Testing
- Unit: C10.
- UI (preview server on a DB copy, headless browser): redirect to `/login` for `/`, a deep link and a
  media URL; forced password change for `admin`; admin creates role (Run History = View) + user;
  that user sees only Run History, only own runs, action buttons disabled, View/Download working; role changed to Update →
  buttons enabled on reload; another user's run id refused; logout.

## Rollout
1. On the first start after the upgrade: tables created, `admin` user created; existing runs get no
   owner (admin-only).
2. Set the admin password with `NETCONFIG_ADMIN_PASSWORD` before the first start, or log in with
   `admin`/`admin` and set it, or `python -m core.auth set-password admin` on the server.

## Risks
- NiceGUI 3.17.1 middleware + `app.storage.user` to be probed first (T004).
- Action buttons a section creates later (dialogs, refreshed
  lists): the server-side `require(update)` covers those; the generic pass runs after each refresh.
- The preview/screenshot harness needs the storage secret and a login step.
