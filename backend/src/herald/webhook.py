"""Sending to Discord: one webhook, one POST, no library.

A webhook is chosen over a bot on purpose. A webhook has no commands, no
permissions, no presence: it can write to one channel and nothing else. A
leaked webhook costs spam in one channel -- a leaked bot token costs the server.

HTTP is taken from the standard library for the same reason the liveness
check in the `Dockerfile` does without curl: no point dragging a client into
the image for one request every two minutes. The synchronous call goes to a
thread so as not to stall the worker loop for the duration of the network.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from collections.abc import Sequence

from src.runtime import DISCORD_CONTENT_LIMIT, HERALD_TIMEOUT

#: Who is knocking. Discord asks callers to introduce themselves, and by this
#: string one can later see whose webhook is making noise.
AGENT = "OctoVerse-Herald (+https://octoverse.world)"


class WebhookError(Exception):
    """Discord did not accept the message. The job will retry in due course."""


def payload(text: str) -> dict[str, object]:
    """The request body.

    `allowed_mentions` is empty not for beauty: names in the game are made up
    by players, and a city may well be called `@everyone`. Without this field
    such a name landing in the chronicle would ping the whole server -- and
    it would be us doing that, not the player.
    """
    return {
        "content": text[:DISCORD_CONTENT_LIMIT],
        "allowed_mentions": {"parse": []},
    }


def chunks(lines: Sequence[str]) -> list[str]:
    """Glue lines into messages without crossing the Discord limit."""
    messages: list[str] = []
    current: list[str] = []
    length = 0
    for line in lines:
        one_ = line[:DISCORD_CONTENT_LIMIT]
        if current and length + len(one_) > DISCORD_CONTENT_LIMIT:
            messages.append("\n".join(current))
            current, length = [], 0
        current.append(one_)
        #: The newline in gluing -- it takes room too.
        length += len(one_) + 1
    if current:
        messages.append("\n".join(current))
    return messages


def _post(url: str, body: dict[str, object], timeout: float) -> None:
    request = urllib.request.Request(  # noqa: S310 -- our own address, from settings
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": AGENT},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:  # noqa: S310
            answer.read()
    except urllib.error.HTTPError as refusal:
        #: 429 goes here too: rate exceeded is an ordinary delivery failure, and
        #: a job retry spaces us from the limit better than sleeping inside a transaction.
        raise WebhookError(f"вебхук ответил {refusal.code}") from refusal
    except OSError as cutoff:
        raise WebhookError(f"вебхук недоступен: {cutoff}") from cutoff


async def send(url: str, text: str, *, timeout: float = HERALD_TIMEOUT) -> None:
    """Send one message. A delivery error is an exception, not silence."""
    await asyncio.to_thread(_post, url, payload(text), timeout)
