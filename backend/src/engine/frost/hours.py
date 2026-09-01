# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The world's own hours: the tick that brings the cold to every body doing
nothing, keeps one thing of its own -- the death -- and burns the
braziers' fuel.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import stock
from src.engine.frost._base import BRAZIER, HEAT, _planet_marks, climate_of
from src.engine.frost.body import _advance, _lock, _on_the_road, limit_of
from src.engine.frost.warmth import _class_names, is_warm
from src.models.identity import Body, BodyState
from src.models.inventory import Container, ContainerKind, Item
from src.models.world import Node
from src.units import amount, amount_float

# --- the world's own hours ----------------------------------------------------


async def tick_bodies(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    *,
    now: datetime | None = None,
) -> int:
    """Settle every body standing on a planet with a climate; kill the ones the
    cold has run out of. Returns how many died.

    The world does not wait for a login. A night in the frost is exactly the
    mistake Aurora kills for (10-world/05), and there is no offline mercy: one
    world for everybody, and hibernation restores less than the cold takes.
    """
    moment = now or datetime.now(UTC)
    weather = await _planet_marks(session)
    if not weather:
        return 0
    bodies = (
        (
            await session.execute(
                select(Body)
                .join(Node, Node.id == Body.node_id)
                .where(
                    Body.state == BodyState.ALIVE,
                    Node.planet.in_([planet.value for planet in weather]),
                )
            )
        )
        .scalars()
        .all()
    )
    #: Warmth is a property of the node, and bodies stand in the same few nodes:
    #: asked once per node for the whole pass, not once per body. Kept in a
    #: local rather than in `remember`, which every write throws away.
    warm_here: dict[uuid.UUID, bool] = {}
    dead = 0
    for found in bodies:
        if await _burn(session, constants, catalog, found, now=moment, warm_here=warm_here):
            dead += 1
    return dead


async def _burn(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    *,
    now: datetime,
    warm_here: dict[uuid.UUID, bool],
) -> bool:
    """One body's stretch of cold, and its end if it has come.

    The arithmetic and the stamina are `_advance`'s, on the locked row -- the
    tick brings the world's own hours to a body that is doing nothing, and it
    keeps one thing of its own: **the death**. Dying is not something to do to a
    body in the middle of somebody's command, and a minute's delay changes
    nothing for a body that has already spent its last strength in the frost.
    """
    locked = await _lock(session, body)
    node = await session.get(Node, locked.node_id)
    if node is None:  # pragma: no cover -- a body without a node is a bug
        return False
    if node.id not in warm_here:
        warm_here[node.id] = await is_warm(session, constants, node)
    warm = warm_here[node.id] and not await _on_the_road(session, locked)
    ceiling = await limit_of(session, constants, catalog, locked)

    spell = await _advance(session, constants, catalog, locked, now=now, warm=warm, ceiling=ceiling)
    #: Death is the pair: no strength left, and still in the cold. Warm again
    #: and empty is a body that must eat and sleep, not a corpse.
    if spell.left > 0 or float(locked.stamina) > 0:
        return False

    from src.engine import death  # noqa: PLC0415 -- lazy: breaks the cycle with death

    await death.die(
        session,
        constants,
        locked,
        #: The climate key itself: the journal's `cause` is a payload key
        #: (D-251), and the two climates already have their names.
        cause="heat" if await climate_of(session, node) == HEAT else "cold",
        now=now,
    )
    return True


async def tick_fires(session: AsyncSession, constants: Constants, *, hours: float) -> float:
    """Braziers burn what lies with them. Returns how much fuel went up.

    Counted by the tick's own period, the way wear and roads are: a fire is not
    a machine with a meter, and a brazier that burned a minute less because a
    tick was late is nobody's loss. No fuel in the node -- nothing burns, and
    the node goes cold by itself with no second rule for it.

    A brazier standing where a plant already heats burns all the same: there is
    no switch on a fire, and one left in a fuel store is the owner's mistake,
    not the world's arithmetic.
    """
    if hours <= 0:  # pragma: no cover -- the tick period is never zero
        return 0.0
    fuels: dict[str, float] = constants[R.ENERGY_FUEL_ENERGY]
    if not fuels:  # pragma: no cover -- the vault always names a fuel
        return 0.0
    weather = await _planet_marks(session)
    if not weather:
        return 0.0
    #: Only where a fire is a mechanic at all. A brazier standing in a Terran
    #: yard next to the coal pile must not quietly eat the city's fuel: on a
    #: planet without a climate nobody lights one, and nothing burns.
    yards = (
        select(Container.id)
        .join(Node, Node.id == Container.owner_id)
        .where(
            Container.kind == ContainerKind.NODE,
            Node.planet.in_([planet.value for planet in weather]),
        )
    )
    #: City by city and, inside a city, node by node -- exactly the way the
    #: energy step walks (`energy.tick_pools` by pool node, `produce` by node
    #: id). Both steps lock the fuel lying in a yard and run in the same tick,
    #: and two orders over one set of stacks are a deadlock waiting for a busy
    #: world. Yards outside any city come last: no pool reaches them, so the
    #: energy step never touches them at all.
    braziers = (
        await session.execute(
            select(Container.id, func.sum(Item.amount))
            .join(Item, Item.container_id == Container.id)
            .join(Node, Node.id == Container.owner_id)
            .where(
                Item.type_key.in_(tuple(_class_names(BRAZIER))),
                Container.id.in_(yards),
            )
            .group_by(Container.id, Node.id)
            .order_by(Node.parent_id.nulls_last(), Node.id)
        )
    ).all()
    burnt = 0.0
    for container_id, fires in braziers:
        stacks = await stock.locked_stacks(session, container_id, fuels)
        if not stacks:
            continue
        #: Every fire eats: two braziers in a yard burn twice the fuel, and
        #: they fold into one stack when nobody has touched them (D-214).
        need = constants[R.FROST_BRAZIER_FUEL_DRAW] * hours * amount_float(int(fires))
        burnt += amount_float(await stock.consume(session, stacks, amount(need)))
    return burnt
