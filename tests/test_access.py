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


def _db_with_runs(tmp_path):
    from storage.db import init_database

    async def make():
        db = await init_database(str(tmp_path / "p.db"))
        for rid, owner in (("r1", "u1"), ("r2", "u2"), ("r3", None), ("r4", "cli")):
            await db.execute_insert(
                "INSERT INTO pipeline_runs (id, document_set_id, status, started_at, started_by) "
                "VALUES (?, 'd', 'completed', '2026-11-04', ?)", (rid, owner))
            await db.execute_insert(
                "INSERT INTO llm_usage (run_id, step, model, prompt_tokens, completion_tokens, total_tokens, "
                "cost_usd, elapsed_sec, created_at) VALUES (?, 's', 'm', 1, 1, 2, 0.1, 1, '2026-11-04T00:00:00')",
                (rid,))
        return db
    return asyncio.run(make())


def test_run_scope_filters_and_checks(tmp_path, monkeypatch):
    db = _db_with_runs(tmp_path)
    ana = CurrentUser("u1", "ana@x.io", "Ana", "analyst", {"history": UPDATE})
    trail = _page(monkeypatch, ana)
    scope = ui_access.scope()

    async def scenario():
        where, params = scope.where("r")
        mine = await db.execute(f"SELECT id FROM pipeline_runs r WHERE {where}", params)
        return ([r["id"] for r in mine], await scope.owns(db, "r1"), await scope.check(db, "r2", "run.rerun", "history"))
    mine, own, other = asyncio.run(scenario())
    assert mine == ["r1"] and own and not other
    assert trail.entries == [("run.rerun", "refused", "r2")]

    _page(monkeypatch, CurrentUser("a", "admin@x.io", "Admin", ADMIN_ROLE, {}))
    admin = ui_access.scope()
    where, params = admin.where("r")
    assert len(asyncio.run(db.execute(f"SELECT id FROM pipeline_runs r WHERE {where}", params))) == 4
    assert asyncio.run(admin.owns(db, "r3")) and admin.owner is None   # ownerless runs: admin only
    assert ui_access.RunScope(None).where("r") == ("1 = 0", ())         # no page user: nothing


def test_usage_by_owner(tmp_path):
    from shared import llm
    db = _db_with_runs(tmp_path)
    llm.set_usage_db(db)
    try:
        assert [r["run_id"] for r in asyncio.run(llm.load_usage_stats(owner="u1"))] == ["r1"]
        assert len(asyncio.run(llm.load_usage_stats())) == 4
        asyncio.run(llm.clear_usage_stats(owner="u1"))  # a user clears only their own runs' usage
        assert sorted(r["run_id"] for r in asyncio.run(llm.load_usage_stats())) == ["r2", "r3", "r4"]
    finally:
        llm.set_usage_db(None)


def test_one_pipeline_at_a_time(tmp_path, monkeypatch):
    db = _db_with_runs(tmp_path)

    class Runner:
        def __init__(self, active):
            self.active = active

        def active_run(self):
            return self.active

    trail = _page(monkeypatch, CurrentUser("u1", "ana@x.io", "Ana", "analyst", {"new_run": UPDATE}))
    assert asyncio.run(ui_access.may_start_run(Runner(None), db))
    assert asyncio.run(ui_access.may_start_run(Runner({"run_id": "r1", "started_by": "u1", "status": "running"}), db))
    assert not asyncio.run(ui_access.may_start_run(Runner({"run_id": "r2", "started_by": "u2",
                                                           "status": "running"}), db))
    assert trail.entries == [("run.start", "refused", "r2")]


def test_document_views_recorded_with_run_and_relative_file(monkeypatch):
    trail = _page(monkeypatch, CurrentUser("u1", "ana@x.io", "Ana", "analyst", {"outputs": VIEW}))
    scheduled = []
    monkeypatch.setattr(ui_access, "run_later", lambda coro: scheduled.append(coro))
    ui_access.record_document("document.view",
                              "data/runs/aac54518-d059-4a82-9dfc-f551c8f9fd4f/exec_report/x_summary.html")
    assert len(scheduled) == 1  # the user is taken now, the write runs later
    asyncio.run(scheduled[0])
    assert trail.entries == [("document.view", None, "x_summary.html")]
