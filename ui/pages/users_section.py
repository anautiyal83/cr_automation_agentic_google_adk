"""Users & Roles (spec 006 step 5) — the administrator's screen.

Users: add, edit (name, role, enabled), reset password (the user sets a new one at the next
login), delete. Roles: add / edit with a level per screen (None, View, Update), delete. The
service (``core.auth.AuthService``) enforces the rules (default and last administrator, built-in
admin role, roles in use); every change and refused attempt goes to the activity trail.
"""
from __future__ import annotations

import secrets
import string

from nicegui import ui

from core.audit import FAILED
from core.auth import ADMIN_ROLE, MIN_PASSWORD_LENGTH, SCREENS, UPDATE, VIEW, AuthError
from ui.access import requires_admin
from ui.components import card_title

_LEVEL_LABELS = {"": "None", VIEW: "View", UPDATE: "Update"}


def _generated_password() -> str:
    """A strong initial password (letters + digits + a symbol, 14 characters)."""
    alphabet = string.ascii_letters + string.digits
    core = "".join(secrets.choice(alphabet) for _ in range(12))
    return core[:6] + secrets.choice("!#%+-=?@") + secrets.choice(string.digits) + core[6:]


def _levels_text(levels: dict) -> str:
    if not levels:
        return "No screens"
    return " · ".join(f"{SCREENS.get(k, (k,))[0]}: {_LEVEL_LABELS[v]}" for k, v in levels.items() if k in SCREENS)


