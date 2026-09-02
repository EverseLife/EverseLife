# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The starting world: built by the same rules as a player's city (D-216).

There was no test for the seed at all, and it cost dearly. Renaming the
engine's constants to thing classes (D-215) left the capital holding «Терминал»
and «Верфь» -- names the vault does not know -- and `python -m src.seed` died
on its first look at the marketplace. Seven hundred tests missed it, because
every one of them builds its own small world by hand and none of them builds
*the* world.

So this file builds the real one and asks the questions only it can answer:
is everything in it a thing the vault knows, did it come from a recipe, and
does the engine recognise the city it was handed.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src import seed_catchup, seed_parts, seed_world
from src.constants import Catalog, ConstantError, Constants, current_catalog, display_name
from src.constants import registry as R
from src.constants.catalog import ItemKind
from src.engine import (
    death,
    estate,
    events,
    explore,
    frost,
    justice,
    market,
    oxygen,
    ship,
    travel,
    world,
)
from src.models.city import City
from src.models.estate import Building
from src.models.event import Event, EventKind
from src.models.inventory import Container, ContainerKind, Item
from src.models.world import Layer, Node, Planet, Vein
from src.runtime import CITY_NAME_LIMIT
from src.seed import CORE, seed
from src.seed_surfaces import PYROXIS_FIELDS, PYROXIS_PLATEAU, pyroxis_field_key


def aurora_cities() -> list[str]:
    """The cities of Aurora **as the vault declares them** (D-232, D-243).

    Asked of the scenario rather than of a list frozen in the test: the three
    cities are a layout now, and a fourth one added in the editor's «Мир» tab
    must not break the test that says a ship can reach every one of them.
    """
    scenario = seed_world.load_scenario()
    return [
        spec.key
        for spec in scenario.nodes
        if spec.planet is Planet.AURORA and spec.layer is Layer.PLANET
    ]


def aurora_hall(city: str) -> str:
    return f"{city}.hall"


def aurora_port(city: str) -> str:
    return f"{city}.port"


@pytest.fixture
async def capital(session: AsyncSession) -> Node:
    """The starting world, created once for the test that asks about it."""
    return await seed(session)


async def _things(session: AsyncSession) -> list[tuple[str, str, int]]:
    rows = (
        await session.execute(
            select(Node.key, Item.type_key, func.sum(Item.amount))
            .join(Container, Container.owner_id == Node.id)
            .join(Item, Item.container_id == Container.id)
            .where(Container.kind == ContainerKind.NODE)
            .group_by(Node.key, Item.type_key)
        )
    ).all()
    return [(key, thing, total) for key, thing, total in rows]


def _known(catalog: Catalog, name: str) -> bool:
    """Whether the vault knows this thing at all: by recipe or by the registry."""
    book = catalog.recipes
    try:
        book.recipe(name)
        return True
    except ConstantError:
        return any(material.type_key == name for material in book.materials)


async def test_capital_holds_only_things_the_vault_knows(
    capital: Node, session: AsyncSession, catalog: Catalog
) -> None:
    """Every item of the starting world is a thing, not a class and not a word.

    This is the guard that was missing. A class name placed as an item looks
    like a machine in the database and is invisible to the engine, which finds
    machines by class membership -- so the marketplace had a terminal nobody
    could trade at.
    """
    unknown = [
        (key, thing) for key, thing, _ in await _things(session) if not _known(catalog, thing)
    ]
    assert not unknown, f"в столице стоит то, чего нет в вольте: {unknown}"


async def test_the_engine_recognises_the_city_it_was_handed(
    capital: Node, session: AsyncSession, catalog: Catalog
) -> None:
    """The seed places things; the engine looks for classes. They must meet."""

    async def node(key: str) -> Node:
        found = (await session.execute(select(Node).where(Node.key == key))).scalar_one()
        return found

    #: Trade at all: no terminal, no order book (D-003).
    await market.terminal(session, await node("terra.capital.market"))
    #: A ship couples to whatever the spaceport class stands in (D-206).
    assert await world.has_station(session, await node("terra.capital.port"), ship.SPACEPORT)
    #: The library window is shown where its machine stands (D-176).
    assert await world.is_library(session, await node("terra.capital.library"))
    #: A penal colony is a machine, not a property (D-174).
    assert await justice.is_prison(session, await node("terra.capital.jail"))
    #: The door into the world never closes (D-028).
    assert await world.is_door(session, await node("terra.capital.core"))


