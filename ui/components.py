"""Shared UI building blocks so every page looks the same (styles in app.CUSTOM_CSS)."""
from __future__ import annotations

from nicegui import ui

# Status pill for a table column: colour from the status text (completed, failed, running, …)
STATUS_CELL_SLOT = r'''
    <q-td :props="props">
        <span :class="'cr-status cr-status-' + String(props.value || '').toLowerCase()
                       .replace('pipelinestatus.', '').replace(/[^a-z]+/g, '-')">
            {{ String(props.value || '—').replace('PipelineStatus.', '').replace(/^queued$/, 'waiting').replace(/_/g, ' ') }}
        </span>
    </q-td>
'''


# Sidebar group of each page, shown as the header eyebrow ("RESULTS / RUN HISTORY")
_PAGE_GROUPS = {
    "Dashboard": "Workspace", "New Run": "Workspace", "Live Run": "Workspace", "Pipeline Queue": "Workspace",
    "Run Artifacts": "Results", "Artifact Cache": "Results", "Run History": "Results",
    "LLM Usage": "Admin", "Artifact Cleanup": "Admin", "Settings": "Admin",
    "Users & Roles": "Admin", "Audit Trail": "Admin",
}


def page_header(title: str, subtitle: str = "") -> ui.row:
    """Eyebrow, title + subtitle on the left; returns the right-hand slot for page actions."""
    with ui.row().classes("cr-page-header"):
        with ui.column().classes("gap-0"):
            group = _PAGE_GROUPS.get(title)
            ui.label(f"{group} / {title}" if group else title).classes("cr-page-eyebrow")
            ui.label(title).classes("cr-page-title")
            if subtitle:
                ui.label(subtitle).classes("cr-page-subtitle")
        actions = ui.row().classes("items-center gap-2")
    return actions


def card_title(text: str, icon: str = "") -> None:
    """Heading inside a card (same size and colour everywhere)."""
    with ui.row().classes("cr-card-title"):
        if icon:
            ui.icon(icon, size="16px").classes("cr-text-accent")
        ui.label(text)


def stat_card(title: str, value: str, icon: str, color: str) -> ui.label:
    """Equal-width KPI tile (lives in a ``.cr-stat-grid``); returns the value label."""
    with ui.element("div").classes("cr-stat-card"):
        with ui.element("div").classes("cr-stat-icon-wrap").style(f"color: {color}; background: {color}1A"):
            ui.icon(icon, size="18px")
        with ui.column().classes("gap-0 min-w-0"):
            ui.label(title).classes("cr-stat-title-text")
            value_label = ui.label(value).classes("cr-stat-value-text")
    return value_label


def status_cells(table: ui.table, column: str = "status") -> ui.table:
    """Render ``column`` of ``table`` as coloured status pills."""
    table.add_slot(f"body-cell-{column}", STATUS_CELL_SLOT)
    return table


def confirm(title: str, message: str, on_confirm, ok_label: str = "Delete", ok_icon: str = "delete") -> None:
    """Ask before a delete / clear / remove (spec 008 U-D5): ``on_confirm`` (sync or async) runs only
    on the confirm button; Cancel or closing the dialog does nothing."""
    with ui.dialog() as dialog:
        card = ui.card().classes("p-4 gap-2 cr-card-inner").style("min-width: 380px; max-width: 520px")
    with card:
        ui.label(title).classes("text-subtitle1 font-semibold")
        ui.label(message).classes("text-body2 cr-text-dim")

        async def ok():
            dialog.close()
            result = on_confirm()
            if hasattr(result, "__await__"):
                await result

        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("Cancel", on_click=dialog.close).props("flat no-caps")
            ui.button(ok_label, icon=ok_icon, on_click=ok).props("unelevated color=negative no-caps")
    dialog.open()


class PhaseOptions:
    """The run phase checkboxes of an activity (spec 010): every phase and sub-phase a checkbox
    with its purpose, a group ticks / clears its sub-phases and stays ticked while one is, "via
    repo server" only under Backup."""

    def __init__(self, options: dict | None = None, hint: str = ""):
        from shared import run_options
        self._boxes: dict[str, ui.checkbox] = {}
        self._groups: dict[str, ui.checkbox] = {}
        self._syncing = False
        with ui.column().classes("cr-phase-options w-full gap-0"):
            if hint:
                ui.label(hint).classes("text-caption cr-text-dim")
            done: set[str] = set()
            for key, label, group in run_options.PHASES:
                if group and group not in done:
                    done.add(group)
                    self._groups[group] = ui.checkbox(run_options.GROUPS[group], value=True,
                                                      on_change=lambda e, g=group: self._on_group(g, e.value))
                with ui.row().classes("items-center gap-1 no-wrap" + (" cr-phase-sub" if group else "")):
                    self._boxes[key] = ui.checkbox(label, value=True,
                                                   on_change=lambda e, g=group: self._on_phase(g))
                    ui.label(run_options.PURPOSE[key]).classes("text-caption cr-text-dim")
                if key == "backup":
                    with ui.row().classes("cr-phase-sub") as self._repo_row:
                        self._via_repo = ui.checkbox(
                            "via repo server — every file (input files, backups, restores) goes through "
                            "the repo server", value=False)
            self._error = ui.label("Select at least one phase").classes("text-caption") \
                .style("color: var(--cr-error)")
        self.set(options)

    def _on_group(self, group: str, value: bool) -> None:
        from shared import run_options
        if self._syncing:
            return
        self._syncing = True
        for key, _, g in run_options.PHASES:
            if g == group:
                self._boxes[key].value = value
        self._syncing = False
        self._on_phase(group)

    def _on_phase(self, group: str) -> None:
        from shared import run_options
        if not self._syncing and group:
            self._syncing = True
            self._groups[group].value = any(self._boxes[k].value for k, _, g in run_options.PHASES if g == group)
            self._syncing = False
        backup = self._boxes["backup"].value
        self._repo_row.set_visibility(backup)
        if not backup:
            self._via_repo.value = False
        self._error.set_visibility(not any(b.value for b in self._boxes.values()))

    @property
    def value(self) -> dict:
        from shared import run_options
        return run_options.normalize({"phases": {k: b.value for k, b in self._boxes.items()},
                                      "backup_via_repo": self._via_repo.value})

    def set(self, options: dict | None) -> None:
        from shared import run_options
        opts = run_options.normalize(options)
        self._syncing = True
        for k, b in self._boxes.items():
            b.value = opts["phases"][k]
        for g, box in self._groups.items():
            box.value = any(opts["phases"][k] for k, _, gg in run_options.PHASES if gg == g)
        self._via_repo.value = opts["backup_via_repo"]
        self._syncing = False
        self._on_phase("")
