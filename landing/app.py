"""Лендинг octoverse.world: страница и приём заявок на бету.

Одна служба на весь домен: отдаёт `index.html` и принимает `POST /api/signup`.
Заявка — почта в SQLite (`/data/signups.db`, том состава): своя маленькая база,
чтобы не пускать лендинг в мир игры. Выгрузка — `python export.py` в контейнере.

Ответ на заявку всегда `{"ok": true}`, даже на повтор: чужой почтой нельзя
проверить, подписан ли её хозяин. Роботов отсеивают приманка-поле `website`
и ограничение частоты с адреса.
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

#: Вебхук служебного канала в Discord. Пусто — лендинг молчит.
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


def _notify(total: int) -> None:
    """Сказать в служебный канал Discord, что заявок стало больше.

    Адреса здесь нет и не будет. Заявка — чужая почта, а канал видят
    посторонние: наружу идёт только счётчик, а сами адреса забираются с
    сервера выгрузкой (`export.py`). Молчащий вебхук заявку не роняет —
    она к этому моменту уже записана.
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
        headers={"Content-Type": "application/json", "User-Agent": "OctoVerse-Landing"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=NOTIFY_TIMEOUT) as answer:
            answer.read()
    except OSError:
        pass


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
            курсор = conn.execute(
                "INSERT OR IGNORE INTO signups (email, created_at, ip) VALUES (?, ?, ?)",
                (email, datetime.now(UTC).isoformat(timespec="seconds"), ip),
            )
            #: Повтор той же почты не новость: считаем и сообщаем только про
            #: действительно новую запись.
            новая = курсор.rowcount == 1
            всего = conn.execute("SELECT COUNT(*) FROM signups").fetchone()[0]
    finally:
        conn.close()

    if новая:
        #: Отдельным потоком: ответ заявителю не должен ждать чужой сети.
        threading.Thread(target=_notify, args=(всего,), daemon=True).start()
    return JSONResponse({"ok": True})
