# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A floor above the ground is a node of its own (D-247).

Height used to be a number on one node: a house of four storeys was forty
metres of floor in the same place as the yard, and "the third floor" was
nowhere one could stand. Now the ground floor **is** the plot -- the door, the
yard and the way in are there -- and every floor above it is a location with a
staircase to it.

What is checked is the whole of the rule:

* a one-storey house opens nothing; a tall one opens a floor per storey, in a
  row, each reached from the one below;
* the metres add up: the plot's floor and every storey are one footprint each,
  and their sum is the usable area of the house;
* a storey is a floor and not land -- no yard, no foraging, no strips, no
  building on it;
* the floors are held by whoever holds the plot, and change hands with it;
* the house is one thing: the plot answers for the bill, the wear and the tax,
  and the floors go down with it -- taken apart into the yard, or buried;
* a body upstairs comes down rather than standing in a node that stopped
  existing;
* the city, the meter and the walls do not stop at the first floor.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import city as town
from src.engine import estate, farm, forage, storage, utility, world
from src.models.estate import Building
from src.models.identity import Body
from src.models.world import Edge, Layer, Node


async def _plot(session: AsyncSession, *, area: float = 200) -> Node:
    return await world.create_node(
        session,
        f"terra.lot.{uuid.uuid4().hex[:8]}",
        "Участок",
        area_m2=area,
        layer=Layer.PLANET,
        properties={"fertility": 50},
    )


async def _holder(session: AsyncSession, node: Node) -> Body:
    identity = await world.create_identity(session, f"Хозяин-{uuid.uuid4().hex[:6]}")
    body = await world.print_body(session, identity, node)
    node.owner_identity_id = identity.id
    await session.flush()
    return body


async def _house(
    session: AsyncSession,
    constants: Constants,
    node: Node,
    *,
    footprint: float = 40,
    floors: int = 1,
) -> tuple[Building, list[Node]]:
    """A finished house with its floors already open."""
    house = Building(
        node_id=node.id,
        area_m2=footprint * floors,
        footprint_m2=footprint,
        floors=floors,
        kind=estate.kinds(constants)[0],
    )
    session.add(house)
    await session.flush()
    return house, await estate.open_storeys(session, constants, node)


async def _stairs(session: AsyncSession, a: Node, b: Node) -> Edge | None:
    """The staircase between two nodes, whichever way round it was cut."""
    return (
        await session.execute(
            select(Edge).where(
                ((Edge.node_a_id == a.id) & (Edge.node_b_id == b.id))
                | ((Edge.node_a_id == b.id) & (Edge.node_b_id == a.id))
            )
        )
    ).scalar_one_or_none()


# --- what a house opens -------------------------------------------------------


async def test_a_single_storey_house_opens_nothing(
    session: AsyncSession, constants: Constants
) -> None:
    """The ground floor is the plot: the world as it was needs no rewriting."""
    node = await _plot(session)
    _, rooms = await _house(session, constants, node, footprint=40, floors=1)
    assert rooms == []
    assert await estate.storeys_of(session, node) == []


async def test_a_tall_house_opens_a_floor_per_storey_in_a_row(
    session: AsyncSession, constants: Constants
) -> None:
    """Four storeys are three rooms above the plot, and one climbs them in order."""
    node = await _plot(session)
    house, rooms = await _house(session, constants, node, footprint=50, floors=4)

    assert [estate.storey_of(room) for room in rooms] == [2, 3, 4]
    assert all(room.layer is Layer.LOCATION for room in rooms)
    assert all(room.parent_id == node.id for room in rooms)
    assert all(float(room.area_m2) == float(house.footprint_m2) for room in rooms)

    #: A staircase from each floor to the one below it, and none from the plot
    #: straight to the top: height is climbed, not teleported into.
    flight = int(constants[R.BUILD_STAIR_SECONDS])
    ladder = [node, *rooms]
    for below, above in zip(ladder, ladder[1:], strict=False):
        edge = await _stairs(session, below, above)
        assert edge is not None, f"нет лестницы между {below.name} и {above.name}"
        assert edge.base_seconds == flight
    assert await _stairs(session, node, rooms[-1]) is None, (
        "с земли на верхний этаж лестницы нет — идут пролётами"
    )


