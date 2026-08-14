"""Переход между узлами (D-045, D-097, D-107).

Карта — **взвешенный граф**, а не сетка: отсюда узкие места, мосты и перевалы,
за которые стоит драться. Перейти можно только по существующему ребру и только
ногами: телепорта в этом мире нет ни для людей, ни для вещей.

## Откуда взялось время перехода

**Покрытие решает всё.** `road.*_multiplier` заданы как множители времени
относительно дороги-эталона: бездорожье вдвое-втрое дольше, мощёный тракт
быстрее. Значит время перехода — это собственное время ребра, помноженное на
покрытие:

    время = base_seconds × road.<покрытие>_multiplier

Собственное время ребра разыгрывается при появлении карты из `travel.city_step`
внутри города и `travel.inter_node` между узлами — и хранится в секундах, чтобы
шаг по кварталу и переход через степь не жили в разных единицах.

## Дорога стоит выносливости (D-147)

Время — плохая цена: закрыл вкладку и пришёл. Поэтому у перехода есть вторая
цена, и платит её тело:

    расход = travel.stamina_per_hour × часы дороги × сытость

Расход идёт **от времени**, а не от числа переходов: иначе шаг по кварталу
стоил бы столько же, сколько переход через степь, и география вывернулась бы
наизнанку. Число малое — час ходьбы в несколько раз дешевле часа у забоя:
дорога утомляет, но не заменяет работу.

С обозом расход множится на `transport.stamina_k` = 0: везёт транспорт, а не
ноги.

## Обоз меняет и скорость, и саму карту (D-107, D-157)

Впряжённый транспорт (`engine.transport`) делает три вещи разом: везёт груз в
трюме, идёт в `transport.speed_k` раз быстрее пешего — и **сужает граф**.
Бездорожье транспорт не пускает вовсе, тяжёлому нужен мощёный тракт, поэтому
автопуть с обозом строится по проходимым рёбрам, а упершийся в непроходимое
маршрут останавливается на последнем узле — там же, где он останавливается на
нехватке сил и на таможне.

Отсюда следствие, ради которого всё и сделано: **дорога — предусловие
торговли, а не удобство.**

Списывается **вперёд**, как материалы партии: выйти в дорогу, на которую не
хватает сил, нельзя. На автопути это значит, что маршрут обрывается там,
докуда хватило, — тело остаётся в узле, а не падает посреди перегона.

## Граница считается на выходе (D-123)

Пошлина, запрет и беспошлинная норма живут в `engine.customs`, а здесь стоит
единственная точка, где тело меняет город. Считается **до** выхода: не хватило
на пошлину — переход не начинается вовсе, и долга при этом не возникает.

## Пока идёшь — тебя нет

Присутственные действия закрыты все до одного: добыча, крафт, погрузка,
покупка, копирование рецепта. Удалённое (ордера, счёт, переписка) работает —
информация идёт по Сети, материя требует присутствия (D-044, D-047).

Это и есть цена дороги: пока ты в пути, партию выкупят, а цену собьют. Знать
цену — не значит получить товар.
"""

from __future__ import annotations

import heapq
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import events
from src.engine.jobs import enqueue, handler
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.job import Job, JobKind
from src.models.travel import Travel, TravelState
from src.models.world import Edge, Node, Surface
from src.units import SECONDS_PER_HOUR


class TravelError(Exception):
    pass


class NoEdge(TravelError):
    """Ребра нет. По прямой в этом мире не ходят."""


class NoRoute(NoEdge):
    """Пути нет вовсе: узлы не связаны рёбрами даже через другие узлы."""


class InTransit(TravelError):
    """Тело в пути. Материя требует присутствия, а присутствия сейчас нет."""


class AlreadyGoing(TravelError):
    pass


class Imprisoned(TravelError):
    """Заключение: тело держат узлом до срока (D-095, D-166)."""


class NoStrength(TravelError):
    """Сил на дорогу не хватает. Сначала поесть или поспать (D-147)."""


@dataclass(frozen=True, slots=True)
class Exit:
    """Куда отсюда можно и сколько это стоит времени."""

    edge_id: uuid.UUID
    node_id: uuid.UUID
    key: str
    name: str
    surface: Surface
    seconds: float
    #: Состояние покрытия, 0…100 (D-158): дорога без содержания зарастает, и
    #: видеть это игрок обязан заранее — обоз встанет там, где она заросла.
    condition: float


