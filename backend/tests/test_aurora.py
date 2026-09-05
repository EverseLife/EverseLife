# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Aurora: three cities, their reactors and the beacons that hang on them (D-232).

Checked is the sujet of the planet and nothing decorative:

* the reactor gives without fuel and without people, and **fades**: a straight
  line to nothing over a year of real time, visible long before it matters;
* its energy pays for the relics of its own city and reaches no battery -- free
  energy for export does not exist;
* the beacon shines while the port is warm and its yard has power. Both die
  together with the reactor, and then only players can bring them back;
* **the blackout is irreversible in the only way that matters**: a brazier
  warms a dead port but does not light its beacon -- power has to be walked in;
* what the Forerunners left stays where it was found: not taken down, not
  picked up, not made.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import energy, frost, ruins, ship, station, storage, travel, world
from src.models.world import Layer, Node, Planet, Surface
from src.units import HOURS_PER_DAY, amount_float

PLANT = "precursor_heat_plant"
REACTOR = "precursor_isotope_reactor"
YARD = "precursor_shipyard"
BRAZIER = "brazier"
COAL = "coal"
COAL_PLANT = "coal_plant"


async def _aurora(session: AsyncSession) -> Node:
    return await world.create_node(
        session,
        "aurora",
        "Аврора",
        planet=Planet.AURORA,
        area_m2=1,
        layer=Layer.SPACE,
        properties={frost.FROST: True, ruins.PRECURSOR: True},
    )


async def _city(session: AsyncSession, *, age_days: float = 0.0) -> tuple[Node, Node, Node]:
    """A city of the Forerunners: the hall with the reactor, the pier one step away.

    `age_days` is how long ago its reactor started fading -- the seed writes the
    anchor into the hall's node, and the test writes it just the same.
    """
    sphere = await _aurora(session)
    stamp = uuid.uuid4().hex[:8]
    place = await world.create_node(
        session,
        f"aurora.{stamp}",
        "Город",
        planet=Planet.AURORA,
        area_m2=1,
        layer=Layer.PLANET,
        parent=sphere,
        properties={ruins.PRECURSOR: True, ruins.KIND: "столица"},
    )
    hall = await world.create_node(
        session,
        f"aurora.{stamp}.hall",
        "Зал",
        planet=Planet.AURORA,
        area_m2=600,
        layer=Layer.PLANET,
        parent=place,
        properties={
            ruins.PRECURSOR: True,
            ruins.DEPTH: 1,
            energy.REACTOR_SINCE: (datetime.now(UTC) - timedelta(days=age_days)).isoformat(),
        },
    )
    port = await world.create_node(
        session,
        f"aurora.{stamp}.port",
        "Космодром",
        planet=Planet.AURORA,
        area_m2=240,
        layer=Layer.PLANET,
        parent=place,
        properties={ruins.PRECURSOR: True, ruins.DEPTH: 0},
    )
    await travel.connect(session, hall, port, base_seconds=30, surface=Surface.PAVED)
    for node, thing in ((hall, PLANT), (hall, REACTOR), (port, YARD)):
        await world.grant_item(
            session,
            await world.node_container(session, node),
            thing,
            quality=60,
            origin="тест: наследие Предтеч",
        )
    return place, hall, port


# --- the reactor --------------------------------------------------------------


async def test_the_reactor_fades_to_nothing_over_a_year(
    session: AsyncSession, constants: Constants
) -> None:
    """Not a switch but a fading, and the city can see the day it will have to
    stand on its own coal long before that day comes."""
    _, hall, _ = await _city(session)
    now = datetime.now(UTC)
    fresh = energy.reactor_output(constants, hall, now=now)
    assert fresh == pytest.approx(constants[R.REACTOR_OUTPUT], rel=0.01)

    half = now + timedelta(days=constants[R.REACTOR_LIFETIME] / 2)
    assert energy.reactor_output(constants, hall, now=half) == pytest.approx(
        constants[R.REACTOR_OUTPUT] / 2, rel=0.02
    )

    after = now + timedelta(days=constants[R.REACTOR_LIFETIME] + 1)
    assert energy.reactor_output(constants, hall, now=after) == 0

    #: The day itself goes to the client, not the output: the line is straight
    #: and the catalog holds both its ends (D-225).
    dies = energy.reactor_dies_at(constants, hall)
    assert dies is not None
    assert dies - now == pytest.approx(
        timedelta(days=constants[R.REACTOR_LIFETIME]), abs=timedelta(minutes=1)
    )