async def test_the_capital_is_assembled_from_recipes(
    capital: Node, session: AsyncSession, catalog: Catalog
) -> None:
    """Matter enters the world once, as raw material, and the rest is made of it.

    The capital used to be conjured machine by machine. Now the seed walks the
    same ladder a player walks, so a broken rung -- a recipe without a
    composition, an input nobody makes, a circle -- stops the world from being
    created at all instead of handing the player a city they could not repeat.
    """
    payloads = (
        (await session.execute(select(Event.payload).where(Event.kind == EventKind.ITEM_CREATED)))
        .scalars()
        .all()
    )
    grounds = [str(payload.get("origin", "")) for payload in payloads]

    assert any("сырьё столицы" in ground for ground in grounds), "сырьё не пришло в мир"
    assert any("по рецепту" in ground for ground in grounds), "ничего не собрано переделом"
    #: Nothing arrives without a named ground (pillar P1).
    assert all(grounds), "материя появилась без основания"

    #: The assembly spends exactly what it laid out: leftovers would mean the
    #: bill and the spend disagree, and matter would quietly accumulate.
    book = catalog.recipes
    deliberate = {"coal", "iron_ore"}
    left = [
        (key, thing)
        for key, thing, _ in await _things(session)
        #: A relic is in the registry of things that simply exist (D-215), like
        #: ore -- but it is the Forerunners' machinery standing where they left
        #: it (D-232), not raw material somebody failed to spend.
        if book.is_raw(thing) and not book.is_relic(thing) and thing not in deliberate
    ]
    assert not left, f"после сборки в узлах осталось сырьё: {left}"


async def test_every_capital_machine_could_be_made_by_a_player(
    capital: Node, session: AsyncSession, catalog: Catalog
) -> None:
    """What the Forerunners built, a player can build too.

    Not a formality: the capital is the only picture of a finished city the
    player ever sees, and a machine in it that no recipe makes is a promise the
    world cannot keep.
    """
    from src.engine import craft

    book = catalog.recipes
    for _, thing, _ in await _things(session):
        try:
            recipe = book.recipe(thing)
        except ConstantError:
            continue
        if recipe.kind not in (ItemKind.STATION, ItemKind.FURNITURE):
            continue
        method = craft.procedure(catalog, thing)
        assert method.per_unit, f"«{thing}» не из чего делать"
        if method.station is not None:
            made = book.recipe(method.station)
            assert made.kind is ItemKind.STATION, (
                f"«{thing}» делается на «{method.station}», а это не станция"
            )


async def test_the_planets_carry_their_climate(
    capital: Node, session: AsyncSession, constants: Constants
) -> None:
    """Permafrost and furnace are properties of the **planet** (D-231), written
    into its node on the space layer -- so what a planet does to a body is a
    fact of the world and not a list of names in the engine."""
    for key, weather in (("aurora", frost.FROST), ("pyroxis", frost.HEAT), ("terra", None)):
        sphere = await session.scalar(select(Node).where(Node.key == key))
        assert sphere is not None
        assert await frost.climate_of(session, sphere) == weather

    port = await session.scalar(select(Node).where(Node.key == aurora_port(aurora_cities()[0])))
    assert port is not None
    #: And the city is warm from the first minute, because its reactor is alive:
    #: the plant of the Forerunners heats the hall and the pier one step away
    #: (D-231, D-232). That is what a ship lands by.
    assert await frost.is_warm(session, constants, port)