def surface_multiplier(constants: Constants, surface: Surface) -> float:
    """Множитель времени по покрытию. Дорога — эталон (D-107)."""
    if surface is Surface.TRAIL:
        return constants[R.ROAD_TRAIL_MULTIPLIER]
    if surface is Surface.PAVED:
        return constants[R.ROAD_PAVED_MULTIPLIER]
    return constants[R.ROAD_ROAD_MULTIPLIER]


def edge_seconds(constants: Constants, edge: Edge) -> float:
    return edge.base_seconds * surface_multiplier(constants, edge.surface)


async def exits(session: AsyncSession, constants: Constants, node: Node) -> tuple[Exit, ...]:
    """Куда ведут рёбра из узла. Ребро ненаправленное, поэтому смотрим оба конца."""
    rows = (
        (
            await session.execute(
                select(Edge).where(
                    or_(Edge.node_a_id == node.id, Edge.node_b_id == node.id)
                )
            )
        )
        .scalars()
        .all()
    )
    found: list[Exit] = []
    for edge in rows:
        other_id = edge.node_b_id if edge.node_a_id == node.id else edge.node_a_id
        other = await session.get(Node, other_id)
        if other is None:  # pragma: no cover — ребро в никуда это баг
            continue
        found.append(
            Exit(
                edge_id=edge.id,
                node_id=other.id,
                key=other.key,
                name=other.name,
                surface=edge.surface,
                seconds=edge_seconds(constants, edge),
                condition=float(edge.condition),
            )
        )
    return tuple(sorted(found, key=lambda exit: exit.seconds))


async def has_transport(session: AsyncSession, body: Body) -> bool:
    """Везёт ли тело обоз. Транспорт **впряжён**, а не лежит в кармане (D-157).

    Раньше здесь искалась повозка в руках, и это было бессмыслицей: повозка
    тяжелее предела носимого и в руки не берётся вовсе. Тянуть её можно только
    впрягшись, и упряжка — единственный признак, по которому дорога отличает
    возчика от пешего.
    """
    from src.engine import transport

    return await transport.harnessed(session, body) is not None


def stamina_cost(constants: Constants, seconds: float, *, transport: bool) -> float:
    """Во что обойдётся телу дорога такой длины.

    Расход считается от времени, а не от числа переходов (D-147): иначе шаг по
    кварталу стоил бы столько же, сколько переход через степь.
    """
    расход = constants[R.TRAVEL_STAMINA_PER_HOUR] * seconds / SECONDS_PER_HOUR
    if transport:
        расход *= constants[R.TRANSPORT_STAMINA_K]
    return расход


async def current(session: AsyncSession, body: Body) -> Travel | None:
    """Идущий переход этого тела, если он есть."""
    stmt = select(Travel).where(
        Travel.body_id == body.id, Travel.state == TravelState.GOING
    )
    return (await session.execute(stmt)).scalars().first()


class Asleep(TravelError):
    """Тело спит. Та же недоступность, что и дорога, только добровольная."""


class InField(TravelError):
    """Тело в разведке: оно ушло само и вернётся по сроку либо по отмене."""


async def require_here(session: AsyncSession, body: Body) -> None:
    """Проверка присутствия — одна на все присутственные действия.

    Дорога обязана стоить времени по-настоящему: иначе выход из узла становится
    бесплатным, и география, ради которой всё и сделано, исчезает. Сон стоит на
    той же двери: спящий недоступен для всего присутственного (D-091) — этим
    гибернация и платит за восстановление. Разведка стоит на ней же (D-152):
    разведчик уходит сам, и пока он в поле, в узле его нет.
    """
    if body.sleeping_since is not None:
        raise Asleep("тело спит: сначала проснуться")
    going = await current(session, body)
    if going is not None:
        raise InTransit(
            f"тело в пути и придёт в {going.arrives_at.isoformat()}: "
            "материя требует присутствия"
        )
    from src.engine import explore

    заход = await explore.pending(session, body)
    if заход is not None:
        raise InField(
            f"тело в разведке и вернётся в {заход.run_at.isoformat()}: "
            "отменить заход — «вернуться» на карте"
        )


