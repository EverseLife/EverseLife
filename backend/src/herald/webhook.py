"""Отправка в Discord: один вебхук, один POST, никакой библиотеки.

Вебхук выбран вместо бота намеренно. У вебхука нет ни команд, ни прав, ни
присутствия: он умеет писать в один канал и больше ничего. Утечка вебхука
стоит спама в одном канале — утечка токена бота стоит сервера.

HTTP берётся из стандартной библиотеки по той же причине, по которой проверка
живости в `Dockerfile` обходится без curl: тащить в образ клиент ради одного
запроса раз в две минуты незачем. Синхронный вызов уходит в поток, чтобы не
останавливать цикл воркера на время сети.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from collections.abc import Sequence

from src.runtime import DISCORD_CONTENT_LIMIT, HERALD_TIMEOUT

#: Кто стучится. Discord просит представляться, и по этой строке потом видно,
#: чей именно вебхук шумит.
AGENT = "OctoVerse-Herald (+https://octoverse.world)"


class WebhookError(Exception):
    """Discord не принял сообщение. Задание повторится своим чередом."""


def payload(text: str) -> dict[str, object]:
    """Тело запроса.

    `allowed_mentions` пуст не для красоты: имена в игре придумывают игроки, и
    город вполне можно назвать `@everyone`. Без этого поля такое имя, попав в
    хронику, дёрнуло бы весь сервер — а сделал бы это не игрок, а мы сами.
    """
    return {
        "content": text[:DISCORD_CONTENT_LIMIT],
        "allowed_mentions": {"parse": []},
    }


def chunks(lines: Sequence[str]) -> list[str]:
    """Склеить строки в сообщения, не переступая предел Discord."""
    messages: list[str] = []
    current: list[str] = []
    length = 0
    for line in lines:
        одна = line[:DISCORD_CONTENT_LIMIT]
        if current and length + len(одна) > DISCORD_CONTENT_LIMIT:
            messages.append("\n".join(current))
            current, length = [], 0
        current.append(одна)
        #: Перевод строки при склейке — он тоже занимает место.
        length += len(одна) + 1
    if current:
        messages.append("\n".join(current))
    return messages


def _post(url: str, body: dict[str, object], timeout: float) -> None:
    request = urllib.request.Request(  # noqa: S310 — адрес свой, из настроек
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": AGENT},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:  # noqa: S310
            answer.read()
    except urllib.error.HTTPError as отказ:
        #: 429 сюда же: перебор частоты — обычный сбой доставки, и повтор
        #: задания разведёт нас с пределом лучше, чем сон внутри транзакции.
        raise WebhookError(f"вебхук ответил {отказ.code}") from отказ
    except OSError as обрыв:
        raise WebhookError(f"вебхук недоступен: {обрыв}") from обрыв


async def send(url: str, text: str, *, timeout: float = HERALD_TIMEOUT) -> None:
    """Отправить одно сообщение. Ошибка доставки — исключение, а не молчание."""
    await asyncio.to_thread(_post, url, payload(text), timeout)
