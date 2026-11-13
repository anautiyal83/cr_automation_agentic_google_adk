"""The simulated access gateway (MockWire ``niam.profiles``, MW-13): one server and port for every node reached
through the vendor's gateway (NIAM), the neId a session answers picks the node — as production logs in to all of
them at ``${NIAM_IP}:${M2MPORT}`` and only the neId differs.

One gateway YAML per gateway (``config/<gateway>.yaml``): the login (user, password, password / node-id prompts,
banner — the vendor's access gateway settings, NIAM's defaults) and the routing table, each node's neIds (from the
CIQ JSON, per node type) → that node's own YAML (``config/<instance>.yaml``, unchanged). Nodes not behind the gateway
(the repository server) stay servers of their own.
"""
from __future__ import annotations

import re

from .autofix import dump_yaml

DEFAULT_NAME = "niam"


def gateway_nodes(nodes: list) -> list:
    """The nodes a session reaches through the gateway: those with NIAM ids."""
    return [n for n in nodes if getattr(n, "ne_ids", None)]


def gateway_name(requirements: dict) -> str:
    """``<access gateway name>_gateway`` (`niam_gateway`) — a MockWire instance / file name."""
    gw = ((requirements or {}).get("category_1_node_activity") or {}).get("access_gateway") or {}
    name = re.sub(r"[^a-z0-9]+", "_", str(gw.get("name") or DEFAULT_NAME).lower()).strip("_") or DEFAULT_NAME
    return f"{name}_gateway"


def gateway_config(nodes: list, requirements: dict, node_configs: dict[str, dict]) -> str:
    """The gateway YAML: login + ``niam.profiles`` (paths relative to ``config/``)."""
    gw = ((requirements or {}).get("category_1_node_activity") or {}).get("access_gateway") or {}
    first = nodes[0]
    node_niam = (node_configs.get(first.instance) or {}).get("niam") or {}
    prompts = dict(node_niam.get("prompts") or {"passwordPrompt": "Password: ", "neIdPrompt": "neId: "})
    # the vendor's own gateway wording, when its settings give it
    for key, setting in (("passwordPrompt", "password_prompt"), ("neIdPrompt", "node_id_prompt"),
                         ("banner", "banner")):
        if gw.get(setting):
            prompts[key] = str(gw[setting])
    prompts.setdefault("banner", f"{gw.get('name') or 'NIAM'} gateway (simulated)")
    ne_ids = [i for n in nodes for i in n.ne_ids]
    cfg = {
        "config": {"prompt": "gateway> ", "hostname": str(gw.get("name") or DEFAULT_NAME).lower()},
        "auth": {"enabled": True, "users": [{"username": first.user, "password": first.password}]},
        "niam": {
            "enabled": True,
            "prompts": prompts,
            "mfa": {"enabled": False},
            "users": [{"username": first.user, "password": first.password, "neIds": ne_ids}],
            "profiles": [{
                "name": n.instance,
                "config": f"{n.instance}.yaml",
                "responses": f"../outputs/{n.instance}/responses",
                "vfsRoot": f"../vfs/{n.instance}",
                "eventsFile": f"../logs/{n.instance}.events.jsonl",
                "neIds": list(n.ne_ids),
            } for n in nodes],
        },
    }
    return dump_yaml(cfg)