async def route(
    session: AsyncSession,
    constants: Constants,
    from_node_id: uuid.UUID,
    to_node_id: uuid.UUID,
    *,
    vehicle: str | None = None,
) -> list[uuid.UUID]:
    """Кратчайший по времени путь между узлами: список узлов, без начального.

    Автопуть (D-045) — удобство, а не новая физика: маршрут состоит из тех же
    рёбер, идётся тем же временем и может быть пройден руками отрезок за
    отрезком. Дейкстра по секундам с учётом покрытия; граф целиком в памяти —
    он мал, а станет велик, тогда и появится повод для индексов.

    С обозом граф беднее: бездорожье транспорт не пускает вовсе, а тяжёлому
    нужен мощёный тракт (D-107). Маршрут строится по проходимым рёбрам — вести
    возчика в тупик, чтобы там остановиться, незачем.
    """
    from src.engine import transport

    edges = (await session.execute(select(Edge))).scalars().all()
    graph: dict[uuid.UUID, list[tuple[uuid.UUID, float]]] = {}
    for edge in edges:
        if vehicle is not None and not transport.passable(
            constants, edge.surface, vehicle
        ):
            continue
        seconds = edge_seconds(constants, edge)
        if vehicle is not None:
            seconds /= transport.speed(constants, vehicle)
        graph.setdefault(edge.node_a_id, []).append((edge.node_b_id, seconds))
        graph.setdefault(edge.node_b_id, []).append((edge.node_a_id, seconds))

    best: dict[uuid.UUID, float] = {from_node_id: 0.0}
    came: dict[uuid.UUID, uuid.UUID] = {}
    queue: list[tuple[float, bytes]] = [(0.0, from_node_id.bytes)]
    while queue:
        cost, raw = heapq.heappop(queue)
        here = uuid.UUID(bytes=raw)
        if here == to_node_id:
            break
        if cost > best.get(here, float("inf")):
            continue
        for neighbour, seconds in graph.get(here, ()):
            step = cost + seconds
            if step < best.get(neighbour, float("inf")):
                best[neighbour] = step
                came[neighbour] = here
                heapq.heappush(queue, (step, neighbour.bytes))

    if to_node_id not in best:
        raise NoRoute(
            "пути нет вовсе: узлы не связаны рёбрами"
            if vehicle is None
            else "обозу туда дороги нет: бездорожье транспорт не пускает (D-107)"
        )
    path: list[uuid.UUID] = []
    cursor = to_node_id
    while cursor != from_node_id:
        path.append(cursor)
        cursor = came[cursor]
    path.reverse()
    return path


