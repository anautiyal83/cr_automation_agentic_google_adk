"""Spec 006 step 1: users, roles, levels, default administrator (core/auth.py)."""
from __future__ import annotations

import asyncio

import pytest

from core import auth
from core.auth import ADMIN_ROLE, UPDATE, VIEW, AuthError, AuthService, hash_password, verify_password
from storage.db import init_database


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.delenv("NETCONFIG_ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("NETCONFIG_ADMIN_PASSWORD", raising=False)
    monkeypatch.setattr(auth, "_ITERATIONS", 1000)  # fast hashing in tests

    async def make():
        db = await init_database(str(tmp_path / "pipeline.db"))
        svc = AuthService(db)
        await svc.ensure_defaults()
        return svc
    return run(make())


def test_password_hash_round_trip_and_no_plain_text():
    stored = hash_password("s3cret-pass")
    assert "s3cret-pass" not in stored and stored.startswith("pbkdf2_sha256$")
    assert verify_password("s3cret-pass", stored) and not verify_password("s3cret-pasS", stored)
    assert hash_password("s3cret-pass") != stored  # salted


def test_password_policy_and_email_validation(service):
    for bad in ("short1", "lettersonly", "12345678"):
        with pytest.raises(AuthError):
            run(service.create_user("a@x.io", bad, ADMIN_ROLE))
    for bad in ("not-an-email", "a@b", "@x.io"):
        with pytest.raises(AuthError):
            run(service.create_user(bad, "good-pass1", ADMIN_ROLE))


def test_default_admin_must_change_password(service):
    user = run(service.authenticate("Admin@NetConfig.local", "admin"))  # email case-insensitive
    assert user.is_admin and user.must_change_password
    assert user.can_update("settings") and user.can_view("users") and user.can_view("audit")
    run(service.change_own_password(user.id, "admin", "n3w-secret"))
    assert not run(service.current_user(user.id)).must_change_password
    with pytest.raises(AuthError):
        run(service.authenticate("admin@netconfig.local", "admin"))


def test_default_admin_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("NETCONFIG_ADMIN_EMAIL", "ops@corp.example")
    monkeypatch.setenv("NETCONFIG_ADMIN_PASSWORD", "Corp-pass9")
    monkeypatch.setattr(auth, "_ITERATIONS", 1000)

    async def scenario():
        svc = AuthService(await init_database(str(tmp_path / "p.db")))
        await svc.ensure_defaults()
        await svc.ensure_defaults()  # idempotent: one administrator
        return svc, await svc.authenticate("ops@corp.example", "Corp-pass9"), await svc.list_users()
    _, user, users = run(scenario())
    assert not user.must_change_password and len(users) == 1 and users[0]["is_default_admin"]


def test_only_admin_role_at_start_and_levels(service):
    assert [r["name"] for r in run(service.list_roles())] == [ADMIN_ROLE]  # D2
    run(service.save_role("auditor", {"history": VIEW, "outputs": UPDATE}))
    uid = run(service.create_user("ana@x.io", "first-pass1", "auditor", "Ana"))
    user = run(service.authenticate("ana@x.io", "first-pass1"))
    assert user.must_change_password  # password chosen by the admin
    assert user.levels == {"outputs": UPDATE, "history": VIEW}
    assert user.can_view("history") and not user.can_update("history") and not user.can_view("settings")
    assert not user.can_view("users") and not user.can_view("audit")  # admin-only screens
    role_id = next(r["id"] for r in run(service.list_roles()) if r["name"] == "auditor")
    run(service.save_role("auditor", {"history": UPDATE}, role_id=role_id))
    assert run(service.current_user(uid)).levels == {"history": UPDATE}  # applies on the next load
    for bad in ({"users": UPDATE}, {"audit": VIEW}, {"history": "admin"}):
        with pytest.raises(AuthError):
            run(service.save_role("x", bad))


def test_run_ownership():
    admin = auth.CurrentUser("a", "a@x.io", "A", ADMIN_ROLE)
    ana = auth.CurrentUser("u1", "ana@x.io", "Ana", "ops")
    assert admin.owns_run("u2") and admin.owns_run(None)
    assert ana.owns_run("u1") and not ana.owns_run("u2") and not ana.owns_run(None)


def test_lockout_and_unknown_email_message(service, monkeypatch):
    run(service.save_role("ops", {"dashboard": VIEW}))
    run(service.create_user("bob@x.io", "bob-pass1", "ops"))
    messages = set()
    for email in ("bob@x.io", "nobody@x.io"):
        with pytest.raises(AuthError) as e:
            run(service.authenticate(email, "wrong-pass1"))
        messages.add(str(e.value))
    assert messages == {"Invalid email or password"}  # does not reveal which emails exist
    for _ in range(auth.MAX_FAILED_LOGINS - 1):
        with pytest.raises(AuthError):
            run(service.authenticate("bob@x.io", "wrong-pass1"))
    with pytest.raises(AuthError, match="Too many failed attempts"):
        run(service.authenticate("bob@x.io", "bob-pass1"))  # locked even with the right password
    monkeypatch.setattr(auth, "LOCKOUT_SECONDS", 0)
    assert run(service.authenticate("bob@x.io", "bob-pass1")).email == "bob@x.io"


def test_admin_protection_and_role_rules(service):
    default = run(service.authenticate("admin@netconfig.local", "admin"))
    run(service.save_role("ops", {"dashboard": VIEW}))
    second = run(service.create_user("root2@x.io", "root2-pass", ADMIN_ROLE))
    for action in (lambda: service.update_user(default.id, active=False),
                   lambda: service.update_user(default.id, role="ops"),
                   lambda: service.delete_user(default.id, second)):
        with pytest.raises(AuthError, match="default administrator"):
            run(action())
    with pytest.raises(AuthError, match="own account"):
        run(service.delete_user(second, second))
    run(service.update_user(second, active=False))  # another admin can be disabled
    with pytest.raises(AuthError):
        run(service.authenticate("root2@x.io", "root2-pass"))
    assert run(service.current_user(second)) is None  # session ends on the next page load
    admin_role = next(r["id"] for r in run(service.list_roles()) if r["name"] == ADMIN_ROLE)
    with pytest.raises(AuthError):
        run(service.save_role("admin", {}, role_id=admin_role))
    with pytest.raises(AuthError):
        run(service.delete_role(admin_role))
    ops = next(r["id"] for r in run(service.list_roles()) if r["name"] == "ops")
    uid = run(service.create_user("c@x.io", "c-pass123", "ops"))
    with pytest.raises(AuthError, match="assigned"):
        run(service.delete_role(ops))
    run(service.delete_user(uid, default.id))
    run(service.delete_role(ops))
    with pytest.raises(AuthError, match="already exists"):
        run(service.create_user("ROOT2@x.io", "x-pass123", ADMIN_ROLE))


def test_admin_reset_forces_change(service):
    run(service.save_role("ops", {"dashboard": VIEW}))
    uid = run(service.create_user("d@x.io", "d-pass123", "ops"))
    run(service.set_password(uid, "reset-pass1", must_change=True))
    assert run(service.authenticate("d@x.io", "reset-pass1")).must_change_password


def test_set_password_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(auth, "_ITERATIONS", 1000)
    monkeypatch.delenv("NETCONFIG_ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("NETCONFIG_ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("NETCONFIG_DB", str(tmp_path / "cli.db"))
    answers = iter(["Server-pass1", "Server-pass1"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    assert auth._cli(["set-password", "admin@netconfig.local"]) == 0
    assert "Password set" in capsys.readouterr().out

    async def check():
        db = await init_database(str(tmp_path / "cli.db"))
        user = await AuthService(db).authenticate("admin@netconfig.local", "Server-pass1")
        from core.audit import AuditTrail
        return user, await AuditTrail(db).query(action="password.set_on_server")
    user, entries = run(check())
    assert not user.must_change_password and len(entries) == 1
    assert auth._cli(["set-password"]) == 2


def test_login_gate_paths_and_redirect_targets():
    from ui.pages.login_page import is_public, safe_target
    assert is_public("/login") and is_public("/logout") and is_public("/_nicegui_ws/socket.io/")
    assert is_public("/_nicegui/3.17.1/static/vue.esm-browser.prod.js")
    assert not is_public("/") and not is_public("/_nicegui/auto/media/abc/report.html")  # files need a login
    assert safe_target("/?page=history") == "/?page=history"
    for bad in ("https://evil.example/", "//evil.example", "/\evil", "/logout", "/login?x=1", "", None):
        assert safe_target(bad) == "/"


def test_forced_change_must_differ(service):
    user = run(service.authenticate("admin@netconfig.local", "admin"))
    run(service.set_password(user.id, "Same-pass1", must_change=True))
    with pytest.raises(AuthError, match="different"):
        run(service.set_new_password(user.id, "Same-pass1"))
    run(service.set_new_password(user.id, "Other-pass2"))
    assert not run(service.current_user(user.id)).must_change_password


def test_users_screen_helpers():
    from ui.pages.users_section import _generated_password, _levels_text
    for _ in range(20):
        auth.check_password_policy(_generated_password())  # generated passwords pass the policy
    assert _levels_text({"history": VIEW, "outputs": UPDATE}) == "Run History: View · Output Documents: Update"
    assert _levels_text({}) == "No screens"
