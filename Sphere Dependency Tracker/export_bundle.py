"""Export a static logic bundle for an async, for hosting the tracker page without a server.

    .venv/Scripts/python.exe export_bundle.py --data TestRun --room z0hZZJFoTlKPN7PBu13OLw \
        --slug testrun --title "TestRun async"

--out defaults to ../docs/tracker, the tracker's folder in the combined GitHub Pages site.
Writes <out>/data/<slug>.json and updates <out>/data/rooms.json. The bundle contains logic only
(location names + the item sets that unlock each one) and no item placements, so it is safe to
publish. --spheres additionally stores each location's playthrough sphere, which reveals which
locations hold progression; leave it off for a public page unless you're fine with that.
"""
import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from sdt import live, spoiler as spoiler_mod  # noqa: E402
from sdt.export import evaluate, export  # noqa: E402
from sdt.logic import Engine  # noqa: E402
from sdt.regen import regenerate  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="folder with the *_Spoiler.txt and every player yaml")
    ap.add_argument("--room", required=True, help="archipelago.gg room id")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--title", default=None)
    ap.add_argument("--out", default="../docs/tracker", help="relative paths are from this script's folder")
    ap.add_argument("--spheres", action="store_true", help="include playthrough spheres (mild spoiler)")
    ap.add_argument("--no-validate", action="store_true")
    args = ap.parse_args()

    data = (ROOT / args.data).resolve() if not Path(args.data).is_absolute() else Path(args.data)
    out = (ROOT / args.out).resolve() if not Path(args.out).is_absolute() else Path(args.out)
    sp = spoiler_mod.parse(next(data.glob("*_Spoiler.txt")))
    print(f"Regenerating AP {sp.version} seed {sp.seed} ({len(sp.players)} players)…")
    mw = regenerate(data, sp.seed)
    sphere_of = {(loc, owner): i for i, sph in enumerate(sp.spheres) for loc, owner, _, _ in sph}
    eng = Engine(mw, sphere_of)

    mism = sum(1 for ls in eng.real_locations.values() for l in ls
               if sp.placements.get((l.name, mw.player_name[l.player])) != (l.item.name, mw.player_name[l.item.player]))
    if mism:
        sys.exit(f"Regenerated world differs from the spoiler at {mism} locations; check AP/apworld versions.")
    print("Regeneration matches the spoiler.")

    room = live.fetch(f"room_status/{args.room}")
    t = time.perf_counter()
    bundle, stats = export(eng, args.room, room["tracker"], args.title or args.slug, sp, args.spheres)
    print(f"\nExported logic in {time.perf_counter() - t:.1f}s")
    print(f"{'Player':18} {'Game':28} {'Locs':>5} {'Rules':>6} {'Approx':>6} {'Queries':>8} {'Time':>6}")
    for name, game, nl, nr, na, q, dt in stats:
        print(f"{name:18} {game[:28]:28} {nl:5} {nr:6} {na:6} {q:8} {dt:5.1f}s")

    if not args.no_validate:
        validate(eng, bundle, sp, room["tracker"])

    (out / "data").mkdir(parents=True, exist_ok=True)
    path = out / "data" / f"{args.slug}.json"
    path.write_text(json.dumps(bundle, separators=(",", ":")), encoding="utf-8")
    index_path = out / "data" / "rooms.json"
    rooms = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else []
    rooms = [r for r in rooms if r["slug"] != args.slug] + [{"slug": args.slug, "title": bundle["title"]}]
    index_path.write_text(json.dumps(rooms, indent=1), encoding="utf-8")
    write_site(out)
    print(f"\nWrote {path} ({path.stat().st_size / 1024:.0f} KB), {index_path} and the page files in {out}")


SITE_INDEX = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sphere Dependency Tracker</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <header class="top"><h1>Sphere Dependency Tracker</h1></header>
  <main>
    <section>
      <h2>Asyncs</h2>
      <ul id="rooms" class="room-list"></ul>
    </section>
  </main>
  <script>
    fetch("data/rooms.json", {cache: "no-store"}).then(r => r.json()).then(rooms => {
      const ul = document.getElementById("rooms");
      for (const r of rooms.slice().reverse()) {
        const li = document.createElement("li");
        const a = document.createElement("a");
        a.href = "room.html?r=" + encodeURIComponent(r.slug);
        a.textContent = r.title;
        li.append(a);
        ul.append(li);
      }
    });
  </script>
</body>
</html>
"""


def write_site(out: Path):
    """Copy the page into <out> as a static site (room.html?r=<slug>), reading data/<slug>.json."""
    web = ROOT / "web"
    room = (web / "room.html").read_text(encoding="utf-8")
    room = room.replace('href="/static/style.css"', 'href="style.css"')
    room = room.replace('<script src="/static/room.js"></script>',
                        '<script src="static.js"></script>\n  <script src="room.js"></script>')
    (out / "room.html").write_text(room, encoding="utf-8")
    (out / "index.html").write_text(SITE_INDEX, encoding="utf-8")
    for name in ("room.js", "static.js", "style.css"):
        (out / name).write_text((web / name).read_text(encoding="utf-8"), encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")  # serve files as-is on GitHub Pages


def validate(eng, bundle, sp, tracker_id):
    """Compare the bundle's verdicts with the real logic on many item sets."""
    mw = eng.mw
    loc_obj = {(l.name, mw.player_name[l.player]): l for ls in eng.real_locations.values() for l in ls}
    snapshots = []
    for k in range(len(sp.spheres) + 1):  # "items from spheres < k" for every k
        recv = defaultdict(set)
        for sph in sp.spheres[:k]:
            for loc, owner, _, _ in sph:
                l = loc_obj.get((loc, owner))
                if l:
                    recv[l.item.player].add(l)
        snapshots.append((f"sphere<{k}", recv, {}))
    snap = live.build_snapshot(eng, live.fetch(f"tracker/{tracker_id}"))
    snapshots.append(("live", snap.received_from, snap.extra_items))

    bad = 0
    for label, recv, extra in snapshots:
        st = eng.base_state(recv, extra)
        for slot, locs in eng.real_locations.items():
            counts = Counter(l.item.name for l in recv.get(slot, ()) if l.item.advancement)
            counts.update(it.name for it in extra.get(slot, ()) if it.advancement)
            want = {l.address for l in locs if l.can_reach(st)}
            got = evaluate(bundle, slot, counts)
            if want != got:
                bad += 1
                print(f"  MISMATCH {label} {mw.player_name[slot]}: engine {len(want)} vs bundle {len(got)} "
                      f"(missing {len(want - got)}, extra {len(got - want)})")
    print(f"\nValidation: {len(snapshots)} item snapshots x {len(eng.real_locations)} players, {bad} mismatches")


if __name__ == "__main__":
    main()