def test_the_system_is_keplerian() -> None:
    """Every planet gives the same pull of the star (D-271).

    The star's gravitational parameter is read off the orbit a passage leaves
    from (`course.mu_of`), so two planets that disagreed about it would price
    the same passage differently by the direction it is flown in.
    """
    pulls = [
        ship.course.mu_of((circle.radius, circle.period_days, circle.phase))
        for circle in seed_parts.SYSTEM
    ]
    assert max(pulls) == pytest.approx(min(pulls), rel=1e-3), "r^3 / T^2 одно у всех планет"


async def test_every_open_planet_has_an_orbit_to_hang_over(
    capital: Node, session: AsyncSession
) -> None:
    """One orbital node per playable planet, under the planet itself (D-245).

    The road between worlds goes through it, so a missing one is a planet
    nothing can leave and nothing can reach. A deferred planet gets none: an
    orbit is a destination, and a destination for a world that is not open yet
    would be a way into it.
    """
    for key in ("terra", "aurora", "pyroxis"):
        sphere = await session.scalar(select(Node).where(Node.key == key))
        assert sphere is not None
        orbit = await session.scalar(select(Node).where(Node.key == f"{key}.orbit"))
        assert orbit is not None, f"у планеты {key} нет орбитального узла"
        assert orbit.parent_id == sphere.id, "орбита висит под своей планетой"
        assert orbit.layer is Layer.SPACE and orbit.planet is sphere.planet
        assert ship.is_orbit(orbit), "узел помечен орбитой, и по метке его узнают"
        #: And it is the void: the planet under it changes nothing about that.
        assert not await oxygen.free_air(session, orbit)

    #: Aquatica is drawn and not playable (D-104): no orbit, no way in.
    assert await session.scalar(select(Node).where(Node.key == "aquatica.orbit")) is None
    assert await session.scalar(select(Node).where(Node.key == "aquatica")) is not None


async def test_the_capital_prints_on_the_original(
    capital: Node, session: AsyncSession, constants: Constants
) -> None:
    """The door that never closes is the Forerunners' **machine** (D-028, D-232).

    Free, slow, one of a kind -- and a relic, so nobody builds a second and
    nobody takes this one down. Checked on the seeded world rather than on a
    fixture: this is exactly the kind of thing a test of its own world would
    keep passing while the world itself lost it.
    """
    doors = {door["node"]: door for door in await death.printers(session, constants)}
    core = doors[CORE]
    assert core["precursor"] is True
    assert core["energy"] == 0 and core["iron"] == 0

    place = await session.scalar(select(Node).where(Node.key == CORE))
    assert place is not None
    printers = [
        thing.type_key
        for thing in await world.contents(session, await world.node_container(session, place))
        if thing.type_key in world.station_names(death.PRINTER)
    ]
    book = current_catalog().recipes
    assert printers and all(book.is_relic(name) for name in printers), printers


async def test_the_ice_of_aurora_is_reached_from_a_pier(
    capital: Node, session: AsyncSession, constants: Constants
) -> None:
    """A city of the Forerunners is opened from inside and left through its door.

    The seed lays no wild node on Aurora: a ship lands at a pier, and if the
    pier offered nothing but its own rooms the planet would end at three cities
    (D-232). From the hall one goes deeper in; from the pier, out onto the ice.
    """
    port = await session.scalar(select(Node).where(Node.key == aurora_port(aurora_cities()[0])))
    hall = await session.scalar(select(Node).where(Node.key == aurora_hall(aurora_cities()[0])))
    assert port is not None and hall is not None

    assert await explore.possible(session, hall) == (explore.ROOM,)
    from_pier = await explore.possible(session, port)
    assert explore.ROOM in from_pier
    assert explore.SITE in from_pier, "с причала выходят на лёд, иначе Аврора — тупик"


