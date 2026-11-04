"""Spec 006 step 3: server-side guard of UI actions (ui/access.py)."""
from __future__ import annotations

import asyncio

from core.auth import ADMIN_ROLE, UPDATE, VIEW, CurrentUser
from ui import access as ui_access


class Trail:
    def __init__(self):
        self.entries = []

    async def record(self, action, **kw):
        self.entries.append((action, kw.get("outcome"), kw.get("target_id")))


def _page(monkeypatch, user):
    trail = Trail()
    acc = ui_access.Access(user, trail)
    monkeypatch.setattr(ui_access, "current", lambda: acc)
    monkeypatch.setattr(ui_access.ui, "notify", lambda *a, **k: None)
    return trail


def test_view_level_refuses_actions_and_records(monkeypatch):
    trail = _page(monkeypatch, CurrentUser("u1", "ana@x.io", "Ana", "analyst",
                                           {"history": VIEW, "progress": UPDATE}))
    ran = []

    @ui_access.requires("history", "run.rerun")
    async def rerun(run_id):
        ran.append(run_id)

    @ui_access.requires("progress", "run.cancel")
    def cancel(run_id):  # sync handlers are guarded too
        ran.append(f"cancel {run_id}")

    asyncio.run(rerun("r1"))
    asyncio.run(cancel("r2"))
    assert ran == ["cancel r2"]
    assert trail.entries == [("run.rerun", "refused", "history")]


def test_admin_only_actions(monkeypatch):
    trail = _page(monkeypatch, CurrentUser("u1", "ana@x.io", "Ana", "analyst", {"outputs": UPDATE}))
    done = []

    @ui_access.requires_admin("cache.clear_all")
    async def clear():
        done.append(1)

    asyncio.run(clear())
    assert done == [] and trail.entries == [("cache.clear_all", "refused", "admin")]
    _page(monkeypatch, CurrentUser("a", "admin@x.io", "Admin", ADMIN_ROLE, {"outputs": UPDATE}))
    asyncio.run(clear())
    assert done == [1]


def test_no_page_user_refuses(monkeypatch):
    monkeypatch.setattr(ui_access, "current", lambda: None)
    assert asyncio.run(ui_access.allowed("settings", "settings.save")) is False
