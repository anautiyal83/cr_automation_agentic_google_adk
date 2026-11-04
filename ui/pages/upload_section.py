"""Document upload and validation UI — polished card-based layout."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from nicegui import events, ui

from ui.access import action, record, requires

from models.domain import UCDocumentSet

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
UPLOAD_DIR = PROJECT_ROOT / "data" / "uploads"
UC_OPTIONS_FILE = PROJECT_ROOT / "data" / "uc_options.json"

DEFAULT_NODE_TYPES = ["OTHER"]


def _load_uc_options() -> dict:
    if UC_OPTIONS_FILE.exists():
        try:
            return json.loads(UC_OPTIONS_FILE.read_text())
        except Exception:
            pass
    return {"node_types": list(DEFAULT_NODE_TYPES), "activity_names": []}


def _save_uc_options(node_types: list[str], activity_names: list[str]) -> None:
    UC_OPTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    UC_OPTIONS_FILE.write_text(json.dumps(
        {"node_types": node_types, "activity_names": activity_names},
        indent=2,
    ))

DOC_TYPES = [
    {"key": "ciq", "label": "CIQ Excel", "icon": "table_chart", "accept": ".xlsx,.xls",
     "desc": "Customer Installation Questionnaire", "required": True},
    {"key": "mop", "label": "MOP Document", "icon": "description", "accept": ".docx,.pdf,.txt",
     "desc": "Method of Procedure", "required": True},
    {"key": "sample_io_xml", "label": "Sample I/O", "icon": "data_object",
     "accept": ".xml,.txt,.doc,.docx,.pdf,.json,.yaml,.yml,.csv",
     "desc": "Sample input/output request & response files", "required": False, "multiple": True},
    {"key": "constraints", "label": "Constraints", "icon": "rule", "accept": ".txt,.yaml,.yml,.json,.csv",
     "desc": "Validation rules & business rules", "required": False},
    {"key": "cmd_outputs", "label": "Command Outputs", "icon": "terminal", "accept": ".txt",
     "desc": "CLI/API session logs", "required": True},
]


class UploadSection:
    # Shared across all instances — loaded from disk, persisted on pipeline start
    _options = _load_uc_options()
    _node_types: list[str] = _options["node_types"]
    _activity_names: list[str] = _options["activity_names"]

    def __init__(self, on_start_pipeline=None, db=None) -> None:
        self.on_start_pipeline = on_start_pipeline
        self._db = db
        self.uc_name = ""
        self.node_type = ""
        self.activity_name = ""
        self.selected_vendor_id = ""
        self.selected_node_types: list[str] = []
        self.selected_activity_name = ""
        self.paths: dict[str, str | list[str] | None] = {d["key"]: None for d in DOC_TYPES}
        self.additional_docs: list[dict[str, str]] = []
        self.statuses: dict[str, ui.label] = {}
        self.cards: dict[str, ui.element] = {}
        self.is_valid = False
        self._vendors_cache: list = []
        self._build_ui()

    def _build_ui(self) -> None:
        # Three-step wizard: 1 activity → 2 documents → 3 notes & review → start
        with ui.stepper().props("flat animated header-nav active-color=primary done-color=positive") \
                .classes("cr-wizard") as self.stepper:
            with ui.step("activity", title="Activity", icon="hub").props('caption="Vendor, node types, activity"'):
                ui.label("What is this run for?").classes("text-subtitle1 font-semibold")
                ui.label("The vendor profile fills node and activity options; the UC name is derived "
                         "from your selection.").classes("text-caption cr-text-dim")
                with ui.row().classes("w-full gap-3 items-center no-wrap mt-2"):
                    self.vendor_select = ui.select(
                        {}, label="Vendor",
                        on_change=self._on_vendor_change,
                    ).classes("flex-1").props("outlined dense")

                    self.node_select = ui.select(
                        [], label="Node types",
                        multiple=True,
                        on_change=self._on_node_change,
                    ).classes("flex-1").props("outlined dense use-chips")

                    self.activity_select = ui.select(
                        {}, label="Activity",
                        on_change=self._on_activity_change,
                    ).classes("flex-1").props("outlined dense")

                # Fallback: manual entry when no vendor profiles exist
                with ui.row().classes("w-full gap-3 items-center no-wrap") as self._manual_row:
                    self.manual_node_input = ui.input(
                        "Node type (manual)", placeholder="e.g., PGW",
                    ).classes("flex-1").props("outlined dense")
                    self.manual_node_input.on("update:model-value", self._on_manual_node_change)
                    self.manual_activity_input = ui.input(
                        "Activity name (manual)", placeholder="e.g., IFC_Capability_Modification",
                        autocomplete=self._activity_names,
                    ).classes("flex-1").props("outlined dense")
                    self.manual_activity_input.on("update:model-value", self._on_manual_activity_change)
                self._manual_row.set_visibility(False)

                with ui.row().classes("items-center gap-2 mt-1"):
                    ui.label("Use case:").classes("text-caption cr-text-dim")
                    self.uc_name_label = ui.label("—").classes("cr-file-name font-semibold")

                with ui.row().classes("items-center gap-2 mt-1"):
                    ui.label("Phases:").classes("text-caption cr-text-dim")
                    self.phases_label = ui.label("—").classes("text-caption")
                    ui.icon("info", size="14px").classes("cr-text-dim") \
                        .tooltip("Set per activity in Settings → Vendor Profiles → Activities")

                with ui.stepper_navigation():
                    ui.button("Next: documents", on_click=self.stepper.next) \
                        .props("unelevated color=primary no-caps icon-right=arrow_forward")

            with ui.step("documents", title="Documents", icon="upload_file") \
                    .props('caption="CIQ, MOP, command outputs, …"'):
                ui.label("Upload the use-case documents").classes("text-subtitle1 font-semibold")
                ui.label("Required documents are marked; node session logs (.txt/.log) under Additional "
                         "docs give step 6 real command outputs to check against.") \
                    .classes("text-caption cr-text-dim")
                with ui.element("div").classes("cr-grid-3 mt-2"):
                    for doc in DOC_TYPES:
                        self._upload_card(doc)

                    # Additional documents — compact inline
                    with ui.card().classes("cr-upload-card"):
                        with ui.column().classes("w-full gap-1 items-center"):
                            with ui.row().classes("w-full items-center justify-between"):
                                with ui.row().classes("items-center gap-1"):
                                    ui.icon("attach_file", size="18px").classes("cr-text-accent")
                                    ui.label("Additional Docs").classes("text-caption font-semibold")
                                ui.html('<span class="cr-badge cr-badge-optional">Optional</span>')
                            ui.upload(
                                label="Drop or click",
                                multiple=True,
                                auto_upload=True,
                                on_upload=self._handle_additional_upload,
                            ).props('flat bordered color=grey-1 text-color=primary').classes("w-full")
                            self.additional_docs_list = ui.column().classes("w-full gap-0")

                with ui.stepper_navigation():
                    ui.button("Back", icon="arrow_back", on_click=self.stepper.previous) \
                        .props("flat color=primary no-caps")
                    ui.button("Next: notes & review", on_click=self.stepper.next) \
                        .props("unelevated color=primary no-caps icon-right=arrow_forward")

            with ui.step("review", title="Notes & review", icon="fact_check") \
                    .props('caption="Guidance, summary, start"'):
                with ui.element("div").classes("cr-grid-2"):
                    with ui.column().classes("gap-2"):
                        ui.label("Developer notes").classes("text-subtitle2 font-semibold")
                        ui.label(
                            "Domain guidance step 6 follows as authoritative: SSH details, LDAP strings, "
                            "how to resolve identifiers, node-specific commands, anything not in the MOP/CIQ."
                        ).classes("text-caption cr-text-dim")
                        self.developer_notes = ui.textarea(
                            placeholder="e.g.:\n"
                            "- The test subscriber is given by IMSI or MSISDN; when only the MSISDN is\n"
                            "  given, resolve the IMSI via ldapsearch ... msisdn=... | grep -i provisionedIMSI\n"
                            "- Use NIAM failover: try niamID-1, then niamID-2, then niamID-3\n"
                            "- SSH: kex_algorithms=+diffie-hellman-group14-sha1, host_key_algorithms=+ssh-rsa",
                        ).props("outlined autogrow rows=8").classes("w-full") \
                            .style("font-size: 12px; font-family: monospace")
                    with ui.column().classes("gap-2"):
                        ui.label("Summary").classes("text-subtitle2 font-semibold")
                        self.summary_box = ui.element("div").classes("cr-summary cr-card-inner p-3")

                # Validation + options + action
                self.validation_container = ui.column().classes("w-full")
                with ui.row().classes("w-full justify-between items-center mt-2"):
                    self.review_checkbox = ui.checkbox(
                        "Pause for review at milestones",
                        value=True,
                    )
                    self.validation_label = ui.label("Upload required documents to continue") \
                        .classes("text-caption cr-text-dim")

                with ui.stepper_navigation():
                    ui.button("Back", icon="arrow_back", on_click=self.stepper.previous) \
                        .props("flat color=primary no-caps")
                    self.start_btn = ui.button(
                        "Start pipeline", icon="play_arrow",
                        on_click=self._on_start,
                    ).props("unelevated color=primary no-caps")
                    action(self.start_btn, "new_run")
                    self.start_btn.disable()

        self.stepper.on_value_change(lambda e: self._refresh_summary() if e.value == "review" else None)

        # Load vendor data
        ui.timer(0.5, self._load_vendors, once=True)

    # ------------------------------------------------------------------ run phases (spec 010)

    def _run_options(self) -> dict:
        """The selected activity's phases (Settings → Vendor Profiles); the default without one."""
        from shared import run_options
        vendor = next((v for v in self._vendors_cache if v.id == self.selected_vendor_id), None)
        activity = next((a for a in (vendor.activities if vendor else [])
                         if a.name == self.selected_activity_name), None)
        return run_options.normalize(activity.run_options if activity else None)

    def _show_phases(self) -> None:
        from shared import run_options
        self.phases_label.text = run_options.summary(self._run_options())

    def _refresh_summary(self) -> None:
        """Step 3 summary: what will run, which documents are in."""
        self.summary_box.clear()

        def _files(key):
            v = self.paths.get(key)
            return [v] if isinstance(v, str) and v else list(v or [])

        with self.summary_box:
            for k, v in (("Use case", self.uc_name or "—"),
                         ("Node types", ", ".join(self.selected_node_types) or self.node_type or "—"),
                         ("Activity", self.selected_activity_name or self.activity_name or "—")):
                ui.label(k).classes("cr-kv-key")
                ui.label(v).classes("cr-kv-value")
            from shared import run_options
            ui.label("Phases").classes("cr-kv-key")
            ui.label(run_options.summary(self._run_options())).classes("cr-kv-value")
            for doc in DOC_TYPES:
                files = _files(doc["key"])
                ui.label(doc["label"]).classes("cr-kv-key")
                if files:
                    ui.html("<span class='cr-status cr-status-completed'>"
                            f"{len(files)} file{'s' if len(files) > 1 else ''}</span> "
                            + ", ".join(Path(f).name.split("_", 1)[-1] for f in files[:3]))
                elif doc["required"]:
                    ui.html("<span class='cr-status cr-status-failed'>Missing</span>")
                else:
                    ui.label("—").classes("cr-text-dim")
            ui.label("Additional docs").classes("cr-kv-key")
            ui.label(", ".join(d.get("name", "") for d in self.additional_docs) or "—").classes("cr-kv-value")

    def _upload_card(self, doc: dict) -> None:
        key = doc["key"]
        is_multi = doc.get("multiple", False)
        if is_multi:
            self.paths[key] = []

        with ui.card().classes("cr-upload-card") as card:
            self.cards[key] = card
            with ui.column().classes("w-full gap-1 items-center"):
                with ui.row().classes("w-full items-center justify-between"):
                    with ui.row().classes("items-center gap-1"):
                        ui.icon(doc["icon"], size="18px").classes("cr-text-accent")
                        ui.label(doc["label"]).classes("text-caption font-semibold")
                    badge_cls = "cr-badge cr-badge-required" if doc["required"] else "cr-badge cr-badge-optional"
                    ui.html(f'<span class="{badge_cls}">{"Required" if doc["required"] else "Optional"}</span>')

                ui.upload(
                    label="Drop or click",
                    multiple=True,
                    auto_upload=True,
                    on_upload=lambda e, k=key, m=is_multi: self._handle_upload(e, k, multi=m),
                ).props(f'accept="{doc["accept"]}" flat bordered color=grey-1 text-color=primary').classes("w-full")

                if is_multi:
                    file_list = ui.column().classes("w-full gap-0")
                    self.statuses[key] = file_list
                else:
                    status = ui.label("").classes("text-xs")
                    self.statuses[key] = status

    # ------------------------------------------------------------------
    # Vendor-driven cascading selectors
    # ------------------------------------------------------------------

    async def _load_vendors(self):
        """Load vendor profiles from DB for the cascading selectors."""
        if not self._db:
            self._manual_row.set_visibility(True)
            return
        try:
            from shared.profiles import ProfileManager
            pm = ProfileManager(self._db)
            self._vendors_cache = await pm.list_vendors()
        except Exception:
            self._vendors_cache = []

        if self._vendors_cache:
            vendor_options = {v.id: v.name for v in self._vendors_cache}
            self.vendor_select.options = vendor_options
            self.vendor_select.update()
            self._manual_row.set_visibility(False)
        else:
            # No vendors configured — show manual entry
            self._manual_row.set_visibility(True)
        self._show_phases()

    async def _on_vendor_change(self, e) -> None:
        """When vendor changes, populate node types and activities."""
        self.selected_vendor_id = self.vendor_select.value or ""
        vendor = next((v for v in self._vendors_cache if v.id == self.selected_vendor_id), None)

        if vendor:
            # Populate node types
            node_options = [nt.name for nt in vendor.node_types]
            self.node_select.options = node_options
            self.node_select.value = []
            self.node_select.update()

            # Populate activities
            activity_options = {a.name: a.display_name or a.name for a in vendor.activities}
            self.activity_select.options = activity_options
            self.activity_select.value = None
            self.activity_select.update()
        else:
            self.node_select.options = []
            self.node_select.update()
            self.activity_select.options = {}
            self.activity_select.update()

        self.selected_activity_name = self.activity_select.value or ""
        self._show_phases()
        self._update_uc_name()
        await self._validate()

    async def _on_node_change(self, e) -> None:
        """When node types change, update UC name and filter activities."""
        selected = self.node_select.value or []
        if isinstance(selected, str):
            selected = [selected]
        self.selected_node_types = selected
        self.node_type = "_".join(selected) if selected else ""

        # Filter activities by selected node types
        vendor = next((v for v in self._vendors_cache if v.id == self.selected_vendor_id), None)
        if vendor and selected:
            matching = [a for a in vendor.activities
                        if not a.node_types or any(n in a.node_types for n in selected)]
            activity_options = {a.name: a.display_name or a.name for a in matching}
            self.activity_select.options = activity_options
            self.activity_select.update()

        self._update_uc_name()
        await self._validate()

    async def _on_activity_change(self, e) -> None:
        """When activity changes, update UC name."""
        self.selected_activity_name = self.activity_select.value or ""
        self.activity_name = self.selected_activity_name.upper().replace(" ", "_")
        self._show_phases()
        self._update_uc_name()
        await self._validate()

    # Manual entry fallback (when no vendor profiles exist)
    async def _on_manual_node_change(self, e) -> None:
        val = e.args if isinstance(e.args, str) else (e.args or "")
        self.node_type = val.strip().upper().replace(" ", "_")
        self._update_uc_name()
        await self._validate()

    async def _on_manual_activity_change(self, e) -> None:
        val = e.args if isinstance(e.args, str) else (e.args or "")
        self.activity_name = val.strip().upper().replace(" ", "_")
        self._update_uc_name()
        await self._validate()

    async def _prefill_notes(self) -> None:
        """Developer notes are per use case: reuse the last run's notes until edited (UI-3)."""
        if not (self._db and self.uc_name) or (self.developer_notes.value or "").strip():
            return
        try:
            rows = await self._db.execute(
                "SELECT developer_notes FROM uc_document_sets WHERE uc_name = ? "
                "AND developer_notes IS NOT NULL AND TRIM(developer_notes) != '' "
                "ORDER BY uploaded_at DESC LIMIT 1", (self.uc_name,))
        except Exception:
            return
        if rows and not (self.developer_notes.value or "").strip():
            self.developer_notes.value = rows[0]["developer_notes"]
            ui.notify(f"Developer notes pre-filled from the last {self.uc_name} run", type="info")

    def _update_uc_name(self) -> None:
        if self.node_type and self.activity_name:
            self.uc_name = f"{self.node_type}_{self.activity_name}"
            self.uc_name_label.text = self.uc_name
            ui.timer(0.1, self._prefill_notes, once=True)
        else:
            self.uc_name = ""
            self.uc_name_label.text = "—"

    async def _remove_multi_file(self, doc_key: str, index: int) -> None:
        paths = self.paths.get(doc_key, [])
        if isinstance(paths, list) and 0 <= index < len(paths):
            paths.pop(index)
        self._render_multi_file_list(doc_key)
        if not paths:
            card = self.cards.get(doc_key)
            if card:
                card.classes(remove="uploaded")
        await self._validate()

    async def _handle_additional_upload(self, e: events.UploadEventArguments) -> None:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        uid = str(uuid.uuid4())[:8]
        dest = UPLOAD_DIR / f"{uid}_{e.file.name}"
        await e.file.save(dest)
        doc_entry = {"name": e.file.name, "path": str(dest)}
        await record("document.upload", target_type="document", target_id=e.file.name, details={"slot": "additional"})
        self.additional_docs.append(doc_entry)
        self._render_additional_docs_list()

    def _remove_additional_doc(self, index: int) -> None:
        if 0 <= index < len(self.additional_docs):
            self.additional_docs.pop(index)
        self._render_additional_docs_list()

    def _render_additional_docs_list(self) -> None:
        self.additional_docs_list.clear()
        with self.additional_docs_list:
            for i, doc in enumerate(self.additional_docs):
                with ui.row().classes("items-center gap-2 w-full py-1"):
                    ui.icon("insert_drive_file", size="16px").classes("cr-text-success")
                    ui.label(doc["name"]).classes("text-body2 cr-text-success font-medium flex-1")
                    ui.button(icon="close", on_click=lambda _, idx=i: self._remove_additional_doc(idx)) \
                        .props("flat round size=xs color=grey-6")

    @requires("new_run", "document.upload")
    async def _handle_upload(self, e: events.UploadEventArguments, doc_key: str, multi: bool = False) -> None:
        try:
            UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            uid = str(uuid.uuid4())[:8]
            fname = e.file.name
            dest = UPLOAD_DIR / f"{uid}_{fname}"
            await e.file.save(dest)

            if multi:
                if not isinstance(self.paths[doc_key], list):
                    self.paths[doc_key] = []
                self.paths[doc_key].append(str(dest))
                self._render_multi_file_list(doc_key)
            else:
                self.paths[doc_key] = str(dest)
                self.statuses[doc_key].text = fname
                self.statuses[doc_key].classes(replace="text-xs cr-text-success font-medium")

            card = self.cards.get(doc_key)
            if card:
                card.classes(add="uploaded")

            ui.notify(f"Uploaded: {fname}", type="info")
            await record("document.upload", target_type="document", target_id=fname, details={"slot": doc_key})
        except Exception as exc:
            ui.notify(f"Upload failed for {doc_key}: {exc}", type="negative")
            return

        await self._validate()

    def _render_multi_file_list(self, doc_key: str) -> None:
        container = self.statuses[doc_key]
        container.clear()
        paths = self.paths.get(doc_key, [])
        if not isinstance(paths, list):
            return
        with container:
            for i, p in enumerate(paths):
                fname = Path(p).name.split("_", 1)[-1]  # strip uuid prefix
                with ui.row().classes("items-center gap-1 w-full"):
                    ui.icon("insert_drive_file", size="14px").classes("cr-text-success")
                    ui.label(fname).classes("text-xs cr-text-success font-medium flex-1")
                    ui.button(
                        icon="close",
                        on_click=lambda _, k=doc_key, idx=i: self._remove_multi_file(k, idx),
                    ).props("flat round size=xs color=grey-6")

    async def _validate(self) -> None:
        self.validation_container.clear()

        if not self.uc_name:
            self.start_btn.disable()
            self.validation_label.text = "Select Node Type and Activity Name to continue"
            return

        def _has_value(key):
            v = self.paths[key]
            return bool(v) if not isinstance(v, list) else len(v) > 0

        required_ready = all(_has_value(d["key"]) for d in DOC_TYPES if d["required"])
        if not required_ready:
            self.start_btn.disable()
            missing = [d["label"] for d in DOC_TYPES if d["required"] and not _has_value(d["key"])]
            self.validation_label.text = f"Missing: {', '.join(missing)}"
            return

        # Document validation — try to import, skip if not available
        try:
            from models.validation import validate_documents
            try:
                result = await validate_documents(
                    uc_name=self.uc_name,
                    ciq_path=self.paths["ciq"],
                    mop_path=self.paths["mop"],
                    constraints_path=self.paths["constraints"],
                    command_outputs_path=self.paths["cmd_outputs"],
                )
            except Exception as exc:
                # Validation crashed — treat as warning, still allow pipeline start
                self.is_valid = True
                self.start_btn.enable()
                self.validation_label.text = ""
                with self.validation_container:
                    with ui.row().classes("items-center gap-2 p-3").style(
                        "background: rgba(251,191,36,0.08); border: 1px solid rgba(251,191,36,0.2); border-radius: 8px"
                    ):
                        ui.icon("warning", size="20px").classes("cr-text-warning")
                        ui.label(f"Validation skipped ({type(exc).__name__}): {exc}") \
                            .classes("text-body2 cr-text-warning")
                return

            self.is_valid = result.valid

            if result.valid:
                self.start_btn.enable()
                self.validation_label.text = ""
                with self.validation_container:
                    with ui.row().classes("items-center gap-2 p-3").style(
                        "background: rgba(74,222,128,0.08); border: 1px solid rgba(74,222,128,0.2); border-radius: 8px"
                    ):
                        ui.icon("check_circle", size="20px").classes("cr-text-success")
                        ui.label("All documents validated successfully — ready to start pipeline") \
                            .classes("text-body2 cr-text-success")
            else:
                self.start_btn.disable()
                self.validation_label.text = ""
                with self.validation_container:
                    with ui.column().classes("gap-1 p-3").style(
                        "background: rgba(248,113,113,0.08); border: 1px solid rgba(248,113,113,0.2); border-radius: 8px"
                    ):
                        for err in result.errors:
                            with ui.row().classes("items-center gap-2"):
                                ui.icon("error_outline", size="16px").classes("cr-text-error")
                                ui.label(f"{err.document}: {err.message}").classes("text-body2 cr-text-error")
        except ImportError:
            # document_validator not available — allow start without validation
            self.is_valid = True
            self.start_btn.enable()
            self.validation_label.text = ""
            with self.validation_container:
                with ui.row().classes("items-center gap-2 p-3").style(
                    "background: rgba(251,191,36,0.08); border: 1px solid rgba(251,191,36,0.2); border-radius: 8px"
                ):
                    ui.icon("warning", size="20px").classes("cr-text-warning")
                    ui.label("Document validator not available — validation skipped") \
                        .classes("text-body2 cr-text-warning")

    @requires("new_run", "run.start")
    async def _on_start(self) -> None:
        if not self.is_valid or not self.on_start_pipeline:
            return
        from shared import run_options
        if not run_options.selected(self._run_options()):
            ui.notify("The activity has no phase: tick them in Settings → Vendor Profiles", type="warning")
            return
        # Persist new node type and activity name for future dropdowns
        if self.node_type and self.node_type != "OTHER" and self.node_type not in self._node_types:
            self._node_types.insert(-1, self.node_type)  # before OTHER
        if self.activity_name and self.activity_name not in self._activity_names:
            self._activity_names.append(self.activity_name)
            self._activity_names.sort()
        _save_uc_options(self._node_types, self._activity_names)

        # Build vendor profile context (if vendor selected)
        vendor_profile = {}
        if self.selected_vendor_id and self._db:
            try:
                from shared.profiles import ProfileManager
                pm = ProfileManager(self._db)
                vendor_profile = await pm.build_profile_context(
                    self.selected_vendor_id,
                    self.selected_node_types,
                    self.selected_activity_name,
                )
            except Exception as e:
                import logging
                logging.getLogger(__name__).error("Failed to build vendor profile: %s", e)

        if vendor_profile:
            gw = vendor_profile.get("vendor", {}).get("access_gateway", {})
            n_nodes = len(vendor_profile.get("category_1_node_activity", {}).get("nodes", []))
            ui.notify(
                f"Vendor profile loaded: gateway={'enabled' if gw.get('enabled') else 'disabled'}, "
                f"{n_nodes} nodes",
                type="info",
            )
        elif self.selected_vendor_id:
            ui.notify("Warning: Vendor selected but profile context is empty", type="warning")

        xml_paths = self.paths.get("sample_io_xml", [])
        notes = self.developer_notes.value.strip() if self.developer_notes.value else None
        doc_set = UCDocumentSet(
            uc_name=self.uc_name.strip(),
            ciq_file_path=self.paths["ciq"],
            mop_file_path=self.paths["mop"],
            sample_io_xml_paths=xml_paths if isinstance(xml_paths, list) else [],
            constraints_file_path=self.paths["constraints"],
            command_outputs_path=self.paths["cmd_outputs"],
            additional_documents=self.additional_docs if self.additional_docs else None,
            validated=True,
            developer_notes=notes,
            vendor_profile=vendor_profile,
            run_options=self._run_options(),
        )
        self.start_btn.disable()
        self.start_btn.props("loading")
        await self.on_start_pipeline(doc_set, review_milestones=self.review_checkbox.value)