async def test_other_planets_have_somewhere_to_land(
    capital: Node, session: AsyncSession, constants: Constants
) -> None:
    """A ship flies to a spaceport -- or, where there can be no spaceport, to the
    ground itself.

    Aurora: one pier in each of the three cities (D-232), so a ship arriving
    chooses a city. Pyroxis: **no yard at all** and none possible, because
    nothing is built there (D-230, D-233) -- every surface node is a landing
    site, and the plateau is one of them.
    """
    by_planet: dict[str, int] = {}
    for port in await ship.landings(session):
        by_planet[port.planet.value] = by_planet.get(port.planet.value, 0) + 1
    assert by_planet["aurora"] == len(aurora_cities())
    #: The plateau and its black fields, and not a spaceport among them: on
    #: Pyroxis a ship sets down on the ground itself (D-233).
    assert by_planet["pyroxis"] == 1 + PYROXIS_FIELDS
    for place in await ship.open_landings(session):
        assert not await world.has_station(session, place, ship.SPACEPORT), place.key
    #: And the map draws piers, not landing sites: `ports` is the yards alone,
    #: or the legend would call every black field a spaceport.
    assert "pyroxis" not in {node.planet.value for node in await ship.ports(session)}
    #: And all three are **lit**: their reactors are alive, and a lit port is
    #: the only kind a ship may aim at (D-232).
    lit = [port.key for port in await ship.lit_ports(session, constants)]
    for one in aurora_cities():
        assert aurora_port(one) in lit

    #: Every port is the one door of its own city: a planet-layer node under
    #: Aurora, with the hall of the Forerunners one step away.
    first = await session.scalar(select(Node).where(Node.key == aurora_port(aurora_cities()[0])))
    assert first is not None
    city = await session.get(Node, first.parent_id)
    assert city is not None and city.layer is Layer.PLANET and city.planet is Planet.AURORA
    assert city.key == aurora_cities()[0]
    hall = await session.scalar(select(Node).where(Node.key == aurora_hall(aurora_cities()[0])))
    assert hall is not None
    #: One edge between them: the plant heats its own node and its neighbours,
    #: and the pier is the neighbour (D-231).
    assert await travel.route(session, constants, first.id, hall.id) == [hall.id]

    plateau = await session.scalar(select(Node).where(Node.key == PYROXIS_PLATEAU))
    assert plateau is not None
    identity = await world.create_identity(session, "Пришелец")
    body = await world.print_body(session, identity, plateau)
    #: By the key, not by the sentence: the wording is the locale's (D-251 III).
    with pytest.raises(estate.EstateError) as refused:
        await estate.construct(session, constants, body, plateau, 20)
    assert refused.value.key == "estate-build-not-on-pyroxis"

    #: Running the seed again lays nothing twice.
    await seed(session)
    again = sum(1 for port in await ship.ports(session) if port.planet.value == "aurora")
    assert again == len(aurora_cities())
    twice = sum(1 for place in await ship.landings(session) if place.planet.value == "pyroxis")
    assert twice == 1 + PYROXIS_FIELDS


async def test_the_black_fields_carry_the_planets_own_veins(
    capital: Node, session: AsyncSession, constants: Constants
) -> None:
    """What is rare on Terra lies here in plenty (D-233).

    The weights are one table over the mining paces, not a second rarity table
    (`harvest.planet_weights`), and the plateau carries no vein at all: it is
    the one place an eruption leaves alone, and a vein that never moved would
    be exactly the staked claim the eruptions exist against (D-197).
    """
    fields = [
        await session.scalar(select(Node).where(Node.key == pyroxis_field_key(number)))
        for number in range(1, PYROXIS_FIELDS + 1)
    ]
    assert all(field is not None for field in fields)
    species = set()
    for field in fields:
        vein = await session.scalar(select(Vein).where(Vein.node_id == field.id))
        assert vein is not None, f"{field.key} без жилы"
        species.add(vein.resource)
        #: And the field is named by the word, not by the key it is laid with:
        #: the same seam as the vein an explorer finds (`explore/run.py`), and
        #: a field name is persisted once and never laid again (pillar P2), so
        #: «Чёрное поле №1: pyroxite» would be permanent on a world seeded now.
        assert field.name.endswith(f": {display_name(vein.resource).lower()}"), field.name
        assert vein.resource not in field.name
    #: Nothing here says which species must come up -- only that they come from
    #: the planet's own table, and that the planet is not Terra.
    assert species <= set(constants[R.HARVEST_RATES])

    plateau = await session.scalar(select(Node).where(Node.key == PYROXIS_PLATEAU))
    assert plateau is not None
    assert await session.scalar(select(Vein).where(Vein.node_id == plateau.id)) is None


