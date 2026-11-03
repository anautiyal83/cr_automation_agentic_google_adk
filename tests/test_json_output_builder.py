"""json-output template from the CIQ layout (G-14): node targets (MRF) and group targets (PGW_RDS)."""
from __future__ import annotations

from skills.ciq_validation_json_gen.json_output_builder import build_json_output, split_uc_name


def _sheets(rows):
    return {s: {"columns": list(r[0])} for s, r in rows.items()}


def test_node_targets_mrf_layout():
    rows = {
        "INDEX": [{"NODE": "MRF1", "GROUP": "GRP1", "CRGROUP": "CR1", "EMAIL": "a@x.com"},
                  {"NODE": "MRF2", "GROUP": "GRP1", "CRGROUP": "CR1", "EMAIL": "a@x.com"}],
        "ANNOUNCEMENT_FILES": [{"GROUP": "GRP1", "INPUT_FILE": "f.tar", "MRF_DESTINATION_PATH": "/p"}],
        "Node_Details": [{"NODE TYPE": "MRF", "NODE_NAME": "MRF1", "NIAM NAME": "n1"},
                         {"NODE TYPE": "MRF", "NODE_NAME": "MRF2", "NIAM NAME": "n2"}],
    }
    t = build_json_output(_sheets(rows), rows, "MRF_ANNOUNCEMENT_LOADING")
    # One file for the whole CIQ: crGroups[] at the top, nodeGroups[] inside each CR
    assert t["output_mode"] == "single" and "segregate_by" not in t
    cr = t["data"]["crGroups"]
    assert cr["_each"] == "DISTINCT INDEX.CRGROUP AS $cr" and cr["crGroup"] == "$cr"
    assert cr["email"] == "INDEX.EMAIL WHERE INDEX.CRGROUP = $cr"
    ng = cr["nodeGroups"]
    assert t["data"]["meta"] == {"nodeType": "MRF", "activity": "ANNOUNCEMENT_LOADING"}
    assert ng["_each"] == "DISTINCT INDEX.GROUP WHERE INDEX.CRGROUP = $cr AS $group"
    assert ng["email"] == "INDEX.EMAIL WHERE INDEX.GROUP = $group"
    assert ng["nodes"] == {"_each": "INDEX WHERE GROUP = $group", "node": "NODE",
                           "MRF": {"niamID-1": "Node_Details.'NIAM NAME' WHERE Node_Details.NODE_NAME = NODE"}}
    assert ng["configSequences"]["records"]["_each"] == "ANNOUNCEMENT_FILES WHERE GROUP = $group"


def test_group_targets_pgw_layout():
    rows = {
        "IP": [{"ZONE": "EAST", "Node_Name": "PGW", "NIAM_NAME1": "p1", "NIAM_NAME2": "p2"},
               {"ZONE": "EAST", "Node_Name": "RDS", "NIAM_NAME1": "r1", "NIAM_NAME2": "r2"}],
        "Index": [{"ZONE": "EAST", "CRGroup": "CR1", "Email": "a@x.com"}],
        "Profile Modification": [{"Zone": "EAST", "Test IMSI": "1"}],
    }
    ng = build_json_output(_sheets(rows), rows, "PGW_RDS_1058_IFC_CAPABILITY_MODIFICATION")["data"]["crGroups"]["nodeGroups"]
    assert ng["_each"] == "DISTINCT Index.ZONE WHERE Index.CRGroup = $cr AS $zone"
    assert ng["nodes"][0]["PGW"]["niamID-2"] == "IP.NIAM_NAME2 WHERE IP.Node_Name = PGW AND IP.ZONE = $zone"
    assert ng["configSequences"]["records"]["_each"] == "Profile Modification WHERE Zone = $zone"


def test_uc_name_split():
    assert split_uc_name("MRF_ANNOUNCEMENT_LOADING") == ("MRF", "ANNOUNCEMENT_LOADING")
    assert split_uc_name("PGW_RDS_1058_IFC_X") == ("PGW_RDS", "1058_IFC_X")


def test_ciq_json_reader_both_shapes():
    from tools.ciq_json import node_groups
    g = {"nodeGroup": "GRP1", "nodes": [{"node": "MRF1"}]}
    assert node_groups({"crGroups": [{"crGroup": "CR2", "nodeGroups": [g]}]}) == [("CR2", g)]
    assert node_groups({"nodeGroups": [dict(g, crGroup="CR1")]})[0][0] == "CR1"
