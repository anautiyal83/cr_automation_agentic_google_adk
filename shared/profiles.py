"""Vendor profile management — CRUD for vendors, node types, and activities.

Profiles pre-configure environment defaults (SSH, prompts, escalation,
CIQ mappings) so the pipeline skips repetitive questions.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class NodeType:
    id: str = ""
    vendor_id: str = ""
    name: str = ""
    role: str = ""
    ssh_config: dict = field(default_factory=lambda: {
        "user": "oamsys",
        "port": 22,
        "prompt_pattern": "",
    })
    escalation: dict = field(default_factory=lambda: {
        "method": "none",
        "target_user": "",
        "prompt_pattern": "",
    })


@dataclass
class Activity:
    id: str = ""
    vendor_id: str = ""
    name: str = ""
    display_name: str = ""
    node_types: list[str] = field(default_factory=list)
    default_phases: list[str] = field(default_factory=lambda: [
        "preNodeHealthCheck", "activity", "postNodeHealthCheck"
    ])
    ciq_mapping: dict = field(default_factory=lambda: {
        "node_id_column": "",
        "gateway_id_column": "",
        "grouping_column": "",
        "data_sheet": "",
        "field_mappings": [],
    })


@dataclass
class Vendor:
    id: str = ""
    name: str = ""
    access_gateway: dict = field(default_factory=lambda: {
        "enabled": False,
        "name": "",
        "host": "",
        "port": 22,
        "username": "",
        "password": "",
        "auth_method": "key",
        "kex_algorithms": "",
        "host_key_algorithms": "",
        "prompt_pattern": "",
    })
    ssh_defaults: dict = field(default_factory=lambda: {
        "user": "oamsys",
        "port": 22,
        "kex_algorithms": "",
        "host_key_algorithms": "",
        "connect_timeout_sec": 30,
    })
    node_types: list[NodeType] = field(default_factory=list)
    activities: list[Activity] = field(default_factory=list)
    created_at: str = ""


# ---------------------------------------------------------------------------
# Profile Manager
# ---------------------------------------------------------------------------

class ProfileManager:
    """CRUD operations for vendor profiles stored in SQLite."""

    def __init__(self, db):
        self._db = db

    # --- Vendors ---

    async def list_vendors(self) -> list[Vendor]:
        rows = await self._db.execute(
            "SELECT * FROM vendors ORDER BY name"
        )
        vendors = []
        for r in rows:
            v = self._row_to_vendor(r)
            v.node_types = await self._get_node_types(v.id)
            v.activities = await self._get_activities(v.id)
            vendors.append(v)
        return vendors

    async def get_vendor(self, vendor_id: str) -> Vendor | None:
        row = await self._db.get_by_id("vendors", vendor_id)
        if not row:
            return None
        v = self._row_to_vendor(row)
        v.node_types = await self._get_node_types(v.id)
        v.activities = await self._get_activities(v.id)
        return v

    async def get_vendor_by_name(self, name: str) -> Vendor | None:
        rows = await self._db.execute(
            "SELECT * FROM vendors WHERE name = ?", (name,)
        )
        if not rows:
            return None
        v = self._row_to_vendor(rows[0])
        v.node_types = await self._get_node_types(v.id)
        v.activities = await self._get_activities(v.id)
        return v

    async def save_vendor(self, vendor: Vendor) -> str:
        if not vendor.id:
            vendor.id = str(uuid4())
        if not vendor.created_at:
            vendor.created_at = datetime.now(timezone.utc).isoformat()

        await self._db.insert_model("vendors", {
            "id": vendor.id,
            "name": vendor.name,
            "access_gateway": vendor.access_gateway,
            "ssh_defaults": vendor.ssh_defaults,
            "created_at": vendor.created_at,
        })
        return vendor.id

    async def delete_vendor(self, vendor_id: str) -> None:
        # Delete children first (cascade manually since SQLite FK enforcement may be off)
        await self._db.execute_insert(
            "DELETE FROM node_types WHERE vendor_id = ?", (vendor_id,)
        )
        await self._db.execute_insert(
            "DELETE FROM activities WHERE vendor_id = ?", (vendor_id,)
        )
        await self._db.delete("vendors", vendor_id)

    # --- Node Types ---

    async def save_node_type(self, nt: NodeType) -> str:
        if not nt.id:
            nt.id = str(uuid4())
        await self._db.insert_model("node_types", {
            "id": nt.id,
            "vendor_id": nt.vendor_id,
            "name": nt.name,
            "role": nt.role,
            "ssh_config": nt.ssh_config,
            "escalation": nt.escalation,
        })
        return nt.id

    async def delete_node_type(self, node_type_id: str) -> None:
        await self._db.delete("node_types", node_type_id)

    # --- Activities ---

    async def save_activity(self, act: Activity) -> str:
        if not act.id:
            act.id = str(uuid4())
        await self._db.insert_model("activities", {
            "id": act.id,
            "vendor_id": act.vendor_id,
            "name": act.name,
            "display_name": act.display_name or act.name.replace("_", " ").title(),
            "node_types": act.node_types,
            "default_phases": act.default_phases,
            "ciq_mapping": act.ciq_mapping,
        })
        return act.id

    async def delete_activity(self, activity_id: str) -> None:
        await self._db.delete("activities", activity_id)

    # --- Profile → Requirements ---

    async def build_profile_context(
        self, vendor_id: str, node_type_names: list[str], activity_name: str
    ) -> dict:
        """Build a requirements-compatible dict from a vendor profile selection.

        This is injected into the requirements gathering skill so it
        pre-fills known values and skips related questions.
        """
        vendor = await self.get_vendor(vendor_id)
        if not vendor:
            return {}

        # Find selected node types
        selected_nodes = [
            nt for nt in vendor.node_types
            if nt.name in node_type_names
        ]

        # Find selected activity
        activity = None
        for act in vendor.activities:
            if act.name == activity_name:
                activity = act
                break

        # Build pre-filled requirements
        profile = {
            "vendor": {
                "name": vendor.name,
                "access_gateway": vendor.access_gateway,
                "ssh_defaults": vendor.ssh_defaults,
            },
            "category_1_node_activity": {
                "nodes": [
                    {
                        "name": nt.name,
                        "role": nt.role,
                        "type": "remote",
                        "ssh": nt.ssh_config,
                        "escalation": nt.escalation,
                    }
                    for nt in selected_nodes
                ],
                "access_gateway_enabled": vendor.access_gateway.get("enabled", False),
            },
            "category_3_orchestration": {
                "phases": activity.default_phases if activity else [],
            },
        }

        if activity:
            ciq = activity.ciq_mapping
            if ciq.get("grouping_column"):
                profile["category_3_orchestration"]["grouping"] = {
                    "column": ciq["grouping_column"],
                }
            if ciq.get("data_sheet"):
                profile["category_1_node_activity"]["data_sheet"] = ciq["data_sheet"]
            if ciq.get("field_mappings"):
                profile["ciq_field_mappings"] = ciq["field_mappings"]
            if ciq.get("node_id_column") or ciq.get("gateway_id_column"):
                profile["node_id_config"] = {
                    "node_column": ciq.get("node_id_column", ""),
                    "gateway_id_column": ciq.get("gateway_id_column", ""),
                }

        return profile

    # --- Private helpers ---

    async def _get_node_types(self, vendor_id: str) -> list[NodeType]:
        rows = await self._db.execute(
            "SELECT * FROM node_types WHERE vendor_id = ? ORDER BY name",
            (vendor_id,)
        )
        return [self._row_to_node_type(r) for r in rows]

    async def _get_activities(self, vendor_id: str) -> list[Activity]:
        rows = await self._db.execute(
            "SELECT * FROM activities WHERE vendor_id = ? ORDER BY name",
            (vendor_id,)
        )
        return [self._row_to_activity(r) for r in rows]

    @staticmethod
    def _row_to_vendor(row: dict) -> Vendor:
        return Vendor(
            id=row["id"],
            name=row["name"],
            access_gateway=_parse_json(row.get("access_gateway", "{}")),
            ssh_defaults=_parse_json(row.get("ssh_defaults", "{}")),
            created_at=row.get("created_at", ""),
        )

    @staticmethod
    def _row_to_node_type(row: dict) -> NodeType:
        return NodeType(
            id=row["id"],
            vendor_id=row["vendor_id"],
            name=row["name"],
            role=row.get("role", ""),
            ssh_config=_parse_json(row.get("ssh_config", "{}")),
            escalation=_parse_json(row.get("escalation", "{}")),
        )

    @staticmethod
    def _row_to_activity(row: dict) -> Activity:
        return Activity(
            id=row["id"],
            vendor_id=row["vendor_id"],
            name=row["name"],
            display_name=row.get("display_name", ""),
            node_types=_parse_json(row.get("node_types", "[]")),
            default_phases=_parse_json(row.get("default_phases", "[]")),
            ciq_mapping=_parse_json(row.get("ciq_mapping", "{}")),
        )


def _parse_json(val) -> dict | list:
    if isinstance(val, (dict, list)):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            pass
    return {}