async def test_the_reactor_warms_the_city_and_the_pier(
    session: AsyncSession, constants: Constants
) -> None:
    """The plant of the Forerunners heats its node and its neighbours (D-231),
    and while the reactor lives it needs no pool at all."""
    _, hall, port = await _city(session)
    assert await frost.is_warm(session, constants, hall)
    assert await frost.is_warm(session, constants, port)

    pool = await energy.pool_of(session, constants, hall)
    assert pool is not None and float(pool.stored) == 0, "реактор не копится в пуле"


async def test_a_dead_reactor_leaves_a_cold_dark_city(
    session: AsyncSession, constants: Constants
) -> None:
    """The year is up: from here on the city is the players' business."""
    _, hall, port = await _city(session, age_days=constants[R.REACTOR_LIFETIME] + 1)
    assert not await frost.is_warm(session, constants, hall)
    assert not await frost.is_warm(session, constants, port)
    assert not await ship.beacon_lit(session, constants, port)


async def test_the_reactor_pays_for_its_own_heat_and_gives_nothing_away(
    session: AsyncSession, constants: Constants
) -> None:
    """Its energy feeds the relics of its city and stops there (D-232): a city
    living on a reactor still has nothing to charge a battery with."""
    _, hall, _ = await _city(session)
    pool = await energy.pool_of(session, constants, hall)
    assert pool is not None
    pool.counted_at = datetime.now(UTC) - timedelta(hours=1)
    await session.flush()

    await energy.produce(session, constants, pool, now=datetime.now(UTC))
    #: An hour with a plant burning and a reactor running: nothing gained, and
    #: -- what matters -- nothing lost either.
    assert float(pool.stored) == 0

    with pytest.raises(energy.NotEnough):
        await energy.draw_for_work(
            session,
            constants,
            await _dweller(session, hall),
            constants[R.FROST_PLANT_DRAW],
            goods="iron_ore",
        )


async def _dweller(session: AsyncSession, node: Node):
    identity = await world.create_identity(session, f"Пришелец-{uuid.uuid4().hex[:6]}")
    return await world.print_body(session, identity, node)


async def test_a_fading_reactor_hands_the_bill_to_the_city(
    session: AsyncSession, constants: Constants
) -> None:
    """As the output falls below what the plant eats, the pool pays the rest --
    and a city with an empty pool starts freezing before the year is out."""
    #: Most of the way through the year: the reactor still gives, but less than
    #: the plant takes.
    share = constants[R.FROST_PLANT_DRAW] / constants[R.REACTOR_OUTPUT]
    age = constants[R.REACTOR_LIFETIME] * (1 - share / 2)
    _, hall, _ = await _city(session, age_days=age)
    pool = await energy.pool_of(session, constants, hall)
    assert pool is not None
    pool.stored = Decimal(str(constants[R.FROST_PLANT_DRAW]))
    pool.counted_at = datetime.now(UTC) - timedelta(hours=1)
    await session.flush()

    await energy.produce(session, constants, pool, now=datetime.now(UTC))
    #: The shortfall came out of the pool, and there was just enough of it.
    assert float(pool.stored) < constants[R.FROST_PLANT_DRAW]


async def test_the_reactor_does_not_heat_what_people_carried_in(
    session: AsyncSession, constants: Constants
) -> None:
    """Two purses, and they are not interchangeable (D-232).

    The Forerunners' plant runs on the Forerunners' reactor; a heater somebody
    hauled in runs on the city pool and on nothing else. Were it one purse, a
    reactor city would heat everything anybody brought, free, for a year -- and
    «город на мерзлоте платит за само своё существование» would be off exactly
    where it must bite.
    """
    city, _, _ = await _city(session)
    yard = await world.create_node(
        session,
        f"{city.key}.yard",
        "Двор",
        planet=Planet.AURORA,
        area_m2=200,
        layer=Layer.PLANET,
        parent=city,
    )
    await world.grant_item(
        session,
        await world.node_container(session, yard),
        "heater",
        quality=60,
        origin="тест",
    )
    #: The pool is empty and the reactor is alive: the relics burn, this does not.
    assert not await frost.is_warm(session, constants, yard)

    pool = await energy.pool_of(session, constants, yard)
    assert pool is not None
    pool.stored = Decimal(str(constants[R.FROST_HEATER_DRAW]))
    await session.flush()
    assert await frost.is_warm(session, constants, yard)


