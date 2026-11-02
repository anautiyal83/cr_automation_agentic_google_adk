"""Shared UI building blocks so every page looks the same (styles in app.CUSTOM_CSS)."""
from __future__ import annotations

from nicegui import ui

# Status pill for a table column: colour from the status text (completed, failed, running, …)
STATUS_CELL_SLOT = r'''
    <q-td :props="props">
        <span :class="'cr-status cr-status-' + String(props.value || '').toLowerCase()
                       .replace('pipelinestatus.', '').replace(/[^a-z]+/g, '-')">
            {{ String(props.value || '—').replace('PipelineStatus.', '').replace(/_/g, ' ') }}
        </span>
    </q-td>
'''


def page_header(title: str, subtitle: str = "") -> ui.row:
    """Title + subtitle on the left; returns the right-hand slot for page actions."""
    with ui.row().classes("cr-page-header"):
        with ui.column().classes("gap-0"):
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
            value_label = ui.label(value).classes("cr-stat-value-text")
            ui.label(title).classes("cr-stat-title-text")
    return value_label


def status_cells(table: ui.table, column: str = "status") -> ui.table:
    """Render ``column`` of ``table`` as coloured status pills."""
    table.add_slot(f"body-cell-{column}", STATUS_CELL_SLOT)
    return table
