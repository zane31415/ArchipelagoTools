#!/usr/bin/env python3
"""Setting-ban tools for the Archipelago async.

  python ban_tool.py filter NextYamlBatch     apply the ban list, write edited copies to FilteredYamls
  python ban_tool.py ban FilteredYamls        once the run is over, add that run's settings to the ban list
  python ban_tool.py export                   write docs/checker/ban_data.json for the checker page

See README.md for the rules these implement. Requires PyYAML.
"""
import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

DEFAULT_TEMPLATES = r"C:\ProgramData\Archipelago\Players\Templates"
DEFAULT_BANS = "banned_settings.csv"
DEFAULT_SUBSETTINGS = "subsettings.csv"
DEFAULT_OUT = "FilteredYamls"
DEFAULT_EXPORT = "docs/checker/ban_data.json"

BAN_COLUMNS = ["GAME", "SETTING", "VALUE", "STATE", "ORIGINAL", "SOURCE"]
SUBSETTING_COLUMNS = ["GAME", "SETTING", "SUBSETTING", "PARENT", "NOTES"]

# Options every game has that are never banned. progression_balancing is capped instead.
IGNORED = {
    "accessibility", "progression_balancing", "local_items", "non_local_items",
    "start_inventory", "start_inventory_from_pool", "start_hints", "start_location_hints",
    "exclude_locations", "priority_locations", "item_links", "plando_items",
}
PROGRESSION_CAP = 50
PROGRESSION_ALIASES = {"disabled": 0, "normal": 50, "extreme": 99}

SUBSETTING_NAME = re.compile(r"death_?link_?.+|.*trap.*|.*monument.*", re.IGNORECASE)
RANDOM_RANGE = re.compile(r"random-range-(?:(low|middle|high)-)?(-?\d+)-(-?\d+)$")
TRUE_WORDS = ("true", "on", "yes", "enabled")
FALSE_WORDS = ("false", "off", "no", "disabled")
THIRDS = ("bottom", "middle", "top")


@dataclass
class Option:
    name: str
    kind: str  # choice, range, LIST, DICT or TEXT
    default: object = None
    values: list = field(default_factory=list)  # choice only
    low: int = 0  # range only
    high: int = 0  # range only
    aliases: dict = field(default_factory=dict)  # range only

    @property
    def buckets(self):
        if self.kind == "choice":
            return [str(v).lower() for v in self.values]
        if self.kind == "range":
            inside = buckets_between(self, self.low, self.high)
            return [b for b in ("min", "max") + THIRDS if b in inside]
        return []


@dataclass
class Selection:
    is_random: bool = False
    buckets: set = field(default_factory=set)
    original: str = ""
    warning: str = ""


# --- yaml text helpers -------------------------------------------------------

def read_text(path):
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return handle.read()


def split_documents(text):
    """Split a yaml file into documents, keeping each document's exact text."""
    docs, current = [], []
    for line in text.splitlines(keepends=True):
        if re.match(r"---\s*$", line):
            docs.append(current)
            current = [line]
        else:
            current.append(line)
    docs.append(current)
    return ["".join(d) for d in docs if any(l.strip() and not l.lstrip().startswith("#") for l in d)]


def find_game_section(lines, game):
    """Return (start, end): the game's header line and the line after its section."""
    start = None
    for i, line in enumerate(lines):
        if not line.strip() or line[0] in " #-":
            continue
        if start is not None:
            return start, i
        try:
            parsed = yaml.safe_load(line)
        except yaml.YAMLError:
            continue
        if isinstance(parsed, dict) and game in parsed:
            start = i
    if start is None:
        raise ValueError(f"no section for game {game!r}")
    return start, len(lines)


def option_indent(lines, start, end):
    for line in lines[start + 1:end]:
        if line.strip() and not line.lstrip().startswith("#"):
            return len(line) - len(line.lstrip())
    return 2