async def depart(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    target: Node,
    *,
    now: datetime | None = None,
    _plan: list[uuid.UUID] | None = None,
) -> Travel:
    """Выйти в узел. В несоседний — автопутём: маршрут строится сам (D-045).

    Дальше переход идёт сам, в том числе офлайн: каждый отрезок — задание
    журнала, и приход отрезка сам выводит тело в следующий.
    """
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        raise TravelError("мёртвое тело никуда не идёт")
    #: Выйти в дорогу — присутственное начало: спящий не идёт, идущий не выходит
    #: дважды. Та же дверь, что и у всех присутственных.
    if body.sleeping_since is not None:
        raise Asleep("тело спит: сначала проснуться")
    if await current(session, body) is not None:
        raise AlreadyGoing("тело уже в пути")
    if target.id == body.node_id:
        raise NoEdge("это тот же узел")

    #: Заключение — принудительное ограничение перемещения узлом (D-095,
    #: D-166). Исполняет его движок, а не стража: приговор не зависит от того,
    #: онлайн ли кто-нибудь.
    from src.engine import justice

    сидит = await justice.imprisoned(session, body.identity_id)
    if сидит is not None:
        raise Imprisoned(
            "заключение: выходить из узла запрещено до "
            + (сидит.until.isoformat() if сидит.until else "решения суда")
        )

    #: Несостоятельность держит в узле так же, но накладывает её не власть, а
    #: банковская система: это физика мира, а не приговор (D-063, D-168).
    from src.engine import bank

    держит = await bank.restrained(session, constants, body.identity_id, now=moment)
    if держит is not None:
        raise Imprisoned(
            "долг не обслуживается: выходить из узла нельзя, пока не "
            "рассчитаетесь. Заплатить за вас вправе кто угодно"
        )

    #: Обоз меняет и скорость, и саму проходимость рёбер (D-107, D-157).
    from src.engine import transport

    обоз = await transport.harnessed(session, body)

    plan = list(_plan or [])
    edge = await _edge_between(session, body.node_id, target.id)
    if edge is None:
        #: Соседнего ребра нет — строим маршрут. Первый отрезок выходится
        #: сейчас, хвост ложится в план и идётся приходами отрезков.
        legs = await route(
            session,
            constants,
            body.node_id,
            target.id,
            vehicle=None if обоз is None else обоз.type_key,
        )
        edge = await _edge_between(session, body.node_id, legs[0])
        assert edge is not None  # noqa: S101 — маршрут состоит из рёбер
        next_node = await session.get(Node, legs[0])
        if next_node is None:  # pragma: no cover — маршрут по живым узлам
            raise TravelError("маршрут ведёт в исчезнувший узел")
        target = next_node
        plan = legs[1:] + plan

    #: Вышел из мастерской — вышел из разговора: кружок не ходит следом (D-043).
    from src.engine import chat

    await chat.leave_groups(session, body.identity_id)

    seconds = edge_seconds(constants, edge)
    if обоз is not None:
        #: Покрытие решает не только время, но и саму возможность проехать.
        if not transport.passable(constants, edge.surface, обоз.type_key):
            raise transport.Impassable(
                f"«{обоз.type_key}» здесь не пройдёт: "
                f"{edge.surface.value} транспорт не пускает. "
                "Распрягитесь либо ищите дорогу (D-107)"
            )
        seconds /= transport.speed(constants, обоз.type_key)

    #: Граница считается **до** выхода: обе стороны уже известны, а платить на
    #: приходе значило бы пускать в город то, за что заплатить нечем (D-123).
    from src.constants import current_catalog
    from src.engine import customs

    откуда_узел = await session.get(Node, body.node_id)
    if откуда_узел is not None:
        await customs.cross(
            session, constants, current_catalog(), body, откуда_узел, target,
            now=moment,
        )

    #: Дорога стоит выносливости, и платится она вперёд (D-147). Сытость
    #: замедляет расход ровно так же, как на работе: обед — это обед.
    from src.engine import food

    расход = stamina_cost(
        constants, seconds, transport=await has_transport(session, body)
    ) * food.drain_multiplier(constants, body, moment)
    if расход > float(body.stamina):
        raise NoStrength(
            f"на дорогу нужно {расход:.1f} выносливости, а есть "
            f"{float(body.stamina):.1f}: сначала поесть или поспать"
        )
    body.stamina = Decimal(str(float(body.stamina) - расход))

    travel = Travel(
        body_id=body.id,
        from_node_id=body.node_id,
        to_node_id=target.id,
        edge_id=edge.id,
        plan=[str(node_id) for node_id in plan] or None,
        arrives_at=moment + timedelta(seconds=seconds),
    )
    session.add(travel)
    await session.flush()

    event = await events.record(
        session,
        EventKind.TRAVEL_STARTED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        travel_id=str(travel.id),
        to_node=target.key,
        seconds=seconds,
        surface=edge.surface.value,
        stamina=расход,
    )
    await enqueue(
        session,
        JobKind.TRAVEL_LEG,
        travel.arrives_at,
        payload={"travel": str(travel.id)},
        dedup_key=f"travel.leg:{travel.id}",
        cause_event_id=event.id,
        body_id=body.id,
    )
    return travel