async def test_the_city_pays_for_its_own_stoves_even_under_a_reactor(
    session: AsyncSession, constants: Constants
) -> None:
    """The reactor covers the relics' hours and not one more."""
    city, _, _ = await _city(session)
    yard = await world.create_node(
        session,
        f"{city.key}.yard",
        "Двор",
        planet=Planet.AURORA,
        area_m2=200,
        layer=Layer.PLANET,
        parent=city,
    )
    await world.grant_item(
        session,
        await world.node_container(session, yard),
        "heater",
        quality=60,
        origin="тест",
    )
    pool = await energy.pool_of(session, constants, yard)
    assert pool is not None
    stored = constants[R.FROST_HEATER_DRAW] * 3
    pool.stored = Decimal(str(stored))
    pool.counted_at = datetime.now(UTC) - timedelta(hours=1)
    await session.flush()

    await energy.produce(session, constants, pool, now=datetime.now(UTC))
    #: Exactly the heater's hour left the pool: the relic plant's hour was the
    #: reactor's business, and its surplus went nowhere.
    assert float(pool.stored) == pytest.approx(stored - constants[R.FROST_HEATER_DRAW], rel=1e-3)


# --- the beacon ---------------------------------------------------------------


async def test_the_beacon_needs_warmth_and_power(
    session: AsyncSession, constants: Constants
) -> None:
    _, _, port = await _city(session)
    assert await ship.beacon_lit(session, constants, port)
    assert port.key in [one.key for one in await ship.lit_ports(session, constants)]


async def test_a_brazier_warms_a_dead_port_but_does_not_light_it(
    session: AsyncSession, constants: Constants
) -> None:
    """The rule the whole planet hangs on (D-232).

    Were warmth enough, anybody could walk to a dead city with a brazier and
    land a ship on it, and the blackout would be an inconvenience rather than
    the loss of a planet. Power has to be walked in.
    """
    _, _, port = await _city(session, age_days=constants[R.REACTOR_LIFETIME] + 1)
    yard = await world.node_container(session, port)
    await world.grant_item(session, yard, BRAZIER, quality=60, origin="тест")
    await world.grant_item(session, yard, COAL, amount=20, quality=60, origin="тест")

    assert await frost.is_warm(session, constants, port), "жаровня греет"
    assert not await ship.beacon_lit(session, constants, port), "но маяк не светит"

    #: Bring generation in, and the port comes back: that is the revival of a
    #: city, and it is meant to cost a haul.
    pool = await energy.pool_of(session, constants, port)
    assert pool is not None
    pool.stored = Decimal(str(constants[R.FROST_PLANT_DRAW]))
    await session.flush()
    assert await ship.beacon_lit(session, constants, port)


async def test_a_port_where_nothing_freezes_is_always_lit(
    session: AsyncSession, constants: Constants
) -> None:
    """Terra and Pyroxis are not part of Aurora's sujet: a port there is a port,
    and an unpaid bill is the meter's business, not the sky's."""
    home = await world.create_node(
        session, f"terra.port.{uuid.uuid4().hex[:6]}", "Космодром", area_m2=240
    )
    assert await ship.beacon_lit(session, constants, home)


# --- opening a city -----------------------------------------------------------


async def test_a_room_opens_with_its_type_depth_and_contents(
    session: AsyncSession, constants: Constants
) -> None:
    """Exploring inside a city **reveals** what stood there before anybody came
    (D-232): the room arrives complete -- type, depth and what lies in it."""
    city, hall, _ = await _city(session)
    city.properties = {**(city.properties or {}), ruins.KIND: "столица"}
    await session.flush()

    room = await ruins.open_room(session, constants, random.Random(1), hall, who=None)
    assert room.parent_id == city.id
    assert room.properties[ruins.ROOM_MARK] in constants[R.RUINS_ROOM_TYPES]["столица"]
    #: The hall is one step in from the pier, so its first room is two.
    assert ruins.depth_of(room) == ruins.depth_of(hall) + 1
    assert room.name == str(room.properties[ruins.ROOM_MARK]).capitalize()

    lying = await world.contents(session, await world.node_container(session, room))
    assert lying, "раскрытое помещение не бывает пустым: пусто — это несостоявшийся поиск"
    #: What lies there is what the vault says may lie in a room of this type.
    may = constants[R.RUINS_ROOM_FINDS][str(room.properties[ruins.ROOM_MARK])]
    assert all(thing.type_key in may for thing in lying)