def find_option_block(lines, start, end, indent, name):
    """Return (first, last_exclusive) line numbers of an option, or None if it isn't there."""
    key_line = re.compile(r" {%d}(?![ #-])(.+?):(\s|$)" % indent)
    for i in range(start + 1, end):
        match = key_line.match(lines[i])
        if not match or yaml.safe_load(match.group(1)) != name:
            continue
        j = i + 1
        while j < end:
            line = lines[j]
            if line.strip():
                line_indent = len(line) - len(line.lstrip())
                if line_indent < indent or (line_indent == indent and not line.lstrip().startswith("- ")):
                    break
            j += 1
        while j > i + 1 and not lines[j - 1].strip():
            j -= 1
        return i, j
    return None


def set_option(text, game, name, value, note):
    """Set one option to a plain value, leaving the rest of the file's text as it was."""
    lines = text.splitlines(keepends=True)
    start, end = find_game_section(lines, game)
    indent = option_indent(lines, start, end)
    new_line = f"{' ' * indent}{name}: {value}  # ban_tool: {note}\n"
    block = find_option_block(lines, start, end, indent, name)
    if block:
        lines[block[0]:block[1]] = [new_line]
    else:
        last = end
        while last > start + 1 and not lines[last - 1].strip():
            last -= 1
        if not lines[last - 1].endswith("\n"):
            lines[last - 1] += "\n"
        lines.insert(last, new_line)
    return "".join(lines)


# --- templates ---------------------------------------------------------------

def load_template(path):
    text = read_text(path)
    data = yaml.safe_load(text)
    game = data["game"]
    lines = text.splitlines(keepends=True)
    start, end = find_game_section(lines, game)
    indent = option_indent(lines, start, end)
    options = {}
    for name, value in data[game].items():
        block = find_option_block(lines, start, end, indent, name)
        options[name] = classify_option(name, value, lines[block[0]:block[1]])
    return options


def classify_option(name, value, block):
    if isinstance(value, list) or value is None:
        return Option(name, "LIST")
    if not isinstance(value, dict):
        return Option(name, "TEXT")
    if not value:
        return Option(name, "DICT")

    joined = "".join(block)
    low = re.search(r"# Minimum value is (-?\d+)", joined)
    high = re.search(r"# Maximum value is (-?\d+)", joined)
    weights = {key_text(k): w for k, w in value.items()}
    chosen = [k for k, w in weights.items() if w]
    if low and high:
        aliases = {m.group(1): int(m.group(2))
                   for m in re.finditer(r"^\s*(\S+): \d+ # equivalent to (-?\d+)", joined, re.M)}
        return Option(name, "range", default=chosen[0] if chosen else "random",
                      low=int(low.group(1)), high=int(high.group(1)), aliases=aliases)
    if len(value) == 1:
        return Option(name, "TEXT")  # free text is written as "default: 50"
    if len(chosen) == 1 and weights[chosen[0]] == 50 and all(isinstance(w, int) for w in weights.values()):
        return Option(name, "choice", default=chosen[0], values=list(weights))
    return Option(name, "DICT")  # per-item numbers look like weights but aren't


def key_text(key):
    if key is True:
        return "true"
    if key is False:
        return "false"
    return str(key)


class Templates:
    def __init__(self, folder):
        self.paths, self.loaded = {}, {}
        for path in Path(folder).glob("*.yaml"):
            match = re.search(r"^game: *(.+?) *$", read_text(path), re.M)
            if match:
                self.paths[yaml.safe_load(match.group(1))] = path

    def get(self, game):
        if game not in self.paths:
            return None
        if game not in self.loaded:
            self.loaded[game] = load_template(self.paths[game])
        return self.loaded[game]


# --- working out what a yaml selected ----------------------------------------

def selection_of(option, value):
    if value is None:
        value = option.default
        original = f"{value} (default)"
    elif isinstance(value, dict):
        original = ", ".join(f"{key_text(k)}: {w}" for k, w in value.items() if w)
    elif isinstance(value, list):
        return Selection(original=str(value), warning=f"{option.name} is a list, which this setting doesn't take")
    else:
        original = key_text(value)

    weights = value if isinstance(value, dict) else {value: 1}
    weights = {key_text(k).lower(): w for k, w in weights.items() if w}
    if not weights:
        return Selection(buckets=set(option.buckets), original=original,
                         warning=f"{option.name} has no non-zero weight")
    resolver = resolve_choice if option.kind == "choice" else resolve_range
    selection = resolver(option, weights)
    selection.original = original
    return selection


