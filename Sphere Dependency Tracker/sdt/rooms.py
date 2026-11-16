"""A tracked room: regenerated world + latest tracker snapshot + computed views."""
from __future__ import annotations

import copy
import json
import logging
import threading
import time
import traceback
from collections import defaultdict
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path

from . import live
from . import spoiler as spoiler_mod
from .db import DB
from .logic import Engine
from .placements import match_spoiler
from .regen import regenerate

log = logging.getLogger("sdt")

CHAIN_DEPTH = 3  # how many hops a dependency chain follows
LOW_LOGIC = 3  # "nearly BK" threshold


@dataclass
class RoomConfig:
    slug: str
    title: str
    room_id: str
    data_dir: Path  # holds the spoiler + player yamls
    admin_token: str
    poll_seconds: int = 300


def load_configs(path: Path) -> list[RoomConfig]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for r in raw["rooms"]:
        d = Path(r["data_dir"])
        if not d.is_absolute():
            d = (path.parent / d).resolve()
        out.append(RoomConfig(r["slug"], r.get("title", r["slug"]), r["room_id"], d, r["admin_token"],
                              int(r.get("poll_seconds", 300))))
    return out


def _epoch(s):
    try:
        return parsedate_to_datetime(s).timestamp() if s else None
    except (TypeError, ValueError):
        return None


