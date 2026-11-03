"""NiceGUI application entry point — polished single-page layout."""
from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path

# Suppress HuggingFace warnings — must be set before any HF imports
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", message=".*huggingface.*")
warnings.filterwarnings("ignore", message=".*unauthenticated.*")
import logging as _logging

from core.logging_config import setup_logging, get_flow_logger

# Configure structured logging (flow.log, error.log, trace.log)
setup_logging()

from nicegui import ui, app

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Load .env file if present
_env_file = Path(__file__).resolve().parent.parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

from orchestrator.runner import PipelineRunner
from storage.artifact_store import ArtifactStore
from storage.db import Database, init_database
from ui.components import card_title, page_header, stat_card, status_cells
from ui.pages.upload_section import UploadSection
from ui.pages.progress_section import ProgressSection
from ui.pages.history_section import HistorySection
from ui.pages.cleanup_section import CleanupSection
from ui.pages.usage_section import UsageSection
from ui.pages.settings_section import SettingsSection
from ui.pages.vendor_section import VendorSection
from ui.pages.output_viewer_section import OutputViewerSection

_db: Database | None = None
_runner: PipelineRunner | None = None
_artifact_store: ArtifactStore | None = None
_config_manager = None

CUSTOM_CSS = """
<style>
/* --- Theme A "Enterprise light": one blue primary, neutral greys, no gradients --- */
body.body--light {
    --cr-primary: #0B5FFF;
    --cr-primary-dark: #0047D6;
    --cr-primary-soft: #EAF1FF;
    --cr-accent: #0B5FFF;
    --cr-surface: #FFFFFF;
    --cr-surface-light: #F9FAFB;
    --cr-surface-hover: #F2F4F7;
    --cr-text: #101828;
    --cr-text-dim: #667085;
    --cr-success: #039855;
    --cr-error: #D92D20;
    --cr-warning: #DC6803;
    --cr-info: #1570EF;
    --cr-border: #E4E7EC;
    --cr-header-bg: #FFFFFF;
    --cr-header-text: #101828;
    --cr-body-bg: #F7F8FA;
    --cr-hero-bg: #FFFFFF;
    --cr-shadow: rgba(16,24,40,0.06);
    --cr-card-value: #101828;
    background: var(--cr-body-bg) !important;
}
body.body--dark {
    --cr-primary: #4C8DFF;
    --cr-primary-dark: #2F74F5;
    --cr-primary-soft: rgba(76,141,255,0.14);
    --cr-accent: #4C8DFF;
    --cr-surface: #161B26;
    --cr-surface-light: #1D2433;
    --cr-surface-hover: #242C3D;
    --cr-text: #F2F4F7;
    --cr-text-dim: #98A2B3;
    --cr-success: #32D583;
    --cr-error: #F97066;
    --cr-warning: #FDB022;
    --cr-info: #53B1FD;
    --cr-border: #2B3446;
    --cr-header-bg: #161B26;
    --cr-header-text: #F2F4F7;
    --cr-body-bg: #0F131C;
    --cr-hero-bg: #161B26;
    --cr-shadow: rgba(0,0,0,0.35);
    --cr-card-value: #F2F4F7;
    background: var(--cr-body-bg) !important;
}
.cr-header {
    background: var(--cr-header-bg) !important;
    color: var(--cr-header-text) !important;
    border-bottom: 1px solid var(--cr-border);
    box-shadow: none;
    z-index: 1000;
}
.cr-logo {
    width: 28px; height: 28px; border-radius: 7px; background: var(--cr-primary); color: #fff;
    display: flex; align-items: center; justify-content: center;
}
.cr-breadcrumb { font-size: 13px; color: var(--cr-text-dim); }
.cr-breadcrumb b { color: var(--cr-text); font-weight: 600; }
.cr-nav-group {
    font-size: 11px; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase;
    color: var(--cr-text-dim); padding: 14px 16px 4px;
}
.cr-header .q-btn {
    z-index: 1001;
    pointer-events: auto !important;
}
.cr-hero {
    background: var(--cr-hero-bg);
    border: 1px solid var(--cr-border);
    border-radius: 10px;
    padding: 14px 18px;
}
/* KPI tiles: equal width, fill the row (ui/components.stat_card) */
.cr-stat-grid {
    display: grid; width: 100%; gap: 10px;
    grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
}
.cr-stat-card {
    background: var(--cr-surface);
    border: 1px solid var(--cr-border);
    border-radius: 10px;
    padding: 12px 14px;
    height: 68px;
    display: flex; align-items: center; gap: 12px;
    transition: border-color 0.2s ease, box-shadow 0.2s ease;
}
.cr-stat-card:hover { border-color: var(--cr-primary); box-shadow: 0 4px 14px var(--cr-shadow); }
.cr-stat-icon-wrap {
    width: 36px; height: 36px; border-radius: 9px; flex-shrink: 0;
    display: flex; align-items: center; justify-content: center;
}
.cr-stat-value-text {
    font-size: 18px; font-weight: 700; line-height: 1.2; color: var(--cr-card-value);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.cr-stat-title-text { font-size: 11px; color: var(--cr-text-dim); white-space: nowrap; }
.cr-stat-value {
    color: var(--cr-card-value) !important;
}
.cr-stat-title {
    color: var(--cr-text-dim) !important;
}
.cr-section {
    background: var(--cr-surface) !important;
    border: 1px solid var(--cr-border) !important;
    border-radius: 8px !important;
    overflow: hidden;
}
.cr-section .q-expansion-item__toggle-icon {
    color: var(--cr-accent) !important;
}
.cr-upload-card {
    background: var(--cr-surface-light) !important;
    border: 2px dashed var(--cr-border) !important;
    border-radius: 8px !important;
    transition: all 0.2s ease;
    padding: 10px;
    min-height: 150px;
    display: flex !important;
    flex-direction: column !important;
}
.cr-upload-card .q-card__section {
    flex: 1;
    display: flex;
    flex-direction: column;
}
.cr-upload-card:hover {
    border-color: var(--cr-primary) !important;
    background: var(--cr-surface-hover) !important;
}
.cr-upload-card.uploaded {
    border-color: var(--cr-success) !important;
    border-style: solid !important;
}
@keyframes cr-spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
.cr-spin .material-icons, .cr-spin { animation: cr-spin 1.5s linear infinite; }
.cr-step-chip {
    border-radius: 8px !important;
    min-width: 90px;
    text-align: center;
}
.cr-badge {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
.cr-badge-required { background: rgba(248,113,113,0.15); color: var(--cr-error); }
.cr-badge-optional { background: rgba(144,144,168,0.15); color: var(--cr-text-dim); }
.q-expansion-item {
    border-radius: 12px !important;
    overflow: hidden;
    margin-bottom: 4px;
}
/* --- Utility classes for theme-aware colors --- */
.cr-text-primary { color: var(--cr-text) !important; }
.cr-text-dim { color: var(--cr-text-dim) !important; }
.cr-text-accent { color: var(--cr-primary) !important; }
.cr-text-success { color: var(--cr-success) !important; }
.cr-text-error { color: var(--cr-error) !important; }
.cr-text-warning { color: var(--cr-warning) !important; }
.cr-bg-surface { background: var(--cr-surface) !important; }
.cr-bg-surface-light { background: var(--cr-surface-light) !important; }
.cr-border { border-color: var(--cr-border) !important; }
.cr-step-circle {
    width: 28px; height: 28px; border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    background: var(--cr-surface-light); border: 2px solid var(--cr-border);
    transition: all 0.3s ease;
}
.cr-step-circle-active { background: rgba(11,95,255,0.15); }
/* Step tracker: 10 equal boxes, fixed height, same border in every state */
.cr-steps { display: flex; align-items: center; width: 100%; padding: 2px 0; }
.cr-step { flex: 1 1 0; min-width: 0; }
.cr-step-box {
    height: 116px; padding: 10px 6px 8px;
    border: 1px solid var(--cr-border); border-radius: 10px;
    background: var(--cr-surface-light);
    display: flex; flex-direction: column; align-items: center; gap: 3px;
    transition: border-color 0.3s ease, background 0.3s ease, box-shadow 0.3s ease;
}
.cr-step-box.is-passed { border-color: var(--cr-success); }
.cr-step-box.is-failed { border-color: var(--cr-error); background: rgba(220,38,38,0.04); }
.cr-step-box.is-retrying { border-color: var(--cr-warning); background: rgba(217,119,6,0.05); }
.cr-step-box.is-in-progress {
    border-color: var(--cr-primary); background: rgba(11,95,255,0.07);
    box-shadow: 0 0 0 1px var(--cr-primary);
}
.cr-step-num { font-size: 10px; font-weight: 600; color: var(--cr-text); }
.cr-step-name {
    font-size: 10px; line-height: 13px; color: var(--cr-text-dim); text-align: center;
    width: 100%; height: 26px; min-height: 26px; overflow: hidden; flex-shrink: 0;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
}
.cr-step-status {
    font-size: 10px; font-weight: 600; color: var(--cr-text-dim); margin-top: auto;
    max-width: 100%; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.cr-step-connector {
    flex: 0 0 18px; display: flex; align-items: center; justify-content: center;
}
.cr-step-connector::before {
    content: ''; display: block; width: 11px; height: 2px;
    background: var(--cr-conn, var(--cr-border)); transition: background 0.3s;
}
.cr-step-connector::after {
    content: ''; display: block; width: 0; height: 0;
    border-top: 4px solid transparent; border-bottom: 4px solid transparent;
    border-left: 5px solid var(--cr-conn, var(--cr-border)); transition: border-color 0.3s;
}
.cr-card-inner {
    background: var(--cr-surface-light) !important;
    border: 1px solid var(--cr-border) !important;
    border-radius: 8px;
}
.cr-settings-tab {
    border: 1px solid var(--cr-border) !important;
    border-radius: 6px !important;
    color: var(--cr-text-dim) !important;
    font-size: 11px !important;
    padding: 2px 8px !important;
    min-height: 28px !important;
    transition: all 0.15s ease;
}
.cr-settings-tab:hover {
    background: var(--cr-surface-hover) !important;
    color: var(--cr-text) !important;
}
.cr-settings-tab.active {
    background: var(--cr-primary) !important;
    color: white !important;
    border-color: var(--cr-primary) !important;
}
.cr-settings-tab.active .q-btn__content, .cr-settings-tab.active .q-icon { color: #fff !important; }
.cr-settings-tab:not(.active) { background: var(--cr-surface) !important; }
/* Layout — entire page is fixed viewport, only cr-content scrolls */
html, body { margin: 0 !important; height: 100vh; overflow: hidden; }
* { box-sizing: border-box; }
.nicegui-content { padding: 0 !important; height: 100vh; overflow: hidden; }
.q-page-container { padding-top: 0 !important; height: 100vh; overflow: hidden; }
.q-page { min-height: 0 !important; padding: 0 !important; height: 100vh; overflow: hidden; }
.q-layout { height: 100vh !important; overflow: hidden !important; }
.q-page-container .q-page-container__page { height: 100vh !important; overflow: hidden !important; }
/* --- Sidebar --- */
.cr-sidebar {
    background: var(--cr-surface) !important;
    border-right: 1px solid var(--cr-border);
    width: 184px;
    min-width: 184px;
    gap: 2px !important;
    height: calc(100vh - 48px);
    max-height: calc(100vh - 48px);
    position: fixed;
    top: 48px;
    left: 0;
    overflow-y: auto;
    overflow-x: hidden;
    z-index: 100;
    padding: 10px 0;
    display: flex;
    flex-direction: column;
}
.cr-sidebar::-webkit-scrollbar { width: 4px; }
.cr-sidebar::-webkit-scrollbar-thumb { background: var(--cr-border); border-radius: 2px; }
.cr-sidebar::-webkit-scrollbar-track { background: transparent; }
.cr-sidebar-item {
    display: flex;
    align-items: center;
    gap: 10px;
    width: 100%;
    padding: 8px 14px 8px 13px;
    cursor: pointer;
    transition: all 0.15s ease;
    color: var(--cr-text-dim);
    font-size: 12px;
    font-weight: 500;
    white-space: nowrap;
    border-left: 3px solid transparent;
    user-select: none;
}
.cr-sidebar-item:hover {
    background: var(--cr-surface-hover);
    color: var(--cr-text);
}
.cr-sidebar-item.active {
    background: var(--cr-primary-soft);
    color: var(--cr-primary);
    border-left-color: var(--cr-primary);
    font-weight: 600;
}
.cr-sidebar-item .material-icons {
    font-size: 16px;
}
.cr-sidebar-divider {
    height: 1px;
    background: var(--cr-border);
    margin: 4px 12px;
}
.cr-content {
    position: fixed;
    top: 48px;
    left: 184px;
    right: 0;
    bottom: 0;
    padding: 14px 20px 24px;
    overflow-y: auto;
    overflow-x: hidden;
}
.cr-content::-webkit-scrollbar { width: 6px; }
.cr-content::-webkit-scrollbar-thumb { background: var(--cr-border); border-radius: 3px; }
.cr-content::-webkit-scrollbar-track { background: transparent; }
/* Let section cards fill width and not constrain height */
.cr-section { overflow: visible !important; width: 100% !important; }
.cr-section > .q-card__section { overflow: visible !important; width: 100% !important; }
/* Force all NiceGUI containers inside content to use full width, no clipping */
.cr-content .nicegui-column, .cr-content .nicegui-row { max-width: 100% !important; }
.cr-content .q-card { max-width: 100% !important; overflow: visible !important; }
.cr-content .q-card__section { overflow: visible !important; }
/* Tables fill width */
.cr-content .q-table { width: 100% !important; }
.cr-content .q-table__container { width: 100% !important; }

/* --- Consistency layer (shared by all pages; see ui/components.py) --- */
.cr-page { width: 100%; gap: 12px !important; }
.cr-page-header {
    width: 100%; display: flex; align-items: flex-end; justify-content: space-between;
    flex-wrap: nowrap; gap: 12px; min-height: 40px;
}
.cr-page-title { font-size: 18px; font-weight: 700; color: var(--cr-text); line-height: 1.25; }
.cr-page-subtitle { font-size: 12px; color: var(--cr-text-dim); margin-top: 2px; }
.cr-card-title {
    display: flex; align-items: center; gap: 6px; flex-wrap: nowrap;
    font-size: 12px; font-weight: 600; color: var(--cr-text); margin-bottom: 8px;
}
/* One card level only: cards inside a page card are flat panels */
.cr-section { border-radius: 12px !important; box-shadow: 0 1px 3px var(--cr-shadow) !important; }
.cr-section .q-card, .cr-section .cr-card-inner { box-shadow: none !important; }
.cr-content .q-card { box-shadow: 0 1px 2px var(--cr-shadow) !important; border: 1px solid var(--cr-border); }
.cr-section { box-shadow: 0 1px 2px var(--cr-shadow) !important; }
.cr-card-inner { border-radius: 10px !important; }
/* Buttons: sentence case everywhere */
.q-btn { text-transform: none !important; letter-spacing: normal !important; }
/* Toolbars: filters left, actions right, aligned on one baseline */
.cr-toolbar { width: 100%; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.cr-toolbar .q-select, .cr-toolbar .q-input { min-width: 170px; }
/* Tables */
.cr-content .q-table thead th {
    font-size: 11px !important; font-weight: 600 !important; color: var(--cr-text-dim) !important;
    background: var(--cr-surface-light);
}
.cr-content .q-table tbody td { font-size: 12px !important; }
.cr-content .q-table--dense .q-table th, .cr-content .q-table--dense .q-table td { padding: 6px 12px !important; }
/* Status pills */
.cr-status {
    display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 11px; font-weight: 600;
    text-transform: capitalize; background: rgba(148,163,184,0.16); color: var(--cr-text-dim);
}
.cr-status-completed, .cr-status-passed, .cr-status-success { background: rgba(22,163,74,0.12); color: var(--cr-success); }
.cr-status-failed, .cr-status-error { background: rgba(220,38,38,0.12); color: var(--cr-error); }
.cr-status-running, .cr-status-in-progress, .cr-status-started { background: rgba(79,70,229,0.12); color: var(--cr-primary); }
.cr-status-paused, .cr-status-waiting, .cr-status-retrying, .cr-status-awaiting-review { background: rgba(217,119,6,0.12); color: var(--cr-warning); }
/* Responsive card grids */
.cr-grid-3 { display: grid; width: 100%; gap: 10px; grid-template-columns: repeat(3, minmax(0, 1fr)); }
@media (max-width: 1100px) { .cr-grid-3 { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 760px) { .cr-grid-3, .cr-grid-2 { grid-template-columns: minmax(0, 1fr); } }
.cr-content .q-table tbody td.text-left { white-space: normal; word-break: break-word; }
.cr-grid-2 { display: grid; width: 100%; gap: 10px; grid-template-columns: repeat(auto-fit, minmax(380px, 1fr)); }
.cr-grid-2 > *, .cr-grid-3 > *, .cr-stat-grid > * { min-width: 0; }
.cr-content .q-table__middle { overflow-x: auto; }
/* Upload slots: light header, compact body, no size/percent noise */
.cr-upload-card { min-height: 0 !important; padding: 10px !important; border-width: 1px !important; }
.cr-upload-card .q-uploader { width: 100%; box-shadow: none; border-radius: 8px; max-height: none; }
.cr-upload-card .q-uploader__header { border-bottom: 1px dashed var(--cr-border); }
.cr-upload-card .q-uploader__subtitle { display: none; }
.cr-upload-card .q-uploader__title { font-size: 12px; font-weight: 600; }
.cr-upload-card .q-uploader__list { min-height: 40px; padding: 6px 8px; }
/* --- Run page (Live Run): run console header, step timeline, tabbed detail --- */
:root { --cr-mono: "JetBrains Mono", "SFMono-Regular", Consolas, monospace; }
@keyframes cr-flow { from { background-position: 0 0; } to { background-position: -200% 0; } }
@keyframes cr-ring { 0% { box-shadow: 0 0 0 0 rgba(11,95,255,0.45); } 100% { box-shadow: 0 0 0 8px rgba(11,95,255,0); } }
@keyframes cr-blink { 50% { opacity: 0.25; } }
.cr-run-banner {
    position: relative; overflow: hidden; padding: 16px 18px 14px; border-radius: 12px;
    background: var(--cr-surface) !important; border: 1px solid var(--cr-border) !important;
}
.cr-run-banner::before { content: ""; position: absolute; left: 0; right: 0; top: 0; height: 3px; background: var(--cr-border); }
.cr-run-banner.is-running::before {
    background: linear-gradient(90deg, var(--cr-primary), #22D3EE, var(--cr-primary));
    background-size: 200% 100%; animation: cr-flow 2.4s linear infinite;
}
.cr-run-banner.is-review::before { background: var(--cr-warning); }
.cr-run-banner.is-completed::before { background: var(--cr-success); }
.cr-run-banner.is-failed::before { background: var(--cr-error); }
.cr-run-banner .q-linear-progress { display: none !important; }
.cr-state {
    display: inline-flex; align-items: center; gap: 7px; padding: 5px 10px; border-radius: 999px;
    font: 700 10px/1 var(--cr-mono); letter-spacing: 0.1em; border: 1px solid;
}
.cr-state i { width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
.cr-state.is-running { color: var(--cr-primary); background: var(--cr-primary-soft); border-color: rgba(11,95,255,0.35); }
.cr-state.is-running i { animation: cr-blink 1s steps(2) infinite; }
.cr-state.is-review { color: var(--cr-warning); background: rgba(220,104,3,0.1); border-color: rgba(220,104,3,0.35); }
.cr-state.is-completed { color: var(--cr-success); background: rgba(3,152,85,0.1); border-color: rgba(3,152,85,0.35); }
.cr-state.is-failed { color: var(--cr-error); background: rgba(217,45,32,0.1); border-color: rgba(217,45,32,0.35); }
.cr-state.is-idle { color: var(--cr-text-dim); background: var(--cr-surface-light); border-color: var(--cr-border); }
.cr-run-chip {
    font: 600 11px/1 var(--cr-mono); color: var(--cr-text); padding: 5px 8px; border-radius: 6px;
    background: var(--cr-surface-light); border: 1px solid var(--cr-border); white-space: nowrap;
}
.cr-run-uc { font: 500 11px/1.2 var(--cr-mono); color: var(--cr-text-dim); white-space: nowrap; overflow: hidden;
             text-overflow: ellipsis; min-width: 0; }
.cr-run-chip:empty, .cr-run-uc:empty { display: none; }
.cr-run-title { font-size: 15px; font-weight: 600; line-height: 20px; color: var(--cr-text); letter-spacing: -0.01em; }
.cr-run-sub { font-size: 12px; line-height: 16px; color: var(--cr-text-dim); }
.cr-seg-bar { display: grid; grid-template-columns: repeat(10, minmax(0, 1fr)); gap: 4px; margin-top: 14px; }
.cr-seg { height: 6px; border-radius: 3px; background: var(--cr-border); transition: background 0.3s; }
.cr-seg.is-passed, .cr-seg.is-completed { background: var(--cr-success); }
.cr-seg.is-failed { background: var(--cr-error); }
.cr-seg.is-in-progress, .cr-seg.is-retrying {
    background: linear-gradient(90deg, var(--cr-primary), #8DB6FF, var(--cr-primary));
    background-size: 200% 100%; animation: cr-flow 1.2s linear infinite;
}
.cr-seg.is-skipped { background: repeating-linear-gradient(45deg, var(--cr-border) 0 4px, transparent 4px 8px); }
.cr-kpis {
    display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); margin-top: 12px;
    border: 1px solid var(--cr-border); border-radius: 10px; overflow: hidden; background: var(--cr-surface-light);
}
.cr-kpi { padding: 6px 12px; border-right: 1px solid var(--cr-border); min-width: 0; }
.cr-kpi:last-child { border-right: none; }
.cr-kpi-label { font: 600 9px/1.5 var(--cr-mono); text-transform: uppercase; letter-spacing: 0.1em; color: var(--cr-text-dim); }
.cr-kpi-value { font: 600 13px/1.5 var(--cr-mono); color: var(--cr-text); white-space: nowrap; overflow: hidden;
                text-overflow: ellipsis; }
@media (max-width: 700px) {
    .cr-kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .cr-kpi:nth-child(2) { border-right: none; }
    .cr-kpi:nth-child(-n+2) { border-bottom: 1px solid var(--cr-border); }
}
.cr-run-layout { display: grid; grid-template-columns: 350px minmax(0, 1fr); gap: 12px; width: 100%;
                 align-items: start; }
@media (max-width: 900px) { .cr-run-layout { grid-template-columns: minmax(0, 1fr); } }
.cr-run-steps {
    background: var(--cr-surface); border: 1px solid var(--cr-border); border-radius: 12px;
    padding: 8px 6px; display: flex; flex-direction: column; gap: 2px;
}
.cr-run-steps-head { padding: 4px 8px 10px; margin-bottom: 4px; border-bottom: 1px solid var(--cr-border); }
.cr-run-steps-title { font: 700 10px/1 var(--cr-mono); text-transform: uppercase; letter-spacing: 0.12em;
                      color: var(--cr-text-dim); }
.cr-run-steps-count { font: 600 11px/1 var(--cr-mono); color: var(--cr-text); }
.cr-step-row {
    position: relative; display: flex; align-items: center; gap: 9px; padding: 5px 8px; border-radius: 8px;
    cursor: pointer; border: 1px solid transparent; transition: background 0.15s, border-color 0.15s;
}
.cr-step-row::before {
    content: ""; position: absolute; left: 17px; top: 25px; bottom: -7px; width: 2px; background: var(--cr-border);
}
.cr-step-row:last-of-type::before { display: none; }
.cr-step-row.is-passed::before { background: var(--cr-success); }
.cr-step-row:hover { background: var(--cr-surface-hover); }
.cr-step-row.is-selected { background: var(--cr-primary-soft); border-color: rgba(11,95,255,0.35); }
.cr-step-row.is-skipped { opacity: 0.55; }
.cr-step-node {
    position: relative; z-index: 1; width: 20px; height: 20px; flex-shrink: 0; border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    background: var(--cr-surface); border: 1.5px solid var(--cr-border); color: var(--cr-text-dim);
}
.cr-step-node-num { font: 700 9px/1 var(--cr-mono); color: var(--cr-text-dim); }
.cr-step-row.is-passed .cr-step-node { background: var(--cr-success); border-color: var(--cr-success); color: #fff; }
.cr-step-row.is-failed .cr-step-node { background: var(--cr-error); border-color: var(--cr-error); color: #fff; }
.cr-step-row.is-in-progress .cr-step-node, .cr-step-row.is-retrying .cr-step-node {
    background: var(--cr-primary-soft); border-color: var(--cr-primary); color: var(--cr-primary);
    animation: cr-ring 1.6s ease-out infinite;
}
.cr-step-row-name { font-size: 12.5px; line-height: 16px; font-weight: 500; color: var(--cr-text);
                    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cr-step-agent { font: 400 10px/13px var(--cr-mono); color: var(--cr-text-dim); white-space: nowrap; overflow: hidden;
                 text-overflow: ellipsis; }
.cr-step-chip {
    font: 700 9px/1 var(--cr-mono); letter-spacing: 0.06em; text-transform: uppercase; padding: 3px 6px;
    border-radius: 999px; color: var(--cr-text-dim); background: var(--cr-surface-light); border: 1px solid var(--cr-border);
}
.cr-step-row.is-passed .cr-step-chip { color: var(--cr-success); background: rgba(3,152,85,0.08); border-color: rgba(3,152,85,0.3); }
.cr-step-row.is-failed .cr-step-chip { color: var(--cr-error); background: rgba(217,45,32,0.08); border-color: rgba(217,45,32,0.3); }
.cr-step-row.is-in-progress .cr-step-chip { color: var(--cr-primary); background: var(--cr-primary-soft); border-color: rgba(11,95,255,0.35); }
.cr-step-row.is-retrying .cr-step-chip { color: var(--cr-warning); background: rgba(220,104,3,0.08); border-color: rgba(220,104,3,0.3); }
.cr-step-duration { font: 500 10px/13px var(--cr-mono); color: var(--cr-text-dim); margin-top: 3px; }
.cr-step-row-name, .cr-step-agent { max-width: 100%; }
.cr-step-right { flex-shrink: 0; }
.cr-file-label { font-size: 12px; font-weight: 600; line-height: 16px; color: var(--cr-text); }
.cr-file-path { font: 400 10px/14px var(--cr-mono); color: var(--cr-text-dim); word-break: break-all; }
.cr-run-banner .q-btn { font-size: 11px; }
.cr-run-detail {
    background: var(--cr-surface); border: 1px solid var(--cr-border); border-radius: 12px;
    padding: 16px 18px; min-height: 460px;
}
.cr-run-detail-head { padding-bottom: 12px; }
.cr-eyebrow { font: 700 10px/1.4 var(--cr-mono); letter-spacing: 0.12em; color: var(--cr-primary); }
.cr-detail-title { font-size: 15px; line-height: 20px; font-weight: 600; color: var(--cr-text); letter-spacing: -0.01em; }
.cr-detail-meta { font: 400 11px/1.5 var(--cr-mono); color: var(--cr-text-dim); }
.cr-spec { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; width: 100%; }
@media (max-width: 700px) { .cr-spec { grid-template-columns: minmax(0, 1fr); } }
.cr-spec-cell { border: 1px solid var(--cr-border); border-radius: 8px; padding: 6px 10px; background: var(--cr-surface-light); min-width: 0; }
.cr-spec-key { font: 700 9px/1.5 var(--cr-mono); text-transform: uppercase; letter-spacing: 0.1em; color: var(--cr-text-dim); }
.cr-spec-value { font-size: 12px; line-height: 16px; color: var(--cr-text); word-break: break-word; }
.cr-console { width: 100%; border-radius: 10px; overflow: hidden; border: 1px solid #1F2937; background: #0B0F17; }
.cr-console-bar {
    display: flex; align-items: center; gap: 10px; padding: 7px 12px; background: #111827;
    border-bottom: 1px solid #1F2937; font: 500 10px/1.4 var(--cr-mono); color: #9CA3AF;
}
.cr-console-bar .dots i { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 5px; }
.cr-console-bar .dots i:nth-child(1) { background: #F87171; }
.cr-console-bar .dots i:nth-child(2) { background: #FBBF24; }
.cr-console-bar .dots i:nth-child(3) { background: #34D399; }
.cr-console-bar .title { flex: 1; }
.cr-console-live { display: inline-flex; align-items: center; gap: 6px; color: #34D399; font-weight: 700; letter-spacing: 0.1em; }
.cr-console-live i { width: 7px; height: 7px; border-radius: 50%; background: #34D399; animation: cr-blink 1s steps(2) infinite; }
.cr-console-body { padding: 8px 0; max-height: 520px; overflow: auto; font: 11px/1.6 var(--cr-mono); color: #D1D5DB;
                   display: flex; flex-direction: column-reverse; }
.cr-console.is-compact .cr-console-body { max-height: 280px; }
.cr-console .ln { display: grid; grid-template-columns: 58px 42px 130px minmax(0, 1fr); gap: 9px; padding: 0 12px; }
.cr-console .ln:hover { background: rgba(255,255,255,0.04); }
.cr-console .ts { color: #6B7280; }
.cr-console .src { color: #A5B4FC; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cr-console .msg { white-space: pre-wrap; word-break: break-word; }
.cr-console .lv { font-weight: 700; }
.cr-console .lv-info { color: #60A5FA; }
.cr-console .lv-flow { color: #34D399; }
.cr-console .lv-warning { color: #FBBF24; }
.cr-console .lv-error { color: #F87171; }
.cr-console .lv-debug { color: #9CA3AF; }
@media (max-width: 700px) {
    .cr-console .ln { grid-template-columns: 56px minmax(0, 1fr); }
    .cr-console .ln .lv, .cr-console .ln .src { display: none; }
}
.cr-tabs { border-bottom: 1px solid var(--cr-border); }
.cr-tabs .q-tab { min-height: 32px; padding: 0 10px; color: var(--cr-text-dim); }
.cr-tabs .q-tab__label { font-size: 12px; font-weight: 500; }
.cr-tabs .q-tab__icon { font-size: 14px; }
.cr-tab-panels, .cr-tab-panels .q-tab-panel { background: transparent !important; padding: 12px 0 0 !important; }
.cr-kv { display: grid; grid-template-columns: 120px minmax(0, 1fr); gap: 8px 12px; width: 100%; }
.cr-kv-key { color: var(--cr-text-dim); font-size: 12px; }
.cr-kv-value { font-size: 13px; color: var(--cr-text); }
.cr-review-panel {
    border: 1px solid var(--cr-warning); background: rgba(220,104,3,0.05); border-radius: 10px;
    padding: 12px 14px; margin-top: 8px;
}
.cr-file-row { width: 100%; gap: 10px; padding: 8px 4px; border-bottom: 1px solid var(--cr-border); }
.cr-file-row:last-child { border-bottom: none; }
.cr-file-name { font-family: "JetBrains Mono", Consolas, monospace; font-size: 12px; color: var(--cr-text);
                word-break: break-all; }
.cr-test-summary { font-size: 13px; color: var(--cr-text-dim); margin-bottom: 4px; }
.cr-test-summary b { color: var(--cr-text); }
.cr-test-row { border: 1px solid var(--cr-border); border-radius: 10px !important; margin-bottom: 6px; }
.cr-log {
    background: #0F131C; color: #D0D5DD; border-radius: 10px; padding: 12px; margin: 0; width: 100%;
    font: 12px/1.5 "JetBrains Mono", Consolas, monospace; max-height: 520px; overflow: auto; white-space: pre-wrap;
}
.cr-empty { padding: 40px 12px; gap: 4px !important; text-align: center; }
.cr-status-pending, .cr-status-skipped { background: rgba(102,112,133,0.12); color: var(--cr-text-dim); }
/* --- New Run wizard --- */
.cr-wizard { width: 100%; background: transparent !important; box-shadow: none !important; }
.cr-wizard .q-stepper__header { border-bottom: 1px solid var(--cr-border); }
.cr-wizard .q-stepper__tab { padding: 12px 16px; }
.cr-wizard .q-stepper__title { font-size: 13px; font-weight: 600; }
.cr-wizard .q-stepper__caption { font-size: 11px; }
.cr-wizard .q-stepper__step-inner { padding: 16px 4px 4px !important; }
.cr-wizard .q-stepper__nav { padding: 12px 4px 4px !important; }
.cr-summary { display: grid; grid-template-columns: 150px minmax(0, 1fr); gap: 8px 12px; width: 100%; }
/* Settings tabs + sub-tabs share one look */
.cr-settings-tab { min-height: 30px !important; font-size: 12px !important; padding: 2px 12px !important; }
</style>
"""


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"