def resolve_choice(option, weights):
    values = {v.lower() for v in option.values}
    if set(weights) == {"random"}:
        return Selection(is_random=True)
    if any(is_random_word(k) and k not in values for k in weights):
        return Selection(buckets=values, warning=f"{option.name} is a weighted random, so it uses every value")
    resolved = {canonical_choice(k, values) for k in weights}
    if resolved == values and len(set(weights.values())) == 1:
        return Selection(is_random=True)  # equal weights on every value is true random
    unknown = sorted(resolved - values)
    warning = f"{option.name} has a value the template doesn't list: {', '.join(unknown)}" if unknown else ""
    return Selection(buckets=resolved, warning=warning)


def is_random_word(value):
    """Archipelago's random keywords. Some games have real values like random_all, so match exactly."""
    return value == "random" or value.startswith("random-")


def canonical_choice(value, values):
    if value in values:
        return value
    for words in (TRUE_WORDS, FALSE_WORDS):
        if value in words:
            return next((w for w in words if w in values), value)
    return value


def resolve_range(option, weights):
    if set(weights) == {"random"}:
        return Selection(is_random=True)
    buckets, warning = set(), ""
    for key in weights:
        match = RANDOM_RANGE.match(key)
        if match:
            weighted, a, b = match.group(1), int(match.group(2)), int(match.group(3))
            a, b = min(a, b), max(a, b)
            if len(weights) == 1 and not weighted and a <= option.low and b >= option.high:
                return Selection(is_random=True)
            buckets |= buckets_between(option, a, b)
            warning = f"{option.name} is a weighted or narrowed random, so it uses every value it can land on"
        elif is_random_word(key):
            buckets |= set(option.buckets)
            warning = f"{option.name} is a weighted random, so it uses every value"
        else:
            number = option.aliases.get(key, parse_int(key))
            if number is None or not option.low <= number <= option.high:
                warning = f"{option.name} value {key} is not valid for {option.low}-{option.high}"
            else:
                buckets |= buckets_between(option, number, number)
    return Selection(buckets=buckets, warning=warning)


def buckets_between(option, a, b):
    buckets = set()
    if a <= option.low <= b:
        buckets.add("min")
    if a <= option.high <= b:
        buckets.add("max")
    first, last = max(a, option.low + 1), min(b, option.high - 1)
    if first <= last:
        buckets.update(THIRDS[third_of(option, first):third_of(option, last) + 1])
    return buckets


def third_of(option, value):
    """Which third a value falls in, counting only the values between min and max."""
    return (value - option.low - 1) * 3 // (option.high - option.low - 1)


def parse_int(text):
    try:
        return int(text)
    except ValueError:
        return None


# --- csv files ---------------------------------------------------------------

def load_csv(path, columns):
    if not Path(path).exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if rows and set(columns) - set(rows[0]):
        sys.exit(f"{path} is missing columns: {', '.join(sorted(set(columns) - set(rows[0])))}")
    return rows


def write_csv(path, columns, rows):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def ban_index(rows):
    """Excel may rewrite true as TRUE, so states and values are compared in lowercase."""
    return {(r["GAME"], r["SETTING"], r["VALUE"].strip().lower()): r["STATE"].strip().lower() for r in rows}


def game_progress(options, index, game):
    """Return (finished settings, counted settings). A value marked fine counts as finished."""
    counted = [o for o in options.values() if o.kind in ("choice", "range") and o.name not in IGNORED]
    finished = sum(all(index.get((game, o.name, b)) in ("banned", "fine") for b in o.buckets) for o in counted)
    return finished, len(counted)