async def test_the_plateau_of_an_old_world_gets_its_mark(
    capital: Node, session: AsyncSession, constants: Constants
) -> None:
    """A world laid before D-233 has the plateau, and has it **bare**: the old
    seed made that node with no properties at all.

    Unmarked it is not the plateau: `plates._exempt` returns nothing, and the
    planet shakes its own anvil -- burns what stands on it, tears its ways, and
    moves a vein onto the one place from which nothing can ever move it off
    again. The catch-up sets the mark; this is what says so.
    """
    from src.engine import plates
    from src.seed import seed

    plateau = await session.scalar(select(Node).where(Node.key == PYROXIS_PLATEAU))
    assert plateau is not None
    #: Back to how a world of before D-233 holds it.
    plateau.properties = {}
    await session.flush()
    assert plateau.id not in await plates._exempt(session), "фикстура не воспроизвела старый мир"

    await seed(session)

    again = await session.scalar(select(Node).where(Node.key == PYROXIS_PLATEAU))
    assert (again.properties or {}).get(plates.ANVIL) is True
    assert again.id in await plates._exempt(session)
    #: And the planet leaves it alone again.
    for attempt in range(20):
        shaken = await plates._choose(session, constants, random.Random(attempt))
        assert again.id not in {node.id for node in shaken}


async def test_a_plot_of_an_old_world_gets_its_soil(capital: Node, session: AsyncSession) -> None:
    """A city plot laid before D-246 carries the mark alone, and grows nothing.

    An absent property reads as nought, so every plot inside every city was
    barren rock: the strips window never appeared on one, and the vault's own
    "on civic land the holder runs the estate" had nowhere to happen.
    """
    lot = await session.scalar(select(Node).where(Node.key == "terra.capital.lot1"))
    assert lot is not None
    #: Back to how a world of before D-246 holds it.
    lot.properties = {explore.PLOT: True}
    await session.flush()

    await seed(session)

    again = await session.scalar(select(Node).where(Node.key == "terra.capital.lot1"))
    assert float((again.properties or {}).get("fertility", 0)) > 0, "участку не дали почвы"
    assert "water" in (again.properties or {})

    #: And a second run rolls nothing again: the soil is the world's now.
    before = dict(again.properties)
    await seed(session)
    await session.refresh(again)
    assert again.properties == before


async def test_a_tall_house_of_an_old_world_gets_its_floors(
    capital: Node, session: AsyncSession
) -> None:
    """A house raised before D-247 holds all its storeys in one node.

    The catch-up opens the floors above the ground and cuts the stairs; what
    stood and lay in the house stays on the ground floor, because the engine
    may add rooms and may not decide for the owner what belongs upstairs.
    """
    from src.models.estate import Building

    lot = await session.scalar(select(Node).where(Node.key == "terra.capital.lot2"))
    assert lot is not None
    session.add(Building(node_id=lot.id, area_m2=90, footprint_m2=30, floors=3, kind="wooden"))
    await session.flush()
    assert await estate.storeys_of(session, lot) == [], "фикстура не воспроизвела старый мир"

    await seed(session)

    rooms = await estate.storeys_of(session, lot)
    assert [estate.storey_of(room) for room in rooms] == [2, 3]
    assert all(float(room.area_m2) == 30 for room in rooms)

    #: A second run cuts no second staircase.
    await seed(session)
    assert len(await estate.storeys_of(session, lot)) == 2


