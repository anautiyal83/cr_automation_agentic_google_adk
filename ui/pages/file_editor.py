"""Edit generated files at a review (spec 012).

One full-screen dialog per page, built once (a dialog created in a click handler took no typing):
files of the review in tabs, the workflow as a table of rows or as YAML, problems in plain language
with their lines, diff and history of versions, a MOP preview, drafts kept in the browser session,
download / upload. Saving, restoring and row changes need the run's own review (``editable``) and
Update on Live Run; everything else works read-only.
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable

from nicegui import app, ui

from shared import edit_checks, run_files
from shared import workflow_table as wt
from ui.access import action, record, record_document, requires

logger = logging.getLogger(__name__)

_CSS = """<style>
.cr-problem-line { background: color-mix(in srgb, var(--cr-error) 12%, transparent); }
.cr-ed-pre { margin: 0; padding: 12px 16px; font: 12.5px/1.6 'JetBrains Mono', Consolas, monospace;
  white-space: pre; overflow-x: auto; }
.cr-ed-add { background: color-mix(in srgb, var(--cr-success) 12%, transparent); }
.cr-ed-del { background: color-mix(in srgb, var(--cr-error) 12%, transparent); text-decoration: line-through; }
.cr-ed-fold { color: var(--cr-text-dim, #888); font-style: italic; padding: 2px 0; }
.cr-ed-code .cm-editor { height: 100%; }
.cr-ed-row { border-bottom: 1px solid var(--cr-border); }
.cr-ed-row:hover { background: var(--cr-surface-hover); }
.cr-ed-row.has-problem { box-shadow: inset 3px 0 0 var(--cr-error); }
.cr-ed-mono { font-family: 'JetBrains Mono', Consolas, monospace; font-size: 12px; overflow-wrap: anywhere; }
.cr-ed-grid { display: grid; grid-template-columns: 28px 96px minmax(0, 1fr) minmax(0, 190px) 64px;
  gap: 10px; align-items: start; padding: 8px 12px; width: 100%; box-sizing: border-box; }
.cr-ed-grid > * { min-width: 0; }
.cr-ed-outline { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; display: block; }
</style>"""

_SOURCES = {"generated": "Generated", "user": "Yours", "restored": "Restored"}
_ON_FAILURE = {"": "Default", "stop": "Stop the run", "continue": "Continue"}


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


class FileEditor:
    """``open(...)`` shows the files of one review of one run."""

    def __init__(self) -> None:
        ui.add_head_html(_CSS)
        self.dialog = ui.dialog().props("maximized persistent")
        with self.dialog:
            self.card = ui.card().classes("w-full h-full p-0").style(
                "display: flex; flex-direction: column; max-height: 100vh; background: var(--cr-surface)")
        # A row's form: built once as well
        self.row_dialog = ui.dialog()
        with self.row_dialog:
            self.row_card = ui.card().classes("p-0").style("width: 760px; max-width: 96vw; max-height: 92vh; "
                                                            "display: flex; flex-direction: column")
        self.ctx: dict[str, Any] = {}
        self.files: list[run_files.EditableFile] = []
        self.cur = ""
        self.buf: dict[str, dict] = {}
        self.mode = "yaml"
        self.diff_base: int | None = None
        self.undo: str | None = None
        self.notice = ""
        self.mop_url = ""
        self.mop_busy = False
        self.editor = None
        self.status_label = None

    # ------------------------------------------------------------ opening

    def open(self, *, run_id: str, run_dir: str, uc_name: str, review_step: int, editable: bool,
             requirements: dict, user_email: str = "", state: dict | None = None,
             on_saved: Callable[[], Any] | None = None, rel: str = "", line: int = 0, mode: str = "") -> None:
        self.ctx = {"run_id": run_id, "run_dir": run_dir, "uc": uc_name, "step": review_step, "editable": editable,
                    "requirements": requirements or {}, "by": user_email, "state": state or {}, "on_saved": on_saved}
        self.files = run_files.editable_files(run_dir, uc_name, review_step)
        if not self.files:
            ui.notify("This review has no files to edit", type="warning")
            return
        self.buf = {}
        for f in self.files:
            text = run_files.current(run_dir, f.rel)
            self.buf[f.rel] = {"file": f, "orig": text, "text": text, "problems": None}
        self.cur = rel if rel in self.buf else self.files[0].rel
        self.mode = mode or ("table" if self._file().kind == "workflow" else "yaml")
        self.diff_base, self.undo, self.notice, self.mop_url = None, None, "", ""
        self._check(self.cur)
        self._render()
        self.dialog.open()
        if line:
            self._jump(line)

    # ------------------------------------------------------------ helpers

    def _file(self) -> run_files.EditableFile:
        return self.buf[self.cur]["file"]

    def _text(self) -> str:
        return self.buf[self.cur]["text"]

    def _dirty(self, rel: str | None = None) -> bool:
        b = self.buf[rel or self.cur]
        return b["text"] != b["orig"]

    def _workflow_text(self) -> str:
        wf = next((b for b in self.buf.values() if b["file"].kind == "workflow"), None)
        return wf["text"] if wf else run_files.current(self.ctx["run_dir"], run_files.file_name("workflow_yaml",
                                                                                                 self.ctx["uc"]))

    def _check(self, rel: str) -> None:
        b = self.buf[rel]
        try:
            b["problems"] = edit_checks.check(b["file"].kind, b["text"], self.ctx["requirements"],
                                              workflow_text=self._workflow_text(), rel=rel)
        except Exception as e:  # noqa: BLE001 — a check must not break the editor
            logger.warning("Checking %s failed: %s", rel, e)
            b["problems"] = [edit_checks.Problem(0, f"The check could not run: {e}", str(e))]

    def _set_text(self, text: str) -> None:
        self.buf[self.cur]["text"] = text
        self._save_draft()

    # drafts (E4): the browser session's storage, per run and file
    def _draft_key(self) -> str:
        return f"{self.ctx['run_id']}|{self.cur}"

    def _drafts(self) -> dict:
        try:
            return app.storage.user.setdefault("editor_drafts", {})
        except Exception:  # noqa: BLE001 — no session storage: no drafts
            return {}

    def _save_draft(self) -> None:
        drafts = self._drafts()
        if self._dirty():
            from datetime import datetime
            drafts[self._draft_key()] = {"text": self._text(), "base": _hash(self.buf[self.cur]["orig"]),
                                        "at": datetime.now().strftime("%H:%M")}
        else:
            drafts.pop(self._draft_key(), None)

    def _draft(self) -> dict | None:
        d = self._drafts().get(self._draft_key())
        b = self.buf[self.cur]
        if d and d.get("base") == _hash(b["orig"]) and d.get("text") != b["text"]:
            return d
        return None

    def _jump(self, line: int) -> None:
        if self.mode != "yaml":
            self.mode = "yaml"
            self._render()
        if self.editor is None or not line:
            return
        ui.run_javascript(
            f"setTimeout(() => {{ const c = getElement({self.editor.id}); if (!c || !c.editor) return;"
            f" const d = c.editor.state.doc; const l = d.line(Math.max(1, Math.min({line}, d.lines)));"
            f" c.editor.dispatch({{selection: {{anchor: l.from}}, scrollIntoView: true}}); c.editor.focus(); }}, 150);")

    def _status(self) -> tuple[str, str]:
        b = self.buf[self.cur]
        problems = b["problems"] or []
        if self._dirty():
            return "Unsaved changes", "cr-text-warning"
        if problems:
            return f"{len(problems)} problem{'s' if len(problems) != 1 else ''}", "cr-text-error"
        return "No problems", "cr-text-success"

    # ------------------------------------------------------------ rendering

    def _render(self) -> None:
        self.card.clear()
        self.editor = None
        f = self._file()
        editable = self.ctx["editable"]
        with self.card:
            # header
            with ui.row().classes("w-full items-center justify-between px-4 py-2 no-wrap gap-3").style(
                    "border-bottom: 1px solid var(--cr-border); flex-shrink: 0"):
                with ui.column().classes("gap-0 min-w-0"):
                    ui.label(f"Step {self.ctx['step'] if self.ctx['step'] != 4 else '3–4'} review · "
                             f"{self.ctx['uc']}").classes("text-caption cr-text-dim")
                    with ui.row().classes("items-center gap-2 no-wrap"):
                        ui.label("Edit files" if editable else "View files").classes("text-h6 font-semibold")
                        text, cls = self._status()
                        self.status_label = ui.label(text).classes(f"text-caption font-semibold {cls}")
                with ui.row().classes("items-center gap-1 no-wrap"):
                    ui.button("Validate", icon="rule", on_click=self._validate).props("flat dense no-caps")
                    if editable:
                        action(ui.button("Save", icon="save", on_click=self._save)
                               .props("unelevated dense no-caps color=primary"), "progress")
                    ui.button("Download", icon="download", on_click=self._download).props("flat dense no-caps")
                    if editable:
                        uploader = ui.upload(auto_upload=True, max_files=1, on_upload=self._upload) \
                            .props('accept=".yaml,.yml,.py"').style("display: none")
                        action(ui.button("Upload", icon="upload", on_click=lambda: uploader.run_method("pickFiles"))
                               .props("flat dense no-caps"), "progress")
                    ui.button(icon="close", on_click=self._close).props("flat round dense").tooltip("Close")

            # banners
            with ui.column().classes("w-full gap-1 px-4 pt-2").style("flex-shrink: 0"):
                if not editable:
                    ui.label(f"Read-only — editable at the step {self.ctx['step'] if self.ctx['step'] != 4 else '3–4'} "
                             "review of this run, by its owner.").classes("text-caption cr-text-dim")
                draft = self._draft() if editable else None
                if draft:
                    with ui.row().classes("items-center gap-2 text-caption"):
                        ui.label(f"You have unsaved changes from {draft['at']}.").classes("cr-text-warning")
                        ui.button("Restore draft", on_click=lambda d=draft: self._restore_draft(d)) \
                            .props("flat dense no-caps color=primary")
                        ui.button("Discard", on_click=self._discard_draft).props("flat dense no-caps")
                replaced = run_files.replaced_user_version(self.ctx["run_dir"], self.cur)
                if replaced:
                    with ui.row().classes("items-center gap-2 text-caption"):
                        ui.label(f"A re-run replaced your version {replaced} of this file.").classes("cr-text-warning")
                        ui.button("Compare", on_click=lambda v=replaced: self._show_diff(v)) \
                            .props("flat dense no-caps color=primary")
                        if editable:
                            ui.button(f"Restore v{replaced}", on_click=lambda v=replaced: self._restore(v)) \
                                .props("flat dense no-caps")
                if self.notice:
                    with ui.row().classes("items-center gap-2 text-caption"):
                        ui.label(self.notice).classes("cr-text-accent")
                        if self.undo is not None:
                            ui.button("Undo", on_click=self._undo).props("flat dense no-caps color=primary")

            # body
            with ui.row().classes("w-full gap-0 no-wrap").style("flex: 1; min-height: 0"):
                self._render_nav()
                with ui.column().classes("gap-0 min-w-0").style("flex: 1; min-height: 0; border-left: 1px solid "
                                                               "var(--cr-border); border-right: 1px solid var(--cr-border)"):
                    self._render_modes(f)
                    with ui.element("div").classes("w-full").style("flex: 1; min-height: 0; overflow: auto"):
                        {"table": self._render_table, "yaml": self._render_yaml, "diff": self._render_diff,
                         "history": self._render_history, "mop": self._render_mop}.get(self.mode, self._render_yaml)()
                self._render_problems()

    def _render_nav(self) -> None:
        with ui.column().classes("gap-1 p-2").style("width: 250px; flex-shrink: 0; overflow-y: auto"):
            ui.label("FILES").classes("text-caption font-semibold cr-text-dim px-2")
            for f in self.files:
                b = self.buf[f.rel]
                n = len(b["problems"] or [])
                with ui.row().classes("w-full items-center justify-between no-wrap px-2 py-1 rounded cursor-pointer") \
                        .style("background: var(--cr-surface-hover)" if f.rel == self.cur else "") \
                        .on("click", lambda _, r=f.rel: self._select(r)):
                    with ui.column().classes("gap-0 min-w-0"):
                        ui.label(f.label).classes("text-body2 font-medium")
                        ui.label(f.rel).classes("text-caption cr-text-dim cr-ed-mono")
                    if self._dirty(f.rel):
                        ui.label("●").classes("cr-text-warning").tooltip("Unsaved changes")
                    elif b["problems"]:
                        ui.label(str(n)).classes("text-caption font-bold cr-text-error")
            if self._file().kind == "workflow":
                try:
                    phases = wt.table(self._text())
                except ValueError:
                    phases = []
                if phases:
                    ui.label("OUTLINE").classes("text-caption font-semibold cr-text-dim px-2 mt-3")
                    rows_with_problems = self._row_problems(phases)
                    for ph in phases:
                        ui.label(f"{ph.name} · {len(ph.rows)}").classes("text-caption font-semibold px-2 mt-1")
                        for r in ph.rows:
                            bad = (ph.key, r.number) in rows_with_problems
                            ui.label(f"{r.number} · {wt.show(r.send)}") \
                                .classes("text-caption px-3 cursor-pointer cr-ed-mono cr-ed-outline"
                                         + (" cr-text-error" if bad else "")).tooltip(wt.show(r.send)[:200]) \
                                .on("click", lambda _, rr=r: self._jump(self._row_line(rr)))

    def _render_modes(self, f: run_files.EditableFile) -> None:
        modes = ([("table", "Table")] if f.kind == "workflow" else []) + [("yaml", "YAML" if f.kind != "script" else "Code"),
                                                                       ("diff", "Diff"), ("history", "History")]
        if f.kind == "workflow":
            modes.append(("mop", "MOP preview"))
        with ui.row().classes("w-full items-center gap-1 px-3 py-1").style(
                "border-bottom: 1px solid var(--cr-border); flex-shrink: 0; background: var(--cr-surface-light)"):
            for key, label in modes:
                ui.button(label, on_click=lambda _, k=key: self._set_mode(k)) \
                    .props(f"dense no-caps {'unelevated color=primary' if self.mode == key else 'flat color=grey-8'}")

    # ---- table view (H)
    def _row_line(self, row: wt.Row) -> int:
        return edit_checks._send_line(self._text().splitlines(), row.send)

    def _row_problems(self, phases: list[wt.Phase]) -> dict[tuple[str, int], list]:
        problems = [p for p in (self.buf[self.cur]["problems"] or []) if p.line]
        starts = sorted((self._row_line(r), ph.key, r.number) for ph in phases for r in ph.rows if self._row_line(r))
        out: dict[tuple[str, int], list] = {}
        for p in problems:
            owner = None
            for line, key, number in starts:
                if line <= p.line:
                    owner = (key, number)
            if owner:
                out.setdefault(owner, []).append(p)
        return out

    def _render_table(self) -> None:
        try:
            phases = wt.table(self._text())
        except ValueError as e:
            ui.label(f"This file cannot be shown as a table ({e}). Use the YAML view.").classes("p-4 cr-text-dim")
            return
        editable = self.ctx["editable"]
        problems = self._row_problems(phases)
        with ui.column().classes("w-full gap-3 p-3"):
            for ph in phases:
                with ui.column().classes("w-full gap-0").style("border: 1px solid var(--cr-border); border-radius: 8px"):
                    with ui.row().classes("w-full items-center justify-between px-3 py-2 no-wrap").style(
                            "background: var(--cr-surface-light); border-bottom: 1px solid var(--cr-border)"):
                        with ui.row().classes("items-center gap-2"):
                            ui.label(ph.name).classes("font-semibold")
                            ui.label(f"{len(ph.rows)} step{'s' if len(ph.rows) != 1 else ''}").classes(
                                "text-caption cr-text-dim")
                            if ph.automatic:
                                with ui.row().classes("items-center gap-1 text-caption cr-text-dim"):
                                    ui.icon("lock", size="14px")
                                    ui.label(f"+ {ph.automatic} added automatically").tooltip(
                                        "Backups by naming convention, checksums and record loops are rebuilt from "
                                        "your steps when you save; they cannot be edited here.")
                        if editable:
                            action(ui.button("Add step", icon="add",
                                             on_click=lambda _, k=ph.key: self._row_form("add", k, None))
                                   .props("flat dense no-caps color=primary"), "progress")
                    with ui.element("div").classes("cr-ed-grid text-caption font-semibold cr-text-dim"):
                        for label in ("#", "Runs on", "Command", "Success check", ""):
                            ui.label(label)
                    for r in ph.rows:
                        bad = problems.get((ph.key, r.number), [])
                        with ui.element("div").classes("cr-ed-grid cr-ed-row" + (" has-problem" if bad else "")):
                            ui.label(str(r.number)).classes("text-caption cr-text-dim")
                            ui.label(r.node).classes("text-body2").style("overflow-wrap: anywhere")
                            with ui.column().classes("gap-0"):
                                ui.label(wt.show(r.send)).classes("cr-ed-mono")
                                if r.description:
                                    ui.label(r.description).classes("text-caption cr-text-dim")
                                extra = [x for x in (("Captures " + ", ".join(r.captured_names)) if r.captured_names else "",
                                                     ("Only when " + wt.show(r.when)) if r.when else "") if x]
                                if extra:
                                    ui.label(" · ".join(extra)).classes("text-caption cr-text-accent")                                         .style("overflow-wrap: anywhere")
                                for p in bad[:2]:
                                    ui.label("● " + p.plain).classes("text-caption cr-text-error")
                            ui.label(r.success or r.condition or "—").classes("cr-ed-mono")
                            with ui.row().classes("gap-0 no-wrap"):
                                if editable:
                                    action(ui.button(icon="edit", on_click=lambda _, k=ph.key, rr=r: self._row_form("edit", k, rr))
                                           .props("flat round dense size=sm").tooltip("Edit this step"), "progress")
                                    action(ui.button(icon="delete", on_click=lambda _, k=ph.key, rr=r: self._delete_row(k, rr))
                                           .props("flat round dense size=sm color=negative").tooltip("Delete this step"),
                                           "progress")
                                else:
                                    ui.button(icon="code", on_click=lambda _, rr=r: self._jump(self._row_line(rr))) \
                                        .props("flat round dense size=sm").tooltip("Show in YAML")
                    if not ph.rows:
                        ui.label("No steps in this phase.").classes("text-caption cr-text-dim px-3 py-2")

    def _row_form(self, mode: str, phase: str, row: wt.Row | None) -> None:
        phases = wt.table(self._text())
        by_key = {p.key: p for p in phases}
        nodes = wt.nodes(self._text()) or ["LOCAL"]
        sheets = ((self.ctx["requirements"].get("doc_extracted") or {}).get("ciq_sheets") or {})
        columns = sorted({c for s in sheets.values() for c in (s.get("columns") or [])})
        row = row or wt.Row(phase=phase, number=len(by_key[phase].rows) + 1, node=nodes[0])

        def positions(key: str) -> dict[int, str]:
            rows = by_key[key].rows
            opts = {r.number: f"{r.number} · before “{wt.show(r.send)[:50]}”" for r in rows}
            opts[len(rows) + 1] = f"At the end ({len(rows) + 1})"
            return opts

        self.row_card.clear()
        with self.row_card:
            with ui.column().classes("w-full gap-1 px-5 pt-4 pb-2").style("border-bottom: 1px solid var(--cr-border)"):
                ui.label("Add a step" if mode == "add" else f"Edit step {row.number} · {by_key[phase].name}") \
                    .classes("text-h6 font-semibold")
                ui.label("Steps added automatically (backups, checksums, loops) are rebuilt from your steps when "
                         "the file is saved.").classes("text-caption cr-text-dim")
            with ui.column().classes("w-full gap-3 px-5 py-3").style("overflow-y: auto; flex: 1"):
                if mode == "add":
                    with ui.row().classes("w-full gap-3 no-wrap"):
                        phase_sel = ui.select({k: p.name for k, p in by_key.items()}, value=phase, label="Phase") \
                            .props("outlined dense").classes("w-56 cr-boxed")
                        pos_sel = ui.select(positions(phase), value=len(by_key[phase].rows) + 1, label="Step number") \
                            .props("outlined dense").classes("flex-1 cr-boxed")

                        def phase_changed(e):
                            pos_sel.options = positions(e.value)
                            pos_sel.value = len(by_key[e.value].rows) + 1
                            pos_sel.update()
                        phase_sel.on_value_change(phase_changed)
                else:
                    phase_sel = pos_sel = None
                with ui.row().classes("w-full gap-3 no-wrap"):
                    node_sel = ui.select(nodes, value=row.node if row.node in nodes else nodes[0], label="Runs on") \
                        .props("outlined dense").classes("w-56 cr-boxed")
                    fail_sel = ui.select(_ON_FAILURE, value=row.on_failure if row.on_failure in _ON_FAILURE else "",
                                         label="If it fails").props("outlined dense").classes("w-56 cr-boxed")
                cmd = ui.textarea("Command", value=row.send).props("outlined autogrow").classes("w-full cr-boxed cr-ed-mono")
                if columns:
                    ui.select(columns, label="Insert a CIQ value into the command", with_input=True,
                              on_change=lambda e: (setattr(cmd, "value", (cmd.value or "")
                                                           + "${configSequences.0.records.0.data." + e.value + "}")
                                                   if e.value else None)) \
                        .props("outlined dense clearable").classes("w-full cr-boxed")
                desc = ui.input("What this step does (optional)", value=row.description).props("outlined dense") \
                    .classes("w-full cr-boxed")
                with ui.row().classes("w-full gap-3 no-wrap"):
                    success = ui.input("Success check — text the output must contain (regex)", value=row.success) \
                        .props("outlined dense").classes("flex-1 cr-boxed cr-ed-mono")
                    cond = ui.input("Or a condition on captured values", value=row.condition,
                                    placeholder="e.g. cpuIdle >= 90").props("outlined dense").classes("w-64 cr-boxed")
                caps = ui.textarea("Captures — one pattern per line, e.g. (?<eirNamespace>\\S+)",
                                   value="\n".join(row.captures)).props("outlined autogrow").classes("w-full cr-boxed cr-ed-mono")
                when = ui.input("Run only when (optional)", value=row.when,
                                placeholder="e.g. configSequences.0.records.0.data.BULK_PROVISIONING_FILE != ''") \
                    .props("outlined dense").classes("w-full cr-boxed cr-ed-mono")
            with ui.row().classes("w-full justify-end gap-2 px-5 py-3").style("border-top: 1px solid var(--cr-border)"):
                ui.button("Cancel", on_click=self.row_dialog.close).props("flat no-caps")

                async def submit():
                    new = wt.Row(phase=phase_sel.value if phase_sel else phase,
                                 number=int(pos_sel.value) if pos_sel else row.number,
                                 node=node_sel.value, send=(cmd.value or "").strip(), description=(desc.value or "").strip(),
                                 success=(success.value or "").strip(), condition=(cond.value or "").strip(),
                                 captures=[c.strip() for c in (caps.value or "").splitlines() if c.strip()],
                                 when=(when.value or "").strip(), on_failure=fail_sel.value or "")
                    if not new.send:
                        ui.notify("Enter the command of the step", type="warning")
                        return
                    await self._apply_row("add" if mode == "add" else "edit", new.phase, new.number, new)
                action(ui.button("Add step" if mode == "add" else "Save step", icon="check", on_click=submit)
                       .props("unelevated no-caps color=primary"), "progress")
        self.row_dialog.open()

    @requires("progress", "artifact.edit")
    async def _apply_row(self, act: str, phase: str, number: int, row: wt.Row | None) -> None:
        if not self.ctx.get("editable"):
            return
        before = self._text()
        try:
            after = wt.change(before, act, phase, number, row, self.ctx["requirements"])
        except ValueError as e:
            ui.notify(str(e), type="negative")
            return
        self.row_dialog.close()
        self._set_text(after)
        self.undo = before
        name = next((p.name for p in wt.table(after) if p.key == phase), phase)
        self.notice = {"add": f"Step added as {name} {number}.", "edit": f"{name} step {number} changed.",
                       "delete": f"{name} step {number} deleted."}[act] + " Not saved yet."
        self._check(self.cur)
        self._render()

    def _delete_row(self, phase: str, row: wt.Row) -> None:
        from ui.components import confirm
        confirm(f"Delete step {row.number}?", f"“{wt.show(row.send)[:120]}” is removed from the workflow. "
                "You can undo this until you close the editor.",
                lambda: self._apply_row("delete", phase, row.number, None))

    def _undo(self) -> None:
        if self.undo is None:
            return
        self._set_text(self.undo)
        self.undo, self.notice = None, "Undone. Not saved yet."
        self._check(self.cur)
        self._render()

    # ---- yaml / code view
    def _render_yaml(self) -> None:
        f = self._file()
        problems = [p for p in (self.buf[self.cur]["problems"] or []) if p.line]
        self.editor = ui.codemirror(self._text(), language="Python" if f.kind == "script" else "YAML",
                                    on_change=self._on_edit, indent="  ") \
            .classes("w-full cr-ed-code").style("height: calc(100vh - 190px)")
        self.editor.decorations = [{"kind": "line", "line": p.line, "class": "cr-problem-line"} for p in problems]
        self.editor.line_tooltips = {p.line: p.plain for p in problems}
        if not self.ctx["editable"]:
            self.editor.disable()

    def _on_edit(self, e) -> None:
        if not self.ctx.get("editable"):
            return
        self._set_text(e.value)
        if self.status_label is not None:
            text, cls = self._status()
            self.status_label.text = text
            self.status_label.classes(replace=f"text-caption font-semibold {cls}")

    # ---- diff / history / mop
    def _render_diff(self) -> None:
        rel = self.cur
        versions = run_files.versions(self.ctx["run_dir"], rel)
        options = {0: "Generated by the step"} | {v["version"]: f"v{v['version']} · {_SOURCES.get(v['source'], v['source'])}"
                                                  f" · {v['at']}" for v in versions}
        base_v = self.diff_base if self.diff_base in options else 0
        with ui.row().classes("items-center gap-2 px-4 pt-3"):
            ui.label("Compare with").classes("text-caption cr-text-dim")
            ui.select(options, value=base_v, on_change=lambda e: self._show_diff(e.value)) \
                .props("outlined dense").classes("w-72 cr-boxed")
        base = run_files.generated(self.ctx["run_dir"], rel) if not base_v else \
            run_files.read_version(self.ctx["run_dir"], rel, base_v)
        rows = run_files.diff(base, self._text())
        if not any(sign for sign, _ in rows):
            ui.label("No differences.").classes("p-4 cr-text-dim")
            return
        import html as _html
        added = sum(1 for sign, _ in rows if sign == "+")
        removed = sum(1 for sign, _ in rows if sign == "-")
        ui.label(f"{added} line(s) added · {removed} line(s) removed").classes("text-caption cr-text-dim px-4")
        # unchanged stretches fold to 3 lines of context around each change
        near = {j for i, (sign, _) in enumerate(rows) if sign for j in range(i - 3, i + 4)}
        lines, folded = [], 0
        for i, (sign, text) in enumerate(rows):
            if i not in near:
                folded += 1
                continue
            if folded:
                lines.append(f"<div class='cr-ed-fold'>… {folded} unchanged line(s)</div>")
                folded = 0
            cls = "cr-ed-add" if sign == "+" else "cr-ed-del" if sign == "-" else ""
            lines.append(f"<div class='{cls}'>{_html.escape(sign or ' ')} {_html.escape(text)}</div>")
        if folded:
            lines.append(f"<div class='cr-ed-fold'>… {folded} unchanged line(s)</div>")
        ui.html(f"<div class='cr-ed-pre'>{''.join(lines)}</div>")

    def _render_history(self) -> None:
        versions = run_files.versions(self.ctx["run_dir"], self.cur)
        if not versions:
            ui.label("Not edited yet: the file is as the step generated it. Every save will be listed here.") \
                .classes("p-4 cr-text-dim")
            return
        current = run_files.current(self.ctx["run_dir"], self.cur)
        with ui.column().classes("w-full gap-0 p-3"):
            for v in reversed(versions):
                is_current = run_files.read_version(self.ctx["run_dir"], self.cur, v["version"]) == current \
                    and v is versions[-1]
                with ui.row().classes("w-full items-center justify-between px-3 py-2 no-wrap cr-ed-row"):
                    with ui.column().classes("gap-0"):
                        ui.label(f"v{v['version']} · {_SOURCES.get(v['source'], v['source'])}"
                                 + ("  (current)" if is_current else "")).classes("font-semibold")
                        problems = v.get("problems")
                        ui.label(" · ".join(x for x in (v.get("by") or "the step", v.get("at"),
                                                        f"{problems} problem(s) at save" if problems else "")
                                            if x)).classes("text-caption cr-text-dim")
                    with ui.row().classes("gap-1"):
                        ui.button("Compare", on_click=lambda _, n=v["version"]: self._show_diff(n)) \
                            .props("flat dense no-caps color=primary")
                        if self.ctx["editable"] and not is_current:
                            action(ui.button("Restore", on_click=lambda _, n=v["version"]: self._restore(n))
                                   .props("flat dense no-caps"), "progress")

    def _render_mop(self) -> None:
        with ui.column().classes("w-full gap-2 p-3").style("height: 100%"):
            with ui.row().classes("items-center gap-2"):
                ui.button("Render MOP" if not self.mop_url else "Render again", icon="description",
                          on_click=self._render_mop_preview).props("unelevated dense no-caps color=primary") \
                    .set_enabled(not self.mop_busy)
                ui.label("The MOP the automation framework would produce from the workflow as it is now in the "
                         "editor (saved or not).").classes("text-caption cr-text-dim")
            if self.mop_busy:
                ui.spinner(size="lg")
            elif self.mop_url:
                ui.element("iframe").props(f'src="{self.mop_url}"').style(
                    "width: 100%; flex: 1; min-height: 70vh; border: 1px solid var(--cr-border); border-radius: 8px")

    async def _render_mop_preview(self) -> None:
        from agents.requirements_agent import ciq_file_path
        from skills.network_config_test.jar_executor import run_mop_test
        state = self.ctx["state"] or {}
        cfg = state.get("pipeline_config") or {}
        run_dir, uc = Path(self.ctx["run_dir"]), self.ctx["uc"]
        self.mop_busy = True
        self._render()
        tmp = Path(tempfile.mkdtemp(prefix="mop-preview-"))
        try:
            wf = tmp / f"{uc}.yaml"
            wf.write_text(self._workflow_text(), encoding="utf-8")
            rules = run_dir / f"{uc}_validation-rules.yaml"
            result = await run_mop_test(
                cfg.get("cli_automation_jar") or "lib/cli-automation-standalone.jar", str(wf),
                str(run_dir / f"{uc}_json-output.yaml"), ciq_file_path(state), str(tmp),
                cfg.get("java_home"), ciq_processor_jar=cfg.get("ciq_processor_jar") or "lib/ciq-processor-1.0.3-cli.jar",
                validation_rules_path=str(rules) if rules.exists() else "",
                html_template_path=cfg.get("mop_template") or "")
            pages = sorted((tmp / "mop_output").glob("*.html"))
            if pages:
                keep = run_dir / "edits" / "mop_preview"
                keep.mkdir(parents=True, exist_ok=True)
                target = keep / pages[0].name
                shutil.copy(pages[0], target)
                self.mop_url = app.add_media_file(local_file=str(target))
            else:
                self.mop_url = ""
                ui.notify("The MOP could not be rendered: " + (result.get("stderr") or "no output")[:300],
                          type="negative", multi_line=True)
        except Exception as e:  # noqa: BLE001
            ui.notify(f"The MOP could not be rendered: {e}", type="negative")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            self.mop_busy = False
            self._render()

    # ---- problems panel
    def _render_problems(self) -> None:
        problems = self.buf[self.cur]["problems"] or []
        with ui.column().classes("gap-2 p-3").style("width: 340px; flex-shrink: 0; overflow-y: auto"):
            ui.label(f"PROBLEMS · {len(problems)}").classes("text-caption font-semibold cr-text-dim")
            if self._dirty():
                ui.label("Changed since the last check — Validate to update.").classes("text-caption cr-text-warning")
            if not problems:
                ui.label("No problems found.").classes("text-body2 cr-text-success font-semibold")
            for p in problems:
                with ui.column().classes("w-full gap-1 p-3").style(
                        "border: 1px solid var(--cr-border); border-left: 3px solid var(--cr-error); border-radius: 8px"):
                    ui.label(p.plain).classes("text-body2 font-medium").style("overflow-wrap: anywhere")
                    if p.section:
                        ui.label(p.section).classes("text-caption cr-text-dim")
                    with ui.row().classes("items-center gap-1"):
                        if p.line:
                            ui.button(f"Line {p.line}", on_click=lambda _, n=p.line: self._jump(n)) \
                                .props("flat dense no-caps size=sm")
                        if p.fix and self.ctx["editable"]:
                            action(ui.button("Apply suggested fix", on_click=lambda _, pp=p: self._apply_fix(pp))
                                   .props("unelevated dense no-caps size=sm color=primary"), "progress")
                    with ui.expansion("Technical details").classes("w-full text-caption cr-text-dim"):
                        ui.label(p.technical).classes("cr-ed-mono").style("white-space: pre-wrap")

    # ------------------------------------------------------------ actions

    def _select(self, rel: str) -> None:
        self.cur = rel
        if self.buf[rel]["problems"] is None:
            self._check(rel)
        f = self._file()
        if self.mode == "table" and f.kind != "workflow" or self.mode == "mop" and f.kind != "workflow":
            self.mode = "yaml"
        self.diff_base, self.notice, self.undo = None, "", None
        self._render()

    def _set_mode(self, mode: str) -> None:
        self.mode = mode
        self._render()

    def _show_diff(self, version: int | None) -> None:
        self.diff_base = version or None
        self.mode = "diff"
        self._render()

    def _validate(self) -> None:
        self._check(self.cur)
        n = len(self.buf[self.cur]["problems"] or [])
        ui.notify("No problems found" if not n else f"{n} problem{'s' if n != 1 else ''} found",
                  type="positive" if not n else "warning")
        self._render()

    def _apply_fix(self, problem) -> None:
        before = self._text()
        after = edit_checks.apply_fix(before, problem)
        if after == before:
            ui.notify("This fix could not be applied to the file as it is now", type="warning")
            return
        self._set_text(after)
        self.undo, self.notice = before, "Fix applied. Not saved yet."
        self._check(self.cur)
        self._render()

    @requires("progress", "artifact.edit")
    async def _save(self) -> None:
        if not self.ctx.get("editable"):
            return
        b = self.buf[self.cur]
        text = b["text"]
        if b["file"].kind == "workflow":
            rebuilt = wt.rebuild(text, self.ctx["requirements"])
            if rebuilt != text:
                text = rebuilt
                b["text"] = text
        self._check(self.cur)
        problems = len(b["problems"] or [])
        try:
            version = run_files.save(self.ctx["run_dir"], self.cur, text, self.ctx["by"], problems=problems,
                                     expected=b["orig"])
        except run_files.ChangedSinceOpened:
            self.notice = ("This file changed since you opened it (a re-run or another tab). Your text is kept "
                           "as a draft — close and open the editor again to see the new file.")
            self._render()
            return
        b["orig"] = text
        self._drafts().pop(self._draft_key(), None)
        self.notice, self.undo = f"Saved as version {version}" + (f" with {problems} problem(s)." if problems else "."), None
        await record("artifact.edit", target_type="run", target_id=self.ctx["run_id"], run_id=self.ctx["run_id"],
                     details={"file": self.cur, "version": version, "problems": problems})
        self._render()
        if self.ctx.get("on_saved"):
            result = self.ctx["on_saved"]()
            if hasattr(result, "__await__"):
                await result

    @requires("progress", "artifact.edit")
    async def _restore(self, version: int) -> None:
        if not self.ctx.get("editable"):
            return
        new = run_files.restore(self.ctx["run_dir"], self.cur, version, self.ctx["by"])
        text = run_files.current(self.ctx["run_dir"], self.cur)
        self.buf[self.cur].update({"orig": text, "text": text})
        self._check(self.cur)
        self.notice, self.undo = f"Version {version} restored as version {new}.", None
        await record("artifact.restore", target_type="run", target_id=self.ctx["run_id"], run_id=self.ctx["run_id"],
                     details={"file": self.cur, "from_version": version, "version": new})
        self.mode = "yaml" if self._file().kind != "workflow" else "table"
        self._render()
        if self.ctx.get("on_saved"):
            result = self.ctx["on_saved"]()
            if hasattr(result, "__await__"):
                await result

    def _download(self) -> None:
        record_document("document.download", str(Path(self.ctx["run_dir"]) / self.cur))
        ui.download(self._text().encode("utf-8"), Path(self.cur).name)

    async def _upload(self, e) -> None:
        if not self.ctx.get("editable"):
            return
        try:
            text = await e.file.text()
        except Exception as ex:  # noqa: BLE001
            ui.notify(f"Could not read the file: {ex}", type="negative")
            return
        before = self._text()
        self._set_text(text)
        self.undo, self.notice = before, f"Loaded {e.file.name}. Not saved yet."
        self._check(self.cur)
        self._render()

    def _restore_draft(self, draft: dict) -> None:
        before = self._text()
        self.buf[self.cur]["text"] = draft["text"]
        self.undo, self.notice = before, "Draft restored. Not saved yet."
        self._check(self.cur)
        self._render()

    def _discard_draft(self) -> None:
        self._drafts().pop(self._draft_key(), None)
        self._render()

    def _close(self) -> None:
        if any(self._dirty(rel) for rel in self.buf):
            from ui.components import confirm
            confirm("Close the editor?", "Your unsaved changes are kept as a draft: open the editor again to "
                    "restore them.", self.dialog.close, ok_label="Close", ok_icon="close")
            return
        self.dialog.close()


def load_requirements(run_dir: str, state: dict | None) -> dict:
    """The run's requirements with its options (phases, repo route) — what step 6 used."""
    from agents.requirements_agent import requirements_store
    state = state or {}
    reqs = dict(requirements_store.get(str(state.get("run_id", ""))) or {})
    if not reqs:
        try:
            reqs = json.loads((Path(run_dir) / "requirements.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            reqs = {}
    reqs["run_options"] = (state.get("document_set") or {}).get("run_options") or reqs.get("run_options") or {}
    return reqs
