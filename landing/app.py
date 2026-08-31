# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The everse.life landing page: the page and beta signup intake.

One service for the whole domain: serves the site's pages (see `SITE_PAGES`)
with their shared `site.css`/`site.js`, and accepts `POST /api/signup`.
Every page exists in two languages -- Russian at the root, English under
`/en/` -- and the two are declared to crawlers by `hreflang`, never by a
redirect.
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
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel

DB_PATH = Path(os.environ.get("LANDING_DB", "/data/signups.db"))
ROOT = Path(__file__).parent

#: The site speaks two languages. Russian is the original and stays at the
#: root: those are the addresses search engines already know, and moving them
#: under `/ru/` would be a migration of the whole site that buys nothing.
#: English hangs off `/en/`. Nothing here redirects by `Accept-Language` --
#: an address means the same page for everyone, crawler or not, and the pair
#: is declared instead by `hreflang`, in each page's `<head>` and in the
#: sitemap.
LANGS = ("ru", "en")
DEFAULT_LANG = "ru"

#: Which version answers a reader whose language the site does not have --
#: what `hreflang="x-default"` names. English, not the original: somebody
#: searching from Germany or Brazil reads neither, and of the two they are far
#: likelier to read English. Russian and English readers never reach this at
#: all: `hreflang="ru"` and `hreflang="en"` claim them first, so the choice
#: costs those two audiences nothing.
X_DEFAULT_LANG = "en"

#: The site's pages: one row per page, its URL path in each language. The
#: front page carries the hero and the signup; the rest of the story is split
#: across subpages so the front page stays light. Routes, the sitemap with
#: its alternates, `lastmod.json` and the IndexNow ping are all built from
#: this one table, so a new page -- or a third language -- is added here and
#: nowhere else.
SITE_PAGES = (
    {"ru": "/", "en": "/en/"},
    {"ru": "/gameplay", "en": "/en/gameplay"},
    {"ru": "/world", "en": "/en/world"},
    {"ru": "/alpha", "en": "/en/alpha"},
)


def _page_file(lang: str, path: str) -> Path:
    """The file behind a path: `/gameplay` -> gameplay.html, `/en/` -> en/index.html.

    The folders repeat the shape of the URLs, so there is no second table to
    keep in step with the first: the pages of the default language lie in this
    folder, every other language in the folder its addresses begin with. That
    first segment, and not the language's own tag, is what names the folder --
    the two happen to match for `en`, and a language whose tag is not its
    prefix (`kk` under `/kz/`, `zh-Hans` under `/zh/`) would otherwise be sent
    looking for a folder that does not exist.
    """
    if lang == DEFAULT_LANG:
        return ROOT / f"{path.strip('/') or 'index'}.html"
    prefix, _, rest = path.strip("/").partition("/")
    return ROOT / prefix / f"{rest or 'index'}.html"


#: URL path -> file. `lastmod.py` and `indexnow.py` walk this.
PAGES = {path: _page_file(lang, path) for row in SITE_PAGES for lang, path in row.items()}

#: A page named in the table but missing on disk answers 404 at its own
#: address and takes the whole sitemap down with it (`stat` on a file that is
#: not there). Both are the kind of thing a deploy discovers from a visitor,
#: so the service refuses to start instead: this list is small, and it is read
#: at import anyway.
_missing = [path for path, file in PAGES.items() if not file.is_file()]
if _missing:
    raise RuntimeError(f"pages named in SITE_PAGES but not on disk: {', '.join(_missing)}")

#: URL path -> every language of the same page, its own included. The
#: sitemap's `hreflang` alternates come from here.
ALTERNATES = {path: row for row in SITE_PAGES for path in row.values()}

#: URL path -> the language it is written in, for `Content-Language`.
PAGE_LANG = {path: lang for row in SITE_PAGES for lang, path in row.items()}

#: The pages themselves revalidate every time: without it a browser's
#: heuristic cache may keep showing the previous deploy's page.
PAGE_CACHE = "no-cache"

#: Shared assets, and never `immutable`: the names carry no version, so a
#: year-long pin on `/site.css` would outlive many deploys with no way to call
#: it back. Ten minutes of freshness, then a day of serving the old copy while
#: the new one is fetched behind it. The cost is a deploy taking ten minutes
#: to reach an open tab; what it buys is a round trip saved on every page
#: after the first, which is what hurts when the round trip is to Russia and
#: back. Give the files versioned names and this becomes `immutable`.
ASSET_CACHE = "public, max-age=600, stale-while-revalidate=86400"

