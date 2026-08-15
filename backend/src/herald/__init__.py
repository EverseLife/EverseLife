"""Глашатай: хроника мира наружу, в Discord.

Мост односторонний, и это не упущение. API действий не существует
(60-meta/01-anti-cheat), поэтому Discord умеет ровно одно — узнавать. Ни одной
команды внутрь отсюда не появится: игрок действует только присутственно и
только через сессию клиента.

Что уходит наружу и почему именно это — `chronicle.py`; как уходит —
`webhook.py`; когда — `job.py`. Настройка одна: `OCTOVERSE_DISCORD_WEBHOOK`.
Порядок включения описан в `community/discord-bridge.md`.
"""

from __future__ import annotations

#: Импорт задания — он же регистрация его обработчика: `require_handlers()`
#: обязан находить глашатая при старте, а не в тике посреди ночи.
from src.herald import chronicle, webhook
from src.herald.job import ensure_scheduled, post, run_once

__all__ = [
    "chronicle",
    "ensure_scheduled",
    "post",
    "run_once",
    "webhook",
]
