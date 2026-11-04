# Feature Specification: Role-Based Login for the NetConfig UI

**Feature Branch**: `006-rbac-login` (work continues on `feature/skill-packages`)

**Created**: 2026-11-04

**Status**: Implemented 2026-11-04 (steps 1–7, see tasks.md); open: nothing

**Input**: "Let's implement a login system so we add security. We want a role-based login: an
admin who has access to all sections and who creates users and roles and assigns permissions to
other users. Only screens with permission should be visible to normal users." Follow-ups: "first
add or create specs and plan for this, then we will implement step by step"; the decisions below.

## Decisions (2026-11-04, user)

| # | Question | Decision |
|---|----------|----------|
| D1 | First administrator | A default administrator is created on first start, and its password can be set (no setup form) |
| D9 | Login name | Users log in with their **email address** (unique, case-insensitive); there is no separate username |
| D2 | Starting roles | Only the built-in **`admin`** role; the admin creates every other role |
| D3 | Which runs a normal user sees | **Only the runs they started** |
| D4 | Session length / lockout | 12 h sessions; 5 failed logins lock the email for 5 min |
| D5 | Run ownership | Every run records who started it; the **admin sees all runs**, every other user only their own |
| D6 | Permission levels | A permission is per screen and is either **View** or **Update**. View: the user can only look. Update: the user can look and change things on that screen. Only the admin has Update on every screen |
| D8 | Activity trail | Every action is recorded — who did what, when, on which run — so activities can be tracked. The trail is **visible to the admin only**, on a **separate screen** |
| D7 | View permission in the UI | On a screen with View permission **only action buttons are disabled** — those that start an activity, delete something, or update the pipeline configuration / a run. Reading buttons (View, Download, Refresh, details, navigation) stay enabled |

## Problem

The NiceGUI UI (port 8000) has no authentication. Anyone who can reach the server can start
pipelines, read every run's documents (CIQ data, node addresses, MOPs), change settings
(including the OpenRouter API key and JAR paths), and delete artifacts. All screens are visible
to everyone.

## Actors

| Actor | What they get |
|-------|---------------|
| **Administrator** (`admin` role) | Every screen with Update, every run, and the **Users & Roles** screen |
| **Normal user** | Only the screens their role grants — View or Update per screen — and on them only the runs they started |
| **Not logged in** | Only the login page; no page, file or download is reachable |

## Permission Model

- **Screens** (one permission each):

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
  | `users` | Users & Roles (new) | Admin — **admin role only**, not assignable to other roles |
| `audit` | Audit Trail (new) | Admin — **admin role only**, not assignable to other roles |

- **Level per screen**: *none* (screen hidden), **View**, or **Update** (D6).
- A **role** = name + description + a level for each screen. A **user** has exactly one role.
- Built-in role **`admin`**: Update on every screen (screens added later included) plus Users &
  Roles; cannot be edited or deleted. It is the only role at first start (D2).
- Built-in administrator account (role `admin`), created on first start (D1) with the email
  `NETCONFIG_ADMIN_EMAIL`, default `admin@netconfig.local`; cannot be deleted,
  disabled or moved to another role.

### View vs Update per screen (D6, D7)

With **View**, the screen and its data are shown and the **action buttons are disabled** (D7):
anything that starts an activity, deletes something, or updates the pipeline configuration or a
run. Reading buttons stay enabled. With **Update**, the action buttons work too, limited to the
user's own runs (see Run Visibility).

| Screen | Action buttons — disabled with View | Reading buttons — always enabled |
|--------|-------------------------------------|----------------------------------|
| Dashboard | — | — |
| New Run | Start the run; file uploads | Wizard Next / Back |
| Live Run | Cancel pipeline, Stop, Restart, Re-run step, milestone decisions (Approve & continue, Skip & continue, Clarify in requirements), Answer Questions, Submit Answers, Use All Defaults | View / Download artifact, View artifact, Browse outputs, View full details, tabs, step selection |
| Output Documents | Clear All Cache (admin only — the cache is shared) | View, Download, Refresh, run selection |
| Run History | Delete, Cancel, Cleanup Old Runs, re-run | View, Download, Open, details |
| LLM Usage | Clear | Refresh |
| Artifact Cleanup | Delete, Delete Old Runs, Bulk Cleanup | Refresh |
| Settings | Save Key, Save All Settings; Vendor Profiles: Add Vendor, Add Field, Add, Save, Delete | Export Seed (read-only export) |
| Users & Roles | admin only | admin only |
| Audit Trail | admin only (read-only screen) | admin only |

Server-side: every handler that changes something checks the user's level for that screen (and
run ownership), so a disabled button cannot be bypassed by a crafted event.

