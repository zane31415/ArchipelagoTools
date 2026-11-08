"""Logic engine on top of a regenerated MultiWorld.

Everything is keyed by Archipelago's own objects so that the live tracker data
(item ids / location ids / slots) maps onto it directly.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field


@dataclass
class PlayerStatus:
    slot: int
    name: str
    game: str
    goal_done: bool
    checked: int
    total: int
    available: list  # unchecked locations currently in logic
    bk: bool


@dataclass
class Needs:
    location: object
    reachable_now: bool
    single_unlocks: list[str] = field(default_factory=list)  # any ONE of these opens it
    minimal_set: list[str] = field(default_factory=list)  # all of these together open it
    minimal_counts: list[tuple[str, int]] = field(default_factory=list)  # same, as (name, count)
    unreachable: bool = False  # not reachable even with every remaining item (logic quirk)


class Engine:
    def __init__(self, multiworld, sphere_of: dict[tuple[str, str], int] | None = None):
        self.mw = multiworld
        self.sphere_of = sphere_of or {}
        self.real_locations = defaultdict(list)  # slot -> [Location with address]
        self.event_locations = defaultdict(list)  # slot -> [event Location]
        self.loc_by_id = {}  # (slot, location_id) -> Location
        for loc in multiworld.get_locations():
            if loc.address is None:
                self.event_locations[loc.player].append(loc)
            else:
                self.real_locations[loc.player].append(loc)
                self.loc_by_id[(loc.player, loc.address)] = loc
        # Every progression item each player will ever receive, with where it lives.
        self.prog_items_for = defaultdict(list)  # slot -> [Location holding a progression item for slot]
        for locs in self.real_locations.values():
            for loc in locs:
                if loc.item and loc.item.advancement:
                    self.prog_items_for[loc.item.player].append(loc)

    # ---------- state building ----------

    def base_state(self, received_from: dict[int, set], extra_items: dict[int, list] | None = None):
        """received_from: slot -> set of Locations whose items that slot has received.
        extra_items: slot -> Items received without a matching location (e.g. server-granted)."""
        from BaseClasses import CollectionState
        state = CollectionState(self.mw)  # includes start inventory
        for locs in received_from.values():
            for loc in locs:
                if loc.item.advancement:
                    state.collect(loc.item, prevent_sweep=True)
        for items in (extra_items or {}).values():
            for it in items:
                if it.advancement:
                    state.collect(it, prevent_sweep=True)
        state.sweep_for_advancements(locations=[l for ls in self.event_locations.values() for l in ls])
        return state

    def _with(self, state, slot: int, items) -> "CollectionState":
        s = state.copy()
        for it in items:
            s.collect(it, prevent_sweep=True)
        s.sweep_for_advancements(locations=self.event_locations[slot])
        return s

    # ---------- queries ----------

    def status(self, state, checked: dict[int, set], goals: set[int]) -> list[PlayerStatus]:
        out = []
        for slot, locs in sorted(self.real_locations.items()):
            done = checked.get(slot, set())
            avail = [l for l in locs if l not in done and l.can_reach(state)]
            goal = slot in goals
            out.append(PlayerStatus(
                slot=slot, name=self.mw.player_name[slot], game=self.mw.game[slot],
                goal_done=goal, checked=len(done), total=len(locs),
                available=avail, bk=(not goal and not avail and len(done) < len(locs)),
            ))
        return out

    def needs(self, state, location, received_from: dict[int, set], cap: int = 12) -> Needs:
        slot = location.player
        if location.can_reach(state):
            return Needs(location, reachable_now=True)

        have = received_from.get(slot, set())
        remaining = [l.item for l in self.prog_items_for[slot] if l not in have]

        # 1) single items that open it on their own (one representative per name)
        reps = {}
        for it in remaining:
            reps.setdefault(it.name, it)
        singles = [name for name, it in reps.items() if location.can_reach(self._with(state, slot, [it]))]
        if singles:
            return Needs(location, False, single_unlocks=sorted(singles)[:cap])

        # 2) minimal sufficient set by greedy elimination. Try removing items that show up
        #    latest in the playthrough first, so the answer prefers things that are obtainable earliest.
        if not location.can_reach(self._with(state, slot, remaining)):
            return Needs(location, False, unreachable=True)

        holder = {id(l.item): l for l in self.prog_items_for[slot]}

        def sphere(it):
            l = holder[id(it)]
            return self.sphere_of.get((l.name, self.mw.player_name[l.player]), 999)

        # Collapse to name -> list so whole groups (e.g. 20 "Progressive X") can be dropped in one test.
        groups = defaultdict(list)
        for it in remaining:
            groups[it.name].append(it)
        kept = {n: sorted(v, key=sphere) for n, v in groups.items()}
        order = sorted(kept, key=lambda n: -min(sphere(i) for i in kept[n]))

        def ok(k):
            return location.can_reach(self._with(state, slot, [i for v in k.values() for i in v]))

        for name in order:
            trial = dict(kept)
            del trial[name]
            if ok(trial):
                kept = trial
                continue
            # needed; trim the count down (binary search on how many copies)
            lo, hi = 1, len(kept[name])
            while lo < hi:
                mid = (lo + hi) // 2
                trial = dict(kept)
                trial[name] = kept[name][:mid]
                if ok(trial):
                    hi = mid
                else:
                    lo = mid + 1
            kept[name] = kept[name][:lo]

        counts = sorted((n, len(v)) for n, v in kept.items())[:cap]
        result = [f"{n} x{c}" if c > 1 else n for n, c in counts]
        return Needs(location, False, minimal_set=result, minimal_counts=counts)

    def unlocks(self, state, slot: int, received_from: dict[int, set], checked: set, top: int = 6):
        """Single items that would put new checks into logic for `slot`, with how many each opens."""
        have = received_from.get(slot, set())
        reps = {}
        for l in self.prog_items_for[slot]:
            if l not in have:
                reps.setdefault(l.item.name, l.item)
        open_now = {l for l in self.real_locations[slot] if l not in checked and l.can_reach(state)}
        todo = [l for l in self.real_locations[slot] if l not in checked and l not in open_now]
        out = []
        for name, it in reps.items():
            s = self._with(state, slot, [it])
            gain = sum(1 for l in todo if l.can_reach(s))
            if gain:
                out.append((name, gain))
        out.sort(key=lambda x: (-x[1], x[0]))
        return out[:top]

    def where_is(self, item_name: str, slot: int):
        """Locations holding progression copies of item_name for slot."""
        return [l for l in self.prog_items_for[slot] if l.item.name == item_name]
