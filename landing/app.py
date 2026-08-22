# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The everse.life landing page: the page and beta signup intake.

One service for the whole domain: serves `index.html` and accepts `POST /api/signup`.
A signup is an email in SQLite (`/data/signups.db`, a compose volume): its own
small database, so as not to let the landing into the game world. Export --
`python export.py` in the container.

The reply to a signup is always `{"ok": true}`, even on a repeat: somebody
else's email must not let you check whether its owner is subscribed. Bots are
filtered by the honeypot field `website` and a per-address rate limit.
"""

import json
import os
import re
import sqlite3
import threading
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

DB_PATH = Path(os.environ.get("LANDING_DB", "/data/signups.db"))
INDEX = Path(__file__).parent / "index.html"

#: Self-hosted typefaces (Onest, IBM Plex Mono, Literata -- all OFL), subset to
#: Latin + Cyrillic. Served from our own domain: no CDN, no third-party
#: requests. Only the names listed here are reachable, so a path can never
#: walk out of the folder.
FONTS_DIR = Path(__file__).parent / "fonts"
FONTS = {p.name for p in FONTS_DIR.glob("*.woff2")} if FONTS_DIR.is_dir() else set()
#: The files are immutable per deploy; a new version gets a new name.
FONT_CACHE = "public, max-age=31536000, immutable"

#: The public origin: canonical URL, sitemap and the social card all hang off it.
SITE = "https://everse.life"
OG_IMAGE = Path(__file__).parent / "og.png"

#: Email: non-empty before @, non-empty after, a dot in the domain. Stricter is
#: not needed -- the real check happens when a letter goes to the address.
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
EMAIL_MAX = 254

#: Rate limit: this many signups from one address per window. Lives in process
#: memory -- enough, the landing has one process.
RATE_WINDOW_SECONDS = 60.0
RATE_MAX_PER_WINDOW = 5

#: The Discord service-channel webhook. Empty -- the landing is silent.
DISCORD_WEBHOOK = os.environ.get("LANDING_DISCORD_WEBHOOK", "")
NOTIFY_TIMEOUT = 10.0

app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

_rate: dict[str, list[float]] = {}
_rate_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS signups (
            id INTEGER PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            ip TEXT
        )
        """
    )
    return conn


def _client_ip(request: Request) -> str:
    # Only Caddy faces outside, and it sets X-Forwarded-For.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def _rate_limited(ip: str) -> bool:
    now = time.monotonic()
    with _rate_lock:
        stamps = [t for t in _rate.get(ip, []) if now - t < RATE_WINDOW_SECONDS]
        if len(stamps) >= RATE_MAX_PER_WINDOW:
            _rate[ip] = stamps
            return True
        stamps.append(now)
        _rate[ip] = stamps
        return False


def _notify(total: int) -> None:
    """Tell the Discord service channel that there are more signups.

    There is and will be no address here. A signup is somebody's email, and
    outsiders see the channel: only the counter goes out, and the addresses
    themselves are fetched from the server by export (`export.py`). A silent
    webhook does not drop the signup -- by this moment it is already recorded.
    """
    if not DISCORD_WEBHOOK:
        return
    body = json.dumps(
        {
            "content": f"📨 Заявка на бету. Всего: {total}.",
            "allowed_mentions": {"parse": []},
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        DISCORD_WEBHOOK,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "Everse-Life-Landing"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=NOTIFY_TIMEOUT) as answer:
            answer.read()
    except OSError:
        pass


class Signup(BaseModel):
    email: str
    #: Honeypot: the field is hidden on the page, a human will not fill it.
    website: str = ""


#: HEAD as well as GET: link checkers and uptime monitors ask for headers
#: first, and a 405 there reads as a broken site.
@app.api_route("/", methods=["GET", "HEAD"])
def index() -> FileResponse:
    return FileResponse(INDEX, media_type="text/html")


@app.get("/favicon.svg")
def favicon() -> FileResponse:
    return FileResponse(INDEX.parent / "favicon.svg", media_type="image/svg+xml")


@app.get("/fonts/{name}")
def font(name: str) -> Response:
    if name not in FONTS:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    return FileResponse(
        FONTS_DIR / name,
        media_type="font/woff2",
        headers={"Cache-Control": FONT_CACHE},
    )


@app.get("/og.png")
def og_image() -> FileResponse:
    #: The social card. A day of cache: it changes only with a deploy.
    return FileResponse(
        OG_IMAGE,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/robots.txt")
def robots() -> Response:
    return Response(
        f"User-agent: *\nAllow: /\n\nSitemap: {SITE}/sitemap.xml\n",
        media_type="text/plain",
    )


@app.get("/sitemap.xml")
def sitemap() -> Response:
    #: One page, so the sitemap is one entry; lastmod follows the deployed file.
    stamp = datetime.fromtimestamp(INDEX.stat().st_mtime, UTC).date().isoformat()
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"  <url><loc>{SITE}/</loc><lastmod>{stamp}</lastmod></url>\n"
        "</urlset>\n"
    )
    return Response(xml, media_type="application/xml")


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/api/signup")
def signup(body: Signup, request: Request) -> JSONResponse:
    if body.website:
        # A bot filled the honeypot: agree and record nothing.
        return JSONResponse({"ok": True})

    ip = _client_ip(request)
    if _rate_limited(ip):
        return JSONResponse({"ok": False, "error": "Слишком часто. Подождите минуту."}, status_code=429)

    email = body.email.strip().lower()
    if len(email) > EMAIL_MAX or not EMAIL.match(email):
        return JSONResponse({"ok": False, "error": "Это не похоже на почту."}, status_code=422)

    conn = _connect()
    try:
        with conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO signups (email, created_at, ip) VALUES (?, ?, ?)",
                (email, datetime.now(UTC).isoformat(timespec="seconds"), ip),
            )
            #: A repeat of the same email is not news: we count and report only
            #: a genuinely new record.
            new_one = cursor.rowcount == 1
            in_total = conn.execute("SELECT COUNT(*) FROM signups").fetchone()[0]
    finally:
        conn.close()

    if new_one:
        #: In a separate thread: the applicant's reply must not wait for somebody's network.
        threading.Thread(target=_notify, args=(in_total,), daemon=True).start()
    return JSONResponse({"ok": True})