## Run Visibility (D3, D5)

Every run records the user who started it (`started_by`). The **admin** sees every run; any other
user sees **only their own runs** on every screen that shows runs:

| Screen | What a normal user sees |
|--------|-------------------------|
| Dashboard | Totals, charts, use-case table, LLM usage and recent runs over their own runs |
| Live Run | Their running run only; another user's live run is not shown |
| Output Documents | Run list and documents of their runs; the artifact-cache panel is admin-only |
| Run History | Their runs |
| LLM Usage | Usage of their runs |
| Artifact Cleanup | Their runs |
| Settings | Global settings (the screen permission decides) |

**One pipeline at a time.** The runner runs one pipeline at a time (milestone state is global) and a
new start replaces the active run. A user may replace only their own active run: when another user's
run is in progress, a start (New Run or Run History → Re-run) is refused with a message (the admin is
told who started it, others "another user") and the refusal is recorded. The admin stops another
user's run explicitly from Live Run.

Runs created before this feature have no owner: only the admin sees them. Runs started from the
CLI record `cli` (admin only). Ownership is checked on the server for every action that takes a
run id (open, re-run, cancel, delete, milestone decision, view/download a file), not only by
filtering lists.

## Activity Trail (D8)

Every action is written to an append-only trail: **when**, **who** (user, role), **what**
(action), **on what** (run id / use case / user / role / setting), **outcome** (done, refused,
failed) and the details needed to understand it. Refused attempts (no permission, not the owner,
locked account) are recorded too.

| Area | Recorded actions |
|------|------------------|
| Sessions | login, failed login, account locked, logout, session expired, password changed |
| Users & roles | user created / role changed / enabled / disabled / password reset / deleted; role created / edited (old → new levels) / deleted |
| Pipeline runs | run started (UC, documents), cancelled, stopped, restarted, re-run of a step, deleted |
| Pipeline decisions | milestone decision per step (approve & continue, skip & continue, re-run, clarify in requirements), step 1 questions answered / defaults used |
| Pipeline progress (actor `system`) | step started / passed / failed, automatic retry and its target step, run completed / failed — so a run's trail shows what the pipeline did between user actions |
| Documents | document viewed or downloaded (run, file) |
| Configuration | settings saved (changed keys; secrets such as the API key are never written, only "changed"), vendor profile added / changed / deleted, cache cleared, artifacts / old runs deleted, LLM usage cleared |

- **Screen**: *Audit Trail* (Admin group), admin only. Table newest first with filters (date range,
  user, action, run / use case, outcome) and free-text search; a row expands to its details;
  export of the filtered rows as CSV. A run's trail is reachable from the filter (run id).
- **Integrity**: the UI and the service offer no way to edit or delete trail entries; entries are
  kept (no automatic purge in this iteration).
- Normal users never see the trail, not even for their own runs.

## User Scenarios

### US1 — Log in and see only my screens (P1)
1. Not logged in → any URL redirects to `/login`.
2. Valid credentials → I land on my first permitted screen; the sidebar lists only my permitted
   screens (empty groups hidden).
3. My role lacks Settings → `/?page=settings` does not build the screen and I land on my first
   permitted screen.
4. An in-page jump (e.g. Run History → Open → Live Run) to a screen I lack is refused with a message.

### US2 — View vs Update (P1)
1. My role has View on Run History → I see my runs, their details, and can View/Download their
   documents; Delete, Cancel, Cleanup Old Runs and re-run are disabled.
2. My role has Update on Run History → the buttons work, on my runs only.
3. A crafted event for a disabled action is refused on the server.

### US3 — Administrator manages users (P1)
1. Create a user: email (the login), display name, initial password, role. The email must be a
   valid address and not used by another user.
2. Change a user's role, disable/enable, reset the password (the user must change it at next
   login), delete.
3. The default administrator account and the last active administrator cannot be deleted, disabled or demoted;
   nobody can delete their own account.
4. A disabled or deleted user cannot log in; an open session ends on the next page load.

### US4 — Administrator manages roles (P1)
1. Create a role: for each screen choose None / View / Update.
2. Edit a role; its users get the new levels on their next page load.
3. A role still assigned to users cannot be deleted; `admin` cannot be edited or deleted.

### US5 — Default administrator (P1)
1. First start creates the administrator account: email `NETCONFIG_ADMIN_EMAIL` (default
   `admin@netconfig.local`). Initial password: `NETCONFIG_ADMIN_PASSWORD` when set,
   otherwise `admin` — and then the password must be changed at the first login before any screen
   opens.
