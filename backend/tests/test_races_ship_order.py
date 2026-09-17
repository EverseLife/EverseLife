# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""An order given to a hull while the hull's own tick kills its crew.

One of the race files (see `test_races.py` for the family's method): here the
contended rows are a hull's and its crew's bodies, and the question is the
**order** they are taken in. Two holders of both exist, and only one of them
has a choice about it:

* the worker takes the hull first and the crew through it -- the life support
  settling a stretch its tanks cannot pay for (`oxygen._breathe`), a hull lost
  with everybody aboard (`ship.fate._lose`, under the row the loss job, the
  helm and the hold's sweep hold). It cannot be turned round: a hull is found
  first and its crew only through it, and the helm holds a hull's row for a
  whole flight step before it can know the step ends in the ground;
* an order given from the bridge holds the commander's body (D-211) and wants
  the hull's. That one has a choice, and it takes the hull's row first: in the
  door (`api.commands.transport._ordered`), or -- for the two orders that
  cannot begin with the hull -- at their own lock
  (`ship.command._still_commanded_by`).

Taken the other way round the two are the same pair in two orders: the captain
holds the body and waits for the hull, the tick holds the hull and waits for
the body, and the database kills one of them. Which one is the database's to
choose, and half the time it is the player's: a descent answered with a
database error instead of a ship.

The hull's row is not the outermost lock of everything, and the third race
here is why: a turn-back takes the passage's **job** row before the hull's
(D-242), so it holds three, in the order job, hull, body. An order that began
with the hull would deadlock with the arrival instead -- which is what putting
the turn-back through the door's prologue did, and what this file now pins.

Each race meets on the crossing every time rather than when a pause happens to
line up: one side stops with its rows held and goes on only once the other is
seen waiting on them (`automat_kit._until_blocked_by`). Which side stops is
chosen so that a hand putting the rows back in the old order deadlocks the
race rather than passing it: for the descent the order pauses at its **last**
lock, and for the crossing and the turn-back -- whose locks are inside the
engine, where the door could take the body ahead of them again -- the worker
goes first and the order walks into what it holds. The deaths themselves --
the pocket of a dying crew member against a hand reaching into it -- are
`test_races_death.py`'s.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import _until_blocked_by
from ship_kit import (
    _flightworthy,
    _fuel,
    _hull,
    _in_orbit,
    _laid,
    _orbit,
    _port,
    _shipwright,
)
from src.api.commands import transport
from src.api.commands.transport import _ship_fly, _ship_land, _ship_recall, _ship_rename
from src.constants import Catalog, Constants
from src.engine import oxygen, ship
from src.engine.ship import fate
from src.engine.ship._base import ShipError
from src.engine.ship.belonging import crew_of
from src.models.identity import Body, BodyState
from src.models.job import Job, JobState
from src.models.ship import Ship
from src.models.world import Node, Planet

#: Fuel enough for the leg the order asks for, and no more of a setup than
#: that: the race is about the rows, not about the arithmetic of a passage.
DESCENT_FUEL = 200.0
CROSSING_FUEL = 5000.0


def _dry(vessel: Ship, owner: Body) -> None:
    """A stretch the hull's tanks cannot cover, on a crew whose grace is spent.

    The hull carries nothing to breathe, so the stretch is short whatever the
    line reads; the countdown is already running, so the stretch kills rather
    than starting one. Both write the crew's rows -- the death is the one that
    also reaches into their pockets.
    """
    moment = datetime.now(UTC)
    vessel.air_at = moment - timedelta(hours=1)
    owner.choking_since = moment - timedelta(hours=2)


async def _struck(session: AsyncSession, constants: Constants, vessel: Ship) -> int:
    """The hull comes down on its own row: the loss the helm and the loss job
    run, once they hold it (`fate.strike` -> `_lose` -> `lock_crew`)."""
    crew = await crew_of(session, vessel)
    await fate.strike(
        session, constants, vessel, now=datetime.now(UTC), t=0.0, r=(0.0, 0.0), body=None, gone=True
    )
    return sum(body.state is BodyState.DEAD for body in crew)


def _dying(
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    death: str,
    vessel_id: uuid.UUID,
) -> Callable[[], Awaitable[int]]:
    """The death of the whole crew as the worker runs it, in the two shapes
    that hold a hull's row and reach for its crew: the tanks failing to cover
    the stretch (`oxygen.tick_ships`), and the hull coming down on its own row
    (under the lock the helm and the loss job take)."""

    async def choke() -> int:
        async with factory() as db, db.begin():
            _, dead = await oxygen.tick_ships(db, constants, catalog)
            return dead

    async def strike() -> int:
        async with factory() as db, db.begin():
            vessel = await db.get(Ship, vessel_id, with_for_update=True)
            assert vessel is not None
            return await _struck(db, constants, vessel)

    return choke if death == "choke" else strike


#: What the name-plate is nailed with, when the race is about the nameplate.
NAMED = "Заря-2"


@pytest.mark.parametrize("door", ["land", "rename"])
@pytest.mark.parametrize("death", ["choke", "strike"])
async def test_an_order_given_as_the_hull_kills_its_crew_is_not_a_knot(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
    death: str,
    door: str,
) -> None:
    """The captain writes to the hull's row in the second it kills them.

    Both doors go through the prologue, which takes the hull's row and then
    the captain's; the death walks in holding the hull and wanting the crew,
    and waits for an order that holds nothing it will want. Taken body first,
    the order would then wait for the hull's row the death is holding, and the
    two would wait on each other for ever.

    `rename` is here because a nameplate is not an order and still takes the
    hull's row: an `UPDATE` of `ship.name` takes it as surely as
    `FOR UPDATE` does, and `shape._mine` has the one nailing it standing
    aboard -- which is to say in the crew the life support is choking.

    The pause is at `_alive`, which is the order's **last** lock here and
    would be its first in the old order -- so a hand that puts the body back
    in front deadlocks this race rather than passing it. Which of the two goes
    first is not the point and is not asserted: the point is that both land,
    and that neither comes back with a database error where the world owes a
    ship, a name or a refusal.
    """
    port = await _port(session)
    vessel, owner = await _hull(session, constants, catalog, port, fuel=DESCENT_FUEL)
    if death == "choke":
        _dry(vessel, owner)
    vessel_id, owner_id, port_key = vessel.id, owner.id, port.key
    state = {"identity_id": owner.identity_id}
    await session.commit()

    deaths: list[asyncio.Future[int]] = []
    waited: list[bool] = []
    alive = transport._alive

    async def holding(taken: dict, db: AsyncSession) -> Body:
        #: Whatever the order has taken by now is what the death has to get
        #: past: both rows in the world's order, the body alone in the old one.
        body = await alive(taken, db)
        if not deaths:
            deaths.append(
                asyncio.ensure_future(_dying(factory, constants, catalog, death, vessel_id)())
            )
            waited.append(await _until_blocked_by(factory, db, unless=deaths[0]))
        return body

    monkeypatch.setattr(transport, "_alive", holding)

    async def ordering() -> dict:
        async with factory() as db, db.begin():
            if door == "land":
                return await _ship_land(state, db, {"port": port_key})
            return await _ship_rename(state, db, {"name": NAMED})

    (ordered,) = await asyncio.gather(ordering(), return_exceptions=True)
    (dead,) = await asyncio.gather(*deaths, return_exceptions=True)

    assert waited == [True], "the death did not wait for the order"
    assert isinstance(ordered, dict), ordered
    assert ordered["flight"] if door == "land" else ordered["name"] == NAMED, ordered
    assert dead == 1, dead
    async with factory() as db:
        body = await db.get(Body, owner_id)
        assert body is not None and body.state is BodyState.DEAD


async def test_a_crossing_ordered_onto_a_hull_already_falling_is_refused(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """The one order whose rows are taken inside the engine, not in the door.

    A crossing lays its slider first and may hold neither row while it does
    (D-341), so it takes the hull's row and then the captain's itself
    (`_still_commanded_by`). Here the loss goes **first** and keeps the hull's
    row until the order is seen waiting on it -- which is the interleaving the
    old order died on, and the one the pause has to produce: with the body
    locked in the door the order would hold it while waiting for the hull, and
    the loss would wait for the body to kill it.

    What the order gets is the point: the hull came down with its captain
    while the slider was being laid, so the answer is the refusal for a dead
    commander -- asked again under both rows, which is the whole reason the
    lock is followed by a second `_commanded_by`.
    """
    here = await _port(session)
    await _port(session, name="Порт Авроры", planet=Planet.AURORA)
    far = await _orbit(session, Planet.AURORA)
    _, owner = await _shipwright(session, here)
    vessel = await _laid(session, constants, owner, here)
    await _flightworthy(session, constants, catalog, vessel)
    connector = await session.get(Node, vessel.connector_node_id)
    assert connector is not None
    await _fuel(session, connector, CROSSING_FUEL)
    owner.node_id = connector.id
    await session.flush()
    await _in_orbit(session, constants, catalog, owner, vessel)
    vessel_id, owner_id, far_key = vessel.id, owner.id, far.key
    state = {"identity_id": owner.identity_id}
    await session.commit()

    held = asyncio.Event()
    waited: list[bool] = []
    orders: list[asyncio.Future[dict]] = []

    async def losing() -> int:
        async with factory() as db, db.begin():
            row = await db.get(Ship, vessel_id, with_for_update=True)
            assert row is not None
            held.set()
            waited.append(await _until_blocked_by(factory, db, unless=orders[0]))
            return await _struck(db, constants, row)

    async def ordering() -> dict:
        await held.wait()
        async with factory() as db, db.begin():
            return await _ship_fly(state, db, {"port": far_key})

    orders.append(asyncio.ensure_future(ordering()))
    dead, ordered = await asyncio.gather(losing(), orders[0], return_exceptions=True)

    assert waited == [True], "the order did not walk into the loss"
    assert dead == 1, dead
    assert isinstance(ordered, ShipError) and ordered.key == "ship-command-dead", ordered
    async with factory() as db:
        body = await db.get(Body, owner_id)
        assert body is not None and body.state is BodyState.DEAD
        row = await db.get(Ship, vessel_id)
        assert row is not None and row.lost_at is not None and not row.course


async def test_a_turn_back_ordered_as_the_passage_arrives_is_refused(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """The turn-back's three rows, and why it does not go through the door.

    The passage's job row comes before the hull's (D-242,
    `flight._passage_of`): the journal claims a job first and writes the hull
    second, and an order that took the hull first would be the other half of
    that deadlock. So a turn-back cannot begin with the hull the way the other
    orders do -- it holds job, hull, body, in that order.

    The arrival goes first and keeps the claimed job row until the turn-back
    is seen waiting on it. On the order this file is about, the turn-back is
    holding nothing and simply waits; put it behind the door's prologue and it
    holds the hull the arrival is about to want. What it gets when the arrival
    commits is the refusal for a hull no longer in a passage -- the job it
    would have turned back is done, and the hull is moored.
    """
    port = await _port(session)
    _, owner = await _shipwright(session, port)
    vessel = await _laid(session, constants, owner, port)
    await _flightworthy(session, constants, catalog, vessel)
    connector = await session.get(Node, vessel.connector_node_id)
    assert connector is not None
    owner.node_id = connector.id
    await session.flush()
    #: A climb under way: the leg every turn-back is given against (D-245).
    leg = await ship.ascend(session, constants, catalog, owner, vessel)
    leg_id, term, vessel_id = leg.id, leg.run_at, vessel.id
    state = {"identity_id": owner.identity_id}
    await session.commit()

    held = asyncio.Event()
    waited: list[bool] = []
    orders: list[asyncio.Future[dict]] = []

    async def arriving() -> None:
        #: The journal's own shape: the job row claimed for the transaction
        #: (`jobs._claim`), and the handler taking the hull under it.
        async with factory() as db, db.begin():
            job = await db.get(Job, leg_id, with_for_update=True)
            assert job is not None
            held.set()
            waited.append(await _until_blocked_by(factory, db, unless=orders[0]))
            await ship.arrived(db, job)
            job.state = JobState.DONE
            job.finished_at = term

    async def ordering() -> dict:
        await held.wait()
        async with factory() as db, db.begin():
            return await _ship_recall(state, db, {})

    orders.append(asyncio.ensure_future(ordering()))
    landed, ordered = await asyncio.gather(arriving(), orders[0], return_exceptions=True)

    assert waited == [True], "the turn-back did not walk into the arrival"
    assert landed is None, landed
    assert isinstance(ordered, ShipError) and ordered.key == "ship-not-in-passage", ordered
    async with factory() as db:
        row = await db.get(Ship, vessel_id)
        assert row is not None and row.docked_node_id is not None, "the climb ended on its circle"
