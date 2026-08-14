"""Движок: то, что меняет мир.

Правило слоя: любое изменение состояния идёт через функции этого пакета, а не
через прямые запросы из API. Причина в журнале — событие и его последствия
обязаны фиксироваться вместе (01-tech-notes).
"""

from __future__ import annotations

#: Импорт модуля — он же регистрация его обработчиков заданий, поэтому пакет
#: втягивает их все: обработчик, которого нет, обязан обнаружиться при старте
#: (`require_handlers`), а не в тике посреди ночи.
from src.engine import (
    bank,
    chat,
    city,
    craft,
    customs,
    death,
    estate,
    events,
    explore,
    farm,
    food,
    jobs,
    justice,
    ledger,
    market,
    mining,
    panel,
    pow,
    rest,
    road,
    station,
    tick,
    transport,
    travel,
    utility,
    vote,
    wear,
    world,
)

__all__ = [
    "bank",
    "chat",
    "city",
    "craft",
    "customs",
    "death",
    "estate",
    "events",
    "explore",
    "farm",
    "food",
    "jobs",
    "justice",
    "ledger",
    "market",
    "mining",
    "panel",
    "pow",
    "rest",
    "road",
    "station",
    "tick",
    "transport",
    "travel",
    "utility",
    "vote",
    "wear",
    "world",
]
