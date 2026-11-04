"""Spec 006 D8: append-only activity trail (core/audit.py)."""
from __future__ import annotations

import asyncio

from core import audit
from core.audit import REFUSED, AuditTrail, mask_secrets
from core.auth import CurrentUser
from storage.db import init_database


def test_record_query_filter_and_csv(tmp_path):
    ana = CurrentUser("u1", "ana@x.io", "Ana", "ops")

    async def scenario():
        trail = AuditTrail(await init_database(str(tmp_path / "p.db")))
        await trail.record("login", user=ana)
        await trail.record("run.started", user=ana, run_id="r1", uc_name="MRF_X", target_type="run", target_id="r1")
        await trail.record("step.retry", run_id="r1", uc_name="MRF_X", details={"from": 9, "to": 6})  # system: hidden
        await trail.record("run.cancel", user=ana, run_id="r2", outcome=REFUSED, details={"reason": "view only"})
        await trail.record("settings.saved", user={"id": "a", "email": "admin@x.io", "role": "admin"},
                           details={"api_key": "sk-123", "default_model": "m", "nested": {"password": "p"}})
        return (trail, await trail.query(), await trail.query(run_id="r1"), await trail.query(email="ana@x.io"),
                await trail.query(outcome=REFUSED), await trail.query(text="retry"),
                await trail.export_csv(run_id="r1"), await trail.distinct("email"))
    trail, everything, r1, by_ana, refused, found, csv_text, emails = asyncio.run(scenario())
    # user actions only (D11): the system entry is not shown
    assert [e["action"] for e in everything] == ["settings.saved", "run.cancel", "run.started", "login"]
    assert [e["action"] for e in r1] == ["run.started"] and found == []
    assert len(by_ana) == 3 and [e["action"] for e in refused] == ["run.cancel"]
    saved = everything[0]["details"]
    assert saved["api_key"] == "***" and saved["nested"]["password"] == "***" and saved["default_model"] == "m"
    assert csv_text.splitlines()[0].startswith("at,email,role,action") and len(csv_text.splitlines()) == 2
    assert emails == ["admin@x.io", "ana@x.io"]
    assert not any(hasattr(trail, n) for n in ("delete", "update", "clear"))  # append-only


def test_record_never_raises():
    class Broken:
        async def execute_insert(self, *a):
            raise RuntimeError("db down")
    asyncio.run(AuditTrail(Broken()).record("login"))


def test_mask_secrets():
    assert mask_secrets({"token": "", "Password": "x", "list": [{"secret_key": "y"}]}) == \
        {"token": "", "Password": "***", "list": [{"secret_key": "***"}]}


def test_pipeline_records_no_system_entries():
    """D11: the trail holds people's actions — the runner writes none of its own."""
    import inspect

    from orchestrator import runner
    assert "AuditTrail" not in inspect.getsource(runner)
