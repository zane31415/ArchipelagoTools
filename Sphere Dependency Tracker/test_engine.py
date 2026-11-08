"""PoC step 2: exercise the logic engine against snapshots taken from the spoiler playthrough."""
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from sdt import spoiler as spoiler_mod  # noqa: E402
from sdt.logic import Engine  # noqa: E402
from sdt.regen import regenerate  # noqa: E402

sp = spoiler_mod.parse(next((ROOT / "TestRun").glob("*_Spoiler.txt")))
mw = regenerate(ROOT / "TestRun", sp.seed)
sphere_of = {(loc, owner): i for i, sph in enumerate(sp.spheres) for loc, owner, _, _ in sph}
eng = Engine(mw, sphere_of)
name_to_slot = {v: k for k, v in mw.player_name.items()}
loc_obj = {(l.name, mw.player_name[l.player]): l for ls in eng.real_locations.values() for l in ls}

# --- correctness: items from spheres < k must put every sphere-k location in logic ---
bad = 0
for k in range(1, len(sp.spheres)):
    recv = defaultdict(set)
    for sph in sp.spheres[:k]:
        for loc, owner, _, _ in sph:
            if (loc, owner) in loc_obj:
                l = loc_obj[(loc, owner)]
                recv[l.item.player].add(l)
    st = eng.base_state(recv)
    for loc, owner, _, _ in sp.spheres[k]:
        if (loc, owner) in loc_obj and not loc_obj[(loc, owner)].can_reach(st):
            bad += 1
            print(f"  sphere {k}: {loc} ({owner}) NOT reachable")
print(f"Sphere consistency check: {bad} failures across {len(sp.spheres)} spheres")

# --- demo snapshot: everyone has done playthrough spheres 0..K-1 only ---
K = 4
recv, checked = defaultdict(set), defaultdict(set)
for sph in sp.spheres[:K]:
    for loc, owner, _, _ in sph:
        if (loc, owner) in loc_obj:
            l = loc_obj[(loc, owner)]
            recv[l.item.player].add(l)
            checked[l.player].add(l)

t = time.perf_counter()
st = eng.base_state(recv)
statuses = eng.status(st, checked, goals=set())
print(f"\nSnapshot after sphere {K - 1} (status computed in {time.perf_counter() - t:.2f}s):")
for s in statuses:
    print(f"  {s.name:18} {s.game[:28]:28} {s.checked:4}/{s.total:<4} available={len(s.available):4} "
          f"{'BK' if s.bk else ''}")

print("\nNeeds for some not-yet-reachable progression locations (deepest per player):")
shown = 0
seen_owner = set()
for sph in reversed(sp.spheres):
    for loc, owner, item, recv_name in sph:
        l = loc_obj.get((loc, owner))
        if not l or l.can_reach(st) or owner in seen_owner:
            continue
        seen_owner.add(owner)
        t = time.perf_counter()
        n = eng.needs(st, l, recv)
        dt = time.perf_counter() - t
        what = (f"any of {n.single_unlocks}" if n.single_unlocks else
                f"all of {n.minimal_set}" if n.minimal_set else "UNREACHABLE?")
        print(f"  [{dt:.2f}s] {loc} ({owner}) -> {what}")
        for need in (n.single_unlocks or [x.split(' x')[0] for x in n.minimal_set])[:2]:
            for h in eng.where_is(need, l.player)[:1]:
                print(f"           {need} is at {h.name} ({mw.player_name[h.player]}), "
                      f"in logic for them now: {h.can_reach(st)}")
        shown += 1
