"""Activity trail (spec 006, D8): who did what, when, on which run — admin-only reading.

Append-only: this module offers ``record`` and reading functions, nothing that edits or deletes an
entry. ``record`` never raises — a trail write that fails is logged and the action goes on.
Pipeline progress is recorded with the actor ``system``.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

SYSTEM = "system"
DONE, REFUSED, FAILED = "done", "refused", "failed"
OUTCOMES = (DONE, REFUSED, FAILED)

# keys whose values never reach the trail (only that they changed)
_SECRET_KEY = re.compile(r"pass(word)?|secret|token|api[_-]?key|credential", re.I)
_COLUMNS = ("at", "email", "role", "action", "outcome", "target_type", "target_id", "run_id",
            "uc_name", "details")


def mask_secrets(details: Any) -> Any:
    """Copy of ``details`` with every secret-looking key's value replaced by ``***``."""
    if isinstance(details, dict):
        return {k: ("***" if _SECRET_KEY.search(str(k)) and v not in (None, "") else mask_secrets(v))
                for k, v in details.items()}
    if isinstance(details, list):
        return [mask_secrets(v) for v in details]
    return details


class AuditTrail:
    """The ``audit_log`` table of the pipeline DB (``storage.db.Database``)."""

    def __init__(self, db) -> None:
        self.db = db

    async def record(self, action: str, *, user=None, outcome: str = DONE, target_type: str = "",
                     target_id: str = "", run_id: str = "", uc_name: str = "",
                     details: dict | None = None) -> None:
        """One entry. ``user``: a ``core.auth.CurrentUser``, ``None`` for the pipeline (``system``),
        or a dict ``{"id", "email", "role"}`` (e.g. a failed login with no user yet)."""
        if isinstance(user, dict):
            uid, uname, role = user.get("id"), user.get("email", ""), user.get("role", "")
        elif user is not None:
            uid, uname, role = user.id, user.email, user.role
        else:
            uid, uname, role = None, SYSTEM, SYSTEM
        try:
            await self.db.execute_insert(
                "INSERT INTO audit_log (at, user_id, email, role, action, target_type, target_id, "
                "run_id, uc_name, outcome, details) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (datetime.now(timezone.utc).isoformat(timespec="milliseconds"), uid, uname or "", role or "",
                 action, target_type, str(target_id or ""), str(run_id or ""), uc_name or "",
                 outcome if outcome in OUTCOMES else FAILED,
                 json.dumps(mask_secrets(details or {}), default=str)))
        except Exception as e:  # noqa: BLE001 — the trail must never break the action itself
            logger.error("Audit trail write failed (%s by %s): %s", action, uname, e)

    async def query(self, *, since: str = "", until: str = "", email: str = "", action: str = "",
                    run_id: str = "", uc_name: str = "", outcome: str = "", text: str = "",
                    limit: int = 500, offset: int = 0) -> list[dict]:
        """Entries newest first matching every given filter (ISO dates; ``text`` searches action,
        target, use case and details)."""
        where, params = self._where(since, until, email, action, run_id, uc_name, outcome, text)
        rows = await self.db.execute(
            f"SELECT * FROM audit_log{where} ORDER BY at DESC, id DESC LIMIT ? OFFSET ?",
            (*params, int(limit), int(offset)))
        for r in rows:
            try:
                r["details"] = json.loads(r.get("details") or "{}")
            except ValueError:
                pass
        return rows

    async def count(self, **filters) -> int:
        where, params = self._where(**{k: filters.get(k, "") for k in
                                       ("since", "until", "email", "action", "run_id", "uc_name",
                                        "outcome", "text")})
        rows = await self.db.execute(f"SELECT COUNT(*) AS n FROM audit_log{where}", params)
        return int(rows[0]["n"])

    async def distinct(self, column: str) -> list[str]:
        """Values for the screen's filter lists (users, actions)."""
        if column not in ("email", "action", "outcome"):
            raise ValueError(column)
        rows = await self.db.execute(f"SELECT DISTINCT {column} AS v FROM audit_log ORDER BY v")
        return [r["v"] for r in rows if r["v"]]

    async def export_csv(self, **filters) -> str:
        rows = await self.query(**filters, limit=1_000_000)
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(_COLUMNS)
        for r in rows:
            writer.writerow([json.dumps(r["details"]) if c == "details" else r.get(c, "") for c in _COLUMNS])
        return out.getvalue()

    @staticmethod
    def _where(since="", until="", email="", action="", run_id="", uc_name="", outcome="", text=""):
        clauses, params = [], []
        for col, op, val in (("at", ">=", since), ("at", "<=", until), ("email", "=", email),
                             ("action", "=", action), ("run_id", "=", run_id), ("uc_name", "=", uc_name),
                             ("outcome", "=", outcome)):
            if val:
                clauses.append(f"{col} {op} ?")
                params.append(val)
        if text:
            clauses.append("(action LIKE ? OR target_id LIKE ? OR uc_name LIKE ? OR details LIKE ? "
                           "OR email LIKE ?)")
            params += [f"%{text}%"] * 5
        return (" WHERE " + " AND ".join(clauses) if clauses else ""), tuple(params)
