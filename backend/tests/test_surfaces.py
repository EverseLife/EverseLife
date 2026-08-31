# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A node has two surfaces: the floor of the house and the ground beside it (D-244).

It used to have one, and what it meant depended on whether a roof happened to
stand: goods on an empty plot lay in the open, and the same rows became indoors
the day a house went up over them. That is why a collapse could only guess what
it was burying, and why the land window had no list of its own on a built-up
plot.

What is checked is the whole of the split:

* two surfaces, each with its own metres -- the house's floor, and the plot
  minus its footprint -- told apart by a mark on the thing, not by a second
  store: everything that asks a node "what is in you" must go on getting the
  whole answer;
* a house that covers the whole plot leaves no ground at all;
* what lies in the open survives the house falling on the other half of the
  plot; what was indoors does not;
* machines never reach the open ground: they are placed into a building (D-106);
* two heaps of one ore, one indoors and one out, do not fold into each other;
* a machine carried in from the yard stands **in** the house and falls with it:
  a collapse asks what a thing is, never what somebody marked it;
* what the engine puts into a node without saying where -- loot from a death,
  cargo from a broken cart -- is visible on a plot with no house at all.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import estate, gear, storage, world
from src.models.estate import Building
from src.models.identity import Body
from src.models.inventory import Item
from src.models.world import Node
from src.units import amount_float

CARGO = "iron_ore"
BENCH = "workbench"
CHEST = "chest"


async def _plot(session: AsyncSession, *, area: float = 400) -> Node:
    return await world.create_node(
        session, f"terra.lot.{uuid.uuid4().hex[:8]}", "Участок", area_m2=area
    )


async def _holder(session: AsyncSession, node: Node) -> Body:
    identity = await world.create_identity(session, f"Хозяин-{uuid.uuid4().hex[:6]}")
    body = await world.print_body(session, identity, node)
    node.owner_identity_id = identity.id
    await session.flush()
    return body


async def _house(
    session: AsyncSession, node: Node, *, area: float, ground: float, floors: int = 1
) -> Building:
    house = Building(node_id=node.id, area_m2=area, footprint_m2=ground, floors=floors)
    session.add(house)
    await session.flush()
    return house


async def _in_hand(session: AsyncSession, body: Body, what: str, amount: float):
    pocket = await world.body_container(session, body)
    return await world.grant_item(session, pocket, what, amount=amount, origin="тест")


async def _put(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    kilograms: float,
    *,
    indoors: bool,
) -> None:
    """Put this many **kilograms** of ore down on one of the surfaces.

    By weight rather than by count: area is paid for by mass
    (`build.floor_per_m2`), and a test that counted pieces would be reading a
    different number from the one the engine charges.
    """
    per_unit = gear.mass_of(catalog, CARGO, 1)
    thing = await _in_hand(session, body, CARGO, kilograms / per_unit)
    await storage.drop(session, constants, catalog, body, thing, indoors=indoors)


# --- two places, two budgets --------------------------------------------------


async def test_a_plot_with_no_house_is_all_ground(
    session: AsyncSession, constants: Constants
) -> None:
    """No building, no indoors: the floor has no metres and the yard has the plot."""
    node = await _plot(session, area=400)

    floor = await estate.space(session, constants, node)
    ground = await estate.yard(session, constants, node)
    assert floor["area"] == 0, "пола нет, пока нет дома"
    assert ground["area"] == 400


async def test_a_house_takes_its_footprint_out_of_the_yard(
    session: AsyncSession, constants: Constants
) -> None:
    """Two storeys give twenty metres of floor off ten of plot (D-125): the yard
    loses the ten -- and the floor of the plot is the **ground** floor (D-247).

    The other storey is a node of its own with its own twenty metres, so the sum
    is unchanged and the place one stands in is honest about how much of it is
    underfoot.
    """
    node = await _plot(session, area=400)
    await _house(session, node, area=200, ground=100, floors=2)

    floor = await estate.space(session, constants, node)
    ground = await estate.yard(session, constants, node)
    assert floor["area"] == 100, "пол участка — первый этаж, а не сумма этажей"
    assert ground["area"] == 300, "двор — участок минус пятно дома"

    upstairs = await estate.open_storeys(session, constants, node)
    assert [estate.storey_of(room) for room in upstairs] == [2]
    above = await estate.space(session, constants, upstairs[0])
    assert above["area"] == 100, "этаж — это пятно застройки"
    assert (await estate.yard(session, constants, upstairs[0]))["area"] == 0, "наверху земли нет"


async def test_a_house_over_the_whole_plot_leaves_no_ground(
    session: AsyncSession, constants: Constants
) -> None:
    """Then there is nowhere to put anything down, and the window says nothing."""
    node = await _plot(session, area=120)
    await _house(session, node, area=240, ground=120)

    ground = await estate.yard(session, constants, node)
    assert ground["area"] == 0


# --- putting things down on one or the other ---------------------------------


