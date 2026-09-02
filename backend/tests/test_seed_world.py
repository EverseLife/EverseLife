# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The layout interpreter, and the contract nothing else states (D-243).

`test_seed.py` asks whether the world that comes out is a world the engine
recognises. This one asks the question underneath it: **what does laying the
scenario onto a session do the second time, and the tenth?**

That question used to be answered by a hand-written catch-up -- a per-node list
of "add this if it is missing" -- and it was answered wrong more than once,
because the list and the seed were two places saying the same thing. Now there
is one place, and its promise is exactly four lines:

* a node the world already has is **left alone**, whatever the world did to it;
* a node the world lost, or never had, **arrives whole** -- veins, stocks, place;
* machines are **topped up** every run, so a world laid before the scenario
  gained one learns it on the next deploy;
* a vein is laid **once and never again** (pillar P2): the world works it out,
  and a deploy must not refill what the world spent.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src import seed_world
from src.constants import Constants
from src.constants import registry as R
from src.engine import death, justice, market, places, travel, world
from src.models.estate import Building
from src.models.inventory import Container, Item
from src.models.world import Edge, Layer, Node, Planet, Surface, Vein
from src.runtime import CITY_NAME_LIMIT
from src.seed import seed


@pytest.fixture
async def capital(session: AsyncSession) -> Node:
    return await seed(session)


async def _node(session: AsyncSession, key: str) -> Node | None:
    return await session.scalar(select(Node).where(Node.key == key))


async def _yard_names(session: AsyncSession, node: Node) -> list[str]:
    yard = await world.node_container(session, node)
    return sorted(thing.type_key for thing in await world.contents(session, yard))


async def test_laying_the_scenario_again_adds_nothing(
    capital: Node, session: AsyncSession, constants: Constants
) -> None:
    """The one property the whole catch-up rests on.

    Not "the seed is idempotent" -- that was true before too, node by node --
    but that the *interpreter* is, for every node of every scenario, without
    anybody remembering to write the second half.
    """
    before = (
        await session.scalar(select(func.count()).select_from(Node)),
        await session.scalar(select(func.count()).select_from(Edge)),
        await session.scalar(select(func.count()).select_from(Vein)),
        await session.scalar(select(func.sum(Item.amount))),
    )
    await seed_world.apply(session, constants)
    await seed_world.apply(session, constants)
    after = (
        await session.scalar(select(func.count()).select_from(Node)),
        await session.scalar(select(func.count()).select_from(Edge)),
        await session.scalar(select(func.count()).select_from(Vein)),
        await session.scalar(select(func.sum(Item.amount))),
    )
    assert before == after


async def test_a_worked_out_vein_is_not_refilled(
    capital: Node, session: AsyncSession, constants: Constants
) -> None:
    """Veins are finite, and that is irrevocable (pillar P2).

    A deploy that put the ore back would undo the one thing the mine's whole
    economy is built on -- and it would do it quietly, on a release schedule.
    """
    face = await _node(session, "terra.capital.pit")
    vein = await session.scalar(select(Vein).where(Vein.node_id == face.id))
    vein.remaining = 7
    vein.extracted = 999
    await session.flush()

    await seed_world.apply(session, constants)

    again = await session.scalar(select(Vein).where(Vein.node_id == face.id))
    assert again.remaining == 7
    assert (
        await session.scalar(select(func.count()).select_from(Vein).where(Vein.node_id == face.id))
        == 1
    )


async def test_a_node_the_world_lost_comes_back_whole(
    capital: Node, session: AsyncSession, constants: Constants
) -> None:
    """A world laid before the scenario gained a node gets it on the next deploy.

    Checked by taking one away rather than by inventing an old world: what an
    old world is, is exactly "the scenario has a node and the database does
    not", and the node has to arrive with everything hanging off it.
    """
    prison = await _node(session, "terra.capital.jail")
    for vein in (await session.execute(select(Vein).where(Vein.node_id == prison.id))).scalars():
        await session.delete(vein)
    for edge in (
        await session.execute(
            select(Edge).where((Edge.node_a_id == prison.id) | (Edge.node_b_id == prison.id))
        )
    ).scalars():
        await session.delete(edge)
    #: Every container the node owns, and every row in them: a node has both a
    #: floor and a yard (D-244), and `world.contents` answers what stands there
    #: rather than what the table holds -- half a deletion is a foreign key
    #: violation away.
    boxes = list(
        (await session.execute(select(Container).where(Container.owner_id == prison.id))).scalars()
    )
    for box in boxes:
        for thing in (
            await session.execute(select(Item).where(Item.container_id == box.id))
        ).scalars():
            await session.delete(thing)
    await session.flush()
    for box in boxes:
        await session.delete(box)
    #: And the building the machines stood in (D-106).
    for roof in (
        await session.execute(select(Building).where(Building.node_id == prison.id))
    ).scalars():
        await session.delete(roof)
    await session.flush()
    await session.delete(prison)
    await session.flush()
    assert await _node(session, "terra.capital.jail") is None

    await seed_world.apply(session, constants)

    back = await _node(session, "terra.capital.jail")
    assert back is not None
    assert float(back.area_m2) == 120
    assert await session.scalar(select(Vein).where(Vein.node_id == back.id)) is not None
    #: The machines that make it a prison at all (D-174, D-176).
    for thing_class in (justice.PRISON_CLASS, death.PRINTER, market.TERMINAL):
        assert await world.has_station(session, back, thing_class), thing_class
    gate = await _node(session, "terra.capital.gate")
    assert await travel._edge_between(session, gate.id, back.id) is not None


