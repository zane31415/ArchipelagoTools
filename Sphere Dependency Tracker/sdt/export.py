"""Export a regenerated multiworld's logic as plain data a browser can evaluate.

For every location we compute its requirement as a monotone DNF over the owning player's
progression items: a list of minimal "terms", each term being {item: count}. The location is
in logic iff the player has at least the counts of some term. Rules are treated as a black box
(the real Archipelago `can_reach`), so this works for any apworld whose logic depends only on
the player's own items.

Search per location (exact, with a query budget):
  1. F = every progression item the player will ever get. If F can't reach it, it's never in logic.
  2. Shrink a positive point to a minimal term (QuickXplain over item names, then binary search counts).
  3. Completeness: enumerate the ways to "break" every known term (drop one requirement from each).
     If such a point is still positive, it contains an unknown term -> shrink it and repeat.
     When no breaking point is positive, the term list is provably complete.
"""
from __future__ import annotations

import time
from collections import Counter, OrderedDict

from .logic import Engine

QUERY_BUDGET = 4000  # per location; beyond this the term list is marked approximate
STATE_CACHE = 4096


class PlayerOracle:
    def __init__(self, eng: Engine, slot: int, base_state):
        self.eng = eng
        self.slot = slot
        self.base = base_state  # start inventory for everyone, events swept
        self.pool = Counter()
        self.items_by_name: dict[str, list] = {}
        for loc in eng.prog_items_for[slot]:
            self.pool[loc.item.name] += 1
            self.items_by_name.setdefault(loc.item.name, []).append(loc.item)
        self.names = sorted(self.pool)
        self.events = eng.event_locations[slot]
        self._states: OrderedDict = OrderedDict()
        self.queries = 0

    def state(self, counts: tuple):
        st = self._states.get(counts)
        if st is not None:
            self._states.move_to_end(counts)
            return st
        st = self.base.copy()
        for name, c in zip(self.names, counts):
            for it in self.items_by_name[name][:c]:
                st.collect(it, prevent_sweep=True)
        st.sweep_for_advancements(locations=self.events)
        self._states[counts] = st
        if len(self._states) > STATE_CACHE:
            self._states.popitem(last=False)
        return st

    def reach(self, loc, counts: tuple) -> bool:
        self.queries += 1
        return loc.can_reach(self.state(counts))

    def full(self) -> tuple:
        return tuple(self.pool[n] for n in self.names)


def _shrink(o: PlayerOracle, loc, point: list[int]) -> list[int]:
    """Minimal term below a positive point (list of counts aligned with o.names)."""
    zero = [0] * len(point)

    def with_names(base: list[int], idxs) -> list[int]:
        p = list(base)
        for i in idxs:
            p[i] = point[i]
        return p

    def f(idxs_base, idxs_extra=()):
        return o.reach(loc, tuple(with_names(zero, list(idxs_base) + list(idxs_extra))))

    # QuickXplain: minimal set of item *names* (at their counts in `point`) that suffices.
    def qx(B: list[int], C: list[int], delta: bool) -> list[int]:
        if delta and f(B):
            return []
        if len(C) == 1:
            return C
        mid = len(C) // 2
        C1, C2 = C[:mid], C[mid:]
        X2 = qx(B + C1, C2, bool(C1))
        X1 = qx(B + X2, C1, bool(X2))
        return X1 + X2

    cand = [i for i, c in enumerate(point) if c]
    if f([]):
        return zero
    chosen = qx([], cand, False) if cand else []
    term = with_names(zero, chosen)
    # Now minimise each count (monotone in count) with binary search.
    for i in chosen:
        lo, hi = 1, term[i]
        while lo < hi:
            mid = (lo + hi) // 2
            trial = list(term)
            trial[i] = mid
            if o.reach(loc, tuple(trial)):
                hi = mid
            else:
                lo = mid + 1
        term[i] = lo
    return term


