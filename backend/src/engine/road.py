"""Дороги: покрытие как работа на ребре (D-107, D-158).

Разведка растит карту (D-156), обоз возит по ней груз (D-157) — и на стыке
обнаруживается дыра: до найденного узла ведёт бездорожье, а бездорожье
транспорт не пускает вовсе. Карта росла **непроезжей**, и превратить тропу в
дорогу было нечем.

## Укладка

Тот, кто стоит в одном из концов ребра, тратит `road.surface_per_edge`
дорожного полотна и `road.build_hours` времени, и покрытие поднимается **на
ступень**:

    бездорожье → дорога → мощёный тракт

Каждая ступень — отдельный проект и отдельные сорок единиц полотна. Работа идёт
заданием журнала, как всякая длительная: полотно списывается сразу, дорога
ложится по сроку, и закрытая вкладка ей не мешает.

## Зарастание

У покрытия есть состояние 0…100. Оно падает на `road.decay_rate` в сутки, и на
нуле покрытие опускается на ступень: тракт становится дорогой, дорога —
тропой. Заброшенная дорога возвращается в бездорожье примерно за сто суток.

**Подсыпка** поднимает состояние обратно и стоит полотна ровно в той доле, в
какой дорога просела: провалившаяся наполовину требует половины укладки.

## Почему на ребре, а не на узле

Дорога-постройка на узле сделала бы связность свойством точки, и география
свелась бы к «развитым» и «неразвитым» местам. Дорога на ребре — это отношение
между двумя местами: за него можно драться, его можно перерезать, и оно
достаётся тому, кто вложился в **направление**, а не в точку.

## Чего здесь нет

**Владения ребром и пошлины.** `road.toll_max` в вольте есть, но брать плату
за проезд некому: у дороги нет хозяина, а заводить его молча — значит решить
за гейм-дизайн, кому достаётся общая работа. Ждёт своего решения (D-107).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import events, world
from src.engine.jobs import enqueue, handler
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Item
from src.models.job import Job, JobKind, JobState
from src.models.world import Edge, Node, Surface
from src.units import AMOUNT_SCALE, SCALE_MAX, SCALE_MIN, amount, amount_float

#: Расходник вольта, из которого делается покрытие (D-107).
SURFACE_GOODS = "Дорожное полотно"

#: Ступени покрытия снизу вверх. Порядок — это и есть лестница укладки.
LADDER = (Surface.TRAIL, Surface.ROAD, Surface.PAVED)


class RoadError(Exception):
    pass


class NotHere(RoadError):
    """Ребро не отсюда. Дорогу кладут ногами, стоя в одном из её концов."""


class TopSurface(RoadError):
    """Выше тракта покрытия нет: лестница кончилась."""


class NoSurfaceGoods(RoadError):
    """Полотна не хватает. Дорога — это материалы, а не намерение."""


class AlreadyWorking(RoadError):
    """На этом ребре уже идёт работа. Две бригады одну дорогу не кладут."""


def next_step(surface: Surface) -> Surface:
    """Следующая ступень покрытия. Тракт — потолок."""
    место = LADDER.index(surface)
    if место + 1 >= len(LADDER):
        raise TopSurface("мощёный тракт — верх лестницы: выше класть нечего")
    return LADDER[место + 1]


def lower_step(surface: Surface) -> Surface | None:
    """Ступень вниз: заросшая дорога. Ниже бездорожья ничего нет."""
    место = LADDER.index(surface)
    return LADDER[место - 1] if место > 0 else None


async def pending(session: AsyncSession, edge: Edge) -> Job | None:
    """Идущая работа на этом ребре, если она есть."""
    return (
        await session.execute(
            select(Job).where(
                Job.kind == JobKind.ROAD_WORK.value,
                Job.state == JobState.PENDING,
                Job.payload["edge"].astext == str(edge.id),
            )
        )
    ).scalars().first()


def needed(constants: Constants, edge: Edge, *, mend: bool) -> float:
    """Сколько полотна возьмёт работа: укладка — полную норму, подсыпка — долю.

    Провалившаяся наполовину дорога требует половины укладки: платить за
    содержание как за стройку значило бы сделать содержание невыгодным всегда.
    """
    норма = constants[R.ROAD_SURFACE_PER_EDGE]
    if not mend:
        return норма
    просело = (SCALE_MAX - float(edge.condition)) / SCALE_MAX
    return норма * просело


async def lay(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    edge: Edge,
    *,
    mend: bool = False,
    now: datetime | None = None,
) -> Job:
    """Уложить ступень покрытия либо подсыпать просевшую дорогу.

    Полотно списывается вперёд, как материалы партии: работа, на которую не
    хватило материала, не начинается вовсе.
    """
    from src.engine import travel

    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        raise RoadError("мёртвое тело дорог не кладёт")
    await travel.require_here(session, body)

    if body.node_id not in (edge.node_a_id, edge.node_b_id):
        raise NotHere("дорогу кладут стоя в одном из концов ребра")
    if mend:
        if float(edge.condition) >= SCALE_MAX:
            raise RoadError("дорога цела: подсыпать нечего")
        if edge.surface is Surface.TRAIL:
            raise RoadError("бездорожью подсыпать нечего: сначала уложить дорогу")
        цель = edge.surface
    else:
        цель = next_step(edge.surface)
    if await pending(session, edge) is not None:
        raise AlreadyWorking("на этом ребре уже идёт работа: дождитесь конца")

    #: Проверка идёт до списания: отказ не должен съедать половину полотна.
    нужно = needed(constants, edge, mend=mend)
    в_руках = await _surface_at_hand(session, body)
    if в_руках + _EPS < нужно:
        raise NoSurfaceGoods(
            f"нужно {нужно:.0f} «{SURFACE_GOODS}», а в руках {в_руках:.0f}: "
            "дорога — это материалы, а не намерение"
        )
    списано = await _take_surface(session, body, нужно)

    готово = moment + timedelta(hours=constants[R.ROAD_BUILD_HOURS])
    event = await events.record(
        session,
        EventKind.ROAD_WORK_STARTED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        edge_id=str(edge.id),
        surface=цель.value,
        mend=mend,
        spent=списано,
        ready_at=готово.isoformat(),
    )
    job = await enqueue(
        session,
        JobKind.ROAD_WORK,
        готово,
        payload={"edge": str(edge.id), "surface": цель.value, "mend": mend},
        dedup_key=f"road.work:{edge.id}:{event.id}",
        cause_event_id=event.id,
        body_id=body.id,
    )
    if job is None:  # pragma: no cover — ключ уникален по событию
        raise AlreadyWorking("работа уже поставлена")
    return job


@handler(JobKind.ROAD_WORK)
async def finished(session: AsyncSession, job: Job) -> None:
    """Работа окончена: покрытие поднялось, состояние — как новое."""
    edge = await session.get(Edge, uuid.UUID(job.payload["edge"]))
    if edge is None:  # pragma: no cover — ребро вечно, как и карта
        raise RoadError(f"задание {job.id}: ребра нет")

    было = edge.surface
    edge.surface = Surface(job.payload["surface"])
    edge.condition = Decimal(str(SCALE_MAX))
    await session.flush()

    await events.record(
        session,
        EventKind.ROAD_LAID,
        edge_id=str(edge.id),
        was=было.value,
        surface=edge.surface.value,
        mend=bool(job.payload.get("mend")),
    )


async def decay(session: AsyncSession, constants: Constants) -> int:
    """Суточное зарастание. Возвращает число рёбер, потерявших ступень.

    Дорога, которой не занимаются, возвращается в бездорожье примерно за сто
    суток. Это тот самый постоянный сток материалов, ради которого содержание
    вообще существует (D-107).
    """
    рёбра = (
        await session.execute(select(Edge).where(Edge.surface != Surface.TRAIL))
    ).scalars().all()

    шаг = constants[R.ROAD_DECAY_RATE]
    заросло = 0
    for ребро in рёбра:
        осталось = float(ребро.condition) - шаг
        if осталось > SCALE_MIN:
            ребро.condition = Decimal(str(осталось))
            continue
        ниже = lower_step(ребро.surface)
        if ниже is None:  # pragma: no cover — тропа отобрана запросом
            continue
        было = ребро.surface
        ребро.surface = ниже
        #: Просевшее покрытие обнажает то, что под ним: новая ступень начинает
        #: со свежего состояния, а не с нуля — иначе дорога сыпалась бы до
        #: бездорожья за двое суток.
        ребро.condition = Decimal(str(SCALE_MAX))
        заросло += 1
        await events.record(
            session,
            EventKind.ROAD_DECAYED,
            edge_id=str(ребро.id),
            was=было.value,
            surface=ниже.value,
        )
    await session.flush()
    return заросло


async def view(
    session: AsyncSession, constants: Constants, body: Body
) -> list[dict]:
    """Рёбра из этого узла глазами клиента: что уложено и что можно уложить."""
    from src.engine import travel

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover — тело всегда стоит в узле
        return []
    рёбра = (
        await session.execute(
            select(Edge).where(
                or_(Edge.node_a_id == node.id, Edge.node_b_id == node.id)
            )
        )
    ).scalars().all()

    в_руках = await _surface_at_hand(session, body)
    итог: list[dict] = []
    for ребро in рёбра:
        другой = await session.get(
            Node, ребро.node_b_id if ребро.node_a_id == node.id else ребро.node_a_id
        )
        if другой is None:  # pragma: no cover — ребро в никуда это баг
            continue
        try:
            дальше: str | None = next_step(ребро.surface).value
            нужно: float | None = needed(constants, ребро, mend=False)
        except TopSurface:
            дальше, нужно = None, None
        подсыпать = (
            None
            if ребро.surface is Surface.TRAIL or float(ребро.condition) >= SCALE_MAX
            else needed(constants, ребро, mend=True)
        )
        итог.append(
            {
                "edge": str(ребро.id),
                "to": другой.name,
                "surface": ребро.surface.value,
                "condition": float(ребро.condition),
                "seconds": round(travel.edge_seconds(constants, ребро)),
                "next": дальше,
                "needs": нужно,
                "mend_needs": подсыпать,
                "at_hand": в_руках,
                "working": await pending(session, ребро) is not None,
            }
        )
    return sorted(итог, key=lambda путь: путь["to"])


#: Полотно дробится на тысячные, как всякое сырьё: сравнение «хватило ли»
#: обязано терпеть последний разряд, иначе ровно сорок единиц окажутся
#: недостаточными из-за представления.
_EPS = 1 / AMOUNT_SCALE


async def _surface_at_hand(session: AsyncSession, body: Body) -> float:
    карман = await world.body_container(session, body)
    стопки = (
        await session.execute(
            select(Item).where(
                Item.container_id == карман.id, Item.type_key == SURFACE_GOODS
            )
        )
    ).scalars().all()
    return sum(amount_float(стопка.amount) for стопка in стопки)


async def _take_surface(session: AsyncSession, body: Body, нужно: float) -> float:
    """Списать полотно из рук. Возвращает, сколько удалось взять."""
    карман = await world.body_container(session, body)
    стопки = (
        await session.execute(
            select(Item).where(
                Item.container_id == карман.id, Item.type_key == SURFACE_GOODS
            )
        )
    ).scalars().all()
    осталось = amount(нужно)
    взято = 0
    for стопка in стопки:
        if осталось <= 0:
            break
        сколько = min(осталось, стопка.amount)
        стопка.amount -= сколько
        взято += сколько
        осталось -= сколько
        if стопка.amount <= 0:
            await session.delete(стопка)
    await session.flush()
    return amount_float(взято)
