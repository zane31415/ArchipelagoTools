"""Web server: serves the tracker page + JSON API, polls each room's tracker in the background."""
from __future__ import annotations

import asyncio
import hmac
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .db import DB
from .rooms import Room, load_configs

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
MANUAL_REFRESH_COOLDOWN = 60
VIEWER_TIMEOUT = 300  # stop polling AP this long after the last open page checked in

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("sdt")

rooms: dict[str, Room] = {}


async def _room_worker(room: Room):
    """Poll archipelago.gg only while someone has the page open (and visible)."""
    await asyncio.to_thread(room.load)
    while True:
        await asyncio.sleep(5)
        t = time.time()
        if room.phase != "ready" or t - room.last_viewed > VIEWER_TIMEOUT:
            continue
        if t - room.last_attempt < room.cfg.poll_seconds:
            continue
        try:
            await asyncio.to_thread(room.refresh)
        except Exception as e:
            room.error = f"Last refresh failed: {type(e).__name__}: {e}"
            log.warning("[%s] refresh failed: %s", room.cfg.slug, e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = DB(ROOT / "data" / "sdt.sqlite")
    tasks = []
    for cfg in load_configs(ROOT / "rooms.json"):
        rooms[cfg.slug] = Room(cfg, db)

    async def load_all():
        # Regenerate rooms one at a time: AP generation uses process-global state.
        for room in rooms.values():
            tasks.append(asyncio.create_task(_room_worker(room)))
            while room.phase in ("queued", "regenerating"):
                await asyncio.sleep(0.5)

    starter = asyncio.create_task(load_all())
    yield
    starter.cancel()
    for t in tasks:
        t.cancel()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=WEB), name="static")


def _room(slug: str) -> Room:
    room = rooms.get(slug)
    if not room:
        raise HTTPException(404, "No such room")
    return room


def _is_admin(room: Room, token: str | None) -> bool:
    return bool(token) and hmac.compare_digest(token, room.cfg.admin_token)


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/r/{slug}")
@app.get("/r/{slug}/admin/{token}")
def room_page(slug: str, token: str | None = None):
    room = _room(slug)
    if token is not None and not _is_admin(room, token):
        raise HTTPException(403, "Bad admin token")
    return FileResponse(WEB / "room.html")


@app.get("/api/rooms")
def list_rooms():
    return [{"slug": r.cfg.slug, "title": r.cfg.title, "phase": r.phase} for r in rooms.values()]


@app.get("/api/r/{slug}/state")
def room_state(slug: str, request: Request):
    room = _room(slug)
    room.last_viewed = time.time()
    admin = _is_admin(room, request.headers.get("x-admin-token"))
    view = room.full_view if admin else room.public_view
    return JSONResponse({
        "phase": room.phase, "error": room.error, "title": room.cfg.title,
        "view": view, "admin": admin,
    })


@app.post("/api/r/{slug}/refresh")
async def manual_refresh(slug: str):
    room = _room(slug)
    if room.phase != "ready":
        raise HTTPException(409, "Room is not ready yet")
    if time.time() - room.last_attempt < MANUAL_REFRESH_COOLDOWN:
        raise HTTPException(429, f"Refreshed less than {MANUAL_REFRESH_COOLDOWN}s ago")
    await asyncio.to_thread(room.refresh)
    return {"ok": True}