async def test_a_vein_can_move_on_the_world_the_seed_lays(
    capital: Node, session: AsyncSession, constants: Constants
) -> None:
    """Checked on the **seeded** topology, not on a shape a test built.

    The plateau is never shaken and is not a destination either, so a star --
    every field hanging on the plateau alone -- gives a vein nowhere to go: the
    planet's main mechanic (D-197) would be switched off on the day of the
    deploy, and every test of the move would still pass, because they lay their
    fields in a chain. The seed lays a chain too, and this is what says so.
    """
    from src.engine import plates

    fields = [
        await session.scalar(select(Node).where(Node.key == pyroxis_field_key(number)))
        for number in range(1, PYROXIS_FIELDS + 1)
    ]
    where = {
        vein.id: vein.node_id
        for vein in (
            await session.execute(
                select(Vein).where(Vein.node_id.in_([field.id for field in fields]))
            )
        )
        .scalars()
        .all()
    }
    assert where, "поля сида без жил"

    moved = 0
    for attempt in range(20):
        moved += await plates._move_veins(
            session, constants, random.Random(attempt), fields, now=datetime.now(UTC)
        )
        if moved:
            break
    assert moved, "на карте сида жилам некуда ехать — извержения ничего не решают"

    after = {
        vein.id: vein.node_id
        for vein in (await session.execute(select(Vein).where(Vein.id.in_(where)))).scalars().all()
    }
    assert after != where
    #: And not onto the plateau: there it would stand for ever.
    plateau = await session.scalar(select(Node).where(Node.key == PYROXIS_PLATEAU))
    assert plateau.id not in after.values()


async def test_open_land_keeps_its_yard_and_a_hull_keeps_its_walls(
    session: AsyncSession, constants: Constants
) -> None:
    """A seeded roof outside a city is cut to its machines; nothing else is (D-254).

    The rule that a building is the whole plot belongs inside a city. Applied
    beyond the walls it roofed the world's one river-fed field over and left
    no metre to plough. What must survive the retraction, and did not on the
    first pass, is everything that is a whole-node building for a reason of
    its own: a cabin aboard a ship is its hull.
    """
    field = await world.create_node(
        session, "terra.field.unroof", "Пойма", area_m2=400, properties={"meadow": True}
    )
    cabin = await world.create_node(
        session, "ship.node.unroof", "Каюта", area_m2=40, properties={"aboard": True}
    )
    house = await world.create_node(
        session, "terra.house.unroof", "Дом", area_m2=120, properties={"wild": True}
    )
    for node in (field, cabin, house):
        session.add(
            Building(node_id=node.id, area_m2=float(node.area_m2), footprint_m2=float(node.area_m2))
        )
    #: Only the field has a machine standing in it -- that is what makes its
    #: roof the seed's doing rather than somebody's wall.
    yard = await world.node_container(session, field)
    await world.grant_item(session, yard, "hearth", amount=1, origin="тест")
    await session.flush()

    await seed_catchup._unroof_open_land(session, constants)  # noqa: SLF001

    roofs = {
        node.key: float(building.area_m2)
        for node, building in (
            await session.execute(
                select(Node, Building).join(Building, Building.node_id == Node.id)
            )
        ).all()
    }
    assert roofs["terra.field.unroof"] < 400, "поле обязано освободиться под грядки"
    assert roofs["ship.node.unroof"] == 40, "каюта — это корпус, а не крыша от сеятеля"
    assert roofs["terra.house.unroof"] == 120, "чужой дом не перекраивают"
    assert await estate.free_ground(session, field) > 300


