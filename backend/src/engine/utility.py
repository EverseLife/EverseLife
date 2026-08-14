"""Счётчик: быт узла и счёт за него (D-135, D-149).

Энергия перестала быть только топливом станка: жильё, склад и мастерская тратят
её просто тем, что существуют. Счёт приходит раз в `energy.meter_period` часов,
и считается он в одну строку:

    энергия = площадь × energy.home_draw_per_m2 × часы
    деньги  = энергия / 100 × тариф города

**Кто платит — решает владелец узла, и других правил нет:**

| Узел | Кто платит |
|---|---|
| занят игроком | владелец |
| принадлежит городу | казна: энергия уходит из пула и не продаётся |
| ничей | никто: счёт выставлять некому, а деньгам исчезать некуда (И2) |

Городская постройка существует ради ВВП города и приносит ему налоги; брать за
неё с случайного посетителя значило бы брать плату дважды. Власть, ставящая
мастерскую, обязана понимать, что содержит её казна, — в этом и состоит
решение (D-149).

**Не заплатил — отключён.** Долг остаётся на узле, станки в нём не работают до
оплаты. Отобрать узел за долг движок не вправе: это решение суда, а не
арифметики.

Вне города счётчика нет вовсе: там нет сети, и работают от аккумулятора.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import energy, events, ledger
from src.engine.jobs import handler
from src.models.city import UtilityMeter
from src.models.event import EventKind
from src.models.identity import Identity
from src.models.job import Job, JobKind
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Node
from src.units import ENERGY_PER_TARIFF_UNIT, SECONDS_PER_HOUR, money, money_str


class UtilityError(Exception):
    pass


class NothingDue(UtilityError):
    """Платить нечего. Не ошибка, но и не действие."""


class NotEnoughMoney(UtilityError):
    """На счету меньше долга. Частичная оплата — тоже оплата, а нуля не бывает."""


async def meter_of(
    session: AsyncSession, node: Node, *, create: bool = True
) -> UtilityMeter | None:
    """Счётчик узла. Заводится только там, где есть кому платить и из чего.

    Условия два и оба необходимы: у узла есть владелец (личность или город) и
    узел стоит в городской сети. Ничей узел счёта не порождает, а вне сети
    быта в этом смысле нет — там аккумулятор.
    """
    if node.owner_identity_id is None and node.owner_city_id is None:
        return None
    if await energy.grid_node(session, node) is None:
        return None

    found = (
        await session.execute(select(UtilityMeter).where(UtilityMeter.node_id == node.id))
    ).scalar_one_or_none()
    if found is not None or not create:
        return found

    meter = UtilityMeter(node_id=node.id)
    session.add(meter)
    await session.flush()
    return meter


async def cut_off(session: AsyncSession, node: Node) -> bool:
    """Отключён ли узел за неуплату. Проверяется перед работой станка."""
    meter = await meter_of(session, node, create=False)
    return meter is not None and meter.cut_off


def draw_for(constants: Constants, node: Node, hours: float) -> float:
    """Сколько энергии съедает быт узла за столько часов.

    Берётся с площади (D-135): свет, тепло и вентиляция считаются метрами, а
    не числом станков внутри.
    """
    return float(node.area_m2) * constants[R.ENERGY_HOME_DRAW_PER_M2] * max(0.0, hours)


async def bill(
    session: AsyncSession,
    constants: Constants,
    node: Node,
    *,
    now: datetime | None = None,
) -> int:
    """Выставить счёт за прошедшее время. Возвращает начисленное деньгами.

    Энергия действительно уходит из пула: счётчик не выдумывает расход, а
    списывает его. Пул пуст — списывается то, что в нём было: город без
    топлива не может отпустить того, чего у него нет.
    """
    moment = now or datetime.now(UTC)
    meter = await meter_of(session, node)
    if meter is None:
        return 0

    часов = (moment - meter.counted_at).total_seconds() / SECONDS_PER_HOUR
    if часов <= 0:
        return 0

    pool = await energy.pool_of(session, constants, node)
    if pool is None:  # pragma: no cover — сеть проверена в meter_of
        return 0
    await energy.produce(session, constants, pool, now=moment)

    надо = draw_for(constants, node, часов)
    отпущено = min(надо, float(pool.stored))
    pool.stored = Decimal(str(float(pool.stored) - отпущено))
    meter.counted_at = moment
    meter.last_energy = Decimal(str(отпущено))

    цена = money(отпущено / ENERGY_PER_TARIFF_UNIT * float(pool.tariff))
    await session.flush()

    #: Городской узел содержит казна: она не платит сама себе деньгами, но
    #: платит энергией, которую могла бы продать (D-149).
    if node.owner_identity_id is None:
        await events.record(
            session,
            EventKind.UTILITY_METERED,
            node_id=node.id,
            energy=отпущено,
            hours=часов,
            at_city_expense=True,
            worth=цена,
        )
        return 0

    if цена <= 0:
        await session.flush()
        return 0

    счёт = await ledger.account_for(
        session, AccountKind.IDENTITY, node.owner_identity_id
    )
    казна = await ledger.account_for(session, AccountKind.CITY_TREASURY, pool.node_id)
    остаток = await ledger.balance(session, счёт.id)

    if остаток >= цена:
        await ledger.transfer(
            session,
            PostingReason.ENERGY_BILL,
            debit=счёт.id,
            credit=казна.id,
            amount=цена,
            memo={"счётчик": node.key, "энергии": отпущено},
        )
        оплачено = цена
        начислено = 0
    else:
        #: Платить нечем — долг ложится на узел, а узел отключается. Списать
        #: «сколько есть» нельзя: полумера оставила бы узел работать бесплатно.
        оплачено = 0
        начислено = цена
        meter.debt += цена
        if not meter.cut_off:
            meter.cut_off = True
            await events.record(
                session,
                EventKind.UTILITY_CUT_OFF,
                actor_identity_id=node.owner_identity_id,
                node_id=node.id,
                debt=meter.debt,
            )
    await session.flush()

    await events.record(
        session,
        EventKind.UTILITY_METERED,
        actor_identity_id=node.owner_identity_id,
        node_id=node.id,
        energy=отпущено,
        hours=часов,
        paid=оплачено,
        debt=начислено,
    )
    return цена


async def pay(
    session: AsyncSession,
    constants: Constants,
    identity: Identity,
    node: Node,
) -> int:
    """Погасить долг узла и включить его обратно. Удалённое: это платёж.

    Платить вправе владелец: чужие счета оплачивает договор, а не движок.
    """
    if node.owner_identity_id != identity.id:
        raise UtilityError("узел не ваш: чужие счета оплачивает договор, а не движок")
    meter = await meter_of(session, node, create=False)
    if meter is None or meter.debt <= 0:
        raise NothingDue("долга нет")

    pool = await energy.pool_of(session, constants, node, create=False)
    if pool is None:  # pragma: no cover — счётчик заводится только в сети
        raise UtilityError("здесь нет городской сети")

    счёт = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
    казна = await ledger.account_for(session, AccountKind.CITY_TREASURY, pool.node_id)
    остаток = await ledger.balance(session, счёт.id)
    if остаток < meter.debt:
        raise NotEnoughMoney(
            f"долг {money_str(meter.debt)} ₭, а на счету {money_str(остаток)} ₭"
        )

    долг = meter.debt
    await ledger.transfer(
        session,
        PostingReason.ENERGY_BILL,
        debit=счёт.id,
        credit=казна.id,
        amount=долг,
        memo={"оплата долга": node.key},
    )
    meter.debt = 0
    meter.cut_off = False
    await session.flush()

    await events.record(
        session,
        EventKind.UTILITY_PAID,
        actor_identity_id=identity.id,
        node_id=node.id,
        paid=долг,
    )
    return долг


async def holdings(
    session: AsyncSession, constants: Constants, identity_id: uuid.UUID
) -> list[dict]:
    """Свои узлы и их счета. Удалённое: хозяйство видно откуда угодно.

    Пустой список — не «панель сломалась», а «владений нет»: клиенту этого
    достаточно, чтобы не показывать раздел вовсе.
    """
    узлы = (
        await session.execute(select(Node).where(Node.owner_identity_id == identity_id))
    ).scalars().all()

    out: list[dict] = []
    for узел in узлы:
        meter = await meter_of(session, узел, create=False)
        #: Сеть — свойство места, а не наличия строки в базе: пул города
        #: заводится при первой надобности, а счета у узла есть с первого дня.
        в_сети = await energy.grid_node(session, узел) is not None
        pool = await energy.pool_of(session, constants, узел, create=False)
        за_период = draw_for(constants, узел, constants[R.ENERGY_METER_PERIOD])
        тариф = (
            float(pool.tariff) if pool is not None else constants[R.ENERGY_TARIFF_DEFAULT]
        )
        out.append(
            {
                "node": узел.key,
                "name": узел.name,
                "area": float(узел.area_m2),
                #: Нет сети — узел живёт от аккумулятора, и коммунальных
                #: отношений у него нет вовсе.
                "grid": в_сети,
                "energy_per_period": round(за_период, 1) if в_сети else 0.0,
                "cost_per_period": (
                    money(за_период / ENERGY_PER_TARIFF_UNIT * тариф) if в_сети else 0
                ),
                "debt": 0 if meter is None else meter.debt,
                "cut_off": bool(meter is not None and meter.cut_off),
                "last_energy": 0.0 if meter is None else float(meter.last_energy),
            }
        )
    return out


async def ensure_meters(session: AsyncSession, constants: Constants) -> int:
    """Завести счётчик каждому занятому узлу в сети. Возвращает число заведённых.

    Узел мог быть занят или выделен между проходами, а счётчик, заводящийся
    только при первом счёте, никогда бы не завёлся: первого счёта неоткуда
    взяться. Условия — те же, что в `meter_of`: владелец и сеть.
    """
    занятые = (
        await session.execute(
            select(Node).where(
                Node.owner_identity_id.is_not(None) | Node.owner_city_id.is_not(None)
            )
        )
    ).scalars().all()
    заведено = 0
    for узел in занятые:
        if await meter_of(session, узел, create=False) is None:
            if await meter_of(session, узел) is not None:
                заведено += 1
    return заведено


async def run_meters(
    session: AsyncSession, constants: Constants, *, now: datetime | None = None
) -> int:
    """Обойти все счётчики мира. Возвращает число выставленных счетов.

    Сначала заводятся недостающие: узел мог быть занят между проходами.
    Дальше обходятся именно счётчики, а не узлы, — их ровно столько, сколько
    в мире мест, где есть кому платить.
    """
    moment = now or datetime.now(UTC)
    await ensure_meters(session, constants)
    meters = (await session.execute(select(UtilityMeter))).scalars().all()
    выставлено = 0
    for meter in meters:
        node = await session.get(Node, meter.node_id)
        if node is None:  # pragma: no cover — счётчик без узла это баг
            continue
        await bill(session, constants, node, now=moment)
        выставлено += 1
    return выставлено


def _period() -> timedelta:
    from src.constants import current

    return timedelta(hours=current()[R.ENERGY_METER_PERIOD])


async def schedule_next(session: AsyncSession, after: datetime) -> None:
    """Поставить следующий проход. Ключ — номер периода, а не время вызова:
    два процесса, решившие поставить счётчик разом, поставят одно задание."""
    from src.engine.jobs import enqueue

    period = _period()
    run_at = after + period
    номер = int(run_at.timestamp() // period.total_seconds())
    await enqueue(
        session,
        JobKind.UTILITY_METER,
        run_at,
        dedup_key=f"utility.meter:{номер}",
    )


async def ensure_scheduled(session: AsyncSession, now: datetime | None = None) -> None:
    """Убедиться, что счётчик тикает. Вызывается вместе с часами мира."""
    from src.engine.jobs import enqueue

    moment = now or datetime.now(UTC)
    period = _period()
    номер = int(moment.timestamp() // period.total_seconds())
    await enqueue(
        session,
        JobKind.UTILITY_METER,
        moment,
        dedup_key=f"utility.meter:{номер}",
    )


@handler(JobKind.UTILITY_METER)
async def meter_tick(session: AsyncSession, job: Job) -> None:
    """Счёт по всем узлам разом и следующий проход через период."""
    from src.constants import current

    выставлено = await run_meters(session, current(), now=job.run_at)
    await events.record(
        session,
        EventKind.UTILITY_METERED,
        kind_of_run="all",
        at=job.run_at.isoformat(),
        meters=выставлено,
    )
    await schedule_next(session, job.run_at)
