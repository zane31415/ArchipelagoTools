"""Parse an Archipelago spoiler log (header, Locations, Playthrough)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# "Location Name (Player): Item Name (Player)" -- names may themselves contain parens/colons,
# so anchor on the known player names instead of a generic regex.


@dataclass
class Spoiler:
    version: str
    seed: int
    players: dict[int, tuple[str, str]] = field(default_factory=dict)  # slot -> (name, game)
    placements: dict[tuple[str, str], tuple[str, str]] = field(default_factory=dict)  # (loc, owner) -> (item, receiver)
    spheres: list[list[tuple[str, str, str, str]]] = field(default_factory=list)  # [(loc, owner, item, receiver)]


def _split_entry(line: str, names: list[str]) -> tuple[str, str, str, str] | None:
    """Split 'Loc (P1): Item (P2)' using known player names (longest first)."""
    for owner in names:
        tag = f" ({owner}): "
        idx = line.find(tag)
        if idx == -1:
            continue
        loc = line[:idx]
        rest = line[idx + len(tag):]
        for recv in names:
            if rest.endswith(f" ({recv})"):
                return loc, owner, rest[: -len(recv) - 3], recv
    return None


def parse(path: Path) -> Spoiler:
    text = path.read_text(encoding="utf-8-sig").splitlines()
    m = re.match(r"Archipelago Version (\S+)\s+-\s+Seed: (\d+)", text[0])
    sp = Spoiler(version=m.group(1), seed=int(m.group(2)))

    i = 0
    while i < len(text):
        pm = re.match(r"Player (\d+): (.+)$", text[i])
        if pm:
            game = text[i + 1].split(":", 1)[1].strip()
            sp.players[int(pm.group(1))] = (pm.group(2), game)
        if text[i] == "Locations:":
            break
        i += 1

    names = sorted((n for n, _ in sp.players.values()), key=len, reverse=True)

    i += 1
    while i < len(text) and text[i] != "Playthrough:":
        if text[i].strip():
            e = _split_entry(text[i], names)
            if e:
                sp.placements[(e[0], e[1])] = (e[2], e[3])
        i += 1

    i += 1
    current: list | None = None
    while i < len(text) and text[i] != "Paths:":
        line = text[i]
        if re.match(r"^\d+: \{$", line):
            current = []
        elif line == "}":
            sp.spheres.append(current or [])
            current = None
        elif current is not None and line.strip():
            e = _split_entry(line.strip(), names)
            if e:
                current.append(e)
        i += 1
    return sp
