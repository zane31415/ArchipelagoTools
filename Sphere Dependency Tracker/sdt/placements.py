"""Make a regenerated multiworld's item placements match the spoiler.

Regenerating with the spoiler's seed normally reproduces the run exactly. Some apworlds don't: they
iterate Python sets of strings (e.g. Dicey Dungeons' character option), whose order changes with
each process's hash seed, so the same seed fills differently every time. The generator's hash seed
isn't recorded anywhere, so that run can't be reproduced.

The logic doesn't depend on the fill, though: regions, locations and access rules come from the yaml
options. So when the placements differ, we keep the regenerated world and move every item to where
the spoiler says it is, then replay the spoiler's playthrough to prove the logic agrees with it.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field


class SpoilerMismatch(RuntimeError):
    pass


@dataclass
class PlacementReport:
    differing: int = 0  # real locations whose regenerated item differed from the spoiler
    created: int = 0  # items the regenerated pool didn't have, built with create_item
    unused: int = 0  # regenerated items the spoiler never placed
    unreachable: list[str] = field(default_factory=list)  # playthrough entries the logic can't reach in order

    @property
    def exact(self) -> bool:
        return self.differing == 0


def match_spoiler(mw, sp) -> PlacementReport:
    """Check placements against the spoiler, and if they differ, take the spoiler's.

    Raises SpoilerMismatch when the world itself differs (different locations), or when the
    spoiler's playthrough isn't reachable under the regenerated logic.
    """
    pn = mw.player_name
    slot_of = {name: slot for slot, name in pn.items()}
    real = {(l.name, pn[l.player]): l for l in mw.get_locations() if l.address is not None}
    events = {(l.name, pn[l.player]) for l in mw.get_locations() if l.address is None}

    missing = [k for k in real if k not in sp.placements]
    unknown = [k for k in sp.placements if k not in real and k not in events]
    if missing or unknown:
        raise SpoilerMismatch(
            f"the regenerated world has different locations from the spoiler ({len(missing)} only in the "
            f"rebuild, {len(unknown)} only in the spoiler, e.g. {(missing + unknown)[:3]}). "
            "Check the Archipelago/apworld versions and that every yaml used to generate is in the folder.")

    report = PlacementReport()
    report.differing = sum(1 for k, l in real.items() if sp.placements[k] != (l.item.name, pn[l.item.player]))
    if report.exact:
        return report

    # Reuse the regenerated Item objects, so each keeps the classification its world gave it.
    # Copies of one item name can differ (e.g. one progression copy of a Muse Dash song and one
    # useful copy). The spoiler only names them, but its playthrough lists just the copies that
    # mattered, so those locations get the progression copies and everywhere else gets the rest.
    pool = defaultdict(list)
    for l in real.values():
        pool[(l.item.name, l.item.player)].append(l.item)
    in_playthrough = {(loc, owner) for sphere in sp.spheres for loc, owner, _, _ in sphere}
    order = sorted(real, key=lambda k: k not in in_playthrough)  # playthrough locations first
    for key in order:
        l = real[key]
        item_name, receiver = sp.placements[key]
        stock = pool.get((item_name, slot_of[receiver]))
        if stock:
            wanted = key in in_playthrough
            pick = next((i for i, it in enumerate(stock) if bool(it.advancement) == wanted), len(stock) - 1)
            item = stock.pop(pick)
        else:
            item = mw.worlds[slot_of[receiver]].create_item(item_name)
            report.created += 1
        l.item = item
        item.location = l
    report.unused = sum(len(v) for v in pool.values())

    report.unreachable = replay_playthrough(mw, sp)
    if report.unreachable:
        raise SpoilerMismatch(
            f"after taking the spoiler's placements, {len(report.unreachable)} playthrough locations aren't "
            f"reachable in order under the regenerated logic (e.g. {report.unreachable[:3]}), so the logic "
            "itself differs from the run. Check the Archipelago/apworld versions.")
    return report


def replay_playthrough(mw, sp) -> list[str]:
    """Walk the spoiler's playthrough: each sphere must be reachable with the items from earlier spheres."""
    from BaseClasses import CollectionState

    pn = mw.player_name
    by_key = {(l.name, pn[l.player]): l for l in mw.get_locations()}
    event_locations = [l for l in mw.get_locations() if l.address is None]
    state = CollectionState(mw)
    unreachable = []
    for i, sphere in enumerate(sp.spheres):
        state.sweep_for_advancements(locations=event_locations)
        found = [by_key[(loc, owner)] for loc, owner, _, _ in sphere if (loc, owner) in by_key]
        unreachable += [f"sphere {i}: {l.name} ({pn[l.player]})" for l in found if not l.can_reach(state)]
        for l in found:
            if l.item and l.item.advancement and l.address is not None:
                state.collect(l.item, prevent_sweep=True)
    return unreachable
