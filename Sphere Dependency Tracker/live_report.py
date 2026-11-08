"""PoC step 3: live BK status + hint needs for the TestRun room (read-only web API)."""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from sdt import live, spoiler as spoiler_mod  # noqa: E402
from sdt.logic import Engine  # noqa: E402
from sdt.regen import regenerate  # noqa: E402

ROOM = "z0hZZJFoTlKPN7PBu13OLw"

room = live.fetch(f"room_status/{ROOM}")
tracker = live.fetch(f"tracker/{room['tracker']}")
(ROOT / "live").mkdir(exist_ok=True)
(ROOT / "live" / "tracker.json").write_text(json.dumps(tracker, indent=1))

sp = spoiler_mod.parse(next((ROOT / "TestRun").glob("*_Spoiler.txt")))
mw = regenerate(ROOT / "TestRun", sp.seed)
sphere_of = {(loc, owner): i for i, sph in enumerate(sp.spheres) for loc, owner, _, _ in sph}
eng = Engine(mw, sphere_of)
pn = mw.player_name

snap = live.build_snapshot(eng, tracker)
print(f"Unmatched ids from tracker: {len(snap.unmatched)} {snap.unmatched[:5]}")
t = time.perf_counter()
st = eng.base_state(snap.received_from, snap.extra_items)
statuses = eng.status(st, snap.checked, snap.goals)
print(f"State + status in {time.perf_counter() - t:.2f}s\n")


def frontier_sphere(slot):
    """Earliest playthrough sphere that still has an unchecked location of this player."""
    for i, sph in enumerate(sp.spheres):
        for loc, owner, _, _ in sph:
            l = next((x for x in eng.real_locations[slot] if x.name == loc), None) if owner == pn[slot] else None
            if l and l not in snap.checked[slot]:
                return i
    return None


print(f"{'Player':18} {'Game':26} {'Checks':>10} {'InLogic':>8}  {'Sphere':>6}  Status        Last activity")
for s in statuses:
    tag = "GOAL" if s.goal_done else ("BK" if s.bk else "")
    fs = frontier_sphere(s.slot)
    print(f"{s.name:18} {s.game[:26]:26} {s.checked:4}/{s.total:<5} {len(s.available):8}  {fs if fs is not None else '-':>6}  "
          f"{tag:12}  {snap.activity.get(s.slot, '')}")

print("\nOpen hints (not found) and what the finder needs to reach them:")
for h in snap.hints:
    if h.found or h.location is None:
        continue
    n = eng.needs(st, h.location, snap.received_from)
    if n.reachable_now:
        what = "in logic now"
    elif n.single_unlocks:
        what = "needs any of: " + ", ".join(n.single_unlocks)
    elif n.minimal_set:
        what = "needs all of: " + ", ".join(n.minimal_set)
    else:
        what = "not reachable in logic?!"
    print(f"  [{h.status}] {h.item_name} ({pn[h.receiver]}) @ {h.location.name} ({pn[h.finder]})\n      -> {what}")
