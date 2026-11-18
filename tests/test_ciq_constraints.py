"""The CIQ's Constraints sheet (user 2026-11-18): read at step 1 (no data sheet), applied to the validation rules at
step 3 — Required / Type / Allowed values in code, "must differ from <column>" as the JAR's cross_check."""
from __future__ import annotations

from openpyxl import Workbook

from shared import ciq_constraints
from skills.ciq_validation_json_gen.rules_baseline import _column_rule


def _ciq(path):
    wb = Workbook()
    ws = wb.active
    ws.title = "NTP Config"
    for r in (("NodeGroup", "NTP Server 1", "NTP Server 2"), ("NG1", "10.10.1.10", "10.10.1.11")):
        ws.append(r)
    c = wb.create_sheet("Constraints")
    for r in (("Sheet", "Column", "Required", "Type", "Allowed values", "Rule"),
              ("NTP Config", "NodeGroup", "Yes", "List", "NG1, NG2", ""),
              ("NTP Config", "NTP Server 1", "Yes", "IPv4", "", "must differ from NTP Server 2"),
              ("NTP Config", "NTP Server 2", "No", "IPv4", "", "")):
        c.append(r)
    wb.save(path)
    return str(path)


def test_the_constraints_sheet_is_read_and_applied(tmp_path):
    cons = ciq_constraints.read(_ciq(tmp_path / "ciq.xlsx"))
    assert [c["column"] for c in cons] == ["NodeGroup", "NTP Server 1", "NTP Server 2"]
    assert ciq_constraints.is_sheet("Constraints") and not ciq_constraints.is_sheet("NTP Config")
    rules = {"sheets": {"NTP Config": {"columns": {"NodeGroup": {"type": "string", "required": True},
                                                   "NTP Server 1": {"type": "string", "required": True},
                                                   "NTP Server 2": {"type": "string", "required": True}}}}}
    assert ciq_constraints.apply(rules, cons) == 4
    cols = rules["sheets"]["NTP Config"]["columns"]
    assert cols["NodeGroup"] == {"type": "enum", "required": True, "values": ["NG1", "NG2"]}
    assert cols["NTP Server 1"] == {"type": "ip", "required": True, "accepts": "ipv4",
                                    "description": "must differ from NTP Server 2"}
    assert cols["NTP Server 2"] == {"type": "ip", "required": False, "accepts": "ipv4"}
    assert rules["workbook_rules"] == [{"cross_check": {
        "on": ["NodeGroup"], "columns": [["NTP Config", "NTP Server 1"], ["NTP Config", "NTP Server 2"]],
        "relation": "not_equals", "message": "NTP Server 1 and NTP Server 2 must differ ({NodeGroup})"}}]
    assert ciq_constraints.apply(rules, cons) == 0  # once


def test_the_jar_copy_keeps_only_the_constraints_cross_check(tmp_path):
    import yaml
    from skills.network_config_test.jar_executor import _sanitize_rules_for_jar
    p = tmp_path / "UC_validation-rules.yaml"
    p.write_text(yaml.safe_dump({"workbook_rules": [
        {"cross_check": {"on": ["NodeGroup"], "columns": [["S", "A"], ["S", "B"]], "relation": "not_equals"}},
        {"subset": {"from": "S.A", "message": "an LLM rule"}}],
        "sheets": {"S": {"columns": {"A": {"type": "string"}}}}}), encoding="utf-8")
    kept = yaml.safe_load(open(_sanitize_rules_for_jar(str(p)), encoding="utf-8"))["workbook_rules"]
    assert kept == [{"cross_check": {"on": ["NodeGroup"], "columns": [["S", "A"], ["S", "B"]],
                                     "relation": "not_equals"}}]


def test_a_column_of_ip_addresses_is_an_ip_column_whatever_its_name():
    """"NTP Server 1" holds 10.10.1.10: validated as text before (its name lacks "ip")."""
    assert _column_rule("NTP Server 1", ["10.10.1.10", "10.10.2.10"]) == {"type": "ip", "required": True,
                                                                          "accepts": "ipv4"}
    assert _column_rule("Peer", ["2001:db8::1"])["accepts"] == "ipv6"
    assert _column_rule("Peer", ["10.0.0.1", "2001:db8::1"])["accepts"] == "both"
    assert _column_rule("VER", ["13.0.3.7"])["type"] == "string"  # a version, by its column name
    assert _column_rule("NTP Server 1", ["10.10.1.10", "ntp.example.com"])["type"] == "string"
