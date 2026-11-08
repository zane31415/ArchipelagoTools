"""Map archipelago.gg tracker API data onto a regenerated MultiWorld."""
from __future__ import annotations

import json
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field

API = "https://archipelago.gg/api"
GOAL_STATUS = 30
START_INVENTORY_LOCATION = -2  # how MultiServer reports start_inventory items
HINT_STATUS = {0: "unspecified", 10: "no priority", 20: "avoid", 30: "priority", 40: "found"}


def fetch(path: str) -> dict:
    req = urllib.request.Request(f"{API}/{path}", headers={"User-Agent": "sphere-dependency-tracker"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


@dataclass
class Hint:
    receiver: int
    finder: int
    location: object  # Location or None
    item_name: str
    found: bool
    status: str


@dataclass
class Snapshot:
    received_from: dict = field(default_factory=lambda: defaultdict(set))  # slot -> {Location}
    extra_items: dict = field(default_factory=lambda: defaultdict(list))  # slot -> [Item] not tied to a location
    checked: dict = field(default_factory=lambda: defaultdict(set))  # slot -> {Location}
    goals: set = field(default_factory=set)
    activity: dict = field(default_factory=dict)  # slot -> last activity string
    received_count: dict = field(default_factory=dict)  # slot -> number of items received
    hints: list = field(default_factory=list)
    unmatched: list = field(default_factory=list)


def build_snapshot(engine, tracker: dict) -> Snapshot:
    mw = engine.mw
    snap = Snapshot()
    for entry in tracker["player_checks_done"]:
        for lid in entry["locations"]:
            loc = engine.loc_by_id.get((entry["player"], lid))
            if loc:
                snap.checked[entry["player"]].add(loc)
            else:
                snap.unmatched.append(("check", entry["player"], lid))
    for entry in tracker["player_items_received"]:
        slot = entry["player"]
        snap.received_count[slot] = len(entry["items"])
        for item_id, loc_id, sender, _flags in entry["items"]:
            if loc_id == START_INVENTORY_LOCATION:
                continue  # already in CollectionState via precollected items
            loc = engine.loc_by_id.get((sender, loc_id))
            if loc and loc.item and loc.item.player == slot and loc.item.code == item_id:
                snap.received_from[slot].add(loc)
            else:  # start inventory / !getitem / mismatch: build the item directly
                name = mw.worlds[slot].item_id_to_name.get(item_id)
                if name is None:
                    snap.unmatched.append(("item", slot, item_id))
                    continue
                snap.extra_items[slot].append(mw.worlds[slot].create_item(name))
    for entry in tracker["player_status"]:
        if entry["status"] == GOAL_STATUS:
            snap.goals.add(entry["player"])
    for entry in tracker["activity_timers"]:
        snap.activity[entry["player"]] = entry["time"]
    seen = set()
    for entry in tracker["hints"]:
        for recv, finder, loc_id, item_id, found, _ent, _flags, status in entry["hints"]:
            if (finder, loc_id) in seen:
                continue
            seen.add((finder, loc_id))
            snap.hints.append(Hint(
                receiver=recv, finder=finder, location=engine.loc_by_id.get((finder, loc_id)),
                item_name=mw.worlds[recv].item_id_to_name.get(item_id, f"#{item_id}"),
                found=found, status=HINT_STATUS.get(status, str(status)),
            ))
    return snap