async def test_a_machine_the_scenario_gained_is_put_up(
    capital: Node, session: AsyncSession, constants: Constants
) -> None:
    """What the hand-written catch-up did one `_machine_if_missing` at a time.

    The scenario is edited here rather than in the vault: the test is about the
    interpreter's rule, and it must not go stale the day somebody rearranges
    the capital's workshop.
    """
    forge = await _node(session, "terra.capital.forge")
    scenario = seed_world.load_scenario()
    grown = replace(
        scenario,
        nodes=tuple(
            spec
            if spec.key != "terra.capital.forge"
            else replace(
                spec, machines=(*spec.machines, seed_world.Machine("Ткацкий станок", None, 55))
            )
            for spec in scenario.nodes
        ),
    )
    assert "Ткацкий станок" not in await _yard_names(session, forge)
    await seed_world.lay(session, constants, grown)
    assert "Ткацкий станок" in await _yard_names(session, forge)
    #: And not a second one on the run after.
    await seed_world.lay(session, constants, grown)
    names = [
        thing.type_key
        for thing in await world.contents(session, await world.node_container(session, forge))
    ]
    assert names.count("Ткацкий станок") == 1


async def test_a_kept_stock_is_replenished_and_a_plain_one_is_not(
    capital: Node, session: AsyncSession, constants: Constants
) -> None:
    """`ensure` is the difference between a stock the world must have and a
    stock the world was given once.

    Iron in the printer is the first (without it there is nothing to print
    from, D-013); the first delivery of coal to the power station is the
    second -- players haul it from then on, and a deploy that topped it up
    would quietly take the supply problem away.
    """
    forge = await _node(session, "terra.capital.forge")
    yard = await world.node_container(session, forge)
    for thing in await world.contents(session, yard):
        if thing.type_key in (death.IRON, "coal"):
            await session.delete(thing)
    await session.flush()

    await seed_world.apply(session, constants)

    names = await _yard_names(session, forge)
    assert death.IRON in names, "запас железа в принтере обязан вернуться"
    assert "coal" not in names, "первый подвоз угля — разовый, дальше возят игроки"


async def test_a_node_the_world_changed_is_left_alone(
    capital: Node, session: AsyncSession, constants: Constants
) -> None:
    """The world is eternal and has no wipes (D-007).

    Somebody bought the plot, somebody paved the road, somebody widened the
    yard -- and a deploy is not the moment any of that is taken back.
    """
    plot = await _node(session, "terra.capital.lot1")
    identity = await world.create_identity(session, "Хозяйка")
    plot.owner_identity_id = identity.id
    plot.owner_city_id = None
    plot.name = "Двор Хозяйки"
    plot.properties = {**(plot.properties or {}), "ring": 2, "обжито": True}
    forge = await _node(session, "terra.capital.forge")
    road = await travel._edge_between(session, forge.id, plot.id)
    road.base_seconds = 99
    await session.flush()

    await seed_world.apply(session, constants)

    again = await _node(session, "terra.capital.lot1")
    assert again.owner_identity_id == identity.id
    assert again.name == "Двор Хозяйки"
    assert (again.properties or {}).get("обжито") is True
    assert (await travel._edge_between(session, forge.id, again.id)).base_seconds == 99


def _spec(key: str, **changes) -> seed_world.NodeSpec:
    """A node of a scenario written for a test, with the defaults of a plain plot."""
    return replace(
        seed_world.NodeSpec(
            key=key,
            name=key,
            layer=Layer.PLANET,
            planet=Planet.TERRA,
            parent=None,
            anchor=None,
            area_m2=100,
            place=None,
            city=False,
            properties={},
            machines=(),
            relics=(),
            veins=(),
            items=(),
        ),
        **changes,
    )


