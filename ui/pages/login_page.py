"""Login page, forced password change and logout (spec 006, step 2).

The session lives in NiceGUI's signed ``app.storage.user``: ``{"user_id", "login_at"}``. Every
request without a valid session is sent here by ``AuthMiddleware`` (``ui/app.py``).
"""
from __future__ import annotations

import time
from typing import Awaitable, Callable
from urllib.parse import quote

from fastapi.responses import RedirectResponse
from nicegui import app, ui
from starlette.middleware.base import BaseHTTPMiddleware

from core.audit import FAILED, REFUSED
from core.auth import SESSION_HOURS, AuthError

# Reachable without a session: the login page, NiceGUI's own assets and its websocket.
# Files served with add_media_file (/_nicegui/auto/media/…) are NOT public.
_PUBLIC_PREFIXES = ("/_nicegui_ws",)


def is_public(path: str) -> bool:
    if path in ("/login", "/logout", "/favicon.ico") or path.startswith(_PUBLIC_PREFIXES):
        return True
    return path.startswith("/_nicegui/") and not path.startswith("/_nicegui/auto/media")


def safe_target(target: str | None) -> str:
    """Only a local path of this app (no open redirect to another site)."""
    target = target or "/"
    if (not target.startswith("/") or target.startswith("//") or "\\" in target
            or target.startswith(("/login", "/logout"))):
        return "/"
    return target


def session_user_id() -> str | None:
    """The logged-in user id, or None when there is no session or it is older than 12 h."""
    user_id = app.storage.user.get("user_id")
    login_at = float(app.storage.user.get("login_at") or 0)
    if user_id and time.time() - login_at <= SESSION_HOURS * 3600:
        return user_id
    return None


def session_expired() -> bool:
    return bool(app.storage.user.get("user_id")) and session_user_id() is None


def start_session(user_id: str) -> None:
    app.storage.user.clear()
    app.storage.user.update({"user_id": user_id, "login_at": time.time()})


def end_session() -> None:
    app.storage.user.clear()


class AuthMiddleware(BaseHTTPMiddleware):
    """Every non-public request needs a valid session (FR-001)."""

    async def dispatch(self, request, call_next):
        path = request.url.path
        if is_public(path) or session_user_id():
            return await call_next(request)
        target = path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"/login?redirect_to={quote(target)}")


def register(services: Callable[[], Awaitable[tuple]], css: str) -> None:
    """Pages ``/login`` and ``/logout``. ``services()`` → ``(AuthService, AuditTrail)``."""

    @app.get("/logout")
    async def logout():
        auth, audit = await services()
        user = await auth.current_user(app.storage.user.get("user_id"))
        if user:
            await audit.record("logout", user=user)
        end_session()
        return RedirectResponse("/login")

    @ui.page("/login", title="Azalio Autonomous Network Platform — Sign in")
    async def login_page(redirect_to: str = "/"):
        auth, audit = await services()
        target = safe_target(redirect_to)
        if session_expired():
            user = await auth.current_user(app.storage.user.get("user_id"))
            if user:
                await audit.record("session.expired", user=user)
            end_session()
        user = await auth.current_user(session_user_id())
        if user and not user.must_change_password:
            return RedirectResponse(target)

        ui.add_head_html(css)
        ui.add_head_html('<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700'
                         '&display=swap" rel="stylesheet">')
        ui.add_head_html("""<style>
body { font-family: "Inter", sans-serif !important; font-size: 13px; background: var(--cr-body-bg); }
.cr-login-wrap { min-height: 100vh; width: 100%; display: flex; align-items: center; justify-content: center; }
.cr-login-card { width: 380px; max-width: calc(100vw - 32px); padding: 28px 28px 22px !important;
  border: 1px solid var(--cr-border); border-radius: 12px !important; box-shadow: var(--cr-shadow); }
.cr-login-title { font-size: 18px; font-weight: 600; color: var(--cr-text); }
.cr-login-sub { font-size: 12px; color: var(--cr-text-dim); }
.cr-login-error { font-size: 12px; color: var(--cr-error); min-height: 16px; }
</style>""")
        ui.colors(primary="#0B5FFF")

        with ui.element("div").classes("cr-login-wrap"):
            with ui.card().classes("cr-login-card gap-3"):
                with ui.row().classes("items-center gap-2 no-wrap"):
                    with ui.element("div").classes("cr-logo"):
                        ui.icon("hub", size="18px")
                    ui.label("Azalio Autonomous Network Platform").classes("font-bold").style("font-size: 16px")
                if user and user.must_change_password:
                    _change_password_form(auth, audit, user, target)
                else:
                    _login_form(auth, audit, target)


def _login_form(auth, audit, target: str) -> None:
    ui.label("Sign in").classes("cr-login-title")
    ui.label("Use your email address and password").classes("cr-login-sub")
    email = ui.input("Email").props("outlined dense autofocus autocomplete=username").classes("w-full")
    password = ui.input("Password", password=True, password_toggle_button=True) \
        .props("outlined dense autocomplete=current-password").classes("w-full")
    error = ui.label("").classes("cr-login-error")

    async def submit():
        error.text = ""
        address = (email.value or "").strip().lower()
        try:
            user = await auth.authenticate(address, password.value or "")
        except AuthError as e:
            locked = str(e).startswith("Too many")
            await audit.record("login.locked" if locked else "login.failed",
                               user={"id": None, "email": address, "role": ""},
                               outcome=REFUSED, target_type="user", target_id=address,
                               details={"reason": str(e)})
            error.text = str(e)
            password.value = ""
            return
        start_session(user.id)
        await audit.record("login", user=user)
        # the default / admin-chosen password is replaced first (the page shows the change form)
        ui.navigate.to(f"/login?redirect_to={quote(target)}" if user.must_change_password else target)

    password.on("keydown.enter", submit)
    email.on("keydown.enter", lambda: password.run_method("focus"))
    ui.button("Sign in", on_click=submit).props("unelevated color=primary no-caps").classes("w-full")


def _change_password_form(auth, audit, user, target: str) -> None:
    ui.label("Set a new password").classes("cr-login-title")
    ui.label(f"{user.email} — the current password was set for you; choose your own to continue.") \
        .classes("cr-login-sub")
    new = ui.input("New password", password=True, password_toggle_button=True) \
        .props("outlined dense autofocus autocomplete=new-password").classes("w-full")
    again = ui.input("Repeat new password", password=True) \
        .props("outlined dense autocomplete=new-password").classes("w-full")
    ui.label("At least 8 characters, mixing letters with digits or symbols.").classes("cr-login-sub")
    error = ui.label("").classes("cr-login-error")

    async def submit():
        error.text = ""
        if (new.value or "") != (again.value or ""):
            error.text = "The two passwords differ"
            return
        try:
            await auth.set_new_password(user.id, new.value or "")
        except AuthError as e:
            await audit.record("password.changed", user=user, outcome=FAILED, details={"reason": str(e)})
            error.text = str(e)
            return
        await audit.record("password.changed", user=user, details={"forced": True})
        ui.navigate.to(target)

    again.on("keydown.enter", submit)
    with ui.row().classes("w-full gap-2 no-wrap"):
        ui.button("Sign out", on_click=lambda: ui.navigate.to("/logout")) \
            .props("flat no-caps color=grey-8")
        ui.button("Save and continue", on_click=submit) \
            .props("unelevated color=primary no-caps").classes("flex-1")
