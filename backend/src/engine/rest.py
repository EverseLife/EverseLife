"""Hibernation: sleep restores stamina offline (D-091).

Stamina is the body's only resource, and it is spent by work. Recovery is set
by the vault with two numbers: `body.hibernation_rate` units per hour and the
multiplier `body.hibernation_home_k` if you sleep at home. "At home" here
means a bed in the location -- there are no own buildings yet (E3), and for
now the bed is the whole home.

Crediting happens **on waking**, by the time actually slept: sleep is a
long-running action, it continues while the player is offline, and it needs
no tick. A sleeping body is unavailable for everything in-person -- that is
how sleep pays: overslept -- the lot got bought.
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

#: The bed's name in `build/recipes.json`.
BED = "Кровать"


class RestError(Exception):
    pass


class NotTired(RestError):
    """Stamina is full: no reason to lie down, and the server will not let you sleep in advance."""


class NotSleeping(RestError):
    pass


async def sleep(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    *,
    now: datetime | None = None,
) -> Body:
    """Lie down. In person: one sleeps with the body, not by order."""
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        raise RestError("мёртвое тело не спит — оно мертво")
    #: `require_here` will refuse a sleeper itself: sleep stands at the same door as the road.
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
    """Wake up. Returns how much stamina the sleep restored.

    Credited by actual time: `body.hibernation_rate` per hour, at home
    `body.hibernation_home_k` times faster. The ceiling is `body.stamina_max`:
    no sleeping in advance.
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

    #: Storage precision is set by the column (Numeric 6,2), not by rounding in code.
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
    """Whether there is a bed in the location. Until own buildings exist, the bed is the home."""
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