async def test_the_metres_add_up_to_the_house(session: AsyncSession, constants: Constants) -> None:
    """Plot floor plus every storey is the usable area, and the yard is the rest."""
    node = await _plot(session, area=200)
    house, rooms = await _house(session, constants, node, footprint=50, floors=4)

    ground = await estate.space(session, constants, node)
    assert ground["area"] == 50, "пол участка — первый этаж"
    upstairs = [(await estate.space(session, constants, room))["area"] for room in rooms]
    assert upstairs == [50, 50, 50]
    assert ground["area"] + sum(upstairs) == float(house.area_m2)

    assert (await estate.yard(session, constants, node))["area"] == 150
    for room in rooms:
        assert (await estate.yard(session, constants, room))["area"] == 0, "наверху двора нет"


async def test_a_storey_carries_its_own_places(session: AsyncSession, constants: Constants) -> None:
    """Machines take the metres of the floor they stand on, not of the whole house."""
    node = await _plot(session)
    _, rooms = await _house(session, constants, node, footprint=40, floors=3)
    per_place = constants[R.BUILD_SLOTS_PER_AREA]

    total, _ = await estate.slots(session, constants, node)
    above, _ = await estate.slots(session, constants, rooms[0])
    assert total == int(40 // per_place)
    assert above == total, "у этажа столько же мест, сколько у первого: пятно одно"


# --- a floor is not land ------------------------------------------------------


async def test_a_storey_is_not_land(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Nothing is built on a floor, nothing is marked out of it, nothing gathered."""
    node = await _plot(session, area=200)
    body = await _holder(session, node)
    _, rooms = await _house(session, constants, node, footprint=40, floors=2)
    upstairs = rooms[0]

    body.node_id = upstairs.id
    await session.flush()

    assert await estate.spare_ground(session, upstairs) == 0
    assert await forage.empty_area(session, upstairs) == 0
    assert await forage.view(session, constants, catalog, body, upstairs) is None
    with pytest.raises(estate.EstateError):
        await estate.construct(session, constants, body, upstairs, 20)
    with pytest.raises(farm.FarmError):
        await farm.mark(session, constants, body, name="грядка", area=20)


async def test_the_floors_are_held_by_whoever_holds_the_plot(
    session: AsyncSession, constants: Constants
) -> None:
    """A storey is not bought or sold apart from the ground it stands on."""
    node = await _plot(session)
    body = await _holder(session, node)
    _, rooms = await _house(session, constants, node, footprint=40, floors=3)
    assert all(room.owner_identity_id == body.identity_id for room in rooms)

    buyer = await world.create_identity(session, f"Покупатель-{uuid.uuid4().hex[:6]}")
    await world.hand_over(session, node, buyer.id)
    for room in rooms:
        await session.refresh(room)
        assert room.owner_identity_id == buyer.id, "этаж ушёл вместе с участком"


async def test_a_storey_stands_in_the_city_below_it(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Laws, taxes and the market do not stop at the first floor.

    And the land tax is still charged once, on the plot: the floors take no
    ground, which is the whole point of height (D-125, D-221).
    """
    from tests.test_estate import _city

    city, _, node, _ = await _city(session, catalog)
    _, rooms = await _house(session, constants, node, footprint=40, floors=2)

    assert (await town.of_node(session, rooms[0])).id == city.id
    assert await estate.land_tax_of(session, constants, catalog, rooms[0]) == 0


async def test_a_floor_of_a_civic_house_is_not_nobody_s(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A sub-node with no holder of its own is disposed of by its place (D-247).

    Read as land, a storey of a civic house was nobody's -- no holder on the
    row and no city either -- and any passer-by could carry a machine up into
    it, or take one out.
    """
    from src.engine import station
    from tests.test_estate import _city

    _, _, node, _ = await _city(session, catalog)
    _, rooms = await _house(session, constants, node, footprint=40, floors=2)
    stranger = await world.create_identity(session, f"Прохожий-{uuid.uuid4().hex[:6]}")
    body = await world.print_body(session, stranger, rooms[0])

    assert not await station.may_build(session, body, rooms[0])
    assert not await station.may_build(session, body, node), "и внизу тоже нельзя"


async def test_the_floors_are_not_holdings_of_their_own(
    session: AsyncSession, constants: Constants
) -> None:
    """A floor comes with the plot: it has no meter and no bill (D-149, D-247)."""
    node = await _plot(session)
    body = await _holder(session, node)
    await _house(session, constants, node, footprint=40, floors=4)

    listed = await utility.holdings(session, constants, body.identity_id)
    assert [row["node"] for row in listed] == [node.key]


async def test_the_meter_of_a_floor_is_the_meter_below(
    session: AsyncSession, constants: Constants
) -> None:
    """A workshop upstairs is lit by the house, and shut down with it (D-149)."""
    node = await _plot(session)
    await _holder(session, node)
    _, rooms = await _house(session, constants, node, footprint=40, floors=2)

    assert await utility.payer_of(session, rooms[0]) == await utility.payer_of(session, node)
    assert await utility.cut_off(session, rooms[0]) == await utility.cut_off(session, node)


async def test_the_window_upstairs_says_which_floor_it_is(
    session: AsyncSession, constants: Constants
) -> None:
    """`look` names the storey and describes the floor one stands on (D-225, D-247).

    The client cannot work either out: a storey carries no building record of
    its own, and its area alone says nothing about height. Downstairs the key
    is absent, and the land windows have their answers there.
    """
    from src.api.commands.look import _look

    node = await _plot(session, area=200)
    body = await _holder(session, node)
    _, rooms = await _house(session, constants, node, footprint=40, floors=3)

    seen = (await _look({"identity_id": body.identity_id}, session, {}))["look"]
    assert "storey" not in seen["node"], "на земле этажа нет — там участок"
    assert seen["node"]["building"]["area"] == 120, "у участка площадь всего дома"

    body.node_id = rooms[1].id
    await session.flush()
    seen = (await _look({"identity_id": body.identity_id}, session, {}))["look"]
    assert seen["node"]["storey"] == 3
    assert seen["node"]["building"]["area"] == 40, "у этажа своя площадь — пятно"
    assert seen["node"]["building"]["floors"] == 3, "и высота дома, чтобы сказать «3-й из 3»"
    assert seen["floor"]["space"]["area"] == 40
    assert "ground" not in seen, "наверху земли нет, и списка на ней тоже"
    assert "door" not in seen["node"], "дверь осталась внизу"


async def test_the_window_offers_no_door_on_a_city_location(
    session: AsyncSession, constants: Constants
) -> None:
    """A gate switch whose every button refuses is worse than none (D-282).

    Whether a location has a door is the engine's question, and the window used
    to answer half of it on its own -- "is it mine and is it ground". On a city
    location still standing in somebody's name that came out `true`, and the
    holder was shown a gate and two list fields where `access.set_gate` and
    `access.add` both refuse with `access-no-holder`.
    """
    from src.api.commands.look import _look

    node = await _plot(session)
    body = await _holder(session, node)
    seen = (await _look({"identity_id": body.identity_id}, session, {}))["look"]
    assert "door" in seen["node"], "у своего участка дверь есть"

    #: The same node, now the city's own location: a title, and no door.
    node.owner_city_id = uuid.uuid4()
    await session.flush()
    seen = (await _look({"identity_id": body.identity_id}, session, {}))["look"]
    assert "door" not in seen["node"], "у городской локации двери нет"


# --- two hands on one plot ----------------------------------------------------


async def test_a_house_and_a_bed_do_not_take_the_same_metres(
    factory: async_sessionmaker[AsyncSession], constants: Constants
) -> None:
    """Two sessions spending the plot's remainder in the same second (D-246).

    The plot's area is a remainder like money and grain, and since D-246 **two
    different commands** spend it against one sum: a house takes its footprint,
    a strip takes its metres. Both read `free_ground` and then write, so without
    the plot's row taken for the transaction "build sixty" and "mark out sixty"
    both pass on a plot of a hundred -- and nothing afterwards ever re-adds the
    parts to notice.
    """
    async with factory() as session, session.begin():
        #: Nobody's land beyond the walls: work on it is open to everyone
        #: (D-198), so both hands reach the same ground and the race is about
        #: the metres rather than about whose plot it is.
        node = await _plot(session, area=100)
        builder = await world.print_body(
            session,
            await world.create_identity(session, f"Первый-{uuid.uuid4().hex[:6]}"),
            node,
        )
        farmer = await world.print_body(
            session,
            await world.create_identity(session, f"Второй-{uuid.uuid4().hex[:6]}"),
            node,
        )
        #: The builder's timber, so the race is about the ground and nothing else.
        pocket = await world.body_container(session, builder)
        for name, quantity in estate.estimate(
            constants, footprint=60, floors=1, kind=estate.kinds(constants)[0]
        ).items():
            await world.grant_item(
                session, pocket, name, amount=quantity + 1, quality=60, origin="тест"
            )
        node_id, builder_id, farmer_id = node.id, builder.id, farmer.id

    ready = asyncio.Barrier(2)

    async def build() -> None:
        async with factory() as db, db.begin():
            body = await db.get(Body, builder_id)
            place = await db.get(Node, node_id)
            await ready.wait()
            await estate.construct(db, constants, body, place, 60)

    async def mark() -> None:
        async with factory() as db, db.begin():
            body = await db.get(Body, farmer_id)
            await ready.wait()
            await farm.mark(db, constants, body, name="грядка", area=60)

    both = await asyncio.gather(build(), mark(), return_exceptions=True)

    async with factory() as check:
        place = await check.get(Node, node_id)
        left = await estate.free_ground(check, place)
    #: One of the two must have been refused -- and by the ground, not by luck.
    assert left >= 0, (
        f"участок ушёл в минус ({left:g} м²): дом и делянка взяли одни и те же метры; {both}"
    )
    refused = [one for one in both if isinstance(one, Exception)]
    assert len(refused) == 1, f"один из двух обязан получить отказ, а получили: {both}"
    assert isinstance(refused[0], (estate.NoRoom, farm.NoLand)), refused[0]


async def test_a_collapse_does_not_race_a_build_on_one_plot(
    factory: async_sessionmaker[AsyncSession], constants: Constants
) -> None:
    """The two ends of a house's life meeting on one plot leave it whole.

    They run in different places -- a build finishes in the worker's journal,
    the daily wear runs in the tick -- and both decide the plot's floors from
    `height_of`, which is a reading of a remainder followed by a write.

    The harmful second is the one staged here: the builder has raised its house
    and opened its floors, and **has not committed**. A wear that reads then
    sees a plot of no height at all and cuts the stairs to four storeys that
    are about to exist -- leaving a house standing with no way into any of them,
    machines and cargo included, until somebody builds or demolishes here again.
    Both ends take the plot's row (`estate.hold_ground`), so one of them waits;
    without it this assertion comes back `[True, True, True] != [False, False,
    False]`.
    """
    async with factory() as session, session.begin():
        node = await _plot(session, area=400)
        old_house, rooms = await _house(session, constants, node, footprint=40, floors=4)
        node_id, house_id = node.id, old_house.id
        assert len(rooms) == 3

    holding = asyncio.Event()

    async def build() -> None:
        async with factory() as db, db.begin():
            place = await db.get(Node, node_id)
            #: The plot is held for the whole of the work, as `construct` holds it.
            await estate.hold_ground(db, place)
            holding.set()
            db.add(
                Building(
                    node_id=place.id,
                    area_m2=200,
                    footprint_m2=50,
                    floors=4,
                    kind=estate.kinds(constants)[0],
                )
            )
            await db.flush()
            await estate.open_storeys(db, constants, place)
            #: The house stands and its floors are open -- **and none of it is
            #: committed yet**. This is the second the wear beside it must not
            #: be allowed to read: it would see a plot of no height at all and
            #: cut the stairs to four storeys that are about to exist.
            await asyncio.sleep(0.2)

    async def fall() -> None:
        await holding.wait()
        async with factory() as db, db.begin():
            place = await db.get(Node, node_id)
            doomed = await db.get(Building, house_id)
            await estate.collapse(db, place, doomed)

    await asyncio.gather(build(), fall(), return_exceptions=True)

    async with factory() as check:
        place = await check.get(Node, node_id)
        height = await estate.height_of(check, place)
        rooms = await estate.storeys_of(check, place)
        reachable = []
        for step, room in enumerate(rooms):
            below = place if step == 0 else rooms[step - 1]
            reachable.append(await _stairs(check, below, room) is not None)
        standing = [await estate.storey_area(check, room) > 0 for room in rooms]
    #: Every floor with metres has a way in, and every floor without has none.
    assert standing == reachable, (
        f"дом в {height} эт.: этажи с метрами {standing}, с лестницей {reachable}"
    )


# --- and the house is one thing ----------------------------------------------


async def test_demolition_brings_the_floors_down_into_the_yard(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """What was upstairs comes to lie in the yard, and the people come down."""
    node = await _plot(session, area=400)
    body = await _holder(session, node)
    house, rooms = await _house(session, constants, node, footprint=40, floors=2)
    upstairs = rooms[0]

    body.node_id = upstairs.id
    await session.flush()
    pocket = await world.body_container(session, body)
    goods = await world.grant_item(session, pocket, "pipe", amount=2, origin="тест")
    await storage.drop(session, constants, catalog, body, goods)

    #: Demolition is ordered on foot from the plot: one takes a house apart from
    #: the ground, not from its third floor.
    body.node_id = node.id
    await session.flush()
    job = await estate.demolish(session, constants, body, node)
    await estate.finish_demolish(session, job)

    #: The rooms stay -- nothing here is deleted (D-007) -- but nothing holds
    #: them up and nothing leads to them any more.
    left = await estate.storeys_of(session, node)
    assert [estate.storey_of(room) for room in left] == [2]
    assert await estate.storey_area(session, left[0]) == 0
    assert await _stairs(session, node, left[0]) is None
    _, outside = await estate.split(session, node)
    assert any(thing.type_key == "pipe" for thing in outside), "трубу вынесли во двор"
    assert house.floors == 2  # the record itself is gone with the rest


async def test_a_floor_walked_to_still_comes_down(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A climbed staircase writes a transit, and the floor must still fall.

    This is the whole reason a floor is closed rather than deleted (D-007,
    D-247): `travel` keeps a row for every leg ever walked, and two dozen other
    tables keep one for every word said and every order made in a place. A node
    somebody has been to cannot be taken out of the world, and a collapse that
    tried would break the daily wear of **every** house there is -- the tick
    rolls back whole, and comes back to the same wall the next day.
    """
    from src.engine import travel
    from src.models.job import Job, JobKind, JobState

    node = await _plot(session, area=400)
    body = await _holder(session, node)
    house, rooms = await _house(session, constants, node, footprint=40, floors=2)
    upstairs = rooms[0]

    #: On foot, up the stairs -- not by writing `node_id`.
    await travel.depart(session, constants, body, upstairs)
    leg = (
        await session.execute(
            select(Job).where(
                Job.body_id == body.id,
                Job.kind == JobKind.TRAVEL_LEG.value,
                Job.state == JobState.PENDING,
            )
        )
    ).scalar_one()
    await travel.arrive(session, leg)
    assert body.node_id == upstairs.id, "тело не поднялось по лестнице"

    await estate.collapse(session, node, house)

    assert await estate.storey_area(session, upstairs) == 0
    assert await _stairs(session, node, upstairs) is None
    await session.refresh(body)
    assert body.node_id == node.id, "тело спустилось, а не осталось наверху"


async def test_a_body_on_the_stairs_is_turned_back(
    session: AsyncSession, constants: Constants
) -> None:
    """A floor closing under somebody on the way up must not leave them there.

    The leg would fire on schedule and put the body down on a floor with no
    metres and no stairs -- a node nothing leads out of, which is the one thing
    this world does not have (pillar P6).
    """
    from src.engine import travel
    from src.models.travel import Travel, TravelState

    node = await _plot(session, area=400)
    body = await _holder(session, node)
    house, rooms = await _house(session, constants, node, footprint=40, floors=2)

    going = await travel.depart(session, constants, body, rooms[0])
    assert going.state is TravelState.GOING

    await estate.collapse(session, node, house)

    await session.refresh(going)
    assert going.state is TravelState.CANCELLED, "подъём не отменили"
    assert body.node_id == node.id, "тело осталось идти на этаж, которого нет"
    assert (
        await session.scalar(
            select(func.count()).select_from(Travel).where(Travel.state == TravelState.GOING)
        )
        == 0
    )


async def test_a_house_built_again_walks_back_into_the_same_rooms(
    session: AsyncSession, constants: Constants
) -> None:
    """A closed floor is reopened, not made anew (D-007, D-247).

    The node stayed -- it had to, everything ever done in it points at it -- so
    building up to that height again gives back the same room with the name its
    owner gave it, and cuts no second staircase.
    """
    node = await _plot(session, area=400)
    await _holder(session, node)
    house, rooms = await _house(session, constants, node, footprint=40, floors=3)
    rooms[0].name = "workshop"
    await session.flush()
    keys = [room.key for room in rooms]

    await estate.collapse(session, node, house)
    assert [room.key for room in await estate.storeys_of(session, node)] == keys

    session.add(
        Building(
            node_id=node.id,
            area_m2=120,
            footprint_m2=40,
            floors=3,
            kind=estate.kinds(constants)[0],
        )
    )
    await session.flush()
    again = await estate.open_storeys(session, constants, node)

    assert [room.key for room in again] == keys, "этажи завели заново вместо того, чтобы открыть"
    assert again[0].name == "workshop", "имя, данное хозяином, пережило обрушение"
    assert await estate.storey_area(session, again[0]) == 40
    assert await _stairs(session, node, again[0]) is not None, "лестницу прорезали снова"


async def test_a_collapse_buries_the_upper_floors(
    session: AsyncSession, constants: Constants
) -> None:
    """A storey has no yard to be rained on: everything on it is under the roof."""
    node = await _plot(session, area=400)
    body = await _holder(session, node)
    house, rooms = await _house(session, constants, node, footprint=40, floors=2)
    upstairs = rooms[0]

    store = await world.node_container(session, upstairs)
    await world.grant_item(session, store, "pipe", amount=3, origin="тест")
    body.node_id = upstairs.id
    await session.flush()

    await estate.collapse(session, node, house)

    assert await estate.storey_area(session, upstairs) == 0, "этаж потерял стены"
    assert await _stairs(session, node, upstairs) is None, "лестницу срезало"
    await session.refresh(body)
    assert body.node_id == node.id, "тело спустилось, а не осталось в исчезнувшем узле"
    left = await world.contents(session, await world.node_container(session, node))
    assert not [thing for thing in left if thing.type_key == "pipe"]
