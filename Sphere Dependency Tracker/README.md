# Sphere Dependency Tracker

A web tracker for Archipelago asyncs that sits between CheeseTracker and SphereTracker:

- **Automatic BK status.** The tracker rebuilds the multiworld from the spoiler and yamls, so the real game
  logic is available. On every poll it works out how many unchecked locations each player has in logic.
  Players with none are marked **BK**; players with 3 or fewer are marked **nearly BK**.
- **What would unblock a BK player.** Lists the items that would open new checks on their own, and how many each opens.
- **Hint needs.** For every unfound hint, it works out what the finder still needs before the location
  is in logic: either "any one of" a few items, or a minimal "all of" set. Needed items that have
  already been hinted are tagged. This appears beside each hint in the Hints section, and in the
  players table's "Hints in their world" column.
- **Dependency chains.** Each needed item is followed to where it is, whether it is in logic for that
  player, and if not, what *they* need (3 hops deep, with circular dependencies detected).
- **Sphere progress.** Shows how far the run is through the spoiler playthrough, and who still holds open locations in the earliest spheres.

## Spoiler safety

There are two views of each room:

| | Public `/r/<slug>` | Admin `/r/<slug>/admin/<token>` |
|---|---|---|
| BK status, in-logic counts, sphere progress | yes | yes |
| What a location / player needs (item names) | yes | yes |
| Where a needed item is | **only if that location is hinted** | always |
| Open location names per sphere | no | yes |

Redaction happens on the server, so the public API never contains unhinted locations.

## Static version (GitHub Pages, no server)

A static host can't run Archipelago, so `export_bundle.py` does the Archipelago part once, offline. It
rebuilds the world, then runs each location's real logic against many item combinations. It records the
minimal item sets that unlock each location: "any one of these sets is enough". The browser only has to
check "do I have one of these sets?" against the live tracker. archipelago.gg allows cross-origin
requests, so each viewer's browser fetches the tracker directly: at most every 2 minutes, and only while
the tab is visible.

Once per async:

```bash
.venv/Scripts/python.exe export_bundle.py --data path/to/spoiler_and_yamls --room <archipelago.gg room id> --slug myasync --title "My async"
```

This writes to `../docs/tracker`, the tracker's folder in the combined GitHub Pages site (see the
README one folder up). Pass `--out` to write somewhere else. Then commit and push `docs/`. Each export
adds `data/<slug>.json` and lists it in `data/rooms.json`, so older asyncs stay up. Exporting takes a few minutes (about 3 for the 12-player TestRun, ~450 KB of data).

What the exporter checks before writing:
- The rebuilt world must match the spoiler exactly, or it stops.
- It compares the bundle with the real logic on every spoiler sphere plus the current live state, and prints any differences.

**What's in the bundle:** location names, item names, and the logic requirements. There are **no item
placements**, so it's safe to publish. Dependency chains only follow *hinted* locations, the same as the
server's public view. `--spheres` also stores each location's playthrough sphere. That turns the
Sphere column and section back on, but it reveals which locations hold progression, so it's off by default.

**Approximate rules:** some rules are "N of a group" style, e.g. "3 of 11 prosthetic tools" or a
combat-strength score. These have too many combinations to list, so the exporter stops after a budget
and marks them approximate. The combinations it lists are always right, so the page never shows
something as in logic when it isn't. It may miss that one of these locations *is* in logic. The page
shows those as "maybe": the In logic column reads e.g. `0 +6?`, a BK pill becomes `BK?`, and the needs
list says "one known option". In TestRun, 33 of 1,211 rules were approximate.

**Timing in the static version:** with no server, the BK / un-BK and "last received" times are only
what *your* browser has seen while you had the page open (kept in localStorage).

## Running it

Requires Python 3.12. The `.venv` and `vendor/Archipelago` (a clean 0.6.8 checkout) are already set up.

```bash
.venv/Scripts/python.exe -m uvicorn sdt.app:app --host 127.0.0.1 --port 8765
```

Open http://127.0.0.1:8765. Each room takes about 20s to rebuild on startup.

**Polling only happens while someone is watching.** Open pages check in with the server every 30s, but
only while the tab is visible. The server fetches the archipelago.gg tracker API at most once every
`poll_seconds`, and only if a page has checked in within the last 5 minutes. With nobody watching, it
makes no requests at all. Startup makes one `room_status` call to find the tracker ID. **Refresh now**
works at most once a minute. The tracker only reads the website API; it never connects to the game server.

### Timing data

Archipelago's tracker API doesn't timestamp received items; it only gives each player's last check and
last connection. So the "BK for…", "un-BK'd … ago" and "Last received" columns come from this tracker
noticing changes between its own polls (saved in `data/sdt.sqlite`, so they survive restarts). When a
change happened while nobody was watching, the page shows the window it fell in (e.g. "un-BK'd 2h–5h ago").

### Players table

- Click a column header to sort, and click it again to reverse.
- Filter with the status chips and the player/game search.
- Pick columns from **Columns**.
- **Screenshot view** switches to Player / Status / In logic / Last active.
- **Copy as image** puts the table on the clipboard as a PNG, ready to paste into Discord. It uses html2canvas from cdnjs, loaded on first use.
- Your choices are remembered in the browser.

To let others reach it, run with `--host 0.0.0.0` (or put it behind a reverse proxy) and share the
public link. Keep the admin link to yourself.

## Adding a room

1. Make a folder with the `*_Spoiler.txt` and **every** player yaml used to generate.
2. Make sure `vendor/Archipelago/custom_worlds/` has the same apworld versions used to generate.
   The AP version must match too (the spoiler's first line).
3. Add an entry to `rooms.json`:

```json
{
  "slug": "myasync",
  "title": "My async",
  "room_id": "<id from archipelago.gg/room/<id>>",
  "data_dir": "path/to/folder",
  "admin_token": "<long random string>",
  "poll_seconds": 300
}
```

On startup the tracker compares the rebuilt world with the spoiler, location by location. If anything
differs (wrong apworld or AP version, missing yaml), the room shows an error instead of wrong data.

## Layout

- `sdt/regen.py`: rebuilds the multiworld in memory (`Generate` + `Main` with `--skip_output`)
- `sdt/spoiler.py`: parses the spoiler (players, placements, playthrough spheres)
- `sdt/logic.py`: reachability, BK status, needs (single unlocks and a minimal set by greedy elimination), unlocks
- `sdt/live.py`: archipelago.gg tracker API → Archipelago objects
- `sdt/rooms.py`: per-room polling and view building, plus the public redaction
- `sdt/app.py`: FastAPI server and JSON API
- `sdt/db.py`: SQLite store for BK / last-received timing seen across polls (`data/sdt.sqlite`)
- `web/`: the page (plain HTML/JS, no build step). `static.js` is the serverless mode, which evaluates a bundle in the browser.
- `sdt/export.py` and `export_bundle.py`: the logic exporter for the static version (writes `../docs/tracker/`)
- `verify_regen.py`, `test_engine.py`, `live_report.py`: command-line checks used during development

## Limits

- One Archipelago version per server process (all rooms share `vendor/Archipelago`).
- The needs calculation assumes a player's logic depends only on their own items, which holds for nearly all games.
- "Any one of" lists are exact. An "all of" set is *a* minimal route, not necessarily the only one.
