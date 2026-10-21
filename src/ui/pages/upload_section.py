"""Document upload and validation UI — polished card-based layout."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from nicegui import events, ui

from src.core.models import UCDocumentSet

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
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
    {"key": "sample_io_xml", "label": "Sample I/O XMLs", "icon": "data_object", "accept": ".xml",
     "desc": "Sample input/output XML request & response files", "required": True, "multiple": True},
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

    def __init__(self, on_start_pipeline=None) -> None:
        self.on_start_pipeline = on_start_pipeline
        self.uc_name = ""
        self.node_type = ""
        self.activity_name = ""
        self.paths: dict[str, str | list[str] | None] = {d["key"]: None for d in DOC_TYPES}
        self.additional_docs: list[dict[str, str]] = []  # [{"name": filename, "path": saved_path}]
        self.statuses: dict[str, ui.label] = {}
        self.cards: dict[str, ui.element] = {}
        self.is_valid = False
        self._build_ui()

    def _build_ui(self) -> None:
        with ui.column().classes("w-full gap-2"):
            # UC Name selection — compact single row
            with ui.card().classes("w-full p-3 cr-card-inner").style(
                "background: rgba(108,99,255,0.06)"
            ):
                with ui.row().classes("w-full gap-2 items-end"):
                    ui.icon("badge", size="20px").classes("cr-text-accent")
                    self.node_select = ui.select(
                        self._node_types, label="Node Type",
                        with_input=True, new_value_mode="add-unique",
                        on_change=self._on_node_change,
                    ).classes("flex-1").props("outlined dense")
                    self.custom_node_input = ui.input(
                        "Custom Node Type",
                        placeholder="e.g., CSCF",
                    ).classes("flex-1").props("outlined dense")
                    self.custom_node_input.set_visibility(False)
                    self.custom_node_input.on("update:model-value", self._on_custom_node_change)
                    self.activity_input = ui.input(
                        "Activity Name",
                        placeholder="e.g., Provisioning",
                        autocomplete=self._activity_names,
                    ).classes("flex-1").props("outlined dense")
                    self.activity_input.on("update:model-value", self._on_activity_change)
                    self.uc_name_label = ui.label("") \
                        .classes("text-body2 font-mono font-semibold cr-text-accent")

            # Document upload grid — all 5 + additional in 2 rows
            with ui.row().classes("w-full gap-2 flex-wrap"):
                for doc in DOC_TYPES[:3]:
                    self._upload_card(doc)

            with ui.row().classes("w-full gap-2 flex-wrap"):
                for doc in DOC_TYPES[3:]:
                    self._upload_card(doc)

                # Additional documents — compact inline
                with ui.card().classes("cr-upload-card flex-1 min-w-48"):
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
                        ).props('flat bordered color=deep-purple-6').classes("w-full")
                        self.additional_docs_list = ui.column().classes("w-full gap-0")

            # Developer Notes — domain knowledge input
            with ui.expansion("Developer Notes & Guidance", icon="engineering") \
                    .classes("w-full").props("dense header-class='text-caption font-semibold'"):
                ui.label(
                    "Provide domain-specific guidance for YAML generation: SSH details, "
                    "LDAP connection strings, environment paths, retry strategies, "
                    "node-specific commands, or any knowledge not in the MOP/CIQ."
                ).classes("text-caption cr-text-dim")
                self.developer_notes = ui.textarea(
                    placeholder="e.g.:\n"
                    "- Use NIAM failover: try niamID-1, then niamID-2, then niamID-3\n"
                    "- Root escalation via 'su - root' with password 'siemens'\n"
                    "- LDAP on RDS: ldapsearch -x -h localhost -p 16611 -D 'cn=pgwAdminUser' -w 8123Admi\n"
                    "- Backup config before changes using 'configtool export'\n"
                    "- SSH: kex_algorithms=+diffie-hellman-group14-sha1, host_key_algorithms=+ssh-rsa",
                ).props("outlined autogrow rows=3").classes("w-full").style("font-size: 11px; font-family: monospace")

            # Validation + options + action — compact single row
            self.validation_container = ui.column().classes("w-full")

            with ui.row().classes("w-full justify-between items-center"):
                self.review_checkbox = ui.checkbox(
                    "Review at milestones",
                    value=True,
                ).classes("text-caption")
                self.validation_label = ui.label("Upload required documents to continue") \
                    .classes("text-caption cr-text-dim")
                self.start_btn = ui.button(
                    "Start Pipeline", icon="play_arrow",
                    on_click=self._on_start,
                ).props("unelevated color=deep-purple-6 size=md rounded") \
                    .classes("px-6")
                self.start_btn.disable()

    def _upload_card(self, doc: dict) -> None:
        key = doc["key"]
        is_multi = doc.get("multiple", False)
        if is_multi:
            self.paths[key] = []

        with ui.card().classes("cr-upload-card flex-1 min-w-48") as card:
            self.cards[key] = card
            with ui.column().classes("w-full gap-1 items-center"):
                with ui.row().classes("w-full items-center justify-between"):
                    with ui.row().classes("items-center gap-1"):
                        ui.icon(doc["icon"], size="18px").classes("cr-text-accent")
                        ui.label(doc["label"]).classes("text-caption font-semibold")
                    badge_cls = "cr-badge cr-badge-required" if doc["required"] else "cr-badge cr-badge-optional"
                    ui.html(f'<span class="{badge_cls}">{"Req" if doc["required"] else "Opt"}</span>')

                ui.upload(
                    label="Drop or click",
                    multiple=True,
                    auto_upload=True,
                    on_upload=lambda e, k=key, m=is_multi: self._handle_upload(e, k, multi=m),
                ).props(f'accept="{doc["accept"]}" flat bordered color=deep-purple-6').classes("w-full")

                if is_multi:
                    file_list = ui.column().classes("w-full gap-0")
                    self.statuses[key] = file_list
                else:
                    status = ui.label("").classes("text-xs")
                    self.statuses[key] = status

    async def _on_node_change(self, e) -> None:
        raw = (self.node_select.value or "").strip().upper()
        if raw == "OTHER":
            self.custom_node_input.set_visibility(True)
            self.custom_node_input.value = ""
            self.node_type = ""
        else:
            self.custom_node_input.set_visibility(False)
            self.node_type = raw
        self._update_uc_name()
        await self._validate()

    async def _on_custom_node_change(self, e) -> None:
        val = e.args if isinstance(e.args, str) else (e.args or "")
        self.node_type = val.strip().upper().replace(" ", "_")
        if self.node_type and self.node_type not in self._node_types:
            self._node_types.insert(-1, self.node_type)  # before OTHER
            self.node_select.options = list(self._node_types)
            self.node_select.update()
        self._update_uc_name()
        await self._validate()

    async def _on_activity_change(self, e) -> None:
        val = e.args if isinstance(e.args, str) else (e.args or "")
        self.activity_name = val.strip().upper().replace(" ", "_")
        self._update_uc_name()
        await self._validate()

    def _update_uc_name(self) -> None:
        if self.node_type and self.activity_name:
            self.uc_name = f"{self.node_type}_{self.activity_name}"
            self.uc_name_label.text = f"UC Name: {self.uc_name}"
        else:
            self.uc_name = ""
            self.uc_name_label.text = ""

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
            from src.core.document_validator import validate_documents
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

    async def _on_start(self) -> None:
        if not self.is_valid or not self.on_start_pipeline:
            return
        # Persist new node type and activity name for future dropdowns
        if self.node_type and self.node_type != "OTHER" and self.node_type not in self._node_types:
            self._node_types.insert(-1, self.node_type)  # before OTHER
        if self.activity_name and self.activity_name not in self._activity_names:
            self._activity_names.append(self.activity_name)
            self._activity_names.sort()
        _save_uc_options(self._node_types, self._activity_names)

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
        )
        self.start_btn.disable()
        self.start_btn.props("loading")
        await self.on_start_pipeline(doc_set, review_milestones=self.review_checkbox.value)
