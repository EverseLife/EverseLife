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

from src.constants import current, current_catalog
from src.constants import registry as R
from src.engine import bank, chat, energy, events, food, panel, rig, road, wear
from src.engine.jobs import enqueue, handler
from src.models.event import EventKind
from src.models.job import Job, JobKind
from src.telemetry import metrics

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
    #: Живое общение не хранится: буфер доставки подметается каждым тиком.
    swept = await chat.prune(session, now=now)
    #: Станции работают без игроков: пул наполняется временем, а угольная
    #: станция всё это время жжёт привезённый уголь (D-082).
    произведено = await energy.tick_pools(session, current(), now=now)
    #: Буровая тоже не спит: жжёт уголь, наполняет бункер и выедает жилу, пока
    #: хозяин занят другим (D-115). Полный бункер её останавливает.
    добыто = await rig.tick_rigs(session, current(), now=now)
    await events.record(
        session,
        EventKind.TICK_RAN,
        kind_of_tick="world",
        at=now.isoformat(),
        chat_swept=swept,
        energy_produced=произведено,
        rig_mined=добыто,
    )
    #: Сюда придут: износ по времени, истечение ордеров. Партии тика не ждут —
    #: каждая приходит своим заданием на свой срок.
    await schedule_next_tick(session, now)


@handler(JobKind.DAILY_TICK)
async def daily_tick(session: AsyncSession, job: Job) -> None:
    """Суточный тик: налоги, содержание, отчёты, сроки."""
    now = job.run_at
    #: Снаряжение изнашивается от ношения, а не от применения (сток С2, D-129).
    gone = await wear.daily_gear_wear(session, current(), current_catalog())
    #: Протухшее исчезает: порча — честный сток материи (D-119).
    rotten = await food.sweep_spoiled(session, now=now)
    #: Дорога без содержания зарастает и возвращается в бездорожье (D-107).
    заросло = await road.decay(session, current())
    #: Просроченный долг гасится принудительно долей остатка (D-063, D-168).
    удержано = await bank.collect(session, current(), now=now)
    #: Излишек резерва сверх потолка сжигается: второй рычаг банка (D-169).
    сожжено = await bank.sterilize(session, current())
    #: Суточный срез мира. Считает движок, а не дашборд: панель показывает то
    #: же, что игровая сводка города, и второй копии формул быть не должно
    #: (D-139, D-124).
    измерений = await metrics.store(session, current(), now=now)
    #: Срез каждого города — в ту же таблицу: панель, дашборд и проверка
    #: инвариантов считаются одной формулой (D-139, D-140).
    городов = await panel.store_daily(session, current(), now=now)
    await events.record(
        session,
        EventKind.TICK_RAN,
        kind_of_tick="daily",
        at=now.isoformat(),
        gear_worn_out=gone,
        spoiled=rotten,
        roads_decayed=заросло,
        debt_withheld=удержано,
        reserve_burned=сожжено,
        metrics=измерений,
        cities=городов,
    )
    #: Счётчик быта идёт не отсюда: у него свой период (`energy.meter_period`)
    #: и своё задание — сутки планеты и период счётчика совпадать не обязаны.
    #: Сюда придут: содержание построек и торговая сводка города.
    await schedule_next_day(session, now)


async def ensure_scheduled(session: AsyncSession, now: datetime | None = None) -> None:
    """Убедиться, что часы мира идут. Вызывается при старте процесса.

    Счётчик быта (D-149) заводится **отдельно** — `utility.ensure_scheduled`:
    у него свой период, и часы мира не обязаны знать, кто ещё тикает рядом.
    """
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
    #: Пересмотр ключевой ставки идёт своим ритмом (D-167): у денежной
    #: политики свой период, и часы мира не обязаны его знать.
    from src.engine import bank

    await bank.schedule_review(session, current(), after=moment)