async def test_a_pinned_place_is_the_place_the_node_stands(
    session: AsyncSession, constants: Constants
) -> None:
    """A place written into the scenario is the place the engine lays (D-237).

    The whole worth of the editor's map is that what it shows is what the world
    gets; without this a pin would be a number in a file and nothing more.

    On a scenario of two nodes rather than on the capital: what is being asked
    is the interpreter's rule, and the answer must not depend on where the
    capital's core happens to stand today.
    """
    made = seed_world.Scenario(
        nodes=(
            _spec("test.pinned", place=(300.0, -120.0)),
            #: And its neighbour, which has no pin: it must still be seated by
            #: the engine, and not on top of the pinned one.
            _spec("test.loose", anchor="test.pinned"),
        ),
        edges=(),
        pockets={},
    )
    await seed_world.lay(session, constants, made)

    pinned = await _node(session, "test.pinned")
    loose = await _node(session, "test.loose")
    assert places.place_of(pinned) == (300.0, -120.0)
    assert places.place_of(loose) is not None
    assert places.place_of(loose) != places.place_of(pinned)


async def test_an_edge_by_reach_is_priced_by_the_far_end(
    session: AsyncSession, constants: Constants
) -> None:
    """«По дали» means the transit is priced by how far beyond the walls it goes
    (D-180), and every ring out is dearer than the last -- that is the whole
    geography, and a number typed into the file would quietly undo it."""
    made = seed_world.Scenario(
        nodes=(
            _spec("test.gate", properties={travel.EXIT: True}),
            _spec("test.near", anchor="test.gate", properties={travel.REACH: 1}),
            _spec("test.far", anchor="test.gate", properties={travel.REACH: 3}),
        ),
        edges=(
            seed_world.EdgeSpec("test.gate", "test.near", seed_world.BY_REACH, Surface.ROAD),
            seed_world.EdgeSpec("test.gate", "test.far", seed_world.BY_REACH, Surface.ROAD),
        ),
        pockets={},
    )
    await seed_world.lay(session, constants, made)

    gate = await _node(session, "test.gate")
    near = await travel._edge_between(session, gate.id, (await _node(session, "test.near")).id)
    far = await travel._edge_between(session, gate.id, (await _node(session, "test.far")).id)
    assert near.base_seconds == int(travel.frontier_seconds(constants, 1))
    assert far.base_seconds > near.base_seconds


async def test_a_city_step_is_the_same_on_two_servers(
    session: AsyncSession, constants: Constants
) -> None:
    """An edge with no length is a city step, rolled -- but rolled off the edge's
    own name, so two servers replaying one scenario lay one world (D-007)."""
    made = seed_world.Scenario(
        nodes=(_spec("test.one"), _spec("test.two", anchor="test.one")),
        edges=(seed_world.EdgeSpec("test.one", "test.two", None, Surface.PAVED),),
        pockets={},
    )
    await seed_world.lay(session, constants, made)
    one, two = await _node(session, "test.one"), await _node(session, "test.two")
    first = (await travel._edge_between(session, one.id, two.id)).base_seconds

    step = constants[R.TRAVEL_CITY_STEP]
    assert step.min <= first <= step.max
    #: The same roll on the next run: `connect` is idempotent, and even if it
    #: were not, the dice are the edge's name and not the clock.
    await seed_world.lay(session, constants, made)
    assert (await travel._edge_between(session, one.id, two.id)).base_seconds == first


def test_a_city_of_the_layout_is_named_within_the_founding_ceiling() -> None:
    """Every city the vault lays down carries a name founding would have allowed.

    The ceiling on a city's name lives in this repo (`runtime.CITY_NAME_LIMIT`),
    and the vault has its own copy of the number, because neither repository
    can import the other: CI carries `build/*.json` across and nothing else.
    Two copies of one number drift, and the drift is silent -- a vault name
    longer than this ceiling founds a city no player could have named, whose
    official channel then carries a name `net.channel.create` refuses.

    So what is measured here is not the vault's arithmetic but the outcome, and
    against **this** side's constant: the layout is the one thing both sides
    see. It fails whichever side moved -- the vault writing a longer name, or
    this repository lowering the ceiling under a layout already written.
    """
    scenario = seed_world.load_scenario()
    towns = [spec for spec in scenario.nodes if spec.city]
    assert towns, "в разметке есть хоть один город -- иначе проверять нечего"
    too_long = [(spec.key, spec.name) for spec in towns if len(spec.name) > CITY_NAME_LIMIT]
    assert not too_long, (
        f"имя города в разметке длиннее потолка основания ({CITY_NAME_LIMIT}): {too_long}"
    )


def test_the_layout_gives_no_two_cities_one_name() -> None:
    """No two cities of the layout share a name, case ignored.

    A city's name becomes the name of its official channel, and the Net tells
    channel names apart ignoring case. Two cities of one name would hand out
    two channels of one name -- which `net.channel.create` refuses from anybody
    who types it, and which `uq_city_name_lower` now refuses outright, so a
    layout that carried such a pair would fail to seed at all.
    """
    scenario = seed_world.load_scenario()
    seen: dict[str, str] = {}
    clashes: list[tuple[str, str]] = []
    for spec in (one for one in scenario.nodes if one.city):
        first = seen.setdefault(spec.name.lower(), spec.key)
        if first != spec.key:
            clashes.append((first, spec.key))
    assert not clashes, f"два города разметки носят одно имя: {clashes}"