def location_terms(o: PlayerOracle, loc) -> tuple[list[list[int]], bool]:
    """Returns (terms, exact). terms == [] means never reachable; [[0,...]] means always."""
    full = list(o.full())
    start = o.queries
    if not o.reach(loc, tuple(full)):
        return [], True
    terms = [_shrink(o, loc, full)]
    if not any(terms[0]):
        return terms, True

    while True:
        found_new = False
        tried = set()
        # DFS over ways to break each term: lower one required item below its count.
        stack = [tuple(full)]
        while stack:
            if o.queries - start > QUERY_BUDGET:
                return terms, False
            point = stack.pop()
            alive = next((t for t in terms if all(point[i] >= t[i] for i in range(len(t)))), None)
            if alive is None:
                if point in tried:
                    continue
                tried.add(point)
                if o.reach(loc, point):
                    terms.append(_shrink(o, loc, list(point)))
                    found_new = True
                    break
                continue
            for i, need in enumerate(alive):
                if need:
                    p = list(point)
                    p[i] = need - 1
                    stack.append(tuple(p))
        if not found_new:
            return terms, True


def export(eng: Engine, room_id: str, tracker_id: str, title: str, spoiler, include_spheres: bool) -> dict:
    from BaseClasses import CollectionState
    mw = eng.mw
    base = CollectionState(mw)
    base.sweep_for_advancements(locations=[l for ls in eng.event_locations.values() for l in ls])

    games: dict[str, dict] = {}
    players = []
    stats = []
    sphere_of = eng.sphere_of if include_spheres else {}
    for slot in sorted(eng.real_locations):
        t0 = time.perf_counter()
        world = mw.worlds[slot]
        game = mw.game[slot]
        games.setdefault(game, {str(k): v for k, v in world.item_id_to_name.items()})
        o = PlayerOracle(eng, slot, base)
        names = o.names
        idx = {n: i for i, n in enumerate(names)}
        start = Counter(it.name for it in mw.precollected_items[slot] if it.advancement)

        req_table: dict[tuple, int] = {}
        reqs, approx, locs = [], [], []
        for loc in sorted(eng.real_locations[slot], key=lambda l: l.address):
            terms, exact = location_terms(o, loc)
            enc = tuple(sorted(tuple(x for i, c in enumerate(t) if c for x in (i, c)) for t in terms))
            key = (enc, exact)
            if key not in req_table:
                req_table[key] = len(reqs)
                reqs.append([list(t) for t in enc])
                if not exact:
                    approx.append(len(reqs) - 1)
            row = [loc.address, loc.name, req_table[key]]
            if include_spheres:
                row.append(sphere_of.get((loc.name, mw.player_name[slot])))
            locs.append(row)

        item_ids = {}
        for n in names:
            code = o.items_by_name[n][0].code
            if code is not None:
                item_ids[str(code)] = idx[n]
        players.append({
            "slot": slot, "name": mw.player_name[slot], "game": game,
            "items": names, "item_ids": item_ids,
            "start": [[idx[n], c] for n, c in start.items() if n in idx],
            "locs": locs, "reqs": reqs, "approx": approx,
        })
        stats.append((mw.player_name[slot], game, len(locs), len(reqs), len(approx), o.queries,
                      time.perf_counter() - t0))

    bundle = {
        "format": 1,
        "generated": time.time(),
        "title": title,
        "room_id": room_id,
        "tracker_id": tracker_id,
        "seed": str(spoiler.seed),
        "ap_version": spoiler.version,
        "has_spheres": include_spheres,
        "sphere_count": len(spoiler.spheres) if include_spheres else 0,
        "games": games,
        "players": players,
    }
    return bundle, stats


def evaluate(bundle: dict, slot: int, counts: Counter) -> set[int]:
    """Reference evaluator (mirrors the JS) used for validation: location ids in logic."""
    # Terms are measured on top of start inventory; `counts` must exclude start items
    # (the server reports those as received from location -2).
    p = next(x for x in bundle["players"] if x["slot"] == slot)
    have = [counts.get(n, 0) for n in p["items"]]
    ok_req = []
    for terms in p["reqs"]:
        ok_req.append(any(all(have[t[k]] >= t[k + 1] for k in range(0, len(t), 2)) for t in terms))
    return {lid for lid, _name, r, *_ in p["locs"] if ok_req[r]}