async def test_deeper_rooms_are_richer(session: AsyncSession, constants: Constants) -> None:
    """Depth is the whole reward of going in (D-061): the same roll deeper in
    brings more, and by one line rather than by a second table."""
    _, hall, port = await _city(session)
    hall.properties = {**(hall.properties or {}), ruins.DEPTH: 1}
    port.properties = {**(port.properties or {}), ruins.DEPTH: 8}
    await session.flush()

    near = await ruins.open_room(session, constants, random.Random(7), hall, who=None)
    far = await ruins.open_room(session, constants, random.Random(7), port, who=None)
    shallow = await world.contents(session, await world.node_container(session, near))
    deep = await world.contents(session, await world.node_container(session, far))
    assert amount_float(deep[0].amount) > amount_float(shallow[0].amount)


async def test_a_city_is_worked_out_like_a_vein(
    session: AsyncSession, constants: Constants
) -> None:
    """The more of a city is open, the oftener the next door leads nowhere --
    and the chance stops falling at a floor: the last rooms are hard to find,
    not impossible (D-232, D-007)."""
    city, hall, _ = await _city(session)
    assert ruins.worked_out(constants, city) == 1

    city.properties = {**(city.properties or {}), ruins.OPENED: constants[R.RUINS_CITY_ROOMS] / 2}
    await session.flush()
    assert ruins.worked_out(constants, city) == pytest.approx(0.5)

    #: The stock is out: the city is worked out like a vein, and there is
    #: nothing left in it to find. It stays on the map with everything already
    #: opened -- what ends is the finding, not the place (D-007, D-232).
    city.properties = {**(city.properties or {}), ruins.OPENED: constants[R.RUINS_CITY_ROOMS]}
    await session.flush()
    assert ruins.worked_out(constants, city) == 0
    assert ruins.exhausted(constants, city)
    with pytest.raises(ruins.NotRuins):
        await ruins.open_room(session, constants, random.Random(3), hall, who=None)

    #: Opening a room is what wears the city down, and it wears down for
    #: everybody at once: the count lives on the city, not on the scout.
    city.properties = {**(city.properties or {}), ruins.OPENED: 0}
    await session.flush()
    await ruins.open_room(session, constants, random.Random(2), hall, who=None)
    assert ruins.opened(city) == 1


# --- a city beyond the ice ----------------------------------------------------


async def test_a_found_city_comes_frozen_and_dark(
    session: AsyncSession, constants: Constants
) -> None:
    """No ship lands on it, and the only way in is the walk (D-232)."""
    _, _, port = await _city(session)
    found = await ruins.lost_city(session, constants, port, who=None)

    assert not await ship.beacon_lit(session, constants, found)
    assert not await frost.is_warm(session, constants, found)
    town = await session.get(Node, found.parent_id)
    assert town is not None
    hall = await session.scalar(select(Node).where(Node.key == f"{town.key}.hall"))
    assert hall is not None
    assert energy.reactor_output(constants, hall, now=datetime.now(UTC)) == 0
    #: And it is a spaceport all the same: dark, but a place a ship could come
    #: back to once somebody carries energy into it.
    assert await world.has_station(session, found, ship.SPACEPORT)


async def test_the_same_number_finds_the_same_city(
    session: AsyncSession, constants: Constants
) -> None:
    """The map is eternal and must not be rerolled (D-007): a city is generated
    from the planet and its number, never from who happened to find it."""
    _, _, port = await _city(session)
    first = await ruins.lost_city(session, constants, port, who=None)
    second = await ruins.lost_city(session, constants, port, who=None)
    assert first.key != second.key, "второй город — второй номер"

    city_one = await session.get(Node, first.parent_id)
    city_two = await session.get(Node, second.parent_id)
    assert city_one is not None and city_two is not None
    assert city_one.key.endswith("001") and city_two.key.endswith("002")
    assert city_one.properties[ruins.KIND] in constants[R.RUINS_ROOM_TYPES]


# --- what the Forerunners left ------------------------------------------------