#: Self-hosted typefaces (Onest, IBM Plex Mono, Literata -- all OFL), subset to
#: Latin + Cyrillic. Served from our own domain: no CDN, no third-party
#: requests. Only the names listed here are reachable, so a path can never
#: walk out of the folder.
FONTS_DIR = Path(__file__).parent / "fonts"
FONTS = {p.name for p in FONTS_DIR.glob("*.woff2")} if FONTS_DIR.is_dir() else set()
#: The files are immutable per deploy; a new version gets a new name.
FONT_CACHE = "public, max-age=31536000, immutable"

#: The public origin: canonical URL, sitemap and the social cards all hang off it.
SITE = "https://everse.life"

#: The cards a link preview shows, one per language: the headline is drawn
#: into the picture, so a shared English page cannot borrow the Russian one.
#: `og_en.py` draws the English card; the Russian one is by hand.
OG_IMAGES = {
    "/og.png": ROOT / "og.png",
    "/og-en.png": ROOT / "og-en.png",
}

#: IndexNow: Bing and Yandex come to recrawl on notice instead of waiting for
#: a schedule of their own. Ownership is proved by serving the key back at
#: `/{key}.txt`, so the route exists only while the key is set -- an empty
#: setting leaves the landing exactly as it was. Google does not take part:
#: for it there is no way around Search Console. The ping itself is a separate
#: command, `python indexnow.py` in the container -- a deploy is not news, a
#: changed page is.
INDEXNOW_KEY = os.environ.get("LANDING_INDEXNOW_KEY", "")
#: The protocol's own shape for a key: 8..128 of hexadecimal or dash. Checked
#: rather than trusted, because the key becomes a route: anything else here
#: would be somebody's typo turning into a path.
INDEXNOW_KEY_RE = re.compile(r"^[A-Za-z0-9-]{8,128}$")

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
    #: The language of the page the form was sent from, so a refusal comes
    #: back in the language the visitor is reading. Deliberately not
    #: `Accept-Language`: a Russian-locale browser on the English page must be
    #: answered in English, and the page is the one that knows which it is.
    lang: str = DEFAULT_LANG


#: What the intake can refuse with, in the site's languages. An unknown
#: language falls back to the default rather than to a key name.
REFUSALS = {
    "too_often": {
        "ru": "Слишком часто. Подождите минуту.",
        "en": "Too often. Give it a minute.",
    },
    "not_an_email": {
        "ru": "Это не похоже на почту.",
        "en": "That does not look like an email address.",
    },
}


def _refusal(which: str, lang: str) -> str:
    said = REFUSALS[which]
    return said.get(lang, said[DEFAULT_LANG])


def _page_handler(file: Path, lang: str):
    #: `Content-Language` states outright what the page is written in. Google
    #: works that out from the text and from `hreflang` and does not need the
    #: header; Bing and Yandex do read it, and it costs a line. It is not
    #: `Vary: Accept-Language`: nothing here varies by that header, and saying
    #: otherwise would only tell every cache to keep a copy per browser.
    def handler() -> FileResponse:
        return FileResponse(
            file,
            media_type="text/html",
            headers={"Cache-Control": PAGE_CACHE, "Content-Language": lang},
        )

    return handler


#: HEAD as well as GET: link checkers and uptime monitors ask for headers
#: first, and a 405 there reads as a broken site.
for _path, _file in PAGES.items():
    app.api_route(_path, methods=["GET", "HEAD"])(_page_handler(_file, PAGE_LANG[_path]))


def _redirect_handler(target: str):
    def handler() -> RedirectResponse:
        return RedirectResponse(target, status_code=301)

    return handler


#: `/ru/...` is not where the Russian pages live -- they are at the root, and
#: that is deliberate -- but it is the address a person guesses once `/en/`
#: exists, and the one a stray link will use. A permanent redirect answers it
#: without putting a second copy of the page into anybody's index.
for _row in SITE_PAGES:
    _root_path = _row[DEFAULT_LANG]
    _mirror = f"/{DEFAULT_LANG}{_root_path}".rstrip("/")
    app.api_route(_mirror, methods=["GET", "HEAD"])(_redirect_handler(_root_path))

#: A front page's address ends in a slash, and `/en` without it is what gets
#: typed and linked. Starlette would answer that on its own -- with a 307,
#: which says the address may move back tomorrow. It will not, and the
#: neighbouring `/ru` answers 301, so this one says the same.
for _path in PAGES:
    if _path != "/" and _path.endswith("/"):
        app.api_route(_path.rstrip("/"), methods=["GET", "HEAD"])(_redirect_handler(_path))


#: The shared assets every page links: URL path -> (file, media type).
ASSETS = {
    "/site.css": (ROOT / "site.css", "text/css"),
    "/site.js": (ROOT / "site.js", "text/javascript"),
    "/space.js": (ROOT / "space.js", "text/javascript"),
}