async def _load_config(db):
    """Load pipeline config from YAML file + DB settings overlay."""
    global _config_manager
    from core.config import ConfigManager
    mgr = ConfigManager(db_accessor=None)
    config = await mgr.get_config()

    # Overlay DB-saved settings (highest priority)
    try:
        rows = await db.execute("SELECT key, value FROM settings", ())
        db_settings = {r["key"]: r["value"] for r in rows}
        updates = {}
        if db_settings.get("api_key"):
            updates["openrouter_api_key"] = db_settings["api_key"]
        if db_settings.get("default_model"):
            updates["default_model"] = db_settings["default_model"]
        if db_settings.get("test_retry_limit"):
            updates["test_retry_limit"] = int(db_settings["test_retry_limit"])
        if db_settings.get("infra_retry_limit"):
            updates["infra_retry_limit"] = int(db_settings["infra_retry_limit"])
        if db_settings.get("backoff_multiplier"):
            updates["infra_retry_backoff"] = float(db_settings["backoff_multiplier"])
        if db_settings.get("ciq_processor_jar"):
            updates["ciq_processor_jar"] = db_settings["ciq_processor_jar"]
        if db_settings.get("cli_automation_jar"):
            updates["cli_automation_jar"] = db_settings["cli_automation_jar"]
        if db_settings.get("execution_report_jar"):
            updates["execution_report_jar"] = db_settings["execution_report_jar"]
        if db_settings.get("mop_template"):
            updates["mop_template"] = db_settings["mop_template"]
        if db_settings.get("exec_summary_template"):
            updates["exec_summary_template"] = db_settings["exec_summary_template"]
        if db_settings.get("exec_failure_template"):
            updates["exec_failure_template"] = db_settings["exec_failure_template"]
        if db_settings.get("step_models"):
            import json
            try:
                sm = json.loads(db_settings["step_models"])
                if sm:
                    updates["step_models"] = {int(k): v for k, v in sm.items()}
            except Exception:
                pass
        if updates:
            config = await mgr.update_config(updates)
    except Exception:
        pass

    # Also set API key in environment for liteLLM
    if config.openrouter_api_key:
        os.environ["OPENROUTER_API_KEY"] = config.openrouter_api_key

    _config_manager = mgr
    return config


