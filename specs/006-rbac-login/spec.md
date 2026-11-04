# Feature Specification: Role-Based Login for the NetConfig UI

**Feature Branch**: `006-rbac-login` (work continues on `feature/skill-packages`)

**Created**: 2026-11-04

**Status**: Draft — spec and plan for review; implementation step by step after approval

**Input**: "Let's implement a login system so we add security. We want a role-based login: an
admin who has access to all sections and who creates users and roles and assigns permissions to
other users. Only screens with permission should be visible to normal users." Follow-up: "first
add or create specs and plan for this, then we will implement step by step".

## Problem

The NiceGUI UI (port 8000) has no authentication. Anyone who can reach the server can start
pipelines, read every run's documents (CIQ data, node addresses, MOPs), change settings
(including the OpenRouter API key and JAR paths), and delete artifacts. All nine screens are
visible to everyone.

## Users and Roles

| Actor | What they need |
|-------|----------------|
| **Administrator** | Every screen, plus a **Users & Roles** screen: create/disable/delete users, reset passwords, create roles, choose each role's screens, assign a role to each user |
| **Normal user** | Logs in and sees only the screens their role grants — in the sidebar *and* by URL (`/?page=settings` must not open a screen they lack) |
| **Anyone not logged in** | Sees only the login page; no page, file or download is reachable |

## Permission Model

- A **permission** = access to one screen. Screens today:

  | Key | Screen | Sidebar group |
  |-----|--------|---------------|
  | `dashboard` | Dashboard | Workspace |
  | `new_run` | New Run | Workspace |
  | `progress` | Live Run | Workspace |
  | `outputs` | Output Documents | Results |
  | `history` | Run History | Results |
  | `usage` | LLM Usage | Admin |
  | `cleanup` | Artifact Cleanup | Admin |
  | `settings` | Settings (incl. Vendor Profiles) | Admin |
  | `users` | Users & Roles (new) | Admin |

- A **role** = a name, a description and a set of permissions. A **user** has exactly one role.
- Built-in role **`admin`**: always every permission (screens added later included); it cannot be
  edited or deleted.
- Default roles created on first start (editable by the admin): **`operator`** — Dashboard, New
  Run, Live Run, Output Documents, Run History; **`viewer`** — Dashboard, Live Run, Output
  Documents, Run History.

## User Scenarios

### US1 — Log in and see only my screens (P1)
1. Given I am not logged in, when I open any URL, then I am sent to `/login`.
2. Given valid credentials, when I log in, then I land on my first permitted screen and the
   sidebar lists only my permitted screens (empty groups hidden).
3. Given my role lacks Settings, when I open `/?page=settings`, then the screen is not built and
   I land on my first permitted screen.
4. Given a screen offers a jump to another screen (e.g. Run History → "Open" → Live Run), when I
   lack the target screen, then the jump is refused with a message.

### US2 — Administrator manages users (P1)
1. The admin creates a user with username, display name, initial password and role.
2. The admin changes a user's role, disables/enables them, resets their password, deletes them.
3. The admin cannot delete themselves, and the last active admin cannot be disabled, deleted or
   moved to another role.
4. A disabled user cannot log in; their open session ends on the next page load.

### US3 — Administrator manages roles and permissions (P1)
1. The admin creates a role and ticks the screens it may see.
2. The admin edits a role's screens; users of that role get the new set on their next page load.
3. A role still assigned to users cannot be deleted; `admin` cannot be edited or deleted.

### US4 — First start (P1)
1. Given no user exists, the login page asks to create the first administrator (username,
   password twice) — or the admin is created from `NETCONFIG_ADMIN_USER` /
   `NETCONFIG_ADMIN_PASSWORD` when set (headless deployments).
2. After that, the setup form is never shown again.

### US5 — Account hygiene (P2)
1. Every user can change their own password (header → user menu), confirming the current one.
2. Logout from the user menu ends the session.
3. Sessions expire after 12 hours.
4. Five failed logins for a username lock it for 5 minutes.

## Requirements

### Functional
- **FR-001** Every HTTP route except `/login` and NiceGUI's static assets MUST require a logged-in
  session — this includes served files (`/_nicegui/auto/media/…`, used by View/Download).
- **FR-002** The main page MUST build only the screens the user's role permits; the sidebar MUST
  list only those; navigation (sidebar, deep link, in-page jumps) MUST refuse others.
- **FR-003** Permissions MUST be read from the database on every page load, so role changes,
  disabling and deletion take effect without a server restart.
- **FR-004** Passwords MUST be stored only as salted PBKDF2-SHA256 hashes (≥ 310 000 iterations);
  plain passwords are never logged or stored.
- **FR-005** Password policy: ≥ 8 characters, not only letters or only digits.
- **FR-006** Login failures MUST NOT reveal whether the username exists (same message, same work).
- **FR-007** The session cookie MUST be signed with a secret from `NETCONFIG_STORAGE_SECRET`, else
  a random secret generated once into `data/.storage_secret` (git-ignored).
- **FR-008** The admin role and the last active administrator MUST be protected (US2.3, US3.3).
- **FR-009** The header MUST show the logged-in user and role, with Change password and Logout.
- **FR-010** CLI entry points (`main.py`, `test_run.py`, `test_step.py`) are unaffected — the
  login guards the UI only.

### Non-functional
- No new third-party dependency (stdlib `hashlib`/`hmac`/`secrets` + NiceGUI user storage).
- Users and roles live in the existing SQLite DB (`data/pipeline.db`).

## Out of Scope (this iteration)
- Single sign-on / LDAP / OAuth, multi-factor authentication.
- Per-run or per-use-case data permissions (who may see which runs) — every permitted screen
  shows all runs.
- Action-level permissions inside a screen (e.g. view runs but not cancel them) — a screen is
  all or nothing.
- HTTPS termination (deploy behind a TLS proxy; see plan).
- Audit log of admin actions (candidate for a later iteration).

## Open Questions (please confirm)

| # | Question | Proposed default |
|---|----------|------------------|
| Q1 | First administrator: setup form on the login page when no user exists, or only from env vars / a CLI command? | Setup form **and** env vars |
| Q2 | Default roles `operator` and `viewer` (above) — keep, rename or start with `admin` only? | Keep both |
| Q3 | Should normal users see all runs, or only the runs they started? | All runs (per-user runs out of scope) |
| Q4 | Session length 12 h and lockout 5 failures / 5 min — OK? | Yes |
| Q5 | Should a run record who started it (shown in Run History)? | Yes — small addition, useful for accountability |

## Success Criteria
- **SC-001** Without a login, every page and file URL redirects to `/login` (checked per route).
- **SC-002** A user with role `viewer` sees exactly Dashboard, Live Run, Output Documents and Run
  History, and `/?page=settings|new_run|cleanup|usage|users` opens none of those screens.
- **SC-003** The admin creates a user and a role in the UI; that user logs in and sees exactly the
  role's screens; after the admin edits the role, the user sees the new set on reload.
- **SC-004** The DB contains no plain-text password; unit tests cover hashing, policy, lockout,
  last-admin protection, role deletion rules.
