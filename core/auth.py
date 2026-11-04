"""Role-based login for the NetConfig UI (spec 006).

- Users log in with their email address + password (PBKDF2-SHA256, 310 000 iterations, per-user
  salt). Emails are stored lower-case.
- A role gives each screen a level: ``view`` (look only — action buttons disabled) or ``update``
  (look and act). A user has one role.
- The built-in ``admin`` role has ``update`` on every screen plus the admin-only screens (Users &
  Roles, Audit Trail); it cannot be changed or deleted. The default administrator account and the
  last active administrator cannot be deleted, disabled or demoted.
- Five failed logins lock an email for five minutes (in memory, per server process).
- ``python -m core.auth set-password <email>`` sets a password on the server.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

VIEW, UPDATE = "view", "update"
LEVELS = (VIEW, UPDATE)
# Screens a role can grant: key (the UI section) → (label, sidebar group)
SCREENS: dict[str, tuple[str, str]] = {
    "dashboard": ("Dashboard", "Workspace"),
    "new_run": ("New Run", "Workspace"),
    "progress": ("Live Run", "Workspace"),
    "queue": ("Pipeline Queue", "Workspace"),
    "outputs": ("Run Artifacts", "Results"),
    "history": ("Run History", "Results"),
    "usage": ("LLM Usage", "Admin"),
    "cleanup": ("Artifact Cleanup", "Admin"),
    "settings": ("Settings", "Admin"),
}
# Screens only the admin role has (never assignable to another role)
ADMIN_SCREENS: dict[str, tuple[str, str]] = {
    "cache": ("Artifact Cache", "Results"),  # shared by every user's runs (spec 008 U-D3)
    "users": ("Users & Roles", "Admin"),
    "audit": ("Audit Trail", "Admin"),
}
# Screens split off an older screen: a role saved before keeps the old screen's level (spec 008 U-D1)
_DERIVED_SCREENS = {"queue": "progress"}
ADMIN_ROLE = "admin"
DEFAULT_ADMIN_EMAIL = "admin@netconfig.local"
DEFAULT_ADMIN_PASSWORD = "admin"  # only good for setting a new one (must_change_password)
MIN_PASSWORD_LENGTH = 8
MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 300
SESSION_HOURS = 12
_ITERATIONS = 310_000
_EMAIL = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
                    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+$")


class AuthError(ValueError):
    """A user-facing reason an auth operation was refused."""


# --------------------------------------------------------------------------- passwords

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        _ITERATIONS, base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        computed = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                       base64.b64decode(salt), int(iterations))
        return hmac.compare_digest(computed, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


def check_password_policy(password: str) -> None:
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise AuthError(f"Password must have at least {MIN_PASSWORD_LENGTH} characters")
    if password.isdigit() or password.isalpha():
        raise AuthError("Password must mix letters with digits or symbols")


def normalize_email(email: str) -> str:
    email = (email or "").strip().lower()
    if len(email) > 254 or not _EMAIL.match(email):
        raise AuthError("Enter a valid email address")
    return email


def storage_secret(data_dir: str | Path = "data") -> str:
    """Secret that signs the browser session cookie: NETCONFIG_STORAGE_SECRET, else a random
    one kept in ``data/.storage_secret`` (created on first start)."""
    env = os.environ.get("NETCONFIG_STORAGE_SECRET")
    if env:
        return env
    path = Path(data_dir) / ".storage_secret"
    if path.is_file() and path.read_text(encoding="utf-8").strip():
        return path.read_text(encoding="utf-8").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_urlsafe(48)
    path.write_text(secret, encoding="utf-8")
    return secret


# --------------------------------------------------------------------------- current user

@dataclass
class CurrentUser:
    id: str
    email: str
    display_name: str
    role: str
    levels: dict[str, str] = field(default_factory=dict)  # screen → view | update
    must_change_password: bool = False

    @property
    def is_admin(self) -> bool:
        return self.role == ADMIN_ROLE

    def can_view(self, screen: str) -> bool:
        return screen in self.levels

    def can_update(self, screen: str) -> bool:
        return self.levels.get(screen) == UPDATE

    def owns_run(self, started_by: str | None) -> bool:
        """The admin sees every run; anyone else only the runs they started (D3/D5)."""
        return self.is_admin or (bool(started_by) and started_by == self.id)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _levels(raw) -> dict[str, str]:
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return {}
    if isinstance(data, list):  # early draft stored a list of screens
        data = {k: UPDATE for k in data}
    levels = {k: v for k, v in data.items() if k in SCREENS and v in LEVELS}
    # a screen added later takes the level of the screen it came from (roles saved before it)
    for new, origin in _DERIVED_SCREENS.items():
        if new not in data and origin in levels:
            levels[new] = levels[origin]
    return levels


# --------------------------------------------------------------------------- service

class AuthService:
    """Users and roles in the pipeline DB (``storage.db.Database``)."""

    def __init__(self, db) -> None:
        self.db = db
        self._failures: dict[str, list[float]] = {}

    # -- setup -----------------------------------------------------------------------
    async def ensure_defaults(self) -> None:
        """The ``admin`` role and, when no administrator account exists, the default one:
        NETCONFIG_ADMIN_EMAIL (default admin@netconfig.local) with NETCONFIG_ADMIN_PASSWORD, or
        the password ``admin`` that must be changed at the first login."""
        admin_levels = json.dumps({k: UPDATE for k in SCREENS})
        if not await self.db.execute("SELECT 1 FROM roles WHERE name = ?", (ADMIN_ROLE,)):
            await self.db.execute_insert(
                "INSERT INTO roles (id, name, description, permissions, is_system, created_at) "
                "VALUES (?, ?, ?, ?, 1, ?)",
                (str(uuid.uuid4()), ADMIN_ROLE, "Full access; manages users, roles and the audit trail",
                 admin_levels, _now()))
        else:  # new screens included
            await self.db.execute("UPDATE roles SET permissions = ?, is_system = 1 WHERE name = ?",
                                  (admin_levels, ADMIN_ROLE))
        if not await self.db.execute("SELECT 1 FROM users u JOIN roles r ON r.id = u.role_id "
                                     "WHERE r.name = ? LIMIT 1", (ADMIN_ROLE,)):
            await self._create_default_admin()
        await self._adopt_ownerless_runs()

    async def _adopt_ownerless_runs(self) -> None:
        """Runs from before the login (no ``started_by``) belong to the default administrator."""
        rows = await self.db.execute(
            "SELECT u.id FROM users u JOIN settings s ON s.key = 'default_admin_email' AND s.value = u.email")
        if rows:
            await self.db.execute("UPDATE pipeline_runs SET started_by = ? WHERE started_by IS NULL OR started_by = ''",
                                  (rows[0]["id"],))

    async def _create_default_admin(self) -> None:
        email = normalize_email(os.environ.get("NETCONFIG_ADMIN_EMAIL") or DEFAULT_ADMIN_EMAIL)
        password = os.environ.get("NETCONFIG_ADMIN_PASSWORD") or ""
        if password:
            check_password_policy(password)
        await self.db.execute_insert(
            "INSERT INTO users (id, email, display_name, password_hash, role_id, active, must_change_password, "
            "created_at, password_changed_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)",
            (str(uuid.uuid4()), email, "Administrator", hash_password(password or DEFAULT_ADMIN_PASSWORD),
             await self._role_id(ADMIN_ROLE), int(not password), _now(), _now()))
        await self._set_setting("default_admin_email", email)

    # -- login -----------------------------------------------------------------------
    def locked_for(self, email: str) -> int:
        """Seconds the email stays locked after too many failed logins (0 = not locked)."""
        key = (email or "").strip().lower()
        now = time.time()
        recent = [t for t in self._failures.get(key, []) if now - t < LOCKOUT_SECONDS]
        self._failures[key] = recent
        return int(LOCKOUT_SECONDS - (now - recent[0])) + 1 if len(recent) >= MAX_FAILED_LOGINS else 0

    async def authenticate(self, email: str, password: str) -> CurrentUser:
        key = (email or "").strip().lower()
        wait = self.locked_for(key)
        if wait:
            raise AuthError(f"Too many failed attempts — try again in {wait // 60 + 1} min")
        rows = await self.db.execute("SELECT * FROM users WHERE email = ?", (key,))
        row = rows[0] if rows else None
        # verify even for an unknown email: the response time does not reveal which emails exist
        ok = verify_password(password or "", row["password_hash"] if row else _dummy_hash())
        if not row or not ok:
            self._failures.setdefault(key, []).append(time.time())
            raise AuthError("Invalid email or password")
        if not row["active"]:
            raise AuthError("This account is disabled")
        self._failures.pop(key, None)
        await self.db.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (_now(), row["id"]))
        return await self.current_user(row["id"])

    async def current_user(self, user_id: str | None) -> CurrentUser | None:
        """The active user with their role's levels now (role edits apply on the next page load)."""
        if not user_id:
            return None
        rows = await self.db.execute(
            "SELECT u.id, u.email, u.display_name, u.active, u.must_change_password, r.name AS role, "
            "r.permissions FROM users u JOIN roles r ON r.id = u.role_id WHERE u.id = ?", (user_id,))
        if not rows or not rows[0]["active"]:
            return None
        r = rows[0]
        if r["role"] == ADMIN_ROLE:
            levels = {k: UPDATE for k in (*SCREENS, *ADMIN_SCREENS)}
        else:
            levels = _levels(r["permissions"])
        return CurrentUser(r["id"], r["email"], r["display_name"] or r["email"], r["role"], levels,
                           bool(r["must_change_password"]))

    # -- users -----------------------------------------------------------------------
    async def list_users(self) -> list[dict]:
        rows = await self.db.execute(
            "SELECT u.id, u.email, u.display_name, u.active, u.must_change_password, u.created_at, "
            "u.last_login_at, r.name AS role FROM users u JOIN roles r ON r.id = u.role_id ORDER BY u.email")
        default = await self._default_admin_email()
        for r in rows:
            r["is_default_admin"] = r["email"] == default
        return rows

    async def create_user(self, email: str, password: str, role: str, display_name: str = "") -> str:
        email = normalize_email(email)
        if await self.db.execute("SELECT 1 FROM users WHERE email = ?", (email,)):
            raise AuthError(f"A user with {email} already exists")
        check_password_policy(password)
        role_id = await self._role_id(role)
        user_id = str(uuid.uuid4())
        # the admin chose this password: the user sets their own at the first login
        await self.db.execute_insert(
            "INSERT INTO users (id, email, display_name, password_hash, role_id, active, must_change_password, "
            "created_at, password_changed_at) VALUES (?, ?, ?, ?, ?, 1, 1, ?, ?)",
            (user_id, email, display_name.strip(), hash_password(password), role_id, _now(), _now()))
        return user_id

    async def update_user(self, user_id: str, *, role: str | None = None, active: bool | None = None,
                          display_name: str | None = None) -> None:
        user = await self._user(user_id)
        demoted = role is not None and role != ADMIN_ROLE
        if user["role"] == ADMIN_ROLE and (demoted or active is False):
            if user["email"] == await self._default_admin_email():
                raise AuthError("The default administrator cannot be disabled or moved to another role")
            if user["active"] and await self._active_admins() <= 1:
                raise AuthError("The last active administrator must stay an active administrator")
        if role is not None:
            await self.db.execute("UPDATE users SET role_id = ? WHERE id = ?", (await self._role_id(role), user_id))
        if active is not None:
            await self.db.execute("UPDATE users SET active = ? WHERE id = ?", (int(active), user_id))
        if display_name is not None:
            await self.db.execute("UPDATE users SET display_name = ? WHERE id = ?", (display_name.strip(), user_id))

    async def set_password(self, user_id: str, password: str, *, must_change: bool = False) -> None:
        """Set a password (``must_change``: an admin reset — the user picks a new one at next login)."""
        check_password_policy(password)
        await self._user(user_id)
        await self.db.execute(
            "UPDATE users SET password_hash = ?, password_changed_at = ?, must_change_password = ? WHERE id = ?",
            (hash_password(password), _now(), int(must_change), user_id))

    async def change_own_password(self, user_id: str, current: str, new: str) -> None:
        rows = await self.db.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,))
        if not rows or not verify_password(current or "", rows[0]["password_hash"]):
            raise AuthError("Current password is wrong")
        if current == new:
            raise AuthError("The new password must differ from the current one")
        await self.set_password(user_id, new)

    async def set_new_password(self, user_id: str, new: str) -> None:
        """The forced change after login (default / admin-chosen password): the user just proved
        the current password; the new one must differ from it."""
        rows = await self.db.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,))
        if not rows:
            raise AuthError("User not found")
        if verify_password(new or "", rows[0]["password_hash"]):
            raise AuthError("Choose a password different from the current one")
        await self.set_password(user_id, new)

    async def delete_user(self, user_id: str, acting_user_id: str) -> None:
        if user_id == acting_user_id:
            raise AuthError("You cannot delete your own account")
        user = await self._user(user_id)
        if user["role"] == ADMIN_ROLE:
            if user["email"] == await self._default_admin_email():
                raise AuthError("The default administrator cannot be deleted")
            if user["active"] and await self._active_admins() <= 1:
                raise AuthError("The last active administrator cannot be deleted")
        await self.db.execute("DELETE FROM users WHERE id = ?", (user_id,))

    async def user_by_email(self, email: str) -> dict | None:
        rows = await self.db.execute("SELECT id, email FROM users WHERE email = ?", ((email or "").strip().lower(),))
        return rows[0] if rows else None

    # -- roles -----------------------------------------------------------------------
    async def list_roles(self) -> list[dict]:
        rows = await self.db.execute(
            "SELECT r.*, (SELECT COUNT(*) FROM users u WHERE u.role_id = r.id) AS users "
            "FROM roles r ORDER BY r.is_system DESC, r.name")
        for r in rows:
            r["levels"] = ({k: UPDATE for k in (*SCREENS, *ADMIN_SCREENS)} if r["name"] == ADMIN_ROLE
                           else _levels(r.pop("permissions", "{}")))
            r.pop("permissions", None)
        return rows

    async def save_role(self, name: str, levels: dict[str, str], description: str = "",
                        role_id: str | None = None) -> str:
        """Create (no ``role_id``) or update a role; ``levels``: screen → view | update."""
        name = (name or "").strip().lower()
        if not name or not name.replace("_", "").replace("-", "").isalnum():
            raise AuthError("Role name: letters, digits, '_' or '-' only")
        if name == ADMIN_ROLE and not role_id:
            raise AuthError("'admin' is the built-in role")
        bad = {k for k in levels if k not in SCREENS} | {v for v in levels.values() if v not in LEVELS}
        if bad:
            raise AuthError(f"Unknown screens or levels: {', '.join(sorted(map(str, bad)))}")
        # registry order; a derived screen left out is stored as "none" (else it would be derived again)
        stored = json.dumps({k: levels.get(k, "none") for k in SCREENS if k in levels or k in _DERIVED_SCREENS})
        clash = await self.db.execute("SELECT id FROM roles WHERE name = ?", (name,))
        if clash and clash[0]["id"] != role_id:
            raise AuthError(f"Role '{name}' already exists")
        if role_id:
            role = await self.db.execute("SELECT * FROM roles WHERE id = ?", (role_id,))
            if not role:
                raise AuthError("Role not found")
            if role[0]["is_system"]:
                raise AuthError("The admin role cannot be changed")
            await self.db.execute("UPDATE roles SET name = ?, description = ?, permissions = ? WHERE id = ?",
                                  (name, description.strip(), stored, role_id))
            return role_id
        role_id = str(uuid.uuid4())
        await self.db.execute_insert(
            "INSERT INTO roles (id, name, description, permissions, is_system, created_at) VALUES (?, ?, ?, ?, 0, ?)",
            (role_id, name, description.strip(), stored, _now()))
        return role_id

    async def delete_role(self, role_id: str) -> None:
        role = await self.db.execute("SELECT * FROM roles WHERE id = ?", (role_id,))
        if not role:
            raise AuthError("Role not found")
        if role[0]["is_system"]:
            raise AuthError("The admin role cannot be deleted")
        if await self.db.execute("SELECT 1 FROM users WHERE role_id = ? LIMIT 1", (role_id,)):
            raise AuthError("Role is assigned to users — move them to another role first")
        await self.db.execute("DELETE FROM roles WHERE id = ?", (role_id,))

    # -- helpers ---------------------------------------------------------------------
    async def _role_id(self, role: str) -> str:
        rows = await self.db.execute("SELECT id FROM roles WHERE name = ?", ((role or "").lower(),))
        if not rows:
            raise AuthError(f"Unknown role '{role}'")
        return rows[0]["id"]

    async def _user(self, user_id: str) -> dict:
        rows = await self.db.execute(
            "SELECT u.*, r.name AS role FROM users u JOIN roles r ON r.id = u.role_id WHERE u.id = ?", (user_id,))
        if not rows:
            raise AuthError("User not found")
        return rows[0]

    async def _active_admins(self) -> int:
        rows = await self.db.execute(
            "SELECT COUNT(*) AS n FROM users u JOIN roles r ON r.id = u.role_id "
            "WHERE r.name = ? AND u.active = 1", (ADMIN_ROLE,))
        return int(rows[0]["n"])

    async def _default_admin_email(self) -> str:
        rows = await self.db.execute("SELECT value FROM settings WHERE key = 'default_admin_email'")
        return rows[0]["value"] if rows else ""

    async def _set_setting(self, key: str, value: str) -> None:
        await self.db.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, value, _now()))


