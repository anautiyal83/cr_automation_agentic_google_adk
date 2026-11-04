"""Screen levels in the UI (spec 006, D6/D7).

- ``action(button)`` marks a button that starts, deletes or updates something (CSS ``cr-action``).
  A screen the user may only *view* gets ``cr-readonly``: CSS disables every marked button in it,
  also the ones a list creates later. Reading buttons (View, Download, Refresh…) are not marked.
- ``@requires(screen, action)`` guards the handler behind an action on the server — the security
  boundary: without Update on ``screen`` the handler does not run, the user is told, and the
  refusal goes to the activity trail.

The page's ``Access`` (logged-in user + trail) is kept per browser page in ``app.storage.client``.
"""
from __future__ import annotations

import asyncio
import functools
import inspect
import logging
from typing import Any

from nicegui import app, ui

from core.audit import REFUSED

logger = logging.getLogger(__name__)

ACTION_CLASS = "cr-action"
READONLY_CLASS = "cr-readonly"
READONLY_CSS = """<style>
.cr-readonly .cr-action, .cr-readonly .cr-action * { pointer-events: none !important; }
.cr-readonly .cr-action { opacity: .4 !important; cursor: not-allowed !important; filter: grayscale(1); }
.cr-view-only-pill { font-size: 11px; font-weight: 600; padding: 1px 10px; border-radius: 10px;
  background: var(--cr-surface-hover); color: var(--cr-text-dim); border: 1px solid var(--cr-border); }
</style>"""


def action(element, screen: str | None = None):
    """Mark an element as an action (disabled on View screens). With ``screen`` it is also
    disabled right away when the page's user may only view that screen — for buttons in dialogs,
    which render outside the screen's panel."""
    element.classes(ACTION_CLASS)
    access = current() if screen else None
    if access is not None and not access.can_update(screen):
        element.disable()
    return element


class Access:
    """The logged-in user of one browser page and where refusals are recorded."""

    def __init__(self, user, audit) -> None:
        self.user = user
        self.audit = audit

    def can_view(self, screen: str) -> bool:
        return self.user.can_view(screen)

    def can_update(self, screen: str) -> bool:
        return self.user.can_update(screen)

    async def refuse(self, screen: str, action_name: str, reason: str, **details) -> None:
        ui.notify(reason, type="warning")
        await self.audit.record(action_name, user=self.user, outcome=REFUSED, target_type="screen",
                                target_id=screen, details={"reason": reason, **details})


def install(user, audit) -> Access:
    """Bind the page's user (call while building the page)."""
    access = Access(user, audit)
    app.storage.client["access"] = access
    return access


def current() -> Access | None:
    try:
        return app.storage.client.get("access")
    except Exception:  # noqa: BLE001 — outside a page (tests, CLI)
        return None


async def allowed(screen: str, action_name: str) -> bool:
    """True when the page's user has Update on ``screen``; otherwise refuse (notify + trail)."""
    access = current()
    if access is None:
        logger.warning("Action %s on %s without a logged-in page: refused", action_name, screen)
        return False
    if access.can_update(screen):
        return True
    await access.refuse(screen, action_name, "View only — you cannot change anything on this screen")
    return False


def requires(screen: str, action_name: str):
    """Decorator for UI handlers of actions: run only with Update on ``screen``."""
    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any):
            if not await allowed(screen, action_name):
                return None
            result = fn(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            return result
        return wrapper
    return deco


def requires_admin(action_name: str):
    """Decorator for handlers of shared, admin-only things (e.g. the artifact cache)."""
    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any):
            access = current()
            if access is None or not access.user.is_admin:
                if access is not None:
                    await access.refuse("admin", action_name, "Only the administrator can do this")
                return None
            result = fn(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            return result
        return wrapper
    return deco


def guard(screen: str, action_name: str, fn):
    """``requires`` for a callable built inline (``on_click=guard(...)(lambda: …)``)."""
    return requires(screen, action_name)(fn)


class RunScope:
    """Which runs a page's user sees (spec 006 D3/D5): the admin every run, anyone else only the
    runs they started. Sections take it when they are built (``scope()``) and use it in their
    queries and before every action on a run id."""

    def __init__(self, access: Access | None) -> None:
        self.access = access

    @property
    def user(self):
        return self.access.user if self.access else None

    def where(self, alias: str = "r") -> tuple[str, tuple]:
        """SQL condition (no WHERE/AND) + params on ``<alias>.started_by``."""
        if self.user is None:
            return "1 = 0", ()
        if self.user.is_admin:
            return "1 = 1", ()
        return f"{alias}.started_by = ?", (self.user.id,)

    @property
    def owner(self) -> str | None:
        """``None`` for the admin (all runs), else the user id."""
        return None if self.user is None or self.user.is_admin else self.user.id

    async def owns(self, db, run_id: str) -> bool:
        if self.user is None or not run_id:
            return False
        if self.user.is_admin:
            return True
        rows = await db.execute("SELECT started_by FROM pipeline_runs WHERE id = ?", (str(run_id),))
        return bool(rows) and self.user.owns_run(rows[0]["started_by"])

    async def check(self, db, run_id: str, action_name: str, screen: str = "") -> bool:
        """``owns`` + notify and record a refusal (another user's run, or a crafted id)."""
        if await self.owns(db, run_id):
            return True
        if self.access is not None:
            ui.notify("This run belongs to another user", type="warning")
            await self.access.audit.record(action_name, user=self.user, outcome=REFUSED, target_type="run",
                                           target_id=str(run_id), run_id=str(run_id),
                                           details={"reason": "not the owner", "screen": screen})
        return False


def scope() -> RunScope:
    """The run scope of the page being built (call in a section's ``__init__``)."""
    return RunScope(current())


async def may_start_run(runner, db) -> bool:
    """One pipeline runs at a time (milestone state is global) and a new start cancels the active
    run: refuse when that run belongs to another user, instead of cancelling it silently."""
    access = current()
    active = runner.active_run() if hasattr(runner, "active_run") else None
    if not active or (access is not None and active["started_by"] == access.user.id):
        return True
    who = "another user"
    if access is not None and access.user.is_admin:
        rows = await db.execute("SELECT email FROM users WHERE id = ?", (active["started_by"],))
        who = rows[0]["email"] if rows else ("the command line" if active["started_by"] == "cli" else who)
    ui.notify(f"A pipeline is already running (started by {who}). Only one pipeline runs at a time — "
              "start yours when it has finished.", type="warning", timeout=8000)
    if access is not None:
        await access.audit.record("run.start", user=access.user, outcome=REFUSED, target_type="run",
                                  target_id=active["run_id"], run_id=active["run_id"],
                                  details={"reason": "another user's run is in progress"})
    return False


def run_later(coro) -> None:
    """Fire-and-forget for sync callers (navigation refusals)."""
    try:
        asyncio.ensure_future(coro)
    except RuntimeError:
        pass