async def test_a_house_somebody_built_is_never_cut_back(
    session: AsyncSession, constants: Constants
) -> None:
    """Beyond the walls a whole-plot house is an ordinary, legal house (D-254).

    `station.may_build` lets anybody build on unowned land, and `estate.build`
    refuses only when the area exceeds what is free -- so a house covering its
    whole plot is a normal thing to own, and it is shaped exactly like the
    seed's overreach. What tells them apart is that a house was raised by a
    job and wrote `BUILDING_BUILT`; a seeded roof never did. Without that the
    retraction would shave somebody's home at every deploy, materials and all.
    """
    mine = await world.create_node(
        session, "terra.built.house", "Дом", area_m2=200, properties={"wild": True}
    )
    session.add(Building(node_id=mine.id, area_m2=200, footprint_m2=200))
    yard = await world.node_container(session, mine)
    await world.grant_item(session, yard, "hearth", amount=1, origin="тест")
    await session.flush()
    await events.record(session, EventKind.BUILDING_BUILT, actor_identity_id=None, node_id=mine.id)

    #: And a two-storey house, whose area is footprint times floors: the step
    #: writes both numbers and knows nothing of keeping that invariant.
    tall = await world.create_node(
        session, "terra.built.tall", "Башня", area_m2=200, properties={"wild": True}
    )
    session.add(Building(node_id=tall.id, area_m2=200, footprint_m2=100, floors=2))
    tall_yard = await world.node_container(session, tall)
    await world.grant_item(session, tall_yard, "hearth", amount=1, origin="тест")
    await session.flush()

    await seed_catchup._unroof_open_land(session, constants)  # noqa: SLF001

    kept = {
        node.key: float(building.area_m2)
        for node, building in (
            await session.execute(
                select(Node, Building).join(Building, Building.node_id == Node.id)
            )
        ).all()
    }
    assert kept["terra.built.house"] == 200, "построенный дом не трогают"
    assert kept["terra.built.tall"] == 200, "двухэтажный дом не трогают: area = пятно × этажи"


async def test_the_seed_leaves_a_yard_around_a_rural_hearth(
    session: AsyncSession, constants: Constants
) -> None:
    """A fresh world must not repeat the retraction's own bug (D-254).

    `_unroof_open_land` puts old worlds right; this is the same rule on the
    laying side, and it is the one that matters for every world made from
    today. Inside a city the plot is still the building -- a forge is its plot.
    """
    #: A real world first: the rule turns on who owns the ground, so the test
    #: needs a city that actually exists to hand a plot to.
    await seed(session)
    town_id = (await session.execute(select(City.id))).scalars().one()
    field = await world.create_node(
        session, "terra.field.lay", "Пойма", area_m2=400, properties={"meadow": True}
    )
    city = await world.create_node(
        session, "terra.city.lay", "Кузница", area_m2=260, properties={"ring": 2}
    )
    city.owner_city_id = town_id
    for node in (field, city):
        yard = await world.node_container(session, node)
        await world.grant_item(session, yard, "hearth", amount=1, origin="тест")
    await session.flush()

    await seed_parts.buildings(session)

    laid = {
        node.key: float(building.area_m2)
        for node, building in (
            await session.execute(
                select(Node, Building).join(Building, Building.node_id == Node.id)
            )
        ).all()
    }
    assert laid["terra.field.lay"] == 10, "очаг у реки — это очаг, а не стена поперёк луга"
    assert laid["terra.city.lay"] == 260, "в городе застройка и есть участок"
    assert await estate.free_ground(session, field) > 350


def test_a_city_of_the_layout_is_named_within_the_ceiling() -> None:
    """A vault node the seed founds a city on carries a name the engine allows.

    Needs no world: the question is about the layout the vault ships, which is
    the one thing the two repositories both see. The vault bounds these names
    at its own end (`WORLD_CITY_NAME_LIMIT` in its `tools/world.py`) and keeps
    its own copy of the number, because its build reads `data/` and CI copies
    only `build/*.json` back -- neither side can import the other's constant.
    This is the other end of that copy.

    Measured against `CITY_NAME_LIMIT` rather than the Net's: the two are equal
    today, but `test_city_founding` pins only `CITY_NAME_LIMIT <= NET_NAME_LIMIT`,
    so the city's own ceiling is the smaller one and the Net's follows from it.
    Getting this wrong would leave the gap the check was written to close --
    a seeded city named past what a player is allowed to type, whose official
    channel (`city.found` opens it straight from the model) is one
    `net.channel.create` would have refused.
    """
    over = [
        spec
        for spec in seed_world.load_scenario().nodes
        if spec.city and len(str(spec.name)) > CITY_NAME_LIMIT
    ]
    assert not over, (
        "a city name becomes a channel name: "
        f"{[(s.key, len(str(s.name))) for s in over]} over {CITY_NAME_LIMIT}"
    )