_DUMMY: list[str] = []


def _dummy_hash() -> str:
    """A hash to verify against for unknown emails (made once, on first use)."""
    if not _DUMMY:
        _DUMMY.append(hash_password(secrets.token_urlsafe(16)))
    return _DUMMY[0]


# --------------------------------------------------------------------------- CLI

def _cli(argv: list[str]) -> int:
    """``python -m core.auth set-password <email>``: set a password on the server (prompts twice)."""
    import asyncio
    import getpass

    if len(argv) != 2 or argv[0] != "set-password":
        print("usage: python -m core.auth set-password <email>")
        return 2

    async def run() -> int:
        from storage.db import init_database
        db = await init_database(os.environ.get("NETCONFIG_DB", "data/pipeline.db"))
        auth = AuthService(db)
        await auth.ensure_defaults()
        user = await auth.user_by_email(argv[1])
        if not user:
            print(f"No user with email {argv[1]}")
            return 1
        first = getpass.getpass("New password: ")
        if first != getpass.getpass("Repeat: "):
            print("Passwords differ")
            return 1
        try:
            await auth.set_password(user["id"], first)
        except AuthError as e:
            print(e)
            return 1
        from core.audit import AuditTrail
        await AuditTrail(db).record("password.set_on_server", user={"id": None, "email": "server-console",
                                                                     "role": "cli"},
                                    target_type="user", target_id=user["email"])
        print(f"Password set for {user['email']}")
        return 0

    return asyncio.run(run())


if __name__ == "__main__":
    import sys
    raise SystemExit(_cli(sys.argv[1:]))
