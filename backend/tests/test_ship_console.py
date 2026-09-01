# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The console, and the way home (D-230, D-242).

The ship is commanded from its bridge and a hull without one hears nothing;
the ground console brings a crewless hull home, at the price of the way
already flown -- never less than a landing, never to a dark or yardless
pier, and never twice for one order. The flight itself lives in
`test_ship_flight.py`.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ship_kit import (
    CONSOLE,
    ENGINE,
    FUEL,
    LIFE,
    _equip,
    _flightworthy,
    _fuel,
    _laid,
    _orbit,
    _port,
    _shipwright,
)
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import frost, ship, storage, travel, world
from src.models.identity import Body
from src.models.job import Job, JobKind, JobState
from src.models.ship import Ship
from src.models.world import Layer, Node, Planet

# --- the console, the tanks and the ship's card (D-230) -----------------------


async def test_ship_is_commanded_from_the_console(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The climb is ordered at the bridge: aboard is not enough, and the
    console must stand in the very room the owner stands in."""
    port = await _port(session)
    _, owner = await _shipwright(session, port, foundations=2)
    vessel = await _laid(session, constants, owner, port)
    connector = await session.get(Node, vessel.connector_node_id)
    await _equip(session, connector, ENGINE)
    await _equip(session, connector, LIFE)
    await _fuel(session, connector, 200)
    owner.node_id = connector.id
    await session.flush()

    with pytest.raises(ship.NoConsole):
        await ship.ascend(session, constants, catalog, owner, vessel)

    #: The console in the next room: still not this one.
    job = await ship.extend(session, constants, owner)
    await ship.keel_laid(session, job)
    hold = next(n for n in await ship.nodes_of(session, vessel) if n.id != connector.id)
    await _equip(session, hold, CONSOLE)
    with pytest.raises(ship.NoConsole):
        await ship.ascend(session, constants, catalog, owner, vessel)

    owner.node_id = hold.id
    await session.flush()
    assert await ship.ascend(session, constants, catalog, owner, vessel) is not None


async def test_fuel_in_a_canister_is_cargo_not_reserve(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The engines draw from the tanks (D-230). A canister of fuel lying in the
    hold weighs like any cargo and buys no passage."""
    port = await _port(session)
    _, owner = await _shipwright(session, port)
    vessel = await _laid(session, constants, owner, port)
    connector = await session.get(Node, vessel.connector_node_id)
    bare = await ship.mass(session, constants, catalog, vessel)

    canister = await _equip(session, connector, "canister")
    inside = await storage.inside(session, canister)
    await world.grant_item(session, inside, FUEL, amount=5, quality=60, origin="тест")
    assert await ship.fuel_aboard(session, vessel) == 0
    assert await ship.mass(session, constants, catalog, vessel) > bare, "канистра с топливом весит"

    await _fuel(session, connector, 40)
    assert await ship.fuel_aboard(session, vessel) == pytest.approx(40)


async def test_card_lists_engines_and_where_the_mass_comes_from(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The console shows what to cut and what to add: engines one by one and
    the mass split into hull, machines and cargo (D-230)."""
    port = await _port(session)
    _, owner = await _shipwright(session, port)
    vessel = await _laid(session, constants, owner, port)
    await _flightworthy(session, constants, catalog, vessel)
    connector = await session.get(Node, vessel.connector_node_id)
    await _equip(session, connector, "pipe", amount=10)

    card = await ship.profile(session, constants, catalog, vessel)
    assert card["engines"] == [
        {
            "name": ENGINE,
            "count": 1,
            "thrust": constants[R.SHIP_THRUST][ENGINE],
            "class": 1,
        }
    ]
    parts = card["mass_parts"]
    assert parts["hull"] == constants[R.SHIP_NODE_MASS]
    assert parts["machines"] > 0 and parts["cargo"] > 0
    assert card["mass"] == pytest.approx(sum(parts.values()), abs=0.1)


# --- the ground console, and turning back (D-242) -----------------------------


GROUND = "ground_console"


async def _ground_console(session: AsyncSession, node: Node) -> None:
    """A ground console standing in a node: the second place an order comes from."""
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, GROUND, quality=60, origin="тест")


async def test_a_hull_whose_crew_died_is_brought_home_from_the_ground(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The hole D-242 exists for: nobody alive aboard, no edges, no way to order.

    Before the ground console this hull hung with its cargo for ever -- the one
    trap a ship could still make, and this world does not build those (P6).
    """
    home = await _port(session, name="Космодром столицы")
    _, owner = await _shipwright(session, home)
    vessel = await _laid(session, constants, owner, home)
    await _flightworthy(session, constants, catalog, vessel)
    connector = await session.get(Node, vessel.connector_node_id)

    owner.node_id = connector.id
    await session.flush()
    await ship.ascend(session, constants, catalog, owner, vessel)
    #: The crew is gone: the owner is back on the ground, printed anew.
    owner.node_id = home.id
    await session.flush()

    #: From bare ground the hull is deaf, exactly as before.
    with pytest.raises(ship.NotAboard):
        await ship.recall(session, constants, catalog, owner, vessel)

    await _ground_console(session, home)
    job = await ship.recall(session, constants, catalog, owner, vessel)
    assert job is not None, "с наземной консоли приказ проходит"


async def test_a_hull_without_a_bridge_hears_nothing_from_the_ground(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The ground console talks to the ship's console: no bridge, no order (D-242).

    That is what keeps the bridge worth building after the ground one exists.
    """
    home = await _port(session, name="Космодром столицы")
    _, owner = await _shipwright(session, home)
    vessel = await _laid(session, constants, owner, home)
    connector = await session.get(Node, vessel.connector_node_id)
    #: Everything a passage needs **except** a console aboard.
    await _equip(session, connector, ENGINE)
    await _equip(session, connector, LIFE)
    await _fuel(session, connector, 200)
    owner.node_id = connector.id
    await session.flush()

    #: Aboard, the missing console is refused as it always was.
    with pytest.raises(ship.NoConsole):
        await ship.ascend(session, constants, catalog, owner, vessel)

    await _equip(session, connector, CONSOLE)
    await ship.ascend(session, constants, catalog, owner, vessel)
    #: Now take the console away and try from the ground.
    yard = await world.node_container(session, connector)
    for thing in await world.contents(session, yard):
        if thing.type_key == CONSOLE:
            await session.delete(thing)
    await session.flush()

    owner.node_id = home.id
    await _ground_console(session, home)
    await session.flush()
    with pytest.raises(ship.Deaf):
        await ship.recall(session, constants, catalog, owner, vessel)


async def test_turning_back_costs_the_way_already_flown(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The way home is as long as the way out has been, and burns its own fuel.

    Shown on a climb, because that is the leg a player takes back most often
    (D-245): "подняться на орбиту" is an order, and an order may be countermanded.
    """
    home = await _port(session, name="Космодром столицы")
    _, owner = await _shipwright(session, home)
    vessel = await _laid(session, constants, owner, home)
    await _flightworthy(session, constants, catalog, vessel)
    connector = await session.get(Node, vessel.connector_node_id)
    await _fuel(session, connector, 3000)
    owner.node_id = connector.id
    await session.flush()

    flight = await ship.ascend(session, constants, catalog, owner, vessel)
    assert vessel.left_node_id == home.id, "причал, с которого ушли, запомнен"
    #: `created_at` is the database's own stamp: read it back rather than off
    #: an object that has not seen the row since the insert.
    await session.refresh(flight)

    #: Half a day out. The way back is half a day, to the pier it left. Well
    #: past the landing floor, so what is pinned here is the rule itself.
    gone = timedelta(hours=12)
    moment = flight.created_at + gone
    before = await ship.fuel_aboard(session, vessel)
    back = await ship.recall(session, constants, catalog, owner, vessel, now=moment)

    assert back.payload["to"] == str(home.id)
    assert back.run_at - moment == gone
    assert await ship.fuel_aboard(session, vessel) < before, "разворот сжёг своё топливо"

    await session.refresh(flight)
    assert flight.state is JobState.CANCELLED, "прежний рейс снят: два прихода на один корпус"


async def test_a_turn_back_is_not_turned_back(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Two clicks must not bring a hull home from anywhere, instantly and free.

    A turn-back counts the hours of the passage it replaced. Counted afresh
    from **itself** they are nought: no fuel, no time, and the hull lands at
    home the same second. That is the whole price of a turn-back gone, so the
    second one is refused outright -- the ship is already going there.
    """
    home = await _port(session, name="Космодром столицы")
    _, owner = await _shipwright(session, home)
    vessel = await _laid(session, constants, owner, home)
    await _flightworthy(session, constants, catalog, vessel)
    connector = await session.get(Node, vessel.connector_node_id)
    await _fuel(session, connector, 3000)
    owner.node_id = connector.id
    await session.flush()

    flight = await ship.ascend(session, constants, catalog, owner, vessel)
    await session.refresh(flight)
    moment = flight.created_at + timedelta(hours=12)
    back = await ship.recall(session, constants, catalog, owner, vessel, now=moment)

    burnt = await ship.fuel_aboard(session, vessel)
    with pytest.raises(ship.InFlight):
        await ship.recall(
            session, constants, catalog, owner, vessel, now=moment + timedelta(seconds=1)
        )
    assert await ship.fuel_aboard(session, vessel) == burnt, "отказ не сжёг топлива"
    #: And the way home is still the half day it was, not nought.
    await session.refresh(back)
    assert back.run_at - moment == timedelta(hours=12)


async def test_a_turn_back_to_a_pier_without_a_yard_is_refused(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A hull is not sent back to a node with nothing to moor to.

    A landing asks two questions of a destination -- is there a yard, and is the
    beacon lit -- and a turn-back must ask both. It used to ask only the second,
    so a pier whose yard was carried off while the hull flew still took the
    turn-back, and the arrival laid a gangway onto a node with no spaceport at
    all. Written first, dismissed as wrong, and right after all (review of
    D-242).
    """
    home = await _port(session, name="Космодром столицы")
    _, owner = await _shipwright(session, home)
    vessel = await _laid(session, constants, owner, home)
    await _flightworthy(session, constants, catalog, vessel)
    connector = await session.get(Node, vessel.connector_node_id)
    owner.node_id = connector.id
    await session.flush()

    flight = await ship.ascend(session, constants, catalog, owner, vessel)
    await session.refresh(flight)

    #: The yard is carried off while the hull is under way.
    yard = await world.node_container(session, home)
    for thing in await world.contents(session, yard):
        if thing.type_key == "space_shipyard":
            await session.delete(thing)
    await session.flush()

    with pytest.raises(ship.NoPort):
        await ship.recall(
            session, constants, catalog, owner, vessel, now=flight.created_at + timedelta(hours=1)
        )


async def test_a_turn_back_never_costs_less_than_a_landing(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Turned round in the first minute, a hull has gone nowhere -- and still
    has to come down.

    Without a floor the arithmetic put it back on the pier at once and for
    nothing, which is a way to skip the hours every descent costs (D-245): lift,
    turn back, and be down again before the gauge has moved.
    """
    home = await _port(session, name="Космодром столицы")
    _, owner = await _shipwright(session, home)
    vessel = await _laid(session, constants, owner, home)
    await _flightworthy(session, constants, catalog, vessel)
    connector = await session.get(Node, vessel.connector_node_id)
    owner.node_id = connector.id
    await session.flush()

    flight = await ship.ascend(session, constants, catalog, owner, vessel)
    await session.refresh(flight)

    before = await ship.fuel_aboard(session, vessel)
    #: Turned round the same second it set out.
    back = await ship.recall(session, constants, catalog, owner, vessel, now=flight.created_at)

    thrust_ratio = await ship.ratio(session, constants, catalog, vessel)
    landing = ship.fall_hours(constants, Planet.TERRA, thrust_ratio)
    assert back.run_at - flight.created_at == pytest.approx(
        timedelta(hours=landing), abs=timedelta(seconds=1)
    ), "разворот в ту же секунду всё равно длится посадку"
    assert await ship.fuel_aboard(session, vessel) < before, "и стоит топлива"


async def test_somebody_elses_ground_console_is_refused(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Orders are given from one's own console, on land one disposes of (D-242)."""
    home = await _port(session, name="Космодром столицы")
    _, owner = await _shipwright(session, home)
    vessel = await _laid(session, constants, owner, home)
    await _flightworthy(session, constants, catalog, vessel)
    connector = await session.get(Node, vessel.connector_node_id)
    owner.node_id = connector.id
    await session.flush()
    await ship.ascend(session, constants, catalog, owner, vessel)

    #: A console standing on somebody else's plot.
    stranger = await world.create_identity(session, f"Сосед-{uuid.uuid4().hex[:6]}")
    yard_node = await world.create_node(
        session, f"terra.yard.{uuid.uuid4().hex[:8]}", "Чужой двор", area_m2=200
    )
    yard_node.owner_identity_id = stranger.id
    await session.flush()
    await _ground_console(session, yard_node)
    owner.node_id = yard_node.id
    await session.flush()

    with pytest.raises(ship.NotYours):
        await ship.recall(session, constants, catalog, owner, vessel)


async def test_an_arrival_that_fires_twice_moors_once(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A hull is docked by exactly one arrival.

    A retry after a failure, or a job that outlived a turn-back, would otherwise
    lay a second gangway and moor a ship that is already moored.
    """
    home = await _port(session, name="Космодром столицы")
    away = await _orbit(session)
    _, owner = await _shipwright(session, home)
    vessel = await _laid(session, constants, owner, home)
    await _flightworthy(session, constants, catalog, vessel)
    connector = await session.get(Node, vessel.connector_node_id)
    owner.node_id = connector.id
    await session.flush()
    job = await ship.ascend(session, constants, catalog, owner, vessel)

    await ship.arrived(session, job)
    berth, docked = vessel.berth, vessel.docked_node_id
    assert docked == away.id

    await ship.arrived(session, job)
    assert vessel.docked_node_id == docked and vessel.berth == berth
    ways = await travel.exits(session, constants, away)
    assert [way.node_id for way in ways].count(connector.id) == 1, "трап один"


async def test_a_turn_back_to_a_dark_pier_is_refused(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The same question `fly` asks of a destination (D-232): a hull is not sent
    where it will not be taken.

    A pier on the permafrost works while its node is warm and its yard has
    power. An unpowered one on Aurora is dark, and a hull turning back to it
    would be turning back to nowhere -- so the turn-back is refused and the
    passage it is on stands. Not a chain of failures: the way on is still
    there, and the fuel for it was guaranteed at the casting off.
    """
    #: The planet's own node carries its climate (D-231): the engine reads the
    #: world, not a constant.
    await world.create_node(
        session,
        Planet.AURORA.value,
        "Аврора",
        area_m2=1,
        planet=Planet.AURORA,
        layer=Layer.SPACE,
        properties={frost.FROST: True},
    )
    home = await _port(session, name="Космодром Мерида", planet=Planet.AURORA)
    _, owner = await _shipwright(session, home)
    vessel = await _laid(session, constants, owner, home)
    await _flightworthy(session, constants, catalog, vessel)
    connector = await session.get(Node, vessel.connector_node_id)
    owner.node_id = connector.id
    await session.flush()

    flight = await ship.ascend(session, constants, catalog, owner, vessel)
    await session.refresh(flight)
    #: No city, no pool, no heat: the pier it left is dark.
    assert not await ship.beacon_lit(session, constants, home)

    with pytest.raises(ship.NoPort):
        await ship.recall(
            session, constants, catalog, owner, vessel, now=flight.created_at + timedelta(hours=1)
        )


async def test_two_turn_backs_in_one_second_burn_one_return(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Two sockets of one player, or an AI citizen (D-224), pressing together.

    The turn-back writes twice -- the passage's job and the tanks -- and both
    writes are worth doubling. The hull's passage is taken under lock before
    anything is decided, so the second order finds a hull already going home.
    """
    async with factory() as session, session.begin():
        home = await _port(session, name="Космодром столицы")
        _, owner = await _shipwright(session, home)
        vessel = await _laid(session, constants, owner, home)
        await _flightworthy(session, constants, catalog, vessel)
        connector = await session.get(Node, vessel.connector_node_id)
        await _fuel(session, connector, 3000)
        owner.node_id = connector.id
        await session.flush()
        flight = await ship.ascend(session, constants, catalog, owner, vessel)
        await session.refresh(flight)
        ship_id, owner_id = vessel.id, owner.id
        flown = 12.0
        moment = flight.created_at + timedelta(hours=flown)
        before = await ship.fuel_aboard(session, vessel)
        #: What one turn-back costs, by the engine's own formula: the hours it
        #: has flown, priced by mass and by the class that pushes it.
        one_turn = ship.fuel_for(
            constants,
            await ship.mass(session, constants, catalog, vessel),
            flown,
            klass=await ship.engine_class(session, constants, vessel),
        )

    ready = asyncio.Barrier(2)

    async def turn() -> str:
        async with factory() as db, db.begin():
            mine = await db.get(Ship, ship_id)
            me = await db.get(Body, owner_id)
            await ready.wait()
            try:
                await ship.recall(db, constants, catalog, me, mine, now=moment)
            #: Whichever refusal the loser gets is the right one, and which it is
            #: depends on where it was standing when the winner committed: the
            #: passage it meant to cancel is gone (`Docked`), or it has already
            #: read the turn-back that replaced it (`InFlight`). What matters is
            #: that the second order changes nothing, and that is asserted below.
            except (ship.Docked, ship.InFlight):
                return "refused"
            return "turned"

    answers = await asyncio.gather(turn(), turn())
    assert sorted(answers) == ["refused", "turned"], f"оба разворота прошли: {answers}"

    async with factory() as session:
        vessel = await session.get(Ship, ship_id)
        left = await ship.fuel_aboard(session, vessel)
        going = (
            (
                await session.execute(
                    select(Job).where(
                        Job.kind == JobKind.SHIP_FLIGHT.value,
                        Job.state == JobState.PENDING,
                        Job.payload["ship"].astext == str(ship_id),
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(going) == 1, "на корпусе один рейс, а не два"
    #: Exactly one turn-back's worth, not "at least some": two burns would pass
    #: a `left < before` and hide the very doubling this test is here for.
    assert before - left == pytest.approx(one_turn, abs=0.01), (
        f"списано {before - left:.2f} вместо {one_turn:.2f}"
    )


async def test_a_hull_that_is_not_flying_has_nothing_to_turn_back(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    port = await _port(session)
    _, owner = await _shipwright(session, port)
    vessel = await _laid(session, constants, owner, port)
    await _flightworthy(session, constants, catalog, vessel)
    connector = await session.get(Node, vessel.connector_node_id)
    owner.node_id = connector.id
    await session.flush()

    with pytest.raises(ship.Docked):
        await ship.recall(session, constants, catalog, owner, vessel)
