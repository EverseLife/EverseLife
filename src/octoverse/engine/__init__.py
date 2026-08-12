"""Движок: то, что меняет мир.

Правило слоя: любое изменение состояния идёт через функции этого пакета, а не
через прямые запросы из API. Причина в журнале — событие и его последствия
обязаны фиксироваться вместе (01-tech-notes).
"""

from __future__ import annotations

from octoverse.engine import events, jobs, ledger, tick, world

__all__ = ["events", "jobs", "ledger", "tick", "world"]
