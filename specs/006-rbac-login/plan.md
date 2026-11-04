# Implementation Plan: Role-Based Login

**Spec**: [spec.md](spec.md) | **Date**: 2026-11-04

## Target Design

```
Browser ──► FastAPI/NiceGUI (port 8000)
              │
              ├─ AuthMiddleware (every request)
              │     no session / expired / unknown user ─► 302 /login?redirect_to=…
              │     allowed without login: /login, /_nicegui/<static assets>
              │     (media files /_nicegui/auto/media/… are NOT exempt)
              │
              ├─ /login  (ui/pages/login_page.py)
              │     no users yet → "Create administrator" form
              │     else → username + password → AuthService.authenticate
              │     success → app.storage.user = {user_id, login_at} → redirect
              │
              └─ /  main page (ui/app.py)
                    user = AuthService.current_user(app.storage.user.user_id)   ← DB, every load
                    menu_groups filtered by user.permissions
                    only permitted panels are built; navigate() refuses others
                    header: user · role · Change password · Logout
                    "users" permission → Users & Roles panel (ui/pages/users_section.py)

core/auth.py ── AuthService(db): users, roles, hashing, policy, lockout, last-admin rules
storage/db.py ── tables roles, users (CREATE IF NOT EXISTS — existing DBs migrate on start)
```

## Components

| # | Component | File | Notes |
|---|-----------|------|-------|
| C1 | Schema | `storage/db.py` | `roles(id, name UNIQUE, description, permissions JSON, is_system, created_at)`; `users(id, username UNIQUE NOCASE, display_name, password_hash, role_id, active, created_at, last_login_at, password_changed_at)` |
| C2 | Auth service | `core/auth.py` | `PERMISSIONS` registry (screen key → label, group) — single source for sidebar, role editor and checks; `hash_password`/`verify_password` (PBKDF2-SHA256, 310k, 16-byte salt, `hmac.compare_digest`); `check_password_policy`; `storage_secret()`; `AuthService`: `ensure_defaults`, `has_users`, `authenticate` (lockout, dummy-hash timing), `current_user`, users CRUD, roles CRUD, last-admin guard; `CurrentUser.can()` |
| C3 | Session + middleware | `ui/app.py` | `ui.run(storage_secret=storage_secret())`; Starlette `BaseHTTPMiddleware` checking `app.storage.user` (`user_id`, `login_at` < 12 h) |
| C4 | Login page | `ui/pages/login_page.py` | Login form; first-admin setup form when no user exists; same theme as the app; `redirect_to` only to local paths (no open redirect) |
| C5 | Main page gating | `ui/app.py` | Resolve user at page load (inactive/deleted → clear session, redirect); filter `menu_groups`; build only permitted panels; guard `navigate()`; deep link `?page=` checked; land on first permitted screen |
| C6 | Header user menu | `ui/app.py` | Display name + role pill; menu: Change password (dialog), Logout (clear `app.storage.user` → `/login`) |
| C7 | Users & Roles screen | `ui/pages/users_section.py` | Tabs **Users** (table: username, name, role, status, last login; add / edit role / enable-disable / reset password / delete) and **Roles** (table: name, description, screens, #users; add / edit with a checkbox per screen grouped like the sidebar / delete). Errors from `AuthError` shown as notifications |
| C8 | Run ownership (Q5) | `storage/db.py`, `orchestrator/runner.py`, Run History | `pipeline_runs.started_by` column; shown in Run History |
| C9 | Tests | `tests/test_auth.py` | Service-level (temp DB): hashing round trip and no plain text, policy, lockout, unknown-user message, current_user reflects role edits/disable, last-admin rules, role delete rules, admin role immutable, permission filtering helper |

## Key Decisions

| ID | Decision | Why |
|----|----------|-----|
| K1 | Screen-level permissions, keyed by the existing section keys | Matches the request ("only screens with permission should be visible"); the UI already switches panels by these keys |
| K2 | Permissions re-read from the DB on each page load, not cached in the cookie | Role edits / disabling take effect without logout (FR-003) |
| K3 | Unpermitted panels are not built at all (not just hidden) | Hidden NiceGUI elements still exist client-side and their handlers are callable; not building them removes the attack surface |
| K4 | Stdlib PBKDF2 instead of bcrypt/argon2 | No new dependency (non-functional requirement); 310k iterations is the OWASP 2023 PBKDF2-SHA256 guidance |
| K5 | NiceGUI `app.storage.user` (signed cookie + server-side store) for the session | Built into NiceGUI 3.x; no separate session table |
| K6 | Lockout state in memory | Single server process; a restart clearing it is acceptable for this iteration |

## Security Notes
- Serve behind HTTPS (reverse proxy) in production — the session cookie travels with every request.
- `data/.storage_secret` and `data/pipeline.db` must not be shared; `data/` is git-ignored.
- Changing `NETCONFIG_STORAGE_SECRET` logs everyone out.
- Media URLs (`add_media_file`) are random but now also require a session (FR-001).

## Testing
- Unit: `tests/test_auth.py` (C9).
- UI (preview server on a DB copy, headless browser): login redirect for `/`, `/?page=settings` and a
  media URL; admin creates role `viewer2` + user; that user's sidebar and deep links; role edit
  applies on reload; logout.

## Rollout
1. Existing installations: on first start after the upgrade the tables are created and the login page
   asks for the first administrator (or env vars create it).
2. Existing runs are unaffected; `started_by` is empty for them.

## Risks
- NiceGUI middleware + `app.storage.user` must be verified on 3.17.1 (probe in T003).
- The preview/screenshot harness (`ui.run` in a script) needs the storage secret too.