async def test_a_dead_city_comes_back_on_coal_brought_in(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The whole point of the planet, end to end (D-232).

    A city whose reactor has run out is cold and dark: the beacon is out, the
    bench does not work, the bed is not a home. Bring generation in and light
    it -- and the node thaws, the machines wake, the beacon comes back. This is
    what the players are supposed to do to Aurora, and it must be walkable.
    """
    from src.engine import city as town

    place, hall, port = await _city(session, age_days=400)
    #: Dead reactor: nothing of the Forerunners' is left to lean on.
    assert energy.reactor_output(constants, hall, now=datetime.now(UTC)) == 0
    assert not await frost.is_warm(session, constants, port)
    assert not await ship.beacon_lit(session, constants, port)
    with pytest.raises(frost.Frozen):
        await frost.require_working(session, constants, port, YARD)

    #: A coal plant hauled in, fuel beside it, and a city to bill it to.
    settlement = await town.found(session, catalog, place, f"Новый {place.key[-4:]}")
    for thing, count in ((COAL_PLANT, 1), (COAL, 200)):
        await world.grant_item(
            session,
            await world.node_container(session, port),
            thing,
            amount=count,
            quality=60,
            origin="привезли",
        )
    pool = await energy.pool_of(session, constants, port)
    assert pool is not None
    await energy.produce(session, constants, pool, now=datetime.now(UTC) + timedelta(hours=2))

    #: The node thaws, the yard works, the beacon is lit -- the city is alive.
    assert await frost.is_warm(session, constants, port), "уголь привезли, а узел не оттаял"
    await frost.require_working(session, constants, port, YARD)
    assert await ship.beacon_lit(session, constants, port), "город ожил, а маяк не зажёгся"
    assert settlement is not None


async def test_a_revived_city_can_be_given_power_and_a_printer(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Full habitability, not a dungeon with a hotel (D-232).

    Two things the decision allows out loud and the engine could easily have
    forbidden by inheriting a rule from Pyroxis: a city-institution is founded
    on Aurora by the ordinary order, and a bioprinter **may** be built there --
    printing on the spot is what makes a planet a home rather than a shift.
    """
    from src.engine import city as town
    from src.engine.estate import building

    place, hall, _ = await _city(session)
    settlement = await town.found(session, catalog, place, f"Мерид-{place.key[-4:]}")
    assert settlement is not None, "город-институт на Авроре не основывается"

    #: And the ground takes a house: `construct` refuses Pyroxis by name, and
    #: Aurora must not be caught by the same net.
    body = await _dweller(session, hall)
    hall.owner_identity_id = body.identity_id
    await session.flush()
    kinds = building.kinds(constants)
    assert kinds, "в реестре нет типов застройки"
    area = 20.0
    pocket = await world.body_container(session, body)
    for name, per_metre in building.composition(constants, kinds[0]).items():
        await world.grant_item(
            session, pocket, name, amount=per_metre * area + 1, quality=60, origin="привезли"
        )
    await building.construct(session, constants, body, hall, area, kind=kinds[0])


async def test_a_relic_is_not_taken_down_or_picked_up(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Found, not made -- and it stays where it was found (D-232)."""
    _, hall, _ = await _city(session)
    body = await _dweller(session, hall)
    yard = await world.node_container(session, hall)
    relic = next(thing for thing in await world.contents(session, yard) if thing.type_key == PLANT)

    with pytest.raises(station.StationError):
        await station.take(session, catalog, body, relic)
    with pytest.raises(storage.StorageError):
        await storage.pick(session, constants, catalog, body, relic)
    assert catalog.recipes.is_relic(PLANT)
    assert not catalog.recipes.is_relic("heat_plant"), "своя ТЭЦ — не реликвия"


async def test_a_relic_is_never_what_the_seed_builds(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The capital stands on what it assembled (D-216): asking the catalog for a
    thing of a class must never hand back the Forerunners' one."""
    for thing_class in ("shipyard", "heat_plant"):
        made = catalog.recipes.made_of_class(thing_class)
        assert made, f"класс «{thing_class}» нечем застроить"
        assert not any(catalog.recipes.is_relic(name) for name in made)


async def test_the_veins_of_a_planet_follow_the_planet(
    session: AsyncSession, constants: Constants
) -> None:
    """Aurora is generous with coal and poor in iron (D-232), and that is one
    line of weights over the same mining paces -- not a second rarity table."""
    weights = constants[R.HARVEST_PLANET_WEIGHTS]
    aurora = weights.get(Planet.AURORA.value, {})
    assert aurora.get(COAL, 1) > 1
    assert aurora.get("iron_ore", 1) < 1
    assert Planet.TERRA.value not in weights, "у Терры весов нет: она и есть мерило"


def test_a_year_of_the_reactor_is_a_year_of_real_time(constants: Constants) -> None:
    """The countdown is in real days, not in the planet's own (D-008 is about
    the world's clocks; this one is about a year of a player's life)."""
    hours = constants[R.REACTOR_LIFETIME] * HOURS_PER_DAY
    assert hours > constants[R.REACTOR_LIFETIME]