def is_banned_game(options, index, game):
    finished, counted = game_progress(options, index, game)
    return counted > 0 and finished == counted


# --- walking through yamls ---------------------------------------------------

def yaml_files(paths):
    files = []
    for path in map(Path, paths):
        if path.is_dir():
            files += sorted(p for p in path.iterdir() if p.suffix.lower() in (".yaml", ".yml"))
        elif path.is_file():
            files.append(path)
        else:
            sys.exit(f"Not found: {path}")
    return files


def parse_document(document, label, alerts):
    """Return (game, settings), or None after raising an alert."""
    try:
        data = yaml.safe_load(document)
    except yaml.YAMLError as error:
        alerts.append(f"{label}: could not read yaml ({error}). SKIPPED")
        return None
    game = data.get("game") if isinstance(data, dict) else None
    if not isinstance(game, str):
        alerts.append(f"{label}: 'game' is not a single game name. SKIPPED, check by hand")
        return None
    settings = data.get(game)
    if not isinstance(settings, dict):
        alerts.append(f"{label}: has no settings section for {game}. SKIPPED")
        return None
    if data.get("triggers") or settings.get("triggers"):
        alerts.append(f"{label}: uses triggers, which can change settings after this check")
    return game, settings


def report_extra_settings(settings, options, label, warnings):
    for name in settings:
        if name not in options and name != "triggers":
            warnings.append(f"{label}: {name} isn't in the template, so it wasn't checked")


def cap_progression(settings):
    """Return the replacement for progression_balancing, or None if it's within the cap."""
    value = settings.get("progression_balancing")
    if value is None:
        return None
    weights = value if isinstance(value, dict) else {value: 1}
    keys = [key_text(k).lower() for k, w in weights.items() if w]
    has_random, over_cap = False, False
    for key in keys:
        match = RANDOM_RANGE.match(key)
        if match:
            ceiling = max(int(match.group(2)), int(match.group(3)))
            has_random, over_cap = True, over_cap or ceiling > PROGRESSION_CAP
        elif is_random_word(key):
            has_random, over_cap = True, True
        else:
            number = PROGRESSION_ALIASES.get(key, parse_int(key))
            over_cap = over_cap or (number is not None and number > PROGRESSION_CAP)
    if not over_cap:
        return None
    return f"random-range-0-{PROGRESSION_CAP}" if has_random else PROGRESSION_CAP


def filter_document(document, label, templates, index, alerts, warnings, changes):
    """Return the edited document, or None if it can't be used."""
    parsed = parse_document(document, label, alerts)
    if not parsed:
        return None
    game, settings = parsed
    options = templates.get(game)
    if options is None:
        alerts.append(f"{label}: no template for {game}. SKIPPED, add its apworld and regenerate templates")
        return None
    if is_banned_game(options, index, game):
        alerts.append(f"{label}: {game} is a fully banned game")

    report_extra_settings(settings, options, label, warnings)
    capped = cap_progression(settings)
    if capped is not None:
        document = set_option(document, game, "progression_balancing", capped,
                              f"capped at {PROGRESSION_CAP}")
        changes.append(f"{label}: progression_balancing capped: now {capped}")

    specified = 0
    for option in options.values():
        if option.name in IGNORED:
            continue
        if option.kind not in ("choice", "range"):
            state = index.get((game, option.name, option.kind.lower()))
            if state == "provisional" and settings.get(option.name):
                warnings.append(f"{label}: {option.name} ({option.kind}) is provisional, left unchanged")
            elif state == "banned":
                alerts.append(f"{label}: {option.name} ({option.kind}) is marked banned, "
                              "but the tool can't randomize this type. Change it by hand")
            continue

        selection = selection_of(option, settings.get(option.name))
        if selection.is_random:
            continue
        if selection.warning:
            warnings.append(f"{label}: {selection.warning}")
        states = {b: index.get((game, option.name, b)) for b in selection.buckets}
        banned = sorted(b for b, s in states.items() if s == "banned")
        if banned:
            document = set_option(document, game, option.name, "random", f"was {selection.original}")
            changes.append(f"{label}: {option.name} = {selection.original} -> random "
                           f"(banned: {', '.join(banned)})")
            continue
        specified += 1
        for bucket, state in sorted((b, s) for b, s in states.items() if s == "provisional"):
            warnings.append(f"{label}: {option.name} = {selection.original} is provisional ({bucket}), left unchanged")

    if not specified:
        alerts.append(f"{label}: NO SPECIFIED SETTINGS REMAIN. All counted settings are random")
    return document


