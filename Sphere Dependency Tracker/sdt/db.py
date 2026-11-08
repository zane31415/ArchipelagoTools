"""Tiny SQLite store for what the tracker observes across polls (BK / receive timing)."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("DROP TABLE IF EXISTS hint_needs")  # removed "I need X" feature
            # What we've observed per player across polls; the AP API has no history of its own.
            # We only poll while someone is watching, so a change is known to lie in a window:
            # (*_from = previous poll, *_at = the poll that noticed it].
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS player_tracking (
                    room TEXT NOT NULL,
                    slot INTEGER NOT NULL,
                    first_seen REAL NOT NULL,
                    last_poll REAL NOT NULL,
                    bk INTEGER NOT NULL,
                    bk_changed_from REAL,
                    bk_changed_at REAL,
                    received INTEGER NOT NULL,
                    last_received_from REAL,
                    last_received_at REAL,
                    PRIMARY KEY (room, slot)
                )""")
            self._conn.commit()

    def update_tracking(self, room: str, now: float, players: dict[int, tuple[bool, int]]) -> dict[int, dict]:
        """players: slot -> (is_bk, items_received). Returns the updated tracking row per slot."""
        out = {}
        with self._lock:
            rows = {r[0]: r for r in self._conn.execute(
                "SELECT slot, first_seen, last_poll, bk, bk_changed_from, bk_changed_at, received, "
                "last_received_from, last_received_at FROM player_tracking WHERE room = ?", (room,))}
            for slot, (bk, received) in players.items():
                if slot not in rows:
                    first_seen = now
                    bk_from = bk_at = recv_from = recv_at = None
                else:
                    (_, first_seen, last_poll, prev_bk, bk_from, bk_at,
                     prev_received, recv_from, recv_at) = rows[slot]
                    if bool(prev_bk) != bk:
                        bk_from, bk_at = last_poll, now
                    if received > prev_received:
                        recv_from, recv_at = last_poll, now
                self._conn.execute(
                    "INSERT OR REPLACE INTO player_tracking VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (room, slot, first_seen, now, int(bk), bk_from, bk_at, received, recv_from, recv_at))
                out[slot] = {"first_seen": first_seen, "bk_changed_from": bk_from, "bk_changed_at": bk_at,
                             "last_received_from": recv_from, "last_received_at": recv_at}
            self._conn.commit()
        return out

