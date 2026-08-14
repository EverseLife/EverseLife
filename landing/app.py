"""Лендинг octoverse.world: страница и приём заявок на бету.

Одна служба на весь домен: отдаёт `index.html` и принимает `POST /api/signup`.
Заявка — почта в SQLite (`/data/signups.db`, том состава): своя маленькая база,
чтобы не пускать лендинг в мир игры. Выгрузка — `python export.py` в контейнере.

Ответ на заявку всегда `{"ok": true}`, даже на повтор: чужой почтой нельзя
проверить, подписан ли её хозяин. Роботов отсеивают приманка-поле `website`
и ограничение частоты с адреса.
"""

import os
import re
import sqlite3
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

DB_PATH = Path(os.environ.get("LANDING_DB", "/data/signups.db"))
INDEX = Path(__file__).parent / "index.html"

#: Почта: непустое до @, непустое после, точка в домене. Строже не нужно —
#: настоящая проверка случится, когда на адрес уйдёт письмо.
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
EMAIL_MAX = 254

#: Ограничение частоты: столько заявок с одного адреса за окно. Живёт в памяти
#: процесса — этого достаточно, у лендинга один процесс.
RATE_WINDOW_SECONDS = 60.0
RATE_MAX_PER_WINDOW = 5

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
    # Наружу смотрит только Caddy, он же ставит X-Forwarded-For.
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


class Signup(BaseModel):
    email: str
    #: Приманка: поле скрыто на странице, человек его не заполнит.
    website: str = ""


@app.get("/")
def index() -> FileResponse:
    return FileResponse(INDEX, media_type="text/html")


@app.get("/favicon.svg")
def favicon() -> FileResponse:
    return FileResponse(INDEX.parent / "favicon.svg", media_type="image/svg+xml")


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/api/signup")
def signup(body: Signup, request: Request) -> JSONResponse:
    if body.website:
        # Робот заполнил приманку: соглашаемся и ничего не записываем.
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
            conn.execute(
                "INSERT OR IGNORE INTO signups (email, created_at, ip) VALUES (?, ?, ?)",
                (email, datetime.now(UTC).isoformat(timespec="seconds"), ip),
            )
    finally:
        conn.close()
    return JSONResponse({"ok": True})