@handler(JobKind.TRAVEL_LEG)
async def arrive(session: AsyncSession, job: Job) -> None:
    """Пришёл. Тело переезжает в новый узел вместе со всем, что несёт."""
    travel = await session.get(Travel, uuid.UUID(job.payload["travel"]))
    if travel is None:  # pragma: no cover
        raise TravelError(f"задание {job.id}: перехода нет")
    if travel.state is not TravelState.GOING:
        #: Повтор задания после сбоя вторым приходом не станет.
        return

    body = await session.get(Body, travel.body_id)
    target = await session.get(Node, travel.to_node_id)
    if body is None or target is None:  # pragma: no cover
        raise TravelError(f"переход {travel.id} ссылается в никуда")

    #: Инвентарь ехать не нужно: он привязан к телу, а не к месту. Товар,
    #: оставленный в терминале, остаётся там — вещи не ездят за хозяином.
    body.node_id = target.id
    #: Горизонт чата: раньше прихода тело здесь ничего не слышало (D-043).
    body.node_since = job.run_at
    travel.state = TravelState.ARRIVED
    travel.arrived_at = job.run_at
    await session.flush()

    await events.record(
        session,
        EventKind.TRAVEL_ARRIVED,
        actor_identity_id=body.identity_id,
        node_id=target.id,
        travel_id=str(travel.id),
    )

    #: Обоз приехал вместе с телом и на этом отрезке износился (D-157).
    #: Разбитый в ноль встаёт здесь, а груз остаётся лежать в узле.
    from src.constants import current, current_catalog
    from src.engine import transport

    обоз = await transport.harnessed(session, body)
    сломался = False
    if обоз is not None:
        await transport.follow(session, обоз, target)
        сломался = await transport.wear_leg(
            session, current(), current_catalog(), body, обоз, target
        )
        if сломался:
            travel.plan = None
            await session.flush()

    #: Автопуть: приход отрезка сам выводит тело в следующий (D-045). Маршрут
    #: не короче дороги руками — он лишь избавляет от будильника на каждом узле.
    if travel.plan:
        next_node = await session.get(Node, uuid.UUID(travel.plan[0]))
        if next_node is None:  # pragma: no cover — маршрут по живым узлам
            raise TravelError(f"переход {travel.id}: план ведёт в исчезнувший узел")
        rest = [uuid.UUID(raw) for raw in travel.plan[1:]]
        from src.engine import customs

        try:
            await depart(
                session, current(), body, next_node, now=job.run_at, _plan=rest
            )
        except (
            NoStrength,
            customs.CustomsError,
            transport.Impassable,
        ) as остановка:
            #: Маршрут обрывается здесь — сил не хватило (D-147), граница не
            #: пропустила груз (D-123) либо дорога не пускает обоз (D-107).
            #: Тело остаётся в узле, а не падает посреди перегона: дошёл,
            #: докуда пустили, дальше решает игрок.
            await events.record(
                session,
                EventKind.TRAVEL_ARRIVED,
                actor_identity_id=body.identity_id,
                node_id=target.id,
                travel_id=str(travel.id),
                route_stopped=type(остановка).__name__,
                why=str(остановка),
            )


async def connect(
    session: AsyncSession,
    a: Node,
    b: Node,
    *,
    base_seconds: float,
    surface: Surface = Surface.ROAD,
) -> Edge:
    """Связать два узла ребром. Ненаправленным — дорога одинакова в обе стороны."""
    existing = await _edge_between(session, a.id, b.id)
    if existing is not None:
        return existing
    edge = Edge(
        node_a_id=a.id,
        node_b_id=b.id,
        base_seconds=int(base_seconds),
        surface=surface,
    )
    session.add(edge)
    await session.flush()
    return edge


async def _edge_between(
    session: AsyncSession, one: uuid.UUID, other: uuid.UUID
) -> Edge | None:
    stmt = select(Edge).where(
        or_(
            (Edge.node_a_id == one) & (Edge.node_b_id == other),
            (Edge.node_a_id == other) & (Edge.node_b_id == one),
        )
    )
    return (await session.execute(stmt)).scalars().first()


async def neighbours(session: AsyncSession, node: Node) -> Sequence[Node]:  # pragma: no cover
    """Соседи узла — для карты и будущего автопути (D-045)."""
    edges = (
        (
            await session.execute(
                select(Edge).where(or_(Edge.node_a_id == node.id, Edge.node_b_id == node.id))
            )
        )
        .scalars()
        .all()
    )
    out: list[Node] = []
    for edge in edges:
        other_id = edge.node_b_id if edge.node_a_id == node.id else edge.node_a_id
        other = await session.get(Node, other_id)
        if other is not None:
            out.append(other)
    return out