async def _ensure_initialized():
    global _db, _runner, _artifact_store
    if _db is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _db = await init_database(str(DATA_DIR / "pipeline.db"))
        _artifact_store = ArtifactStore(str(DATA_DIR / "runs"))
        config = await _load_config(_db)
        _runner = PipelineRunner(
            config=config,
            db=_db,
            artifact_store=_artifact_store,
        )


def create_app() -> None:

    @ui.page("/")
    async def main_page():
        await _ensure_initialized()

        ui.add_head_html(CUSTOM_CSS)
        ui.add_head_html('<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">')
        ui.add_head_html('<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">')
        # Hide disconnect overlay and auto-reconnect
        ui.add_head_html("""<style>
.nicegui-reconnecting { display: none !important; }
.nicegui-disconnected { opacity: 0.3; pointer-events: none; }
</style>""")
        ui.add_head_html("""<style>
body { font-family: "Inter", sans-serif !important; font-size: 13px !important;
       font-feature-settings: "tnum" 1; color: var(--cr-text); }
.q-field--dense .q-field__control { min-height: 32px !important; }
.q-field--dense .q-field__label { font-size: 12px !important; }
.q-field--dense .q-field__native, .q-field--dense input { font-size: 13px !important; }
.q-btn { border-radius: 8px !important; font-weight: 500; }
.q-btn--dense { min-height: 30px !important; font-size: 12px !important; }
.q-card { padding: 8px !important; border-radius: 10px !important; }
.q-card__section { padding: 6px 10px !important; }
.text-h5 { font-size: 20px !important; line-height: 1.3 !important; }
.text-h6 { font-size: 16px !important; line-height: 1.3 !important; }
.text-subtitle1 { font-size: 14px !important; }
.text-subtitle2 { font-size: 13px !important; }
.text-body1 { font-size: 13px !important; }
.text-body2 { font-size: 12px !important; }
.text-caption { font-size: 11px !important; }
.q-field--outlined .q-field__control { border-radius: 8px !important; }
.q-field--outlined .q-field__control:before { border-color: var(--cr-border) !important; }
.q-linear-progress { border-radius: 99px; }
.q-expansion-item__content { padding: 4px 8px !important; }
.q-slider { margin: 2px 0 !important; }
.q-badge { font-size: 10px !important; padding: 1px 6px !important; }
.gap-5 { gap: 10px !important; }
.gap-4 { gap: 8px !important; }
.gap-3 { gap: 6px !important; }
.gap-2 { gap: 4px !important; }
.p-4 { padding: 10px !important; }
.p-5 { padding: 12px !important; }
.p-3 { padding: 8px !important; }
.py-2 { padding-top: 4px !important; padding-bottom: 4px !important; }
.mb-2 { margin-bottom: 4px !important; }
.mt-2 { margin-top: 4px !important; }
</style>""")

        dark = ui.dark_mode(False)
        is_dark = {"value": False}
        # Theme A: Quasar brand colours follow the design tokens
        ui.colors(primary="#0B5FFF", secondary="#475467", accent="#0B5FFF", dark="#161B26",
                  positive="#039855", negative="#D92D20", info="#1570EF", warning="#DC6803")

        # --- Header ---
        with ui.header(fixed=True).classes("cr-header items-center justify-between px-4").style("min-height: 52px; height: 52px"):
            with ui.row().classes("items-center gap-3 no-wrap"):
                with ui.element("div").classes("cr-logo"):
                    ui.icon("hub", size="18px")
                ui.label("NetConfig").classes("font-bold").style("font-size: 15px")
                ui.element("div").style("width:1px;height:20px;background:var(--cr-border);margin:0 4px")
                with ui.row().classes("cr-breadcrumb items-center gap-1 no-wrap"):
                    ui.label("Workspace")
                    ui.icon("chevron_right", size="16px")
                    breadcrumb = ui.label("Dashboard").classes("font-semibold").style("color: var(--cr-text)")
            with ui.row().classes("items-center gap-2"):
                ui.label("v0.1.0").classes("text-caption cr-text-dim")
                theme_btn = ui.button(icon="light_mode") \
                    .props("flat round size=sm color=grey-7") \
                    .tooltip("Toggle theme")

                def toggle_theme():
                    is_dark["value"] = not is_dark["value"]
                    dark.set_value(is_dark["value"])
                    theme_btn.props(f'icon={"dark_mode" if is_dark["value"] else "light_mode"}')

                theme_btn.on_click(toggle_theme)

        progress = None

        # --- Menu items definition (grouped like a product sidebar) ---
        menu_groups = [
            ("Workspace", [
                ("dashboard", "Dashboard", "space_dashboard"),
                ("new_run", "New Run", "add_circle_outline"),
                ("progress", "Live Run", "play_circle_outline"),
            ]),
            ("Results", [
                ("outputs", "Output Documents", "folder_open"),
                ("history", "Run History", "history"),
            ]),
            ("Admin", [
                ("usage", "LLM Usage", "insights"),
                ("cleanup", "Artifact Cleanup", "cleaning_services"),
                ("settings", "Settings", "settings"),
            ]),
        ]
        section_labels = {key: (group, label) for group, items in menu_groups for key, label, _ in items}

        # Content containers dict — filled below
        content_panels: dict = {}
        sidebar_items: dict = {}
        active_section = {"value": "dashboard"}
        on_navigate_hooks: dict[str, list] = {}  # section -> [async callbacks]

        def navigate(section: str):
            prev = active_section["value"]
            active_section["value"] = section
            # Toggle visibility
            for key, panel in content_panels.items():
                panel.set_visibility(key == section)
            # Update sidebar active state
            if prev in sidebar_items:
                sidebar_items[prev].classes(remove="active")
            if section in sidebar_items:
                sidebar_items[section].classes(add="active")
            breadcrumb.text = section_labels.get(section, ("", section))[1]
            # Fire refresh hooks for the target section
            for hook in on_navigate_hooks.get(section, []):
                try:
                    import asyncio
                    async def _safe_hook(fn=hook):
                        try:
                            await fn()
                        except RuntimeError:
                            pass
                    asyncio.ensure_future(_safe_hook())
                except Exception:
                    pass

        async def on_start_pipeline(doc_set, review_milestones=True):
            try:
                # PipelineRunner.start() takes a dict, not a UCDocumentSet
                doc_dict = doc_set.model_dump() if hasattr(doc_set, 'model_dump') else doc_set
                doc_dict["document_set_id"] = str(doc_dict.get("id", doc_dict.get("document_set_id", "")))
                run_id = await _runner.start(doc_dict, review_milestones=review_milestones)
                if progress:
                    from uuid import UUID
                    await progress.start_tracking(UUID(run_id))
                navigate("progress")
                uc_name = doc_dict.get("uc_name", "UC")
                ui.notify(f"Pipeline started for {uc_name}", type="positive")
            except Exception as exc:
                navigate("progress")
                ui.notify(f"Pipeline start failed: {exc}", type="negative")

        # --- Sidebar ---
        with ui.column().classes("cr-sidebar"):
            for group, items in menu_groups:
                ui.label(group).classes("cr-nav-group")
                for key, label, icon in items:
                    item = ui.element("div").classes(
                        f"cr-sidebar-item {'active' if key == 'dashboard' else ''}"
                    )
                    with item:
                        ui.icon(icon)
                        ui.label(label)
                    item.on("click", lambda _, k=key: navigate(k))
                    sidebar_items[key] = item

            # Spacer + footer
            ui.element("div").style("flex:1")
            ui.element("div").classes("cr-sidebar-divider")
            with ui.column().classes("px-5 py-3 gap-0"):
                ui.label("NetConfig v0.1.0").classes("text-[11px] cr-stat-title")

        # --- Content area ---
        with ui.column().classes("cr-content"):

            # Dashboard
            with ui.column().classes("cr-page") as panel:
                content_panels["dashboard"] = panel

                page_header("Dashboard", "Upload telecom UC documents, generate YAML configs via "
                                         "LLM agents, validate and test automatically")

                # Summary stat cards
                with ui.element("div").classes("cr-stat-grid"):
                    runs_label = stat_card("Total Runs", "0", "rocket_launch", "#0B5FFF")
                    success_label = stat_card("Success Rate", "\u2014", "check_circle", "#16A34A")
                    failed_label = stat_card("Failed", "0", "cancel", "#DC2626")
                    duration_label = stat_card("Avg Duration", "\u2014", "timer", "#0891B2")
                    tokens_label = stat_card("LLM Tokens", "0", "token", "#8B5CF6")
                    cost_label = stat_card("Est. Cost", "$0", "attach_money", "#D97706")

                # Charts row
                with ui.element("div").classes("cr-grid-2"):
                    # Status pie chart
                    with ui.card().classes("cr-card-inner p-3"):
                        card_title("Run Status", "donut_large")
                        status_chart = ui.echart({
                            "tooltip": {"trigger": "item"},
                            "series": [{"type": "pie", "radius": ["40%", "70%"],
                                        "label": {"color": "#98A2B3", "fontSize": 11},
                                        "data": []}],
                        }).classes("w-full").style("height: 180px")

                    # UC breakdown bar chart
                    with ui.card().classes("cr-card-inner p-3"):
                        card_title("Runs by Use Case", "bar_chart")
                        uc_chart = ui.echart({
                            "tooltip": {"trigger": "axis"},
                            "legend": {"bottom": 0, "textStyle": {"color": "#98A2B3", "fontSize": 11}},
                            "grid": {"left": 36, "right": 16, "top": 12, "bottom": 52},
                            "xAxis": {"type": "category", "data": [],
                                      "axisLabel": {"color": "#98A2B3", "fontSize": 10, "interval": 0}},
                            "yAxis": {"type": "value", "minInterval": 1,
                                      "axisLabel": {"color": "#98A2B3"}},
                            "series": [
                                {"type": "bar", "name": "Completed", "stack": "total",
                                 "itemStyle": {"color": "#12B76A"}, "data": []},
                                {"type": "bar", "name": "Failed", "stack": "total",
                                 "itemStyle": {"color": "#F04438"}, "data": []},
                            ],
                        }).classes("w-full").style("height: 180px")

                # Per-UC table + LLM usage
                with ui.element("div").classes("cr-grid-2"):
                    with ui.card().classes("cr-card-inner p-3"):
                        card_title("Use Case Summary", "inventory_2")
                        uc_table = ui.table(
                            columns=[
                                {"name": "uc", "label": "Use Case", "field": "uc", "align": "left", "sortable": True},
                                {"name": "runs", "label": "Runs", "field": "runs", "sortable": True},
                                {"name": "pass", "label": "Pass", "field": "pass", "sortable": True},
                                {"name": "fail", "label": "Fail", "field": "fail", "sortable": True},
                                {"name": "rate", "label": "Success %", "field": "rate", "sortable": True},
                                {"name": "avg_dur", "label": "Avg Time", "field": "avg_dur", "sortable": True},
                            ],
                            rows=[], row_key="uc",
                        ).props("dense flat bordered").classes("w-full")

                    with ui.card().classes("cr-card-inner p-3"):
                        card_title("LLM Usage (current session)", "analytics")
                        llm_table = ui.table(
                            columns=[
                                {"name": "step", "label": "Step", "field": "step", "align": "left", "sortable": True},
                                {"name": "calls", "label": "Calls", "field": "calls", "sortable": True},
                                {"name": "tokens", "label": "Tokens", "field": "tokens", "sortable": True},
                                {"name": "cost", "label": "Cost", "field": "cost", "sortable": True},
                            ],
                            rows=[], row_key="step",
                        ).props("dense flat bordered").classes("w-full")

                # Recent runs
                with ui.card().classes("cr-card-inner w-full p-3"):
                    card_title("Recent Runs", "history")
                    recent_table = ui.table(
                        columns=[
                            {"name": "uc", "label": "Use Case", "field": "uc", "align": "left"},
                            {"name": "status", "label": "Status", "field": "status", "align": "center"},
                            {"name": "started", "label": "Started", "field": "started", "align": "left"},
                            {"name": "duration", "label": "Duration", "field": "duration"},
                            {"name": "error", "label": "Error", "field": "error", "align": "left"},
                        ],
                        rows=[], row_key="id",
                    ).props("dense flat bordered").classes("w-full")
                    status_cells(recent_table)

                _STEP_LABELS = {
                    "requirements_gatherer": "Requirements",
                    "ciq_validation_json_gen": "CIQ Gen",
                    "ciq_validation_json_qa": "CIQ QA",
                    "network_config_gen": "Config Gen",
                    "network_simulator_setup": "Simulator",
                    "network_config_mop_qa": "MOP QA",
                    "network_config_exec_qa": "Exec QA",
                    "report_generator": "Report",
                }

                async def refresh_dashboard():
                    try:
                        # --- DB data ---
                        runs = await _db.execute(
                            "SELECT r.id, r.status, r.started_at, r.duration_seconds, "
                            "r.error_summary, d.uc_name "
                            "FROM pipeline_runs r "
                            "LEFT JOIN uc_document_sets d ON r.document_set_id = d.id "
                            "ORDER BY r.started_at DESC", ()
                        )
                        total = len(runs)
                        completed = sum(1 for r in runs if 'completed' in str(r.get('status') or '').lower())
                        failed = sum(1 for r in runs if 'failed' in str(r.get('status') or '').lower())
                        cancelled = sum(1 for r in runs if 'cancelled' in str(r.get('status') or '').lower())
                        running = total - completed - failed - cancelled
                        durations = [r['duration_seconds'] for r in runs
                                     if r.get('duration_seconds') and 'completed' in str(r.get('status') or '').lower()]

                        # Summary cards
                        runs_label.text = str(total)
                        success_label.text = f"{completed * 100 // total}%" if total > 0 else "\u2014"
                        failed_label.text = str(failed)
                        duration_label.text = f"{sum(durations) / len(durations):.0f}s" if durations else "\u2014"

                        # LLM stats
                        from shared.llm import get_usage_stats
                        llm_raw = get_usage_stats()
                        total_tokens = sum(e.get("total_tokens", 0) for e in llm_raw)
                        total_cost = sum(e.get("cost_usd", 0) for e in llm_raw)
                        tokens_label.text = f"{total_tokens:,}" if total_tokens else "0"
                        cost_label.text = f"${total_cost:.2f}" if total_cost else "$0"

                        # Status pie chart
                        pie_data = []
                        if completed: pie_data.append({"value": completed, "name": "Completed", "itemStyle": {"color": "#12B76A"}})
                        if failed: pie_data.append({"value": failed, "name": "Failed", "itemStyle": {"color": "#F04438"}})
                        if cancelled: pie_data.append({"value": cancelled, "name": "Cancelled", "itemStyle": {"color": "#98A2B3"}})
                        if running: pie_data.append({"value": running, "name": "Running", "itemStyle": {"color": "#0B5FFF"}})
                        status_chart.options["series"][0]["data"] = pie_data or [{"value": 0, "name": "No runs"}]
                        status_chart.update()

                        # Per-UC aggregation
                        uc_stats: dict[str, dict] = {}
                        for r in runs:
                            uc = r.get("uc_name") or "Unknown"
                            s = uc_stats.setdefault(uc, {"runs": 0, "pass": 0, "fail": 0, "durations": []})
                            s["runs"] += 1
                            status = str(r.get("status") or "").lower()
                            if "completed" in status:
                                s["pass"] += 1
                                if r.get("duration_seconds"):
                                    s["durations"].append(r["duration_seconds"])
                            elif "failed" in status:
                                s["fail"] += 1

                        # UC bar chart
                        uc_names = list(uc_stats.keys())[:10]
                        uc_chart.options["xAxis"]["data"] = [n.split("_")[0] if "_" in n else n for n in uc_names]
                        uc_chart.options["series"][0]["data"] = [uc_stats[n]["pass"] for n in uc_names]
                        uc_chart.options["series"][1]["data"] = [uc_stats[n]["fail"] for n in uc_names]
                        uc_chart.update()

                        # UC table
                        uc_rows = []
                        for uc, s in sorted(uc_stats.items(), key=lambda x: x[1]["runs"], reverse=True):
                            avg_d = sum(s["durations"]) / len(s["durations"]) if s["durations"] else 0
                            rate = f"{s['pass'] * 100 // s['runs']}%" if s["runs"] > 0 else "\u2014"
                            uc_rows.append({
                                "uc": uc, "runs": s["runs"], "pass": s["pass"],
                                "fail": s["fail"], "rate": rate,
                                "avg_dur": f"{avg_d:.0f}s" if avg_d else "\u2014",
                            })
                        uc_table.rows = uc_rows

                        # LLM per-step table
                        by_step: dict[str, dict] = {}
                        for e in llm_raw:
                            step = e.get("step", "unknown")
                            sd = by_step.setdefault(step, {"calls": 0, "tokens": 0, "cost": 0.0})
                            sd["calls"] += 1
                            sd["tokens"] += e.get("total_tokens", 0)
                            sd["cost"] += e.get("cost_usd", 0)
                        llm_rows = []
                        for step, sd in sorted(by_step.items(), key=lambda x: x[1]["cost"], reverse=True):
                            llm_rows.append({
                                "step": _STEP_LABELS.get(step, step.replace("_", " ").title()),
                                "calls": sd["calls"],
                                "tokens": f"{sd['tokens']:,}",
                                "cost": f"${sd['cost']:.4f}",
                            })
                        llm_table.rows = llm_rows

                        # Recent runs table (last 5)
                        recent_rows = []
                        for r in runs[:5]:
                            status = str(r.get("status") or "").replace("PipelineStatus.", "")
                            started = str(r.get("started_at") or "")[:19]
                            dur = r.get("duration_seconds")
                            err = (r.get("error_summary") or "")[:60]
                            recent_rows.append({
                                "id": r.get("id", ""),
                                "uc": r.get("uc_name") or "\u2014",
                                "status": status,
                                "started": started,
                                "duration": f"{dur:.1f}s" if dur else "\u2014",
                                "error": err or "\u2014",
                            })
                        recent_table.rows = recent_rows

                    except Exception:
                        pass

                on_navigate_hooks.setdefault("dashboard", []).append(refresh_dashboard)
                ui.timer(0.5, refresh_dashboard, once=True)

            # New Pipeline Run
            with ui.column().classes("cr-page") as panel:
                content_panels["new_run"] = panel
                panel.set_visibility(False)
                page_header("New Run", "Set up a pipeline run in three steps: activity, documents, notes & review")
                with ui.card().classes("cr-section w-full p-4"):
                    upload_section = UploadSection(on_start_pipeline=on_start_pipeline, db=_db)
                    on_navigate_hooks.setdefault("new_run", []).append(upload_section._load_vendors)

            # Pipeline Progress
            with ui.column().classes("cr-page") as panel:
                content_panels["progress"] = panel
                panel.set_visibility(False)
                page_header("Live Run", "Follow each step, review milestones, inspect artifacts, test cases and logs")
                with ui.column().classes("w-full gap-0"):
                    async def _progress_view_details(run_id):
                        navigate("history")
                        # Trigger details for this run in history section
                        if hasattr(_refs, 'history') and _refs.history:
                            await _refs.history._on_details(type('E', (), {'args': {'id': run_id}})())

                    async def _progress_view_outputs(run_id):
                        navigate("outputs")
                        if hasattr(_refs, 'output_viewer') and _refs.output_viewer:
                            from uuid import UUID as _UUID
                            await _refs.output_viewer.refresh_for_run(_UUID(run_id))

                    progress = ProgressSection(
                        runner=_runner,
                        on_view_details=_progress_view_details,
                        on_view_outputs=_progress_view_outputs,
                    )

            # Forward refs container
            class _refs:
                history = None
                output_viewer = None

            # Output Documents
            with ui.column().classes("cr-page") as panel:
                content_panels["outputs"] = panel
                panel.set_visibility(False)
                page_header("Output Documents", "Browse and download the artifacts each run produced")
                with ui.card().classes("cr-section w-full p-4"):
                    output_viewer = OutputViewerSection(
                        artifact_store=_artifact_store, runner=_runner,
                    )
                    _refs.output_viewer = output_viewer
                    on_navigate_hooks.setdefault("outputs", []).append(output_viewer._load_runs)

            # Run History
            with ui.column().classes("cr-page") as panel:
                content_panels["history"] = panel
                panel.set_visibility(False)
                page_header("Run History", "All pipeline runs with their status, errors and outputs")
                with ui.card().classes("cr-section w-full p-4"):
                    async def on_rerun(run):
                        navigate("progress")
                        if run and progress:
                            from uuid import UUID as _UUID
                            run_id = run.id if hasattr(run, 'id') else run
                            if isinstance(run_id, str):
                                run_id = _UUID(run_id)
                            await progress.start_tracking(run_id)

                    async def on_view_outputs(run_id):
                        navigate("outputs")
                        if output_viewer:
                            from uuid import UUID as _UUID
                            await output_viewer.refresh_for_run(_UUID(run_id))

                    async def on_open_run(run_id):
                        navigate("progress")
                        if progress:
                            await progress.view_run(str(run_id))

                    history_section = HistorySection(
                        runner=_runner,
                        on_rerun=on_rerun,
                        on_view_outputs=on_view_outputs,
                        on_open_run=on_open_run,
                    )
                    _refs.history = history_section
                    on_navigate_hooks.setdefault("history", []).append(history_section._refresh)

            # Artifact Cleanup
            with ui.column().classes("cr-page") as panel:
                content_panels["cleanup"] = panel
                panel.set_visibility(False)
                page_header("Artifact Cleanup", "Free disk space by deleting the artifacts of old runs")
                with ui.card().classes("cr-section w-full p-4"):
                    cleanup_section = CleanupSection(runner=_runner, artifact_store=_artifact_store)
                    on_navigate_hooks.setdefault("cleanup", []).append(cleanup_section._refresh)

            # LLM Usage
            with ui.column().classes("cr-page") as panel:
                content_panels["usage"] = panel
                panel.set_visibility(False)
                page_header("LLM Usage", "Token consumption and cost per use case, pipeline step and model")
                with ui.card().classes("cr-section w-full p-4"):
                    usage_section = UsageSection()
                    on_navigate_hooks.setdefault("usage", []).append(usage_section._refresh)

            # Settings (tabbed: Pipeline Config | Vendor Profiles)
            with ui.column().classes("cr-page") as panel:
                content_panels["settings"] = panel
                panel.set_visibility(False)

                page_header("Settings", "LLM, retry, external modules and vendor profiles")
                settings_tab_panels = {}
                active_settings_tab = {"value": "config"}

                def switch_settings_tab(tab_key: str):
                    prev = active_settings_tab["value"]
                    active_settings_tab["value"] = tab_key
                    for k, p in settings_tab_panels.items():
                        p.set_visibility(k == tab_key)
                    if prev in settings_tab_btns:
                        settings_tab_btns[prev].classes(remove="active")
                    if tab_key in settings_tab_btns:
                        settings_tab_btns[tab_key].classes(add="active")

                settings_tab_btns = {}
                with ui.row().classes("w-full gap-2 mb-2"):
                    for key, label, icon in [
                        ("config", "Pipeline Config", "tune"),
                        ("vendors", "Vendor Profiles", "business"),
                    ]:
                        btn = ui.button(label, icon=icon,
                                        on_click=lambda _, k=key: switch_settings_tab(k)) \
                            .props("flat no-caps size=sm") \
                            .classes(f"cr-settings-tab {'active' if key == 'config' else ''}")
                        settings_tab_btns[key] = btn

                # Tab: Pipeline Config
                with ui.column().classes("w-full") as config_panel:
                    settings_tab_panels["config"] = config_panel
                    with ui.card().classes("cr-section w-full p-4"):
                        settings_section = SettingsSection(config_manager=_config_manager, db=_db)

                # Tab: Vendor Profiles
                with ui.column().classes("w-full") as vendor_panel:
                    settings_tab_panels["vendors"] = vendor_panel
                    vendor_panel.set_visibility(False)
                    with ui.card().classes("cr-section w-full p-4"):
                        vendor_section = VendorSection(db=_db)

            # Load saved settings
            await settings_section.load_saved_settings()


def run() -> None:
    create_app()

    from shared.state import stop_simulator
    app.on_shutdown(stop_simulator)

    ui.run(
        host="0.0.0.0",
        port=8000,
        title="NetConfig Agentic",
        dark=False,
        reload=False,
        favicon="🔧",
        reconnect_timeout=60,
    )


if __name__ in {"__main__", "__mp_main__"}:
    try:
        run()
    except KeyboardInterrupt:
        pass