async def test_a_thing_goes_where_the_hand_says(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The window names the surface; the engine does not guess between two."""
    node = await _plot(session, area=400)
    await _house(session, node, area=200, ground=100)
    body = await _holder(session, node)
    indoors = await _in_hand(session, body, CARGO, 10)
    outdoors = await _in_hand(session, body, "coal", 10)

    await storage.drop(session, constants, catalog, body, indoors, indoors=True)
    await storage.drop(session, constants, catalog, body, outdoors, indoors=False)

    on_floor = {thing.type_key for thing in await storage.lying(session, node)}
    on_ground = {thing.type_key for thing in await storage.lying(session, node, indoors=False)}
    assert on_floor == {CARGO}
    assert on_ground == {"coal"}


async def test_without_a_house_there_is_nowhere_indoors_to_put_it(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Asked for a floor that does not exist, the engine refuses rather than
    inventing one."""
    node = await _plot(session, area=400)
    body = await _holder(session, node)
    thing = await _in_hand(session, body, CARGO, 5)

    with pytest.raises(storage.NoRoom):
        await storage.drop(session, constants, catalog, body, thing, indoors=True)


async def test_unsaid_means_indoors_where_there_is_a_roof(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The answer a person would give without thinking, and the old behaviour."""
    roofed = await _plot(session, area=400)
    await _house(session, roofed, area=200, ground=100)
    inside = await _holder(session, roofed)
    await storage.drop(
        session, constants, catalog, inside, await _in_hand(session, inside, CARGO, 4)
    )
    assert len(await storage.lying(session, roofed)) == 1
    assert await storage.lying(session, roofed, indoors=False) == []

    bare = await _plot(session, area=400)
    outside = await _holder(session, bare)
    await storage.drop(
        session, constants, catalog, outside, await _in_hand(session, outside, CARGO, 4)
    )
    assert await storage.lying(session, bare) == []
    assert len(await storage.lying(session, bare, indoors=False)) == 1


async def test_the_yard_has_its_own_metres_to_run_out_of(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Area is finite on both surfaces, and a full floor does not fill the yard."""
    node = await _plot(session, area=400)
    await _house(session, node, area=20, ground=20)
    body = await _holder(session, node)

    #: More than the small house holds, and far less than the yard does.
    heap = await _in_hand(session, body, CARGO, 4000)
    with pytest.raises(storage.NoRoom):
        await storage.drop(session, constants, catalog, body, heap, 4000, indoors=True)
    assert await storage.drop(session, constants, catalog, body, heap, 4000, indoors=False) > 0


# --- the house falls, the yard does not ---------------------------------------


async def test_what_lies_in_the_open_outlives_the_house(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A house falling on one half of a plot does not crush the other half.

    Before the split the engine kept one yard per node and could tell what was
    roofed only by whether a roof was left at all -- so a collapse took the lot.
    """
    node = await _plot(session, area=400)
    house = await _house(session, node, area=200, ground=100)
    body = await _holder(session, node)
    await storage.drop(
        session,
        constants,
        catalog,
        body,
        await _in_hand(session, body, CARGO, 10),
        indoors=True,
    )
    await storage.drop(
        session,
        constants,
        catalog,
        body,
        await _in_hand(session, body, "coal", 10),
        indoors=False,
    )

    await estate.collapse(session, node, house)

    assert await storage.lying(session, node) == [], "под крышей всё погибло"
    survived = await storage.lying(session, node, indoors=False)
    assert [thing.type_key for thing in survived] == ["coal"], "во дворе всё уцелело"


# --- reading writes nothing ---------------------------------------------------


async def test_two_heaps_of_one_ore_do_not_fold_across_the_two_surfaces(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Folding them would move half a heap indoors -- out of the rain, and out
    of the reach of a collapse (D-214, D-244)."""
    node = await _plot(session, area=400)
    await _house(session, node, area=200, ground=100)
    body = await _holder(session, node)

    await storage.drop(
        session, constants, catalog, body, await _in_hand(session, body, CARGO, 5), indoors=True
    )
    await storage.drop(
        session, constants, catalog, body, await _in_hand(session, body, CARGO, 7), indoors=False
    )

    inside = await storage.lying(session, node)
    outside = await storage.lying(session, node, indoors=False)
    assert [amount_float(thing.amount) for thing in inside] == [5]
    assert [amount_float(thing.amount) for thing in outside] == [7]


async def test_what_the_engine_drops_on_a_bare_plot_is_out_in_the_open(
    session: AsyncSession, constants: Constants
) -> None:
    """Loot from a death, cargo from a broken cart, materials back from a
    demolition: none of them name a surface, and on a plot with no house there
    is none to name. Everything lying there is outdoors, whatever the mark says.

    This is what lets the rest of the engine stay ignorant of the split -- and
    what a second store took away, dropping such things into a floor that did
    not exist and out of every window on the screen.
    """
    node = await _plot(session, area=400)
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, CARGO, amount=30, origin="тест")

    assert await storage.lying(session, node) == [], "пола нет — значит и на полу ничего"
    outside = await storage.lying(session, node, indoors=False)
    assert [thing.type_key for thing in outside] == [CARGO]
    assert (await estate.yard(session, constants, node))["cargo_mass"] > 0


async def test_a_bench_carried_through_the_yard_still_falls_with_the_house(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A collapse asks what a thing **is**, not what anybody marked it.

    Two ordinary commands -- carry the bench out, put it down on the ground,
    carry it back in -- used to leave it marked "outdoors" while it stood in
    the house, kept its slot and was worked at. The collapse then walked past
    it. Every machine and every chest in a house was one round trip away from
    being proof against the roof falling on it.
    """
    from src.engine import station

    node = await _plot(session, area=400)
    house = await _house(session, node, area=200, ground=100)
    body = await _holder(session, node)

    bench = await _in_hand(session, body, BENCH, 1)
    #: Down in the yard first, then up into the house: the round trip.
    await storage.drop(session, constants, catalog, body, bench, indoors=False)
    assert bench.outdoors is True
    await storage.pick(session, constants, catalog, body, bench)
    await station.place(session, catalog, body, bench)
    assert bench.outdoors is False, "поставленное в здание стоит под крышей"

    await estate.collapse(session, node, house)
    assert await session.get(type(bench), bench.id) is None, "верстак погиб с домом"


async def test_a_chest_marked_outdoors_still_falls_with_the_house(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The same rule read from the other side: the mark on a chest is not
    trusted, because a chest is indoors by what it is (D-181)."""
    node = await _plot(session, area=400)
    house = await _house(session, node, area=200, ground=100)
    body = await _holder(session, node)

    chest = await _in_hand(session, body, CHEST, 1)
    await storage.drop(session, constants, catalog, body, chest, indoors=False)
    assert chest.outdoors is True

    await estate.collapse(session, node, house)
    assert await session.get(type(chest), chest.id) is None


async def test_demolition_weighs_both_surfaces_against_the_plot(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The roof goes, and what was under it comes to lie beside what was already
    outside. Asking only about the floor let a demolition through that left the
    ground overloaded, and quoted a capacity nobody had been measured against.

    Only a house of several storeys can get into this: a single-storey one gives
    exactly as much floor as it takes of the plot, so the two surfaces together
    never hold more than the plot would (D-125).
    """
    #: Forty metres of plot, twenty of footprint, ten storeys of floor: room
    #: indoors for far more than the bare plot could ever take. Since D-247 that
    #: floor is ten rooms of twenty metres, and the load is read across them all.
    node = await _plot(session, area=40)
    await _house(session, node, area=200, ground=20, floors=10)
    rooms = await estate.open_storeys(session, constants, node)
    body = await _holder(session, node)
    holds = 40 * constants[R.BUILD_FLOOR_PER_M2]

    #: The yard alone is well within what the plot holds.
    await _put(session, constants, catalog, body, holds * 0.4, indoors=False)
    assert await estate.demolish_blockers(session, constants, node) == []

    #: And no single floor is over its own capacity -- but everything the house
    #: holds, brought down at once, is more than the plot can take.
    for room in rooms[:2]:
        body.node_id = room.id
        await session.flush()
        await _put(session, constants, catalog, body, holds * 0.4, indoors=True)
    body.node_id = node.id
    await session.flush()
    blockers = await estate.demolish_blockers(session, constants, node)
    assert "estate-blocker-overloaded" in [one.key for one in blockers], blockers


async def test_two_hands_on_one_heap_take_it_once(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Two people pulling from the same heap on the floor, in the same second.

    A stack lying in a node is a shared quantity like any other, and the guard
    is inside `world.move_stack`: it takes the row for the transaction and
    rereads the remainder before it clamps. Without it both would read ten,
    both would write four, and six of the ore would be two heaps of six.
    """
    async with factory() as session, session.begin():
        node = await _plot(session, area=400)
        first = await _holder(session, node)
        second = await world.print_body(
            session,
            await world.create_identity(session, f"Второй-{uuid.uuid4().hex[:6]}"),
            node,
        )
        heap = await _in_hand(session, first, CARGO, 10)
        await storage.drop(session, constants, catalog, first, heap, indoors=False)
        node_id, one_id, two_id = node.id, first.id, second.id
        heap_id = heap.id

    ready = asyncio.Barrier(2)

    async def take(body_id) -> float:
        async with factory() as db, db.begin():
            mine = await db.get(Body, body_id)
            thing = await db.get(Item, heap_id)
            await ready.wait()
            if thing is None:
                return 0.0
            return await storage.pick(db, constants, catalog, mine, thing, 6)

    taken = await asyncio.gather(take(one_id), take(two_id), return_exceptions=True)
    got = sum(value for value in taken if isinstance(value, float))

    async with factory() as session:
        node = await session.get(Node, node_id)
        left = sum(
            amount_float(thing.amount)
            for thing in await storage.lying(session, node, indoors=False)
        )
    assert got + left == pytest.approx(10, abs=0.01), (
        f"взяли {got}, осталось {left} — руда размножилась"
    )