def run_filter(args):
    templates = Templates(args.templates)
    if not Path(args.bans).exists():
        print(f"No {args.bans} yet, so nothing is banned (fine for the first batch).")
    index = ban_index(load_csv(args.bans, BAN_COLUMNS))
    out = Path(args.out)
    files = yaml_files(args.yamls)
    if any(f.parent.resolve() == out.resolve() for f in files):
        sys.exit(f"The output folder {out} also holds the input yamls. Choose a different --out.")
    out.mkdir(parents=True, exist_ok=True)

    alerts, warnings, changes = [], [], []
    written = 0
    for path in files:
        documents = split_documents(read_text(path))
        results = [filter_document(d, path.name, templates, index, alerts, warnings, changes) for d in documents]
        if any(r is None for r in results):
            continue
        with open(out / path.name, "w", encoding="utf-8", newline="") as handle:
            handle.write("".join(results))
        written += 1

    print_section("Changes", changes)
    print_section("Flags", warnings)
    print_section("ALERTS", alerts)
    print(f"\nWrote {written} of {len(files)} yamls to {out}.")
    return 1 if written < len(files) else 0


def run_ban(args):
    templates = Templates(args.templates)
    rows = load_csv(args.bans, BAN_COLUMNS)
    index = ban_index(rows)
    earlier_runs = dict(index)
    sub_rows = load_csv(args.subsettings, SUBSETTING_COLUMNS)
    subsettings = {(r["GAME"], r["SETTING"]): r["SUBSETTING"].strip().lower() for r in sub_rows}
    alerts, warnings = [], []
    new_banned, new_provisional = 0, 0
    games, files = set(), yaml_files(args.yamls)
    skipped = 0

    def add(game, setting, value, state, original, source):
        nonlocal new_banned, new_provisional
        key = (game, setting, value.lower())
        if key in index:
            return
        index[key] = state
        rows.append(dict(GAME=game, SETTING=setting, VALUE=value, STATE=state,
                         ORIGINAL=original, SOURCE=source))
        new_banned += state == "banned"
        new_provisional += state == "provisional"

    for path in files:
        for document in split_documents(read_text(path)):
            parsed = parse_document(document, path.name, alerts)
            if not parsed:
                skipped += 1
                continue
            game, settings = parsed
            options = templates.get(game)
            if options is None:
                alerts.append(f"{path.name}: no template for {game}. SKIPPED, nothing recorded")
                skipped += 1
                continue
            games.add(game)
            report_extra_settings(settings, options, path.name, warnings)
            for option in options.values():
                if option.name in IGNORED:
                    continue
                if option.kind not in ("choice", "range"):
                    add(game, option.name, option.kind, "provisional", "see yaml", path.name)
                    continue
                selection = selection_of(option, settings.get(option.name))
                if selection.is_random:
                    continue
                if selection.warning:
                    warnings.append(f"{path.name}: {selection.warning}")
                if (game, option.name) not in subsettings and SUBSETTING_NAME.fullmatch(option.name):
                    subsettings[(game, option.name)] = "yes"
                    sub_rows.append(dict(GAME=game, SETTING=option.name, SUBSETTING="yes",
                                         PARENT="", NOTES="guessed from name, please review"))
                state = "provisional" if subsettings.get((game, option.name)) == "yes" else "banned"
                for bucket in sorted(selection.buckets):
                    if earlier_runs.get((game, option.name, bucket)) == "banned":
                        warnings.append(f"{path.name}: {option.name} = {selection.original} was already banned. "
                                        "Was this yaml run through filter?")
                    add(game, option.name, bucket, state, selection.original, path.name)

    write_csv(args.bans, BAN_COLUMNS, rows)
    write_csv(args.subsettings, SUBSETTING_COLUMNS, sub_rows)

    print_section("Flags", warnings)
    print_section("ALERTS", alerts)
    print(f"\nAdded {new_banned} banned and {new_provisional} provisional rows to {args.bans}.")
    print(f"{sum(r['STATE'].strip().lower() == 'provisional' for r in rows)} provisional rows are waiting for review.")
    for game in sorted(games):
        finished, counted = game_progress(templates.get(game), index, game)
        print(f"  {game}: {finished} of {counted} settings used up")
        if counted and finished == counted:
            print(f"  ALERT: {game} IS NOW FULLY BANNED")
    return 1 if skipped else 0


