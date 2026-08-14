"""Гибернация: сон восстанавливает выносливость офлайн (D-091).

Выносливость — единственный ресурс тела, и тратится она работой. Восстановление
задано вольтом двумя числами: `body.hibernation_rate` единиц в час и множитель
`body.hibernation_home_k`, если спишь дома. «Дома» здесь означает кровать в
локации — своей постройки ещё нет (Э3), и пока кровать и есть весь дом.

Начисление идёт **при пробуждении**, по фактически проспанному времени: сон —
длительное действие, он продолжается, пока игрок офлайн, и никакого тика ему не
нужно. Спящее тело недоступно для всего присутственного — этим сон и платит:
проспал — партию выкупили.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import events, travel, world
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Item
from src.units import SECONDS_PER_HOUR

#: Имя кровати в `build/recipes.json`.
BED = "Кровать"


class RestError(Exception):
    pass


class NotTired(RestError):
    """Выносливость полная: ложиться незачем, и сервер не даст спать впрок."""


class NotSleeping(RestError):
    pass


async def sleep(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    *,
    now: datetime | None = None,
) -> Body:
    """Лечь. Присутственное: спят телом, а не распоряжением."""
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        raise RestError("мёртвое тело не спит — оно мертво")
    #: Спящему `require_here` откажет сам: сон стоит на той же двери, что дорога.
    await travel.require_here(session, body)
    if float(body.stamina) >= constants[R.BODY_STAMINA_MAX]:
        raise NotTired("выносливость полная: ложиться незачем")

    body.sleeping_since = moment
    body.sleeping_home = await _bed_here(session, body)
    await session.flush()

    await events.record(
        session,
        EventKind.BODY_SLEPT,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        home=body.sleeping_home,
    )
    return body


async def wake(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    *,
    now: datetime | None = None,
) -> float:
    """Проснуться. Возвращает, сколько выносливости вернул сон.

    Начисление по фактическому времени: `body.hibernation_rate` в час, дома —
    в `body.hibernation_home_k` раз быстрее. Потолок — `body.stamina_max`:
    спать впрок нельзя.
    """
    moment = now or datetime.now(UTC)
    if body.sleeping_since is None:
        raise NotSleeping("тело не спит")

    hours = max(0.0, (moment - body.sleeping_since).total_seconds() / SECONDS_PER_HOUR)
    rate = constants[R.BODY_HIBERNATION_RATE]
    if body.sleeping_home:
        rate *= constants[R.BODY_HIBERNATION_HOME_K]

    cap = constants[R.BODY_STAMINA_MAX]
    before = float(body.stamina)
    after = min(cap, before + hours * rate)

    #: Точность хранения задаёт колонка (Numeric 6,2), а не округление в коде.
    body.stamina = Decimal(str(after))
    body.sleeping_since = None
    body.sleeping_home = False
    await session.flush()

    await events.record(
        session,
        EventKind.BODY_WOKE,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        hours=hours,
        restored=after - before,
    )
    return after - before


async def _bed_here(session: AsyncSession, body: Body) -> bool:
    """Есть ли в локации кровать. Пока нет своих построек — кровать и есть дом."""
    from src.models.world import Node

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        return False
    where = await world.node_container(session, node)
    found = await session.scalar(
        select(Item.id)
        .where(Item.container_id == where.id, Item.type_key == BED)
        .limit(1)
    )
    return found is not None
