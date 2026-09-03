# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

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

Sleep is an **occupation** (D-211), and one body does one of those at a time:
one does not lie down in the middle of a search or with a plot under the
plough, and the refusal says which. A batch is the single thing sleep neither
refuses nor is refused by -- lying down freezes it and the machine goes free,
the same as walking out of the node does (D-209); waking sets it going again.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_FLOOR, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import craft, events, occupation, travel, world
from src.engine.errors import Refusal
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Item
from src.models.world import Node
from src.units import ROUND_REMAINDER, ROUND_STAMINA, SECONDS_PER_HOUR, on_grid

#: The bed thing class (D-215): any furniture of this class grants hibernation.
BED = "bed"


class RestError(Refusal):
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
        raise RestError(key="rest-dead-sleeps")
    #: `require_here` will refuse a sleeper itself: sleep stands at the same door as the road.
    await travel.require_here(session, body)
    #: Sleep is an occupation like any other (D-211): one does not lie down in
    #: the middle of a search or with a plot under the plough. The refusal
    #: names what is going on, so that the player ends it and comes back.

    await occupation.require_free(session, body, besides=frozenset({occupation.CRAFT}))
    if float(body.stamina) >= constants[R.BODY_STAMINA_MAX]:
        raise NotTired(key="rest-not-tired")

    body.sleeping_since = moment
    body.sleeping_home = await _bed_here(session, constants, body)
    await session.flush()

    #: A batch is not refused by sleep and does not refuse it: lying down is
    #: stepping away from the bench (D-211). The work freezes with its time
    #: left and the machine goes to whoever is awake.

    await craft.freeze(session, body, now=moment)

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
        raise NotSleeping(key="rest-not-sleeping")

    hours = max(0.0, (moment - body.sleeping_since).total_seconds() / SECONDS_PER_HOUR)
    rate = constants[R.BODY_HIBERNATION_RATE]
    if body.sleeping_home:
        rate *= constants[R.BODY_HIBERNATION_HOME_K]

    cap = constants[R.BODY_STAMINA_MAX]
    before = float(body.stamina)
    #: Down, and on the grid here rather than left to the column. Stamina keeps
    #: hundredths and Postgres rounds a half **up**, so a sleep worth half of
    #: one was credited a whole one: at `body.hibernation_rate` that is a sleep
    #: of two and a quarter seconds, and nothing throttles the pair of commands
    #: -- so the loop paid as many times the vault's rate as the round trip
    #: allowed. The one member of this family that ran the player's way.
    #:
    #: Nothing is carried, and nothing needs to be: a sleep too short to credit
    #: a hundredth simply credits nothing, and sleeping is a deliberate act
    #: with a bed, not something done sixteen times a minute. What a long sleep
    #: cannot show waits for the next one no better than it would in a column.
    #:
    #: The fine grid first, and only then the floor: an hour's credit reached
    #: through floats lands an ulp below itself, and flooring that shaves a
    #: whole hundredth off an ordinary night's sleep -- the shortfall the
    #: others avoid by keeping the sliver, which this one has nowhere to put.
    #: The ceiling goes on the grid too, or a fractional `body.stamina_max` in
    #: the vault would leave a body short of full for ever.
    roof = float(on_grid(cap, ROUND_STAMINA, ROUND_FLOOR))
    earned = on_grid(min(roof, before + hours * rate), ROUND_REMAINDER)
    after = float(on_grid(earned, ROUND_STAMINA, ROUND_FLOOR))
    body.stamina = Decimal(str(after))
    body.sleeping_since = None
    body.sleeping_home = False
    await session.flush()

    #: Back at the bench: the work frozen by sleep goes on, on a free machine
    #: of the same name -- somebody else may have taken the old one (D-211).

    await craft.wake(session, body, now=moment)

    await events.record(
        session,
        EventKind.BODY_WOKE,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        hours=hours,
        restored=after - before,
    )
    return after - before


async def _bed_here(session: AsyncSession, constants: Constants, body: Body) -> bool:
    """Whether there is a bed in the location. Until own buildings exist, the bed is the home."""

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        return False
    #: Furniture does not work in a frozen node either (D-231): a bed in the
    #: cold is a bed, not a home, and sleep there is the mistake Aurora kills
    #: for -- the reserve melts while the sleeper does not see it.
    from src.engine import frost  # noqa: PLC0415 -- lazy: breaks the import cycle with frost

    if not await frost.is_warm(session, constants, node):
        return False
    where = await world.node_container(session, node)
    found = await session.scalar(
        select(Item.id)
        .where(
            Item.container_id == where.id,
            Item.type_key.in_(world.station_names(BED)),
            Item.installed.is_(True),
        )
        .limit(1)
    )
    return found is not None