def _asset_handler(file: Path, media_type: str):
    def handler() -> FileResponse:
        return FileResponse(file, media_type=media_type, headers={"Cache-Control": ASSET_CACHE})

    return handler


for _path, (_file, _media) in ASSETS.items():
    app.api_route(_path, methods=["GET", "HEAD"])(_asset_handler(_file, _media))


@app.get("/favicon.svg")
def favicon() -> FileResponse:
    return FileResponse(ROOT / "favicon.svg", media_type="image/svg+xml")


@app.get("/fonts/{name}")
def font(name: str) -> Response:
    if name not in FONTS:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    return FileResponse(
        FONTS_DIR / name,
        media_type="font/woff2",
        headers={"Cache-Control": FONT_CACHE},
    )


def _card_handler(file: Path):
    def handler() -> FileResponse:
        #: A day of cache: a card changes only with a deploy.
        return FileResponse(
            file,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    return handler


for _path, _file in OG_IMAGES.items():
    app.api_route(_path, methods=["GET", "HEAD"])(_card_handler(_file))


@app.get("/robots.txt")
def robots() -> Response:
    return Response(
        f"User-agent: *\nAllow: /\n\nSitemap: {SITE}/sitemap.xml\n",
        media_type="text/plain",
    )


if INDEXNOW_KEY_RE.match(INDEXNOW_KEY):
    #: The proof of ownership: the file holds the key and nothing else, and
    #: its own name is that key. Registered only for a well-formed key, so a
    #: mistyped setting cannot open a route with a strange name on the domain.
    @app.api_route(f"/{INDEXNOW_KEY}.txt", methods=["GET", "HEAD"])
    def indexnow_key() -> Response:
        return Response(INDEXNOW_KEY, media_type="text/plain")


def _stamps() -> dict[str, str]:
    """When each page's content last changed, by URL path.

    Kept by `lastmod.py` beside the pages, because mtime here is the deploy's
    own timestamp: a checkout rewrites every file, and a sitemap that calls
    every page fresh after a one-line CSS fix teaches search engines to
    ignore the field. Missing or unreadable, the sitemap falls back to mtime --
    a stale date is better than no sitemap.
    """
    file = ROOT / "lastmod.json"
    if not file.is_file():
        return {}
    try:
        kept = json.loads(file.read_text(encoding="utf-8"))
        return {path: one["date"] for path, one in kept.items() if "date" in one}
    except (json.JSONDecodeError, AttributeError, TypeError):
        return {}


@app.get("/sitemap.xml")
def sitemap() -> Response:
    #: One entry per page in each language; lastmod is the day that page's
    #: content last changed. Every entry also names the other languages of
    #: the same page: without the annotation a crawler reads two
    #: translations as two pages competing for one query, instead of one
    #: page it should show to each visitor in their own language. The
    #: annotation has to be reciprocal -- every version lists all of them,
    #: itself included -- which is why the same block goes under both
    #: addresses.
    stamps = _stamps()
    entries = []
    for path, file in PAGES.items():
        #: mtime is the fallback for a page the stamps do not know, so it is
        #: read only for such a page: eight `stat` calls per request, to answer
        #: a question that is almost never asked, is a poor trade.
        stamp = stamps.get(path) or (
            datetime.fromtimestamp(file.stat().st_mtime, UTC).date().isoformat()
        )
        alternates = ALTERNATES[path]
        links = "".join(
            f'    <xhtml:link rel="alternate" hreflang="{lang}" href="{SITE}{other}"/>\n'
            for lang, other in alternates.items()
        )
        #: x-default: the address for a reader whose language the site does
        #: not have at all.
        links += (
            '    <xhtml:link rel="alternate" hreflang="x-default"'
            f' href="{SITE}{alternates[X_DEFAULT_LANG]}"/>\n'
        )
        entries.append(
            f"  <url>\n"
            f"    <loc>{SITE}{path}</loc>\n"
            f"    <lastmod>{stamp}</lastmod>\n"
            f"{links}"
            f"  </url>\n"
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'
        ' xmlns:xhtml="http://www.w3.org/1999/xhtml">\n' + "".join(entries) + "</urlset>\n"
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
        return JSONResponse(
            {"ok": False, "error": _refusal("too_often", body.lang)}, status_code=429
        )

    email = body.email.strip().lower()
    if len(email) > EMAIL_MAX or not EMAIL.match(email):
        return JSONResponse(
            {"ok": False, "error": _refusal("not_an_email", body.lang)}, status_code=422
        )

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
