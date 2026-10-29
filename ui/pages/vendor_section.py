"""Vendor Profile Management UI — CRUD for vendors, node types, and activities."""
from __future__ import annotations

import json
import logging
from typing import Any

from nicegui import ui

from shared.profiles import ProfileManager, Vendor, NodeType, Activity

logger = logging.getLogger(__name__)


class VendorSection:
    """Settings tab for managing vendor profiles."""

    def __init__(self, db=None):
        self._db = db
        self._pm: ProfileManager | None = None
        self._vendors: list[Vendor] = []
        self._build_ui()

    def _ensure_pm(self):
        if not self._pm and self._db:
            self._pm = ProfileManager(self._db)

    def _build_ui(self):
        with ui.column().classes("w-full gap-3"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label("Vendor Profiles").classes("text-subtitle1 font-bold")
                with ui.row().classes("gap-2"):
                    ui.button("Export Seed", icon="download",
                              on_click=self._export_seed) \
                        .props("flat color=grey-6 size=sm no-caps") \
                        .tooltip("Save all vendors, nodes, activities & settings to config/seed.json")
                    ui.button("+ Add Vendor", icon="add",
                              on_click=self._show_add_vendor_dialog) \
                        .props("unelevated color=deep-purple-6 size=sm no-caps")

            self._vendor_container = ui.column().classes("w-full gap-3")

        ui.timer(0.5, self._refresh, once=True)

    async def _export_seed(self):
        """Export all configuration to config/seed.json."""
        if not self._db:
            ui.notify("No database connection", type="warning")
            return
        try:
            path = await self._db.export_seed()
            ui.notify(f"Seed data exported to {path}", type="positive")
        except Exception as e:
            ui.notify(f"Export failed: {e}", type="negative")

    async def _refresh(self):
        self._ensure_pm()
        if not self._pm:
            return

        self._vendors = await self._pm.list_vendors()
        self._vendor_container.clear()

        if not self._vendors:
            with self._vendor_container:
                with ui.card().classes("w-full p-4 cr-card-inner"):
                    ui.label("No vendor profiles configured").classes("text-body2 cr-text-dim")
                    ui.label("Add a vendor to pre-configure SSH, prompts, escalation, and CIQ mappings.") \
                        .classes("text-caption cr-text-dim")
            return

        for v in self._vendors:
            self._render_vendor_card(v)

    def _render_vendor_card(self, v: Vendor):
        with self._vendor_container:
            with ui.card().classes("w-full p-4 cr-card-inner"):
                # Header
                with ui.row().classes("w-full items-center justify-between"):
                    with ui.row().classes("items-center gap-2"):
                        ui.icon("business", size="24px").classes("cr-text-accent")
                        ui.label(v.name).classes("text-subtitle1 font-bold")
                    with ui.row().classes("gap-1"):
                        ui.button(icon="edit", on_click=lambda vid=v.id: self._show_edit_vendor_dialog(vid)) \
                            .props("flat round size=sm color=deep-purple-6")
                        ui.button(icon="delete", on_click=lambda vid=v.id, vn=v.name: self._confirm_delete(vid, vn)) \
                            .props("flat round size=sm color=red-4")

                # Access Gateway
                gw = v.access_gateway
                with ui.row().classes("items-center gap-2 mt-1"):
                    if gw.get("enabled"):
                        ui.icon("vpn_key", size="16px").classes("cr-text-accent")
                        gw_host = gw.get("host", "?")
                        gw_port = gw.get("port", 22)
                        gw_user = gw.get("username", "?")
                        kex = gw.get("kex_algorithms", "") or "default"
                        hka = gw.get("host_key_algorithms", "") or "default"
                        ui.label(f'PAM: {gw.get("name", "?")} @ {gw_user}@{gw_host}:{gw_port} | '
                                 f'Auth: {gw.get("auth_method", "key")} | '
                                 f'KEX: {kex} | HKA: {hka}') \
                            .classes("text-caption cr-text-dim")
                    else:
                        ui.icon("terminal", size="16px").classes("cr-text-dim")
                        ui.label("Access: Direct SSH → Node").classes("text-caption cr-text-dim")

                # SSH defaults
                ssh = v.ssh_defaults
                if ssh and ssh.get("user"):
                    with ui.row().classes("items-center gap-2"):
                        ui.icon("terminal", size="16px").classes("cr-text-dim")
                        ui.label(f'SSH default user: {ssh.get("user", "?")}') \
                            .classes("text-caption cr-text-dim")

                # Node Types
                if v.node_types:
                    with ui.row().classes("items-center gap-2 mt-2"):
                        ui.icon("dns", size="16px").classes("cr-text-accent")
                        ui.label("Node Types").classes("text-caption font-semibold")
                        ui.button("+ Add", icon="add", on_click=lambda vid=v.id: self._show_add_node_dialog(vid)) \
                            .props("flat size=xs color=deep-purple-6 no-caps")

                    for nt in v.node_types:
                        with ui.row().classes("items-center gap-2 ml-6"):
                            ui.badge(nt.name, color="deep-purple-3").props("rounded dense")
                            ssh_cfg = nt.ssh_config or {}
                            elevate = ssh_cfg.get("elevate_to_root", False)
                            prompt = ssh_cfg.get("prompt_pattern", "")
                            via_pam = ssh_cfg.get("login_via_pam", False)
                            parts = []
                            if via_pam:
                                parts.append("via PAM")
                            if elevate:
                                parts.append("elevate_to_root")
                            if prompt:
                                parts.append(f'prompt={prompt[:30]}')
                            ui.label(", ".join(parts) if parts else "default") \
                                .classes("text-[11px] cr-text-dim font-mono")
                            ui.button(icon="edit", on_click=lambda nid=nt.id, vid=v.id: self._show_edit_node_dialog(vid, nid)) \
                                .props("flat round size=xs color=grey-6")
                            ui.button(icon="close", on_click=lambda nid=nt.id: self._delete_node_type(nid)) \
                                .props("flat round size=xs color=red-4")
                else:
                    with ui.row().classes("items-center gap-2 mt-2"):
                        ui.icon("dns", size="16px").classes("cr-text-dim")
                        ui.label("No node types").classes("text-caption cr-text-dim")
                        ui.button("+ Add", icon="add", on_click=lambda vid=v.id: self._show_add_node_dialog(vid)) \
                            .props("flat size=xs color=deep-purple-6 no-caps")

                # Activities
                if v.activities:
                    with ui.row().classes("items-center gap-2 mt-2"):
                        ui.icon("play_circle", size="16px").classes("cr-text-accent")
                        ui.label("Activities").classes("text-caption font-semibold")
                        ui.button("+ Add", icon="add", on_click=lambda vid=v.id: self._show_add_activity_dialog(vid)) \
                            .props("flat size=xs color=deep-purple-6 no-caps")

                    for act in v.activities:
                        with ui.row().classes("items-center gap-2 ml-6"):
                            ui.badge(act.display_name or act.name, color="teal-3").props("rounded dense")
                            nodes_str = ", ".join(act.node_types) if act.node_types else "all"
                            phases_str = ", ".join(act.default_phases) if act.default_phases else "default"
                            n_fields = len(act.ciq_mapping.get("field_mappings", []))
                            fields_str = f' fields={n_fields}' if n_fields else ''
                            ui.label(f'nodes=[{nodes_str}] phases=[{phases_str}]{fields_str}') \
                                .classes("text-[11px] cr-text-dim font-mono")
                            ui.button(icon="edit", on_click=lambda aid=act.id, vid=v.id: self._show_edit_activity_dialog(vid, aid)) \
                                .props("flat round size=xs color=grey-6")
                            ui.button(icon="close", on_click=lambda aid=act.id: self._delete_activity(aid)) \
                                .props("flat round size=xs color=red-4")
                else:
                    with ui.row().classes("items-center gap-2 mt-2"):
                        ui.icon("play_circle", size="16px").classes("cr-text-dim")
                        ui.label("No activities").classes("text-caption cr-text-dim")
                        ui.button("+ Add", icon="add", on_click=lambda vid=v.id: self._show_add_activity_dialog(vid)) \
                            .props("flat size=xs color=deep-purple-6 no-caps")

    # ------------------------------------------------------------------
    # Vendor dialogs
    # ------------------------------------------------------------------

    def _show_add_vendor_dialog(self):
        self._show_vendor_dialog(Vendor(), "Add Vendor")

    async def _show_edit_vendor_dialog(self, vendor_id: str):
        self._ensure_pm()
        v = await self._pm.get_vendor(vendor_id)
        if v:
            self._show_vendor_dialog(v, "Edit Vendor")

    def _show_vendor_dialog(self, vendor: Vendor, title: str):
        dialog = ui.dialog().props("persistent")
        with dialog, ui.card().classes("w-96 p-4"):
            ui.label(title).classes("text-subtitle1 font-bold mb-2")

            name_input = ui.input("Vendor Name", value=vendor.name) \
                .props("outlined dense").classes("w-full")

            gw = vendor.access_gateway
            ui.label("Node Access Method").classes("text-caption font-semibold mt-2")
            gw_enabled = ui.checkbox(
                "Use Privileged Access Manager (PAM) gateway",
                value=gw.get("enabled", False),
            )
            ui.label("When enabled: SSH → PAM Gateway → Node. When disabled: direct SSH → Node.") \
                .classes("text-[11px] cr-text-dim ml-7")

            # PAM fields — only visible when enabled
            gw_fields = ui.column().classes("w-full gap-1 ml-4")
            gw_fields.bind_visibility_from(gw_enabled, "value")
            with gw_fields:
                gw_name = ui.input("Gateway Name", value=gw.get("name", ""),
                                    placeholder="e.g., NIAM, CyberArk") \
                    .props("outlined dense").classes("w-full")
                with ui.row().classes("w-full gap-2"):
                    gw_host = ui.input("Host", value=gw.get("host", ""),
                                        placeholder="e.g., pam-gateway.corp.local") \
                        .props("outlined dense").classes("flex-1")
                    gw_port = ui.number("Port", value=gw.get("port", 22)) \
                        .props("outlined dense").classes("w-24")
                with ui.row().classes("w-full gap-2"):
                    gw_user = ui.input("Username", value=gw.get("username", ""),
                                        placeholder="e.g., oamsys") \
                        .props("outlined dense").classes("flex-1")
                    gw_pass = ui.input("Password", value=gw.get("password", ""),
                                        placeholder="Gateway password") \
                        .props("outlined dense type=password").classes("flex-1")
                gw_auth = ui.select(["key", "password", "certificate"],
                                     value=gw.get("auth_method", "key"), label="Auth Method") \
                    .props("outlined dense").classes("w-full")
                gw_kex = ui.input("KEX Algorithms",
                                   value=gw.get("kex_algorithms", ""),
                                   placeholder="e.g., +diffie-hellman-group14-sha1") \
                    .props("outlined dense").classes("w-full")
                gw_hka = ui.input("Host Key Algorithms",
                                   value=gw.get("host_key_algorithms", ""),
                                   placeholder="e.g., +ssh-rsa") \
                    .props("outlined dense").classes("w-full")
                gw_prompt = ui.input("Gateway Prompt Pattern (regex)",
                                      value=gw.get("prompt_pattern", ""),
                                      placeholder="e.g., .*\\$ or \\[oamsys@gateway ~\\]\\$") \
                    .props("outlined dense").classes("w-full")

            ssh = vendor.ssh_defaults
            ui.label("SSH Defaults").classes("text-caption font-semibold mt-2")
            ssh_user = ui.input("Default User", value=ssh.get("user", "oamsys")) \
                .props("outlined dense").classes("w-full")

            with ui.row().classes("w-full justify-end gap-2 mt-3"):
                ui.button("Cancel", on_click=dialog.close).props("flat no-caps")
                ui.button("Save", icon="save", on_click=lambda: self._save_vendor_from_dialog(
                    dialog, vendor,
                    name_input, gw_enabled, gw_name, gw_host, gw_port, gw_user, gw_pass,
                    gw_auth, gw_kex, gw_hka, gw_prompt,
                    ssh_user,
                )).props("unelevated color=deep-purple-6 no-caps")

        dialog.open()

    async def _save_vendor_from_dialog(self, dialog, vendor, name_input,
                                        gw_enabled, gw_name, gw_host, gw_port,
                                        gw_user, gw_pass, gw_auth,
                                        gw_kex, gw_hka, gw_prompt,
                                        ssh_user):
        self._ensure_pm()
        vendor.name = name_input.value.strip()
        if not vendor.name:
            ui.notify("Vendor name is required", type="warning")
            return

        vendor.access_gateway = {
            "enabled": gw_enabled.value,
            "name": gw_name.value.strip(),
            "host": gw_host.value.strip(),
            "port": int(gw_port.value or 22),
            "username": gw_user.value.strip(),
            "password": gw_pass.value.strip(),
            "auth_method": gw_auth.value,
            "kex_algorithms": gw_kex.value.strip(),
            "host_key_algorithms": gw_hka.value.strip(),
            "prompt_pattern": gw_prompt.value.strip(),
        }
        vendor.ssh_defaults = {
            "user": ssh_user.value.strip() or "oamsys",
            "connect_timeout_sec": 30,
        }

        await self._pm.save_vendor(vendor)
        dialog.close()
        ui.notify(f"Vendor '{vendor.name}' saved", type="positive")
        await self._refresh()

    async def _confirm_delete(self, vendor_id: str, vendor_name: str):
        with ui.dialog() as dlg, ui.card().classes("p-4"):
            ui.label(f"Delete vendor '{vendor_name}' and all its node types and activities?") \
                .classes("text-body2")
            with ui.row().classes("w-full justify-end gap-2 mt-2"):
                ui.button("Cancel", on_click=dlg.close).props("flat no-caps")
                ui.button("Delete", icon="delete",
                          on_click=lambda: self._do_delete_vendor(dlg, vendor_id)) \
                    .props("unelevated color=negative no-caps")
        dlg.open()

    async def _do_delete_vendor(self, dialog, vendor_id):
        self._ensure_pm()
        await self._pm.delete_vendor(vendor_id)
        dialog.close()
        ui.notify("Vendor deleted", type="warning")
        await self._refresh()

    # ------------------------------------------------------------------
    # Node Type dialogs
    # ------------------------------------------------------------------

    def _show_add_node_dialog(self, vendor_id: str):
        self._show_node_dialog(NodeType(vendor_id=vendor_id), vendor_id, "Add Node Type")

    async def _show_edit_node_dialog(self, vendor_id: str, node_id: str):
        self._ensure_pm()
        vendor = await self._pm.get_vendor(vendor_id)
        if not vendor:
            return
        nt = next((n for n in vendor.node_types if n.id == node_id), None)
        if nt:
            self._show_node_dialog(nt, vendor_id, "Edit Node Type")

    def _show_node_dialog(self, nt: NodeType, vendor_id: str, title: str):
        dialog = ui.dialog().props("persistent")
        ssh = nt.ssh_config or {}
        esc = nt.escalation or {}

        # Check if vendor has PAM enabled
        vendor = next((v for v in self._vendors if v.id == vendor_id), None)
        pam_enabled = vendor and vendor.access_gateway.get("enabled", False)

        with dialog, ui.card().classes("w-96 p-4"):
            ui.label(title).classes("text-subtitle1 font-bold mb-2")

            name = ui.input("Node Type Name", value=nt.name, placeholder="e.g., PGW") \
                .props("outlined dense").classes("w-full")

            # Login method — PAM or direct SSH
            if pam_enabled:
                gw_name = vendor.access_gateway.get("name", "PAM")
                login_options = [f"Via {gw_name} (PAM Gateway)", "Direct SSH"]
                current = login_options[0] if ssh.get("login_via_pam", True) else login_options[1]
            else:
                login_options = ["Direct SSH"]
                current = "Direct SSH"

            ui.label("Login Method").classes("text-caption font-semibold mt-2")
            login_method = ui.select(login_options, value=current) \
                .props("outlined dense").classes("w-full")

            ui.label("Prompt Pattern (regex)").classes("text-caption font-semibold mt-2")
            ui.label("Node shell prompt after login — used to detect command completion") \
                .classes("text-[11px] cr-text-dim")
            node_prompt = ui.input("Node Prompt", value=ssh.get("prompt_pattern", ""),
                                    placeholder=r"e.g., (?m)[#$]\s*$") \
                .props("outlined dense").classes("w-full")

            # Elevate to root — Ctrl+P+E automatic root escalation
            elevate_root = ui.checkbox(
                "Elevate to root (Ctrl+P+E)",
                value=ssh.get("elevate_to_root", False) or esc.get("method") == "ctrl_p_e",
            )
            ui.label(
                "Automatically escalates to root after SSH login using Ctrl+P+E. "
                "Sets elevate_to_root in the generated YAML node SSH config."
            ).classes("text-[11px] cr-text-dim ml-7")

            with ui.row().classes("w-full justify-end gap-2 mt-3"):
                ui.button("Cancel", on_click=dialog.close).props("flat no-caps")
                ui.button("Save", icon="save", on_click=lambda: self._save_node_from_dialog(
                    dialog, nt, vendor_id, name, login_method, node_prompt,
                    elevate_root,
                )).props("unelevated color=deep-purple-6 no-caps")

        dialog.open()

    async def _save_node_from_dialog(self, dialog, nt, vendor_id,
                                      name, login_method, node_prompt,
                                      elevate_root):
        self._ensure_pm()
        nt.vendor_id = vendor_id
        nt.name = name.value.strip().upper()
        nt.ssh_config = {
            "login_via_pam": "PAM" in (login_method.value or ""),
            "prompt_pattern": node_prompt.value.strip(),
            "elevate_to_root": elevate_root.value,
        }
        nt.escalation = {
            "method": "ctrl_p_e" if elevate_root.value else "none",
        }

        if not nt.name:
            ui.notify("Node type name is required", type="warning")
            return

        await self._pm.save_node_type(nt)
        dialog.close()
        ui.notify(f"Node type '{nt.name}' saved", type="positive")
        await self._refresh()

    async def _delete_node_type(self, node_id: str):
        self._ensure_pm()
        await self._pm.delete_node_type(node_id)
        ui.notify("Node type deleted", type="warning")
        await self._refresh()

    # ------------------------------------------------------------------
    # Activity dialogs
    # ------------------------------------------------------------------

    def _show_add_activity_dialog(self, vendor_id: str):
        self._show_activity_dialog(Activity(vendor_id=vendor_id), vendor_id, "Add Activity")

    async def _show_edit_activity_dialog(self, vendor_id: str, activity_id: str):
        self._ensure_pm()
        vendor = await self._pm.get_vendor(vendor_id)
        if not vendor:
            return
        act = next((a for a in vendor.activities if a.id == activity_id), None)
        if act:
            self._show_activity_dialog(act, vendor_id, "Edit Activity")

    def _show_activity_dialog(self, act: Activity, vendor_id: str, title: str):
        dialog = ui.dialog().props("persistent")
        ciq = act.ciq_mapping or {}

        # Get vendor's node types for the checkbox list
        vendor = next((v for v in self._vendors if v.id == vendor_id), None)
        available_nodes = [nt.name for nt in vendor.node_types] if vendor else []

        with dialog, ui.card().classes("w-[480px] p-4"):
            ui.label(title).classes("text-subtitle1 font-bold mb-2")

            name = ui.input("Activity Name", value=act.name,
                             placeholder="e.g., IFC_Capability_Modification") \
                .props("outlined dense").classes("w-full")
            display = ui.input("Display Name", value=act.display_name,
                                placeholder="e.g., IFC Capability Modification") \
                .props("outlined dense").classes("w-full")

            ui.label("Applicable Node Types").classes("text-caption font-semibold mt-2")
            node_checks = {}
            with ui.row().classes("gap-2 flex-wrap"):
                for n in available_nodes:
                    node_checks[n] = ui.checkbox(n, value=n in act.node_types) \
                        .props("dense color=deep-purple-6")

            ui.label("Default Phases").classes("text-caption font-semibold mt-2")
            phases = ui.input("Phases (comma-separated)",
                               value=", ".join(act.default_phases),
                               placeholder="preNodeHealthCheck, activity, postNodeHealthCheck") \
                .props("outlined dense").classes("w-full")

            ui.label("CIQ Mapping").classes("text-caption font-semibold mt-2")
            node_id_col = ui.input("Node ID Column", value=ciq.get("node_id_column", ""),
                                    placeholder="e.g., Node_Name") \
                .props("outlined dense").classes("w-full")
            gw_id_col = ui.input("Gateway ID Column", value=ciq.get("gateway_id_column", ""),
                                  placeholder="e.g., NIAM NAME") \
                .props("outlined dense").classes("w-full")
            group_col = ui.input("Grouping Column", value=ciq.get("grouping_column", ""),
                                  placeholder="e.g., Zone") \
                .props("outlined dense").classes("w-full")
            data_sheet = ui.input("Data Sheet", value=ciq.get("data_sheet", ""),
                                   placeholder="e.g., Profile Modification") \
                .props("outlined dense").classes("w-full")

            with ui.row().classes("w-full items-center justify-between mt-2"):
                ui.label("Field Mappings (CIQ Column → Node Field)").classes("text-caption font-semibold")
                add_mapping_btn = ui.button("+ Add Field", icon="add",
                    on_click=lambda: self._add_mapping_row(mappings_container, mapping_rows)) \
                    .props("flat size=xs color=deep-purple-6 no-caps")

            mapping_rows: list[dict] = []
            mappings_container = ui.column().classes("w-full gap-1")

            # Populate existing mappings
            existing_mappings = ciq.get("field_mappings", [])
            for m in existing_mappings:
                self._add_mapping_row(
                    mappings_container, mapping_rows,
                    ciq_col=m.get("ciq_column", ""),
                    node_field=m.get("node_field", ""),
                )

            with ui.row().classes("w-full justify-end gap-2 mt-3"):
                ui.button("Cancel", on_click=dialog.close).props("flat no-caps")
                ui.button("Save", icon="save", on_click=lambda: self._save_activity_from_dialog(
                    dialog, act, vendor_id, name, display, node_checks, phases,
                    node_id_col, gw_id_col, group_col, data_sheet, mapping_rows,
                )).props("unelevated color=deep-purple-6 no-caps")

        dialog.open()

    def _add_mapping_row(self, container, mapping_rows: list[dict],
                         ciq_col: str = "", node_field: str = ""):
        """Add a dynamic CIQ Column → Node Field mapping row."""
        row_data = {}
        with container:
            with ui.row().classes("w-full items-center gap-1") as row_el:
                row_data["ciq_input"] = ui.input(
                    "CIQ Column", value=ciq_col, placeholder="e.g., Mandatory Cap",
                ).props("outlined dense").classes("flex-1").style("font-size: 11px")
                ui.icon("arrow_forward", size="16px").classes("cr-text-dim")
                row_data["field_input"] = ui.input(
                    "Node Field", value=node_field, placeholder="e.g., scscfMandCap",
                ).props("outlined dense").classes("flex-1").style("font-size: 11px")
                row_data["row_el"] = row_el

                def remove(rd=row_data):
                    rd["row_el"].delete()
                    if rd in mapping_rows:
                        mapping_rows.remove(rd)

                ui.button(icon="close", on_click=remove) \
                    .props("flat round size=xs color=red-4")
        mapping_rows.append(row_data)

    async def _save_activity_from_dialog(self, dialog, act, vendor_id,
                                          name, display, node_checks, phases,
                                          node_id_col, gw_id_col, group_col, data_sheet, mapping_rows):
        self._ensure_pm()
        act.vendor_id = vendor_id
        act.name = name.value.strip().replace(" ", "_")
        act.display_name = display.value.strip() or act.name.replace("_", " ").title()
        act.node_types = [n for n, cb in node_checks.items() if cb.value]
        act.default_phases = [p.strip() for p in phases.value.split(",") if p.strip()]

        # Collect field mappings from dynamic rows
        field_mappings = []
        for rd in mapping_rows:
            ciq_col = rd["ciq_input"].value.strip()
            node_field = rd["field_input"].value.strip()
            if ciq_col and node_field:
                field_mappings.append({
                    "ciq_column": ciq_col,
                    "node_field": node_field,
                })

        act.ciq_mapping = {
            "node_id_column": node_id_col.value.strip(),
            "gateway_id_column": gw_id_col.value.strip(),
            "grouping_column": group_col.value.strip(),
            "data_sheet": data_sheet.value.strip(),
            "field_mappings": field_mappings,
        }

        if not act.name:
            ui.notify("Activity name is required", type="warning")
            return

        await self._pm.save_activity(act)
        dialog.close()
        ui.notify(f"Activity '{act.display_name}' saved", type="positive")
        await self._refresh()

    async def _delete_activity(self, activity_id: str):
        self._ensure_pm()
        await self._pm.delete_activity(activity_id)
        ui.notify("Activity deleted", type="warning")
        await self._refresh()
