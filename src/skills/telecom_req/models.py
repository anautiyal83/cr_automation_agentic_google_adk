"""Pydantic models for telecom activity requirements."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SshConfig(BaseModel):
    host: str = ""
    port: int = 22
    user: str = "oamsys"
    auth: str = "key"
    kex_algorithms: str = ""
    connect_timeout_sec: int = 30

    class Config:
        extra = "allow"


class Escalation(BaseModel):
    method: str = ""
    password: str = ""
    prompt: str = ""
    prompt_regex: str = ""

    class Config:
        extra = "allow"


class NodeSpec(BaseModel):
    name: str
    type: str = "remote"
    role: str = ""
    ne_id: str = ""
    ssh: SshConfig = Field(default_factory=SshConfig)
    prompt_regex: str = ""
    escalation: Escalation = Field(default_factory=Escalation)

    class Config:
        extra = "allow"


class CommandSpec(BaseModel):
    cmd: str
    node: str = ""
    purpose: str = ""
    syntax: str = ""
    target_user: str = ""
    validation_criteria: str = ""
    ciq_params: list[str] = Field(default_factory=list)
    register_vars: list[str] = Field(default_factory=list, alias="register")

    class Config:
        extra = "allow"


class LdapConfig(BaseModel):
    host: str = "localhost"
    port: int = 16611
    bind_dn: str = ""
    password: str = ""

    class Config:
        extra = "allow"


class GroupingConfig(BaseModel):
    column: str = ""
    label: str = ""


class XmlMapping(BaseModel):
    ciq_column: str = ""
    xml_tag: str = ""
    xml_sample_file: str = ""
    spml_objectclass: str = ""


class Category1NodeActivity(BaseModel):
    nodes: list[NodeSpec] = Field(default_factory=list)
    niam_failover: bool = False
    niam_columns: list[str] = Field(default_factory=list)
    root_escalation: Escalation = Field(default_factory=Escalation)
    activity_type: str = "per_subscriber"
    data_sheet: str = ""

    class Config:
        extra = "allow"


class Category2Commands(BaseModel):
    commands: list[CommandSpec] = Field(default_factory=list)
    ldap: LdapConfig = Field(default_factory=LdapConfig)

    class Config:
        extra = "allow"


class Category3Orchestration(BaseModel):
    grouping: GroupingConfig = Field(default_factory=GroupingConfig)
    niam_failover: bool = False
    ssh: SshConfig = Field(default_factory=SshConfig)
    paths: dict[str, str] = Field(default_factory=dict)
    phases: list[str] = Field(default_factory=list)

    class Config:
        extra = "allow"


class XmlTemplates(BaseModel):
    description: str = ""
    ciq_to_xml_mapping: list[XmlMapping] = Field(default_factory=list)
    base_xml_structure: str = ""

    class Config:
        extra = "allow"


class GenerationHints(BaseModel):
    step_3: str = ""
    step_4: str = ""
    step_6: str = ""
    step_8: str = ""
    workflow_yaml: dict[str, Any] = Field(default_factory=dict)

    class Config:
        extra = "allow"


class ActivityRequirements(BaseModel):
    """Full requirements specification for a telecom UC activity."""
    category_1_node_activity: Category1NodeActivity = Field(default_factory=Category1NodeActivity)
    category_2_commands: Category2Commands = Field(default_factory=Category2Commands)
    category_3_orchestration: Category3Orchestration = Field(default_factory=Category3Orchestration)
    generation_hints: GenerationHints = Field(default_factory=GenerationHints)
    xml_templates: XmlTemplates = Field(default_factory=XmlTemplates)
    doc_extracted: dict[str, Any] = Field(default_factory=dict)
    developer_answers: dict[str, Any] = Field(default_factory=dict)

    class Config:
        extra = "allow"

    def to_legacy_dict(self) -> dict:
        """Convert to the flat dict shape that downstream step_nodes expect.

        This matches the exact structure that ``requirements_store[run_id]``
        used to hold — ensuring backward compatibility with ``_get_step_data``.
        """
        d = self.model_dump(mode="json")
        # Flatten generation_hints.workflow_yaml for downstream access
        wf = d.get("generation_hints", {}).pop("workflow_yaml", {})
        if wf:
            d["generation_hints"]["workflow_yaml"] = wf
        return d


class MopCommand(BaseModel):
    """A single command extracted from a MOP document."""
    command: str
    description: str = ""
    validation: str = ""
    node: str = "unknown"
    phase: str = "unknown"
    user: str = "oamsys"


class SshCredential(BaseModel):
    bind_dn: str = ""
    password: str = ""


class DocExtracted(BaseModel):
    """Data extracted deterministically from documents (no LLM)."""
    mop_commands: list[MopCommand] = Field(default_factory=list)
    mop_phases: list[str] = Field(default_factory=list)
    ssh_prompts: list[str] = Field(default_factory=list)
    ssh_credentials: list[SshCredential] = Field(default_factory=list)
    ldap_port: str = ""
    xml_files: list[dict[str, Any]] = Field(default_factory=list)
    ciq_sheets: dict[str, dict[str, Any]] = Field(default_factory=dict)

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")


class Question(BaseModel):
    """A question to ask the developer."""
    id: str
    category: str = "orchestration"
    question: str = ""
    importance: str = "recommended"
    input_type: str = "text"
    options: list[str] = Field(default_factory=list)
    default_value: str = ""
    context: str = ""
    helper_text: str = ""
