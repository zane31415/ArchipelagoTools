# Async ban tools

Needs Python with PyYAML (`python -m pip install pyyaml`). Run the commands from this folder.

## Each week

1. Add any new apworlds and regenerate the templates. A game with no template is skipped with an alert, and its yaml isn't written out.
2. Triage `provisional` rows in `banned_settings.csv`, changing each to `banned` or `fine`.
3. Filter the new batch. Originals stay untouched, and edited copies go to `FilteredYamls`:

   ```bash
   python ban_tool.py filter NextYamlBatch
   ```

4. Generate and play the run using `FilteredYamls`.
5. When the run is over, record its settings:

   ```bash
   python ban_tool.py ban FilteredYamls
   ```

6. Refresh the checker page's data and publish it (see below):

   ```bash
   python ban_tool.py export
   ```

Clear `FilteredYamls` between runs. Both commands exit with an error code if any yaml was skipped. Options: `--bans`, `--templates`, and `--out` (filter) or `--subsettings` (ban).

## banned_settings.csv

`GAME, SETTING, VALUE, STATE, ORIGINAL, SOURCE`

- **VALUE:** the choice value, or `min` / `max` / `bottom` / `middle` / `top` for ranges. The thirds split the values between min and max, so on 0-100, `bottom` is 1-33, `middle` is 34-66 and `top` is 67-99. Short ranges may not have all three. It's `LIST` / `DICT` / `TEXT` for settings that can't be judged by value.
- **STATE:**
  - `banned`: the filter replaces this value with `random`.
  - `provisional`: the filter flags it and leaves it alone.
  - `fine`: reviewed and allowed. It counts as used up when checking for a fully banned game.
- **ORIGINAL / SOURCE:** what the yaml actually said, and which yaml first used it.

Existing rows are never changed, so your triage is kept.

## subsettings.csv

`GAME, SETTING, SUBSETTING, PARENT, NOTES`

Settings with `SUBSETTING` = `yes` get `provisional` rows instead of `banned` ones. `ban` adds name-based guesses (`death_link_*`, `*trap*`, `*monument*`). Set a wrong guess to `no`, or add rows for settings it missed. `PARENT` is only for your notes. This file only affects future rows, so also correct any rows already in `banned_settings.csv`.

## Checker page (`docs/checker/`)

Players drop their yamls on the page and see which settings would be forced random, what's pending review, and which values are still open. It also has a per-game view of all bans. Yamls are read in the browser and never uploaded.

**Build a yaml** lets players make a yaml for a game that's already been played:
- Each setting offers `random` plus every value that isn't banned. Values marked `fine` are offered, and `provisional` values are marked "pending review".
- For a range, they pick a min, max or third, then type the number.
- The page writes the standard header (`name`, `description`, `game` and the template's `requires`). It adds `progression_balancing`, capped at 50, and `accessibility`.
- List, dict and text settings are left out, so they use their defaults, and the page says which ones.
- Download stays disabled until there's a player name, every number is in range, and at least one setting isn't random. Each result is run back through the checker before it can be downloaded.
- Games with no template, or with every value banned, show a "not valid for auto-generation" note instead.

- `export` writes `docs/checker/ban_data.json`: the ban list, plus template details for each game in it, including the `requires` block and the `accessibility` choices the builder uses. Rerun it after every `ban`, and after triaging provisional rows.
- `docs/checker/checker.js` is a port of the filter's rules. If you change the rules in `ban_tool.py`, change them there too. After any change to it, bump the `?v=` number on its `<script>` tag in `docs/checker/index.html`, or browsers may keep running the cached old copy.

## Publishing (GitHub Pages)

`docs/` is one Pages site holding both tools:

| Folder | What | Updated by |
|---|---|---|
| `docs/index.html` | Landing page linking to both | by hand |
| `docs/checker/` | Setting checker and yaml builder | `python ban_tool.py export` |
| `docs/tracker/` | Sphere Dependency Tracker, static version | `export_bundle.py` (see `Sphere Dependency Tracker/README.md`) |

They're served at `https://<you>.github.io/<repo>/`, `…/checker/` and `…/tracker/`.

**Setup:**
1. This folder is the repository root (https://github.com/zane31415/ArchipelagoTools). `.gitignore` allows only `docs/` and the tool code, so player yamls, the csvs, spoilers, the tracker's `rooms.json` (admin tokens), `.venv` and `vendor/` stay out. A new file that should be published needs its own `!` line there.
2. In the repository's Settings → Pages, choose Deploy from a branch → `main`, `/docs`.

On a free account the repository has to be public, so check `git status` before each commit to make sure nothing private got in.

**Testing locally:** the pages can't load their data from a file opened directly. Serve the folder instead, then open http://localhost:8000:

```bash
python -m http.server 8000 --bind 127.0.0.1 --directory docs
```

## How values are read

- **Never banned:** `accessibility`, `start_inventory`, `local_items` and the other shared item and location options.
- **`progression_balancing`:** never banned, but capped at 50. Anything that could exceed 50 through randomness becomes `random-range-0-50`.
- **Omitted settings:** count as the template default. If that default is banned, `random` is added to the yaml.
- **Random, bans nothing:** plain `random`, equal non-zero weights on every value, or a `random-range` covering the full range.
- **Everything else random-like:** counts as every value it can land on, including unequal weights, `random-low`, `random-high` and narrowed ranges. If any of those values is banned, the whole setting becomes `random`.
- **`NO SPECIFIED SETTINGS REMAIN`:** every counted setting in the yaml ended up random.