def run_export(args):
    """Write the ban list, plus the template details the checker page needs, to one json file."""
    templates = Templates(args.templates)
    if not Path(args.bans).exists():
        sys.exit(f"{args.bans} not found. Run ban first.")
    games = {}
    for row in load_csv(args.bans, BAN_COLUMNS):
        game = games.setdefault(row["GAME"], {"options": None, "bans": {}})
        game["bans"].setdefault(row["SETTING"], {})[row["VALUE"].strip().lower()] = row["STATE"].strip().lower()
    missing = []
    for name, game in games.items():
        options = templates.get(name)
        if options is None:
            missing.append(name)
            continue
        game["options"] = {o.name: export_option(o) for o in options.values() if o.name not in IGNORED}
        # For the page's yaml builder: the template's header and the one ignored choice it offers.
        game["requires"] = yaml.safe_load(read_text(templates.paths[name])).get("requires")
        if "accessibility" in options and options["accessibility"].kind == "choice":
            game["accessibility"] = export_option(options["accessibility"])

    data = {
        "generated": date.today().isoformat(),
        "progression_cap": PROGRESSION_CAP,
        "progression_aliases": PROGRESSION_ALIASES,
        "ignored": sorted(IGNORED),
        "games": games,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    print(f"Wrote {len(games)} games to {out}.")
    for name in missing:
        print(f"  ALERT: no template for {name}, so the page can't check omitted settings for it")
    return 1 if missing else 0


def export_option(option):
    exported = {"kind": option.kind}
    if option.kind == "choice":
        exported.update(default=option.default, values=option.values)
    elif option.kind == "range":
        exported.update(default=option.default, low=option.low, high=option.high, aliases=option.aliases)
    return exported


def print_section(title, lines):
    if lines:
        print(f"\n{title}:")
        for line in lines:
            print(f"  {line}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    for name, handler, helptext in (
        ("filter", run_filter, "randomize banned settings and write edited copies"),
        ("ban", run_ban, "add the settings used by a finished run to the ban list"),
        ("export", run_export, "write the ban list for the checker page"),
    ):
        command = commands.add_parser(name, help=helptext)
        if name == "export":
            command.add_argument("--out", default=DEFAULT_EXPORT, help=f"output json (default {DEFAULT_EXPORT})")
        else:
            command.add_argument("yamls", nargs="+", help="yaml files and/or folders of yamls")
        command.add_argument("--bans", default=DEFAULT_BANS, help=f"ban list csv (default {DEFAULT_BANS})")
        command.add_argument("--templates", default=DEFAULT_TEMPLATES, help="yaml templates folder")
        command.set_defaults(handler=handler)
        if name == "filter":
            command.add_argument("--out", default=DEFAULT_OUT, help=f"output folder (default {DEFAULT_OUT})")
        elif name == "ban":
            command.add_argument("--subsettings", default=DEFAULT_SUBSETTINGS,
                                 help=f"subsetting mapping csv (default {DEFAULT_SUBSETTINGS})")

    args = parser.parse_args()
    if not Path(args.templates).is_dir():
        sys.exit(f"Templates folder not found: {args.templates}")
    sys.exit(args.handler(args))


if __name__ == "__main__":
    main()