2. The password can be set any time on the server — `python -m core.auth set-password admin@netconfig.local`
   (prompts twice; also recovers a lost password) — or in the UI (own password via the user menu).

### US6 — Only my runs (P1)
1. User A started R1, user B started R2 → A sees only R1 on Dashboard, Live Run, Output Documents,
   Run History, LLM Usage and Artifact Cleanup.
2. A acting on R2's id (crafted event or URL) is refused.
3. The admin sees R1, R2 and the runs without an owner.

### US8 — Activity trail (P1)
1. User A logs in, starts a run, approves a milestone and downloads the MOP; the pipeline retries
   step 6 → the admin's Audit Trail shows each of these with time, user (or `system`), run and details.
2. A View user's refused attempt to cancel a run appears as *refused*.
3. The admin filters by user A and a date range and exports the rows as CSV.
4. A normal user cannot open the Audit Trail (not in the sidebar, deep link refused).

### US7 — Account hygiene (P2)
1. Every user can change their own password (header → user menu), confirming the current one.
2. Logout from the user menu ends the session; sessions expire after 12 h.
3. Five failed logins for an email lock it for 5 minutes.

## Requirements

### Functional
- **FR-001** Every HTTP route except `/login` and NiceGUI's static assets MUST require a logged-in
  session — including served files (`/_nicegui/auto/media/…`, used by View/Download).
- **FR-002** The main page MUST build only the screens the user's role grants (View or Update);
  the sidebar MUST list only those; navigation (sidebar, deep link, in-page jumps) MUST refuse others.
- **FR-003** Levels and run ownership MUST be read from the database on every page load, so role
  edits, disabling and deletion apply without a server restart.
- **FR-004** On a View screen every action button MUST be disabled (D7: start, delete, update);
  reading buttons stay enabled; every changing handler MUST check Update (and run ownership) on
  the server.
- **FR-005** Passwords MUST be stored only as salted PBKDF2-SHA256 hashes (≥ 310 000 iterations);
  plain passwords are never logged or stored. Policy: ≥ 8 characters, not only letters or only digits.
- **FR-006** Login is by email + password. Failures MUST NOT reveal whether the email exists (same
  message, same work). Emails are stored lower-case and compared case-insensitively.
- **FR-007** The session cookie MUST be signed with `NETCONFIG_STORAGE_SECRET`, else a random secret
  generated once into `data/.storage_secret` (git-ignored).
- **FR-008** The `admin` role, the default administrator account and the last active administrator MUST be protected
  (US3.3, US4.3).
- **FR-009** The header MUST show the logged-in user and role, with Change password and Logout.
- **FR-010** CLI entry points (`main.py`, `test_run.py`, `test_step.py`) need no login.
- **FR-011** Every run MUST record `started_by` (user id; `cli` from the CLI).
- **FR-012** Non-admin users MUST see and act on only their own runs (US6); the admin sees all.
- **FR-014** Every action in the Activity Trail table MUST be recorded with time, user, role,
  action, target, run, outcome and details; refused attempts included; secrets never recorded.
- **FR-015** The trail MUST be append-only (no edit/delete in UI or service) and visible only to the
  admin on the separate Audit Trail screen, with filters and CSV export.
- **FR-013** A user with the default password, or whose password the admin reset, MUST set a new one
  at the next login before any screen opens.

### Non-functional
- No new third-party dependency (stdlib `hashlib`/`hmac`/`secrets` + NiceGUI user storage).
- Users and roles live in the existing SQLite DB (`data/pipeline.db`).

## Out of Scope (this iteration)
- Single sign-on / LDAP / OAuth, multi-factor authentication.
- Sharing runs between users; per-use-case permissions.
- Finer levels than View/Update (e.g. per button).
- HTTPS termination (deploy behind a TLS proxy; see plan).

## Success Criteria
- **SC-001** Without a login, every page and file URL redirects to `/login`.
- **SC-002** A role with View on Run History and nothing else: the user sees only Run History, with
  only their runs, action buttons disabled and View/Download working; `/?page=<other screen>` opens nothing else.
- **SC-003** The admin creates a role and a user in the UI; the user sees exactly the role's screens
  at the role's levels; after the admin edits the role, the user sees the new levels on reload.
- **SC-004** User A sees only A's runs on every screen; actions on B's run id are refused; the admin
  sees all runs.
- **SC-006** Every action of US8 appears in the Audit Trail with the right user, run and outcome; a
  normal user cannot reach the screen; there is no way to change or delete an entry.
- **SC-005** The DB contains no plain-text password; unit tests cover hashing, policy, lockout,
  default admin, forced password change, level checks, ownership checks, last-admin and role rules.