class Room:
    def __init__(self, cfg: RoomConfig, db: DB):
        self.cfg = cfg
        self.db = db
        self.lock = threading.Lock()
        self.phase = "queued"  # queued -> regenerating -> ready | error
        self.error: str | None = None
        self.engine: Engine | None = None
        self.sp = None
        self.tracker_id: str | None = None
        self.last_refresh: float | None = None
        self.last_attempt: float = 0.0  # last tracker fetch attempt, successful or not
        self.last_viewed: float = 0.0  # last time a browser asked for this room's state
        self.full_view: dict | None = None
        self.public_view: dict | None = None
        self._snap = None
        self._state = None
        self._needs_cache = {}

    # ------------------------------------------------------------------ setup

    def load(self) -> None:
        try:
            self.phase = "regenerating"
            spoiler_path = next(self.cfg.data_dir.glob("*_Spoiler.txt"))
            self.sp = spoiler_mod.parse(spoiler_path)
            t = time.perf_counter()
            mw = regenerate(self.cfg.data_dir, self.sp.seed)
            log.info("[%s] regenerated in %.1fs", self.cfg.slug, time.perf_counter() - t)
            placed = match_spoiler(mw, self.sp)  # raises if the world or its logic differs from the run
            if not placed.exact:
                log.warning("[%s] regeneration filled %d locations differently (non-deterministic apworld); "
                            "using the spoiler's placements, and its playthrough checks out",
                            self.cfg.slug, placed.differing)
            sphere_of = {(loc, owner): i for i, sph in enumerate(self.sp.spheres) for loc, owner, _, _ in sph}
            self.engine = Engine(mw, sphere_of)
            pn = mw.player_name
            self.sphere_locs = defaultdict(list)  # slot -> [(sphere, Location)]
            by_key = {(l.name, pn[l.player]): l for ls in self.engine.real_locations.values() for l in ls}
            for key, sphere in sphere_of.items():
                if key in by_key:
                    l = by_key[key]
                    self.sphere_locs[l.player].append((sphere, l))
            for v in self.sphere_locs.values():
                v.sort(key=lambda x: x[0])
            room = live.fetch(f"room_status/{self.cfg.room_id}")
            self.tracker_id = room["tracker"]
            # No tracker fetch here: polling only starts once someone opens the page.
            self.phase = "ready"
        except Exception as e:  # surface on the page instead of killing the server
            self.phase = "error"
            self.error = f"{type(e).__name__}: {e}"
            log.error("[%s] load failed\n%s", self.cfg.slug, traceback.format_exc())

    # ---------------------------------------------------------------- refresh

    def refresh(self) -> None:
        with self.lock:
            self.last_attempt = time.time()
            log.info("[%s] fetching tracker from archipelago.gg", self.cfg.slug)
            tracker = live.fetch(f"tracker/{self.tracker_id}")
            eng = self.engine
            snap = live.build_snapshot(eng, tracker)
            st = eng.base_state(snap.received_from, snap.extra_items)
            self._snap, self._state, self._needs_cache = snap, st, {}
            self.full_view = self._build_view()
            self.last_refresh = time.time()
            track = self.db.update_tracking(self.cfg.slug, self.last_refresh, {
                p["slot"]: (p["state"] == "bk", snap.received_count.get(p["slot"], 0))
                for p in self.full_view["players"]})
            for p in self.full_view["players"]:
                t = track[p["slot"]]
                p["tracking_since"] = t["first_seen"]
                # BK started (if bk) / ended (if not) somewhere in (from, at]
                p["bk_changed_from"], p["bk_changed_at"] = t["bk_changed_from"], t["bk_changed_at"]
                p["last_received_from"], p["last_received"] = t["last_received_from"], t["last_received_at"]
            self.full_view["room"]["poll_seconds"] = self.cfg.poll_seconds
            self.full_view["room"]["last_refresh"] = self.last_refresh
            self.public_view = _redact(self.full_view)
            self.error = None

    # ------------------------------------------------------------ view build

    def _needs(self, loc):
        if loc not in self._needs_cache:
            self._needs_cache[loc] = self.engine.needs(self._state, loc, self._snap.received_from)
        return self._needs_cache[loc]

    def _holders(self, slot, name):
        have = self._snap.received_from.get(slot, set())
        hs = [l for l in self.engine.where_is(name, slot) if l not in have]
        st = self._state
        pn = self.engine.mw.player_name
        hs.sort(key=lambda l: (not l.can_reach(st), self.engine.sphere_of.get((l.name, pn[l.player]), 999)))
        return hs

    def _holder_node(self, loc, depth, visited):
        snap, st = self._snap, self._state
        pn = self.engine.mw.player_name
        node = {
            "location": loc.name, "finder": loc.player, "finder_name": pn[loc.player],
            "hinted": (loc.player, loc.address) in self._hinted,
            "checked": loc in snap.checked[loc.player],
            "in_logic": loc.can_reach(st),
        }
        if node["checked"] or node["in_logic"]:
            return node
        if loc in visited:
            node["cycle"] = True
        elif depth < CHAIN_DEPTH:
            node["needs"] = self._needs_node(loc, depth + 1, visited | {loc})
        return node

    def _needs_node(self, loc, depth, visited):
        n = self._needs(loc)
        if n.reachable_now:
            return {"kind": "in_logic", "items": []}
        if n.unreachable:
            return {"kind": "unknown", "items": []}
        if n.single_unlocks:
            kind, pairs = "any", [(x, 1) for x in n.single_unlocks]
        else:
            kind, pairs = "all", n.minimal_counts
        items = []
        for name, count in pairs:
            hs = self._holders(loc.player, name)
            items.append({
                "name": name, "count": count,
                "holders": [self._holder_node(h, depth, visited) for h in hs[:count if kind == "all" else 1]],
                "more_holders": max(0, len(hs) - (count if kind == "all" else 1)),
            })
        return {"kind": kind, "items": items}

    def _build_view(self) -> dict:
        eng, snap, st, sp = self.engine, self._snap, self._state, self.sp
        mw = eng.mw
        pn = mw.player_name
        self._hinted = {(h.finder, h.location.address) for h in snap.hints if h.location}

        players = []
        for s in eng.status(st, snap.checked, snap.goals):
            frontier = next((sph for sph, l in self.sphere_locs[s.slot] if l not in snap.checked[s.slot]), None)
            if s.goal_done:
                state = "goal"
            elif s.checked == s.total:
                state = "done"
            elif s.bk:
                state = "bk"
            elif len(s.available) <= LOW_LOGIC:
                state = "low"
            else:
                state = "ok"
            p = {
                "slot": s.slot, "name": s.name, "game": s.game, "checks": s.checked, "total": s.total,
                "in_logic": len(s.available), "frontier_sphere": frontier, "state": state,
                "last_activity": _epoch(snap.activity.get(s.slot)),
                "unlocks": [],
            }
            if state in ("bk", "low"):
                for name, gain in eng.unlocks(st, s.slot, snap.received_from, snap.checked[s.slot]):
                    hs = self._holders(s.slot, name)
                    p["unlocks"].append({
                        "name": name, "gain": gain,
                        "holders": [self._holder_node(h, CHAIN_DEPTH, frozenset()) for h in hs[:1]],
                        "more_holders": max(0, len(hs) - 1),
                    })
            players.append(p)

        hints = []
        for h in snap.hints:
            if h.location is None:
                continue
            loc = h.location
            entry = {
                "finder": h.finder, "finder_name": pn[h.finder],
                "receiver": h.receiver, "receiver_name": pn[h.receiver],
                "item": h.item_name, "location": loc.name, "location_id": loc.address,
                "status": h.status, "found": h.found or loc in snap.checked[h.finder],
                "in_logic": loc.can_reach(st),
                "needs": None,
            }
            if not entry["found"] and not entry["in_logic"]:
                entry["needs"] = self._needs_node(loc, 0, frozenset({loc}))
            hints.append(entry)

        spheres = []
        for i, sph in enumerate(sp.spheres):
            locs = [l for slot in self.sphere_locs for sphere, l in self.sphere_locs[slot] if sphere == i]
            open_ = [l for l in locs if l not in snap.checked[l.player]]
            spheres.append({
                "sphere": i, "total": len(locs), "checked": len(locs) - len(open_),
                "open_by_player": sorted({pn[l.player] for l in open_}),
                "open_locations": [{"location": l.name, "finder_name": pn[l.player], "in_logic": l.can_reach(st)}
                                   for l in open_],
            })

        return {
            "room": {"slug": self.cfg.slug, "title": self.cfg.title, "poll_seconds": self.cfg.poll_seconds,
                     "seed": str(sp.seed), "ap_version": sp.version},  # str: 20 digits overflow JS numbers
            "players": players, "hints": hints, "spheres": spheres,
        }


# --------------------------------------------------------------------- redaction

def _redact_holder(node):
    if not node.get("hinted"):
        return {"hidden": True}
    out = dict(node)
    if "needs" in out:
        out["needs"] = _redact_needs(out["needs"])
    return out


def _redact_needs(n):
    out = {"kind": n["kind"], "items": []}
    for it in n["items"]:
        holders = [_redact_holder(h) for h in it["holders"]]
        out["items"].append({"name": it["name"], "count": it["count"],
                             "holders": [h for h in holders if not h.get("hidden")]})
    return out


def _redact(view: dict) -> dict:
    """Public view: logic requirements are fine, but where items live is only shown if hinted."""
    v = copy.deepcopy(view)
    v["admin"] = False
    for p in v["players"]:
        for u in p["unlocks"]:
            u["holders"] = [h for h in (_redact_holder(x) for x in u["holders"]) if not h.get("hidden")]
            u.pop("more_holders", None)
    for h in v["hints"]:
        if h["needs"]:
            h["needs"] = _redact_needs(h["needs"])
    for s in v["spheres"]:
        s.pop("open_locations", None)
    view["admin"] = True
    return v