class UsersSection:
    def __init__(self, auth, audit, user):
        self.auth = auth
        self.audit = audit
        self.user = user  # the administrator using the screen
        self._roles: list[dict] = []
        self._build_ui()
        ui.timer(0.3, self.refresh, once=True)

    # ------------------------------------------------------------------ layout
    def _build_ui(self):
        tabs: dict = {}
        panels: dict = {}

        def switch(key: str):
            for k, p in panels.items():
                p.set_visibility(k == key)
            for k, b in tabs.items():
                b.classes(add="active") if k == key else b.classes(remove="active")

        with ui.row().classes("w-full gap-2 mb-2"):
            for key, label, icon in (("users", "Users", "group"), ("roles", "Roles", "admin_panel_settings")):
                tabs[key] = ui.button(label, icon=icon, on_click=lambda _, k=key: switch(k)) \
                    .props("flat no-caps size=sm").classes(f"cr-settings-tab {'active' if key == 'users' else ''}")

        with ui.column().classes("w-full gap-2") as panels["users"]:
            with ui.row().classes("w-full items-center justify-between"):
                card_title("Users", "group")
                ui.button("Add user", icon="person_add", on_click=self._add_user_dialog) \
                    .props("unelevated color=primary size=sm no-caps")
            self.users_table = ui.table(
                columns=[
                    {"name": "email", "label": "Email (login)", "field": "email", "align": "left", "sortable": True},
                    {"name": "display_name", "label": "Name", "field": "display_name", "align": "left"},
                    {"name": "role", "label": "Role", "field": "role", "align": "left", "sortable": True},
                    {"name": "state", "label": "Status", "field": "state", "align": "left"},
                    {"name": "last_login_at", "label": "Last login", "field": "last_login_at", "align": "left"},
                    {"name": "actions", "label": "", "field": "actions", "align": "right"},
                ],
                rows=[], row_key="id",
            ).props("dense flat bordered").classes("w-full")
            self.users_table.add_slot("body-cell-state", r'''
                <q-td :props="props">
                    <span :class="'cr-status cr-status-' + (props.row.active ? 'completed' : 'cancelled')">
                        {{ props.row.active ? 'Active' : 'Disabled' }}</span>
                    <span v-if="props.row.must_change_password" class="cr-status cr-status-paused-milestone"
                          style="margin-left:6px">Must set password</span>
                </q-td>''')
            self.users_table.add_slot("body-cell-actions", r'''
                <q-td :props="props">
                    <q-btn flat dense no-caps size="sm" color="primary" icon="edit" label="Edit"
                           @click="$parent.$emit('edit_user', props.row)" />
                    <q-btn flat dense no-caps size="sm" color="primary" icon="key" label="Reset password"
                           @click="$parent.$emit('reset_password', props.row)" />
                    <q-btn flat dense no-caps size="sm" color="negative" icon="delete" label="Delete"
                           :disable="props.row.is_default_admin || props.row.is_me"
                           @click="$parent.$emit('delete_user', props.row)" />
                </q-td>''')
            self.users_table.on("edit_user", lambda e: self._edit_user_dialog(e.args))
            self.users_table.on("reset_password", lambda e: self._reset_password_dialog(e.args))
            self.users_table.on("delete_user", lambda e: self._delete_user_dialog(e.args))

        with ui.column().classes("w-full gap-2") as panels["roles"]:
            with ui.row().classes("w-full items-center justify-between"):
                card_title("Roles", "admin_panel_settings")
                ui.button("Add role", icon="add", on_click=lambda: self._role_dialog(None)) \
                    .props("unelevated color=primary size=sm no-caps")
            self.roles_table = ui.table(
                columns=[
                    {"name": "name", "label": "Role", "field": "name", "align": "left"},
                    {"name": "description", "label": "Description", "field": "description", "align": "left"},
                    {"name": "screens", "label": "Screens", "field": "screens", "align": "left",
                     "classes": "cr-wrap-cell", "style": "white-space: normal"},
                    {"name": "users", "label": "Users", "field": "users", "align": "center"},
                    {"name": "actions", "label": "", "field": "actions", "align": "right"},
                ],
                rows=[], row_key="id",
            ).props("dense flat bordered").classes("w-full")
            self.roles_table.add_slot("body-cell-actions", r'''
                <q-td :props="props">
                    <span v-if="props.row.is_system" class="text-caption" style="color: var(--cr-text-dim)">
                        Built-in · all screens</span>
                    <template v-else>
                        <q-btn flat dense no-caps size="sm" color="primary" icon="edit" label="Edit"
                               @click="$parent.$emit('edit_role', props.row)" />
                        <q-btn flat dense no-caps size="sm" color="negative" icon="delete" label="Delete"
                               @click="$parent.$emit('delete_role', props.row)" />
                    </template>
                </q-td>''')
            self.roles_table.on("edit_role", lambda e: self._role_dialog(e.args))
            self.roles_table.on("delete_role", lambda e: self._delete_role_dialog(e.args))
        panels["roles"].set_visibility(False)

    async def refresh(self):
        try:
            users = await self.auth.list_users()
            self._roles = await self.auth.list_roles()
        except RuntimeError:
            return
        for u in users:
            u["is_me"] = u["id"] == self.user.id
            u["display_name"] = u["display_name"] or ""
            u["last_login_at"] = (u["last_login_at"] or "—").replace("T", " ")[:16]
        self.users_table.rows = users
        self.users_table.update()
        self.roles_table.rows = [{**r, "screens": "Every screen, users and roles, audit trail"
                                  if r["name"] == ADMIN_ROLE else _levels_text(r["levels"])}
                                 for r in self._roles]
        self.roles_table.update()

    def _role_names(self) -> list[str]:
        return [r["name"] for r in self._roles]

    # ------------------------------------------------------------------ helpers
    async def _done(self, action: str, message: str, **trail) -> None:
        await self.audit.record(action, user=self.user, **trail)
        ui.notify(message, type="positive")
        await self.refresh()

    async def _failed(self, action: str, error: AuthError, error_label, **trail) -> None:
        error_label.text = str(error)
        await self.audit.record(action, user=self.user, outcome=FAILED,
                                details={**trail.pop("details", {}), "reason": str(error)}, **trail)

    @staticmethod
    def _dialog_card(title: str):
        with ui.dialog() as dialog:
            card = ui.card().classes("p-4 gap-2").style("min-width: 420px; max-width: 560px")
        with card:
            ui.label(title).classes("text-subtitle1 font-semibold")
        return dialog, card

    # ------------------------------------------------------------------ users
    @requires_admin("user.create")
    async def _add_user_dialog(self):
        dialog, card = self._dialog_card("Add user")
        with card:
            email = ui.input("Email (login)").props("outlined dense autofocus").classes("w-full")
            name = ui.input("Name").props("outlined dense").classes("w-full")
            roles = self._role_names()
            role = ui.select(roles, value=roles[0] if roles else None, label="Role") \
                .props("outlined dense").classes("w-full")
            with ui.row().classes("w-full items-end gap-2 no-wrap"):
                password = ui.input("Initial password", password=True, password_toggle_button=True) \
                    .props("outlined dense").classes("flex-1")
                ui.button("Generate", icon="casino",
                          on_click=lambda: password.set_value(_generated_password())) \
                    .props("flat dense no-caps size=sm color=primary")
            ui.label(f"The user sets their own password at the first login "
                     f"(at least {MIN_PASSWORD_LENGTH} characters, letters with digits or symbols)."
                     ).classes("text-caption cr-text-dim")
            error = ui.label("").classes("text-caption").style("color: var(--cr-error)")

            async def save():
                error.text = ""
                try:
                    await self.auth.create_user(email.value or "", password.value or "", role.value or "",
                                                name.value or "")
                except AuthError as e:
                    await self._failed("user.create", e, error, target_type="user", target_id=email.value or "",
                                       details={"role": role.value})
                    return
                dialog.close()
                await self._done("user.create", f"User {email.value.strip().lower()} created",
                                 target_type="user", target_id=email.value.strip().lower(),
                                 details={"role": role.value, "name": name.value or ""})

            with ui.row().classes("w-full justify-end gap-2 mt-1"):
                ui.button("Cancel", on_click=dialog.close).props("flat no-caps")
                ui.button("Create user", on_click=save).props("unelevated color=primary no-caps")
        dialog.open()

    @requires_admin("user.update")
    async def _edit_user_dialog(self, row: dict):
        dialog, card = self._dialog_card(f"Edit {row['email']}")
        locked = bool(row.get("is_default_admin"))
        with card:
            name = ui.input("Name", value=row.get("display_name") or "").props("outlined dense").classes("w-full")
            role = ui.select(self._role_names(), value=row["role"], label="Role") \
                .props("outlined dense").classes("w-full")
            active = ui.switch("Active (can log in)", value=bool(row["active"]))
            if locked:
                role.disable()
                active.disable()
                ui.label("The default administrator always stays an active administrator.") \
                    .classes("text-caption cr-text-dim")
            error = ui.label("").classes("text-caption").style("color: var(--cr-error)")

            async def save():
                error.text = ""
                changes = {}
                if (name.value or "") != (row.get("display_name") or ""):
                    changes["display_name"] = name.value or ""
                if role.value != row["role"]:
                    changes["role"] = role.value
                if bool(active.value) != bool(row["active"]):
                    changes["active"] = bool(active.value)
                if not changes:
                    dialog.close()
                    return
                try:
                    await self.auth.update_user(row["id"], **changes)
                except AuthError as e:
                    await self._failed("user.update", e, error, target_type="user", target_id=row["email"],
                                       details={"changes": changes})
                    return
                dialog.close()
                before = {k: row.get(k) for k in changes}
                await self._done("user.update", f"{row['email']} updated", target_type="user",
                                 target_id=row["email"], details={"before": before, "after": changes})

            with ui.row().classes("w-full justify-end gap-2 mt-1"):
                ui.button("Cancel", on_click=dialog.close).props("flat no-caps")
                ui.button("Save", on_click=save).props("unelevated color=primary no-caps")
        dialog.open()

    @requires_admin("user.password_reset")
    async def _reset_password_dialog(self, row: dict):
        dialog, card = self._dialog_card(f"Reset password — {row['email']}")
        with card:
            with ui.row().classes("w-full items-end gap-2 no-wrap"):
                password = ui.input("New password", password=True, password_toggle_button=True) \
                    .props("outlined dense autofocus").classes("flex-1")
                ui.button("Generate", icon="casino",
                          on_click=lambda: password.set_value(_generated_password())) \
                    .props("flat dense no-caps size=sm color=primary")
            ui.label("Give it to the user; they must set their own at the next login.") \
                .classes("text-caption cr-text-dim")
            error = ui.label("").classes("text-caption").style("color: var(--cr-error)")

            async def save():
                error.text = ""
                try:
                    await self.auth.set_password(row["id"], password.value or "", must_change=True)
                except AuthError as e:
                    await self._failed("user.password_reset", e, error, target_type="user", target_id=row["email"])
                    return
                dialog.close()
                await self._done("user.password_reset", f"Password reset for {row['email']}",
                                 target_type="user", target_id=row["email"])

            with ui.row().classes("w-full justify-end gap-2 mt-1"):
                ui.button("Cancel", on_click=dialog.close).props("flat no-caps")
                ui.button("Reset password", on_click=save).props("unelevated color=primary no-caps")
        dialog.open()

    @requires_admin("user.delete")
    async def _delete_user_dialog(self, row: dict):
        dialog, card = self._dialog_card(f"Delete {row['email']}?")
        with card:
            ui.label("The user can no longer log in. Their runs stay (visible to the administrator).") \
                .classes("text-caption cr-text-dim")
            error = ui.label("").classes("text-caption").style("color: var(--cr-error)")

            async def confirm():
                error.text = ""
                try:
                    await self.auth.delete_user(row["id"], self.user.id)
                except AuthError as e:
                    await self._failed("user.delete", e, error, target_type="user", target_id=row["email"])
                    return
                dialog.close()
                await self._done("user.delete", f"{row['email']} deleted", target_type="user",
                                 target_id=row["email"], details={"role": row["role"]})

            with ui.row().classes("w-full justify-end gap-2 mt-1"):
                ui.button("Cancel", on_click=dialog.close).props("flat no-caps")
                ui.button("Delete", icon="delete", on_click=confirm).props("unelevated color=negative no-caps")
        dialog.open()

    # ------------------------------------------------------------------ roles
    @requires_admin("role.save")
    async def _role_dialog(self, row: dict | None):
        dialog, card = self._dialog_card("Add role" if row is None else f"Edit role {row['name']}")
        current = dict((row or {}).get("levels") or {})
        with card:
            name = ui.input("Role name", value=(row or {}).get("name", "")).props("outlined dense") \
                .classes("w-full")
            description = ui.input("Description", value=(row or {}).get("description", "")) \
                .props("outlined dense").classes("w-full")
            ui.label("Screens").classes("text-caption font-semibold mt-1")
            ui.label("View: the screen and its data, action buttons disabled. Update: view and act.") \
                .classes("text-caption cr-text-dim")
            pickers: dict = {}
            group = None
            with ui.element("div").classes("w-full").style(
                    "border: 1px solid var(--cr-border); border-radius: 8px; padding: 4px 10px"):
                for key, (label, grp) in SCREENS.items():
                    if grp != group:
                        group = grp
                        ui.label(grp.upper()).classes("text-caption cr-text-dim font-semibold") \
                            .style("letter-spacing: .06em; margin-top: 6px")
                    with ui.row().classes("w-full items-center justify-between no-wrap"):
                        ui.label(label).classes("text-body2")
                        pickers[key] = ui.toggle(_LEVEL_LABELS, value=current.get(key, "")) \
                            .props("dense no-caps unelevated size=sm toggle-color=primary")
            error = ui.label("").classes("text-caption").style("color: var(--cr-error)")

            async def save():
                error.text = ""
                levels = {k: p.value for k, p in pickers.items() if p.value}
                action = "role.update" if row else "role.create"
                try:
                    await self.auth.save_role(name.value or "", levels, description.value or "",
                                              role_id=(row or {}).get("id"))
                except AuthError as e:
                    await self._failed(action, e, error, target_type="role", target_id=name.value or "",
                                       details={"levels": levels})
                    return
                dialog.close()
                details = {"levels": levels} if row is None else {"before": current, "after": levels,
                                                                   "renamed_from": row["name"]}
                await self._done(action, f"Role {(name.value or '').strip().lower()} saved", target_type="role",
                                 target_id=(name.value or "").strip().lower(), details=details)

            with ui.row().classes("w-full justify-end gap-2 mt-1"):
                ui.button("Cancel", on_click=dialog.close).props("flat no-caps")
                ui.button("Save role", on_click=save).props("unelevated color=primary no-caps")
        dialog.open()

    @requires_admin("role.delete")
    async def _delete_role_dialog(self, row: dict):
        dialog, card = self._dialog_card(f"Delete role {row['name']}?")
        with card:
            error = ui.label("").classes("text-caption").style("color: var(--cr-error)")

            async def confirm():
                error.text = ""
                try:
                    await self.auth.delete_role(row["id"])
                except AuthError as e:
                    await self._failed("role.delete", e, error, target_type="role", target_id=row["name"])
                    return
                dialog.close()
                await self._done("role.delete", f"Role {row['name']} deleted", target_type="role",
                                 target_id=row["name"], details={"levels": row.get("levels")})

            with ui.row().classes("w-full justify-end gap-2 mt-1"):
                ui.button("Cancel", on_click=dialog.close).props("flat no-caps")
                ui.button("Delete", icon="delete", on_click=confirm).props("unelevated color=negative no-caps")
        dialog.open()
