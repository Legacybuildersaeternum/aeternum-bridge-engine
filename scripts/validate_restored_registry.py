#!/usr/bin/env python3
"""One-time registry restore validator for diaspora_registry.json."""

import json
from pathlib import Path

REGISTRY_PATH = Path(__file__).resolve().parents[1] / "data" / "diaspora_registry.json"


def _load_registry() -> dict:
    with REGISTRY_PATH.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, list):
        return {"users": data, "connection_requests": []}
    if not isinstance(data, dict):
        return {"users": [], "connection_requests": []}
    return {
        "users": data.get("users") if isinstance(data.get("users"), list) else [],
        "connection_requests": data.get("connection_requests") if isinstance(data.get("connection_requests"), list) else [],
    }


def main() -> None:
    store = _load_registry()
    users = [u for u in store["users"] if isinstance(u, dict)]

    family_ids = {
        str(u.get("family_id") or "").strip()
        for u in users
        if str(u.get("family_id") or "").strip()
    }

    family_groups = {}
    linked_pairs = 0
    for user in users:
        family_name = str(user.get("family_name") or "Unknown").strip() or "Unknown"
        family_groups[family_name] = family_groups.get(family_name, 0) + 1

        linked_ids = user.get("linked_to_user_ids")
        if isinstance(linked_ids, list):
            linked_pairs += len([x for x in linked_ids if str(x).strip()])

    names = [str(u.get("full_name") or "").strip().lower() for u in users]
    has_joshua = any("joshua" in name for name in names)
    has_jasihr = any("jasihr" in name for name in names)
    has_mekada = any("mekada" in name for name in names)

    print("restored total users:", len(users))
    print("restored total families:", len(family_ids))
    print("restored family groups:", len(family_groups))
    print("family groups detail:", dict(sorted(family_groups.items())))
    print("has Joshua:", has_joshua)
    print("has Jasihr:", has_jasihr)
    print("has Mekada:", has_mekada)
    print("linked relationships exist:", linked_pairs > 0)
    print("total linked relationship refs:", linked_pairs)
    print("connection requests:", len(store["connection_requests"]))


if __name__ == "__main__":
    main()
