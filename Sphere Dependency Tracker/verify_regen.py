"""PoC step 1: regenerate the TestRun seed and check placements match the spoiler exactly."""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from sdt import spoiler as spoiler_mod  # noqa: E402
from sdt.regen import regenerate  # noqa: E402

test_dir = ROOT / "TestRun"
sp = spoiler_mod.parse(next(test_dir.glob("*_Spoiler.txt")))
print(f"Spoiler: AP {sp.version}, seed {sp.seed}, {len(sp.players)} players, "
      f"{len(sp.placements)} placements, {len(sp.spheres)} spheres")

t = time.perf_counter()
mw = regenerate(test_dir, sp.seed)
print(f"Regenerated in {time.perf_counter() - t:.1f}s")

regen = {}
for loc in mw.get_locations():
    if loc.item is None or loc.address is None:
        continue  # events aren't listed in the spoiler's Locations section
    regen[(loc.name, mw.player_name[loc.player])] = (loc.item.name, mw.player_name[loc.item.player])

missing = set(sp.placements) - set(regen)
extra = set(regen) - set(sp.placements)
diff = [k for k in sp.placements.keys() & regen.keys() if sp.placements[k] != regen[k]]
print(f"Compared {len(regen)} locations: missing={len(missing)} extra={len(extra)} mismatched={len(diff)}")

by_player = {}
for k in diff:
    by_player[k[1]] = by_player.get(k[1], 0) + 1
for k in list(missing)[:5] + list(extra)[:5]:
    print("  name mismatch:", k)
for p, n in sorted(by_player.items()):
    print(f"  {p}: {n} mismatched")
for k in diff[:10]:
    print(f"  {k}: spoiler={sp.placements[k]} regen={regen[k]}")
