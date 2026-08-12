"""Тик мира.

Мир живёт без игроков. Тик — не «крон, дёргающий функцию», а обычное задание
журнала, которое **ставит следующее себя же** и потому не может ни потеряться,
ни удвоиться.

Два ритма (20-systems/01-time-model):

* **тик** — `time.tick` минут: продвижение длительных действий, порча, износ
  по времени, срабатывание таймеров;
* **суточный тик** — налоги, содержание построек, отчёты, сроки.

Обработчики ниже пока пустые там, где системы ещё нет. Это сознательно: каркас
времени обязан работать раньше, чем появится хоть одна механика, — на нём
держатся партии, караваны, рост и суточные списания (07-implementation-map).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from octoverse.constants import current
from octoverse.constants import registry as R
from octoverse.engine import events
from octoverse.engine.jobs import enqueue, handler
from octoverse.models.event import EventKind
from octoverse.models.job import Job, JobKind

log = logging.getLogger(__name__)


def tick_period() -> timedelta:
    """Длина тика. Число — из констант (D-065), не из кода."""
    return timedelta(minutes=current()[R.TIME_TICK])


def _slot(moment: datetime, period: timedelta) -> int:
    """Номер интервала от эпохи — он же ключ идемпотентности.

    Два процесса, одновременно решившие поставить тик, поставят задание с
    одним ключом, и оно будет одно.
    """
    return int(moment.timestamp() // period.total_seconds())


async def schedule_next_tick(session: AsyncSession, after: datetime) -> None:
    period = tick_period()
    run_at = after + period
    await enqueue(
        session,
        JobKind.WORLD_TICK,
        run_at,
        dedup_key=f"world.tick:{_slot(run_at, period)}",
    )


async def schedule_next_day(session: AsyncSession, after: datetime) -> None:
    period = timedelta(days=1)
    run_at = after + period
    await enqueue(
        session,
        JobKind.DAILY_TICK,
        run_at,
        dedup_key=f"world.daily:{_slot(run_at, period)}",
    )


@handler(JobKind.WORLD_TICK)
async def world_tick(session: AsyncSession, job: Job) -> None:
    """Обычный тик. Всё, что он делает, делается в его транзакции."""
    now = job.run_at
    await events.record(session, EventKind.TICK_RAN, kind_of_tick="world", at=now.isoformat())
    #: Сюда придут: продвижение партий, порча еды, износ по времени,
    #: истечение ордеров. Каждая система добавляет свой шаг сама.
    await schedule_next_tick(session, now)


@handler(JobKind.DAILY_TICK)
async def daily_tick(session: AsyncSession, job: Job) -> None:
    """Суточный тик: налоги, содержание, отчёты, сроки."""
    now = job.run_at
    await events.record(session, EventKind.TICK_RAN, kind_of_tick="daily", at=now.isoformat())
    #: Сюда придут: содержание построек, счётчики энергии, торговая сводка,
    #: суточные агрегаты метрик.
    await schedule_next_day(session, now)


@handler(JobKind.CRAFT_BATCH)
async def craft_batch(session: AsyncSession, job: Job) -> None:  # pragma: no cover
    raise NotImplementedError("партия крафта приезжает вместе с крафтом (Э1)")


@handler(JobKind.TRAVEL_LEG)
async def travel_leg(session: AsyncSession, job: Job) -> None:  # pragma: no cover
    raise NotImplementedError("переход по ребру приезжает с картой (Э2)")


@handler(JobKind.MARKET_ORDER_EXPIRY)
async def market_order_expiry(session: AsyncSession, job: Job) -> None:  # pragma: no cover
    raise NotImplementedError("срок жизни ордера приезжает со стаканом (Э1)")


@handler(JobKind.SPOILAGE)
async def spoilage(session: AsyncSession, job: Job) -> None:  # pragma: no cover
    raise NotImplementedError("порча приезжает с едой (Э2)")


async def ensure_scheduled(session: AsyncSession, now: datetime | None = None) -> None:
    """Убедиться, что часы мира идут. Вызывается при старте процесса."""
    moment = now or datetime.now(UTC)
    await enqueue(
        session,
        JobKind.WORLD_TICK,
        moment,
        dedup_key=f"world.tick:{_slot(moment, tick_period())}",
    )
    await enqueue(
        session,
        JobKind.DAILY_TICK,
        moment,
        dedup_key=f"world.daily:{_slot(moment, timedelta(days=1))}",
    )
