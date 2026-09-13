# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A death landing on bodies another transaction is busy with.

One of the race files (see `test_races.py` for the family's method): here the
contended rows are a body and what lies in its hands, and the order they are
taken in. Everybody who holds a body takes its row first and the things in its
hands after -- a command through `_alive`, a handover through
`world.lock_bodies`, the world's sweeps body by body in id order. `death.die`
itself takes the pocket and writes the body last, so whoever kills without
holding the body first takes the two the other way round:

* the hull's air runs out, or the hull comes down, under a crew member who is
  picking a sack up off the floor, or being handed a parcel -- the act holds
  the body and reaches for the stack in its hands the arrival folds into
  (D-214), the death holds that stack and writes the body;
* the hull's air runs out while a member walks the gangway out: the crew is
  whoever is aboard once the rows are held, not when they were read;
* two ways break under a walker each while the cold settles them both -- the
  sweep holds the lower id and waits for the higher, the rift held the higher
  first because its way broke first;
* a way breaks as its walker turns back: the walk is whatever it is once the
  rift holds the row.

The handshake is `automat_kit._until_blocked_by`: the act that went first
keeps its transaction open and commits only once the death provably waits on
it. On the code these races catch the database then finds the knot and kills
one of the two.
"""

from __future__ import annotations

import asyncio
import random
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from automat_kit import _until_blocked_by
from lines_kit import _hull, _seal
from pyroxis_kit import _dweller, _surface
from ship_kit import _orbit
from src.api.commands.things import _ground_pick, _item_hand
from src.api.commands.travel import _travel_cancel
from src.constants import Catalog, Constants
from src.engine import craft, frost, gear, oxygen, plates, travel, world
from src.engine.plates import ways
from src.engine.ship import fate
from src.engine.ship.belonging import crew_of
from src.models.event import Event, EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Container
from src.models.job import Job, JobKind
from src.models.ship import Ship
from src.models.travel import Travel
from src.models.world import Edge, Node, Surface
from test_oxygen import _cylinder, _suited

ORE = "iron_ore"
#: Kilograms of the arriving sack or parcel, and of the stack already in the
#: taker's hands it folds into: both well inside the limit, so the weighing
#: never refuses and the race is about the order alone.
ARRIVING_KG = 4.0
HELD_KG = 1.0


async def _ore(session: AsyncSession, catalog: Catalog, container, kg: float):
    return await world.grant_item(
        session,
        container,
        ORE,
        amount=kg / gear.mass_of(catalog, ORE, 1),
        quality=60,
        origin="test",
    )


async def _crew(
    session: AsyncSession, constants: Constants, catalog: Catalog, door: str
) -> tuple[uuid.UUID, Body, Callable[[AsyncSession], Awaitable[float]]]:
    """A hull cast off into the void with ore in a crew member's hands, and
    the act that puts more ore into them. Returns the hull, the taker and the
    act.

    `pick`: the owner alone aboard, a sack on the connector's floor -- the
    owner's floor, so nothing asks whose it is. `hand`: a second body beside
    the owner, the parcel in the higher id's hands, handed to the lower --
    whose death `death.die` reaches first in the crew's id order, the one
    that makes the knot on the old code.
    """
    vessel, owner, connector = await _hull(session, constants)
    if door == "pick":
        taker = owner
        thing = await _ore(
            session, catalog, await world.node_container(session, connector), ARRIVING_KG
        )
        state = {"identity_id": taker.identity_id}
        message = {"item": str(thing.id)}

        async def act(db: AsyncSession) -> float:
            return (await _ground_pick(state, db, message))["picked"]

    else:
        mate = await world.print_body(
            session, await world.create_identity(session, f"Mate-{uuid.uuid4().hex[:6]}"), connector
        )
        taker, giver = sorted((owner, mate), key=lambda body: body.id)
        thing = await _ore(
            session, catalog, await world.body_container(session, giver), ARRIVING_KG
        )
        state = {"identity_id": giver.identity_id}
        message = {"item": str(thing.id), "to": str(taker.id)}

        async def act(db: AsyncSession) -> float:
            return (await _item_hand(state, db, message))["given"]

    await _ore(session, catalog, await world.body_container(session, taker), HELD_KG)
    _seal(vessel)
    await session.flush()
    return vessel.id, taker, act


def _dying(
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    death: str,
    vessel_id: uuid.UUID,
) -> Callable[[], Awaitable[int]]:
    """The death of the whole crew, as the worker runs it: the hull's air on
    the tick (`tick_ships`), or the hull coming down on its own row
    (`fate.strike`, under the lock the helm and the loss job take)."""

    async def choke() -> int:
        async with factory() as db, db.begin():
            _, dead = await oxygen.tick_ships(db, constants, catalog)
            return dead

    async def strike() -> int:
        async with factory() as db, db.begin():
            vessel = await db.get(Ship, vessel_id, with_for_update=True)
            assert vessel is not None
            crew = await crew_of(db, vessel)
            await fate.strike(
                db,
                constants,
                vessel,
                now=datetime.now(UTC),
                t=0.0,
                r=(0.0, 0.0),
                body=None,
                gone=True,
            )
            return sum(body.state is BodyState.DEAD for body in crew)

    return choke if death == "choke" else strike


@pytest.mark.parametrize("door", ["pick", "hand"])
@pytest.mark.parametrize("death", ["choke", "strike"])
async def test_a_crew_dying_under_an_arrival_into_its_hands_both_land(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
    death: str,
    door: str,
) -> None:
    """The arrival lands, and then the crew dies with it in their hands.

    The act holds the taker's row -- their own `_alive`, or both rows of a
    handover -- and has weighed the hands; the death walks in between. Taken
    the way `death.die` takes them, the death held the stack already in the
    taker's hands while it waited to write the body, and the arrival, folding
    into that stack, waited for the death. Taken body first, the death waits
    for the act holding nothing it will want.
    """
    vessel_id, taker, act = await _crew(session, constants, catalog, door)
    vessel = await session.get(Ship, vessel_id)
    assert vessel is not None
    aboard = await crew_of(session, vessel)
    crew = [body.id for body in aboard]
    if death == "choke":
        #: The grace is spent and the tanks are dry: this stretch kills.
        vessel.air_at = datetime.now(UTC) - timedelta(hours=1)
        for body in aboard:
            body.choking_since = datetime.now(UTC) - timedelta(hours=2)
    pocket_id = (await world.body_container(session, taker)).id
    await session.commit()

    deaths: list[asyncio.Future[int]] = []
    waited: list[bool] = []
    weigh = gear.check_carry_thing

    async def weighing(db, *args, **kwargs) -> None:
        await weigh(db, *args, **kwargs)
        if not deaths:
            deaths.append(
                asyncio.ensure_future(_dying(factory, constants, catalog, death, vessel_id)())
            )
            waited.append(await _until_blocked_by(factory, db, unless=deaths[0]))

    monkeypatch.setattr(gear, "check_carry_thing", weighing)

    async def acting() -> float:
        async with factory() as db, db.begin():
            return await act(db)

    (arrived,) = await asyncio.gather(acting(), return_exceptions=True)
    (dead,) = await asyncio.gather(*deaths, return_exceptions=True)

    assert arrived == pytest.approx(ARRIVING_KG / gear.mass_of(catalog, ORE, 1)), arrived
    assert dead == len(crew), dead
    assert waited == [True], "the death did not wait for the act"
    async with factory() as db:
        for body_id in crew:
            body = await db.get(Body, body_id)
            assert body is not None and body.state is BodyState.DEAD
        #: The arrival was in the hands when they died, and went with them:
        #: salvaged to the floor or lost, but not left in a dead pocket.
        assert await world.contents(db, await db.get(Container, pocket_id)) == ()


@pytest.mark.parametrize("alone", [False, True])
async def test_a_member_stepping_off_while_the_air_runs_out_lives(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
    alone: bool,
) -> None:
    """The life support kills whoever is aboard once it holds the rows.

    A suited member walks the gangway out into orbit, and the leg's arrival
    holds their row just as the tanks run dry. The crew was read before the
    wait, with the member still in the connector; after it they stand in the
    void, where the hull's air is not theirs to lack -- the countdown on their
    own body is the oxygen tick's (`tick_bodies`), not the hull's.

    `alone`: the member was all the crew there was, and the hull is left with
    nobody to kill and nobody to warn -- no airless word to an empty hull.
    """
    vessel, owner, connector = await _hull(session, constants)
    port = await session.get(Node, vessel.docked_node_id)
    assert port is not None
    orbit = await _orbit(session)
    #: Moored in orbit (D-245): the one gangway runs to the orbital node.
    await travel.disconnect(session, port, connector)
    await travel.connect(session, orbit, connector, base_seconds=1, surface=Surface.PAVED)
    vessel.docked_node_id = orbit.id
    mate = await world.print_body(
        session, await world.create_identity(session, f"Mate-{uuid.uuid4().hex[:6]}"), connector
    )
    await _suited(session, constants, catalog, mate)
    await _cylinder(session, mate, 1.0)
    await session.flush()
    await travel.depart(session, constants, mate, orbit)
    leg = (
        await session.execute(
            select(Job.id).where(Job.body_id == mate.id, Job.kind == JobKind.TRAVEL_LEG.value)
        )
    ).scalar_one()
    #: The grace is spent and the tanks are dry: this stretch kills the crew.
    vessel.air_at = datetime.now(UTC) - timedelta(hours=1)
    for body in (owner, mate):
        body.choking_since = datetime.now(UTC) - timedelta(hours=2)
    if alone:
        #: The owner stayed on the pier: the member is the whole crew.
        owner.node_id = port.id
    owner_id, mate_id, orbit_id = owner.id, mate.id, orbit.id
    await session.commit()

    ticks: list[asyncio.Future[int]] = []
    waited: list[bool] = []
    settle = frost.settle

    async def settling(db, *args, **kwargs):
        done = await settle(db, *args, **kwargs)
        if not ticks:

            async def breathe() -> int:
                async with factory() as other, other.begin():
                    _, dead = await oxygen.tick_ships(other, constants, catalog)
                    return dead

            ticks.append(asyncio.ensure_future(breathe()))
            waited.append(await _until_blocked_by(factory, db, unless=ticks[0]))
        return done

    monkeypatch.setattr(frost, "settle", settling)

    async def arriving() -> None:
        async with factory() as db, db.begin():
            job = await db.get(Job, leg)
            assert job is not None
            await travel.arrive(db, job)

    (arrived,) = await asyncio.gather(arriving(), return_exceptions=True)
    (dead,) = await asyncio.gather(*ticks, return_exceptions=True)

    assert arrived is None, arrived
    assert dead == (0 if alone else 1), dead
    assert waited == [True], "the life support did not wait for the arrival"
    async with factory() as db:
        stayed = await db.get(Body, owner_id)
        stepped = await db.get(Body, mate_id)
        assert stayed is not None
        assert stayed.state is (BodyState.ALIVE if alone else BodyState.DEAD)
        assert stepped is not None and stepped.state is BodyState.ALIVE
        assert stepped.node_id == orbit_id
        #: Nobody left to breathe it is nobody to warn: the word that the
        #: tanks failed goes to a crew, and this hull has none.
        warned = await db.scalar(select(Event.id).where(Event.kind == EventKind.SHIP_AIRLESS))
        assert warned is None, warned


class _Shaking(random.Random):
    """Dice that break every way the graph lets go of: the rift's choice is
    then the graph's alone, and the test can say in advance which ways go."""

    def random(self) -> float:
        return 0.0


async def _breaking(session: AsyncSession) -> tuple[list[uuid.UUID], list[Edge]]:
    """Two shaken fields and the ways the rift breaks out of them, in the
    order it breaks them -- asked of the graph the way `_redraw` asks it:
    every way out of a shaken field that does not cut anything off."""
    plateau, fields = await _surface(session, count=3)
    shaken = [fields[0], fields[2]]
    graph = await plates._adjacency(session)
    broken: list[Edge] = []
    for node in shaken:
        for other in sorted(graph.get(node.id, set()), key=str):
            if not plates._may_lose(graph, node.id, other, plateau.id):
                continue
            edge = await session.scalar(
                select(Edge).where(
                    or_(
                        (Edge.node_a_id == node.id) & (Edge.node_b_id == other),
                        (Edge.node_a_id == other) & (Edge.node_b_id == node.id),
                    )
                )
            )
            assert edge is not None
            broken.append(edge)
            graph[node.id].discard(other)
            graph[other].discard(node.id)
    assert len(broken) == 2, broken
    return [node.id for node in shaken], broken


async def _walking(session: AsyncSession, walker: Body, way: Edge) -> None:
    """The walker set out along the way, and is on it."""
    walker.node_id = way.node_a_id
    session.add(
        Travel(
            body_id=walker.id,
            from_node_id=way.node_a_id,
            to_node_id=way.node_b_id,
            edge_id=way.id,
            arrives_at=datetime.now(UTC) + timedelta(minutes=5),
        )
    )
    await session.flush()


async def _redrawn(
    factory: async_sessionmaker[AsyncSession], constants: Constants, shaken: list[uuid.UUID]
) -> int:
    """The ways redrawn as the eruption redraws them. Returns who died."""
    async with factory() as db, db.begin():
        nodes = [await db.get(Node, one) for one in shaken]
        _, _, dead = await plates._redraw(db, constants, _Shaking(), nodes, now=datetime.now(UTC))
        return dead


async def test_two_ways_breaking_under_walkers_the_cold_is_settling_both_land(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Walkers on two breaking ways die, and the cold's pass over them ends.

    The cold takes every body on the planet in id order, one after another in
    one transaction. The rift took its walkers way by way, and on each way in
    the order the walks were set out in: with the higher id on the way that
    breaks first, it held that walker and reached for the lower one the cold
    already held -- while the cold waited for the higher. Taken all at once
    in id order, the rift holds both before the cold reaches either.
    """
    shaken, broken = await _breaking(session)
    low, high = sorted(
        [await _dweller(session, await session.get(Node, shaken[0])) for _ in range(2)],
        key=lambda body: body.id,
    )
    #: The higher id on the way that breaks first.
    for walker, way in zip((high, low), broken, strict=True):
        await _walking(session, walker, way)
    walkers = (low.id, high.id)
    await session.commit()

    sweeps: list[asyncio.Future[int]] = []
    waited: list[bool] = []
    consume = ways._consume

    async def consuming(db, things):
        gone = await consume(db, things)
        if not sweeps:

            async def cold() -> int:
                async with factory() as other, other.begin():
                    return await frost.tick_bodies(other, constants, catalog)

            sweeps.append(asyncio.ensure_future(cold()))
            waited.append(await _until_blocked_by(factory, db, unless=sweeps[0]))
        return gone

    monkeypatch.setattr(ways, "_consume", consuming)

    (died,) = await asyncio.gather(_redrawn(factory, constants, shaken), return_exceptions=True)
    (swept,) = await asyncio.gather(*sweeps, return_exceptions=True)

    assert died == 2, died
    assert swept == 0, swept
    assert waited == [True], "the cold did not wait for the rift"
    async with factory() as db:
        for body_id in walkers:
            body = await db.get(Body, body_id)
            assert body is not None and body.state is BodyState.DEAD


async def test_a_walker_turning_back_as_the_way_breaks_lives(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rift kills whoever is on the way once it holds the rows.

    The walker turns back (`travel.cancel`) under their own row, and the rift
    reads the walk before that commits: still going. After the wait the walk
    is cancelled and the body stands where it set out from -- off the way,
    and no business of the rift's.
    """
    shaken, broken = await _breaking(session)
    walker = await _dweller(session, await session.get(Node, shaken[0]))
    await _walking(session, walker, broken[0])
    state = {"identity_id": walker.identity_id}
    walker_id, start = walker.id, broken[0].node_a_id
    await session.commit()

    rifts: list[asyncio.Future[int]] = []
    waited: list[bool] = []
    wake = craft.wake

    async def waking(db, *args, **kwargs):
        await wake(db, *args, **kwargs)
        if not rifts:
            rifts.append(asyncio.ensure_future(_redrawn(factory, constants, shaken)))
            waited.append(await _until_blocked_by(factory, db, unless=rifts[0]))

    monkeypatch.setattr(craft, "wake", waking)

    async def turning() -> dict:
        async with factory() as db, db.begin():
            return await _travel_cancel(state, db, {})

    (turned,) = await asyncio.gather(turning(), return_exceptions=True)
    (died,) = await asyncio.gather(*rifts, return_exceptions=True)

    assert isinstance(turned, dict) and turned["cancelled"], turned
    assert died == 0, died
    assert waited == [True], "the rift did not wait for the walker"
    async with factory() as db:
        body = await db.get(Body, walker_id)
        assert body is not None and body.state is BodyState.ALIVE
        assert body.node_id == start
