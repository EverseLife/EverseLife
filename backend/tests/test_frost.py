# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The cold of places and machines (D-231).

Where warmth comes from -- the plant, the heater, the brazier and its fuel,
the board that is always warm -- and what a frozen node refuses: the bench,
the printer, the terminal, the office, the open ground, the bed. The body's
own ledger of hours lives in `test_frost_body.py`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from frost_kit import HEATER, _ago, _charge, _dweller, _place, _sphere, _town
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import craft, death, energy, frost, rest, travel, world
from src.models.estate import Building
from src.models.identity import Identity
from src.models.world import Layer, Planet

BENCH = "workbench"

TERMINAL = "market_terminal"

HALL = "administration"

#: Made at the workbench out of wood alone -- the simplest honest batch.
MAKE = "handle"

COAL = "coal"

PLANT = "heat_plant"

BRAZIER = "brazier"

# --- the planet ---------------------------------------------------------------


async def test_a_planet_without_a_climate_is_warm(
    session: AsyncSession, constants: Constants
) -> None:
    """Terra costs the mechanic nothing: every node is warm and nothing is settled."""
    _, yard = await _town(session, planet=Planet.TERRA, climate=None)
    assert await frost.climate_of(session, yard) is None
    assert await frost.is_warm(session, constants, yard)


async def test_permafrost_node_is_cold_without_a_stove(
    session: AsyncSession, constants: Constants
) -> None:
    _, yard = await _town(session)
    assert await frost.climate_of(session, yard) == frost.FROST
    assert not await frost.is_warm(session, constants, yard)


async def test_the_scorching_planet_has_no_shelters(
    session: AsyncSession, constants: Constants
) -> None:
    """Nothing is built on Pyroxis (D-230): a stove there heats nothing, and the
    body is saved by the suit and the ship's board -- never by the ground."""
    _, yard = await _town(session, planet=Planet.PYROXIS, climate=frost.HEAT)
    await _place(session, yard, PLANT)
    await _charge(session, constants, yard, constants[R.FROST_PLANT_DRAW])
    assert not await frost.is_warm(session, constants, yard)


async def test_the_board_is_always_warm(session: AsyncSession, constants: Constants) -> None:
    """Life support heats the ship: a node aboard is warm wherever it stands."""
    sphere = await _sphere(session, Planet.AURORA, frost.FROST)
    cabin = await world.create_node(
        session,
        f"ship.{uuid.uuid4().hex[:6]}.cabin",
        "Каюта",
        planet=Planet.AURORA,
        area_m2=20,
        layer=Layer.LOCATION,
        parent=sphere,
        properties={"aboard": True},
    )
    assert await frost.is_warm(session, constants, cabin)


# --- what heats ---------------------------------------------------------------


async def test_the_heater_warms_its_node_on_the_pool(
    session: AsyncSession, constants: Constants
) -> None:
    _, yard = await _town(session)
    await _place(session, yard, HEATER)
    assert not await frost.is_warm(session, constants, yard), "без энергии обогреватель — железо"
    await _charge(session, constants, yard, constants[R.FROST_HEATER_DRAW])
    assert await frost.is_warm(session, constants, yard)


async def test_an_empty_pool_is_a_cold_city(session: AsyncSession, constants: Constants) -> None:
    """The whole price of a city on the permafrost: it pays for its own existence."""
    _, yard = await _town(session)
    await _place(session, yard, PLANT)
    await _charge(session, constants, yard, 0)
    assert not await frost.is_warm(session, constants, yard)


async def test_the_plant_reaches_the_neighbour_and_the_heater_does_not(
    session: AsyncSession, constants: Constants
) -> None:
    """The plant heats its node and every neighbour; the heater only its own (D-231)."""
    city, yard = await _town(session)
    door = await world.create_node(
        session,
        f"{yard.key}.next",
        "Соседний двор",
        planet=Planet.AURORA,
        area_m2=100,
        layer=Layer.CITY,
        parent=city,
    )
    await travel.connect(session, yard, door, base_seconds=60)
    await _charge(session, constants, yard, constants[R.FROST_PLANT_DRAW])

    await _place(session, yard, HEATER)
    assert await frost.is_warm(session, constants, yard)
    assert not await frost.is_warm(session, constants, door), "обогреватель за порог не греет"

    await _place(session, yard, PLANT)
    assert await frost.is_warm(session, constants, door)


async def test_the_brazier_needs_fuel_and_no_grid(
    session: AsyncSession, constants: Constants
) -> None:
    """The first spark of a dead city: it burns its own and asks no pool."""
    wild = await world.create_node(
        session,
        f"aurora.ice.{uuid.uuid4().hex[:6]}",
        "Ледяная равнина",
        planet=Planet.AURORA,
        area_m2=100,
        layer=Layer.PLANET,
        parent=await _sphere(session, Planet.AURORA, frost.FROST),
    )
    await _place(session, wild, BRAZIER)
    assert not await frost.is_warm(session, constants, wild), "пустая жаровня — холодное железо"
    await _place(session, wild, COAL, qty=10)
    assert await frost.is_warm(session, constants, wild)


async def test_braziers_burn_their_fuel(session: AsyncSession, constants: Constants) -> None:
    """A fire nobody watches is still a fire: the tick burns what lies with it."""
    _, yard = await _town(session)
    await _place(session, yard, BRAZIER)
    await _place(session, yard, COAL, qty=10)

    burnt = await frost.tick_fires(session, constants, hours=1)
    assert burnt == pytest.approx(constants[R.FROST_BRAZIER_FUEL_DRAW])


async def test_a_brazier_on_a_warm_planet_burns_nothing(
    session: AsyncSession, constants: Constants
) -> None:
    """A brazier in a Terran yard next to the coal pile must not quietly eat
    the city's fuel: where there is no climate, nobody lights one."""
    _, yard = await _town(session, planet=Planet.TERRA, climate=None)
    await _place(session, yard, BRAZIER)
    await _place(session, yard, COAL, qty=10)
    assert await frost.tick_fires(session, constants, hours=1) == 0


async def test_heat_eats_the_pool(session: AsyncSession, constants: Constants) -> None:
    """A heated node eats round the clock, and `produce` takes it off in one pass."""
    city, yard = await _town(session)
    await _place(session, yard, HEATER)
    pool = await energy.pool_of(session, constants, yard)
    assert pool is not None
    stored = constants[R.FROST_HEATER_DRAW] * 3
    pool.stored = Decimal(str(stored))
    pool.counted_at = _ago(1)
    await session.flush()

    await energy.produce(session, constants, pool, now=datetime.now(UTC))
    assert float(pool.stored) == pytest.approx(stored - constants[R.FROST_HEATER_DRAW], rel=1e-3)


# --- machines in the cold -----------------------------------------------------


async def test_a_frozen_node_stops_the_bench(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """«верстак не крафтит» -- the same refusal an unpaid bill gives (D-231)."""
    _, yard = await _town(session)
    session.add(Building(node_id=yard.id, area_m2=200))
    await _place(session, yard, BENCH)
    body = await _dweller(session, yard)
    master = await session.get(Identity, body.identity_id)
    assert master is not None
    await world.learn(session, master, MAKE)
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, "wood", amount=50, quality=60, origin="тест")

    with pytest.raises(frost.Frozen):
        await craft.start(session, constants, catalog, body, MAKE, 1)

    #: Heat it, and the same batch starts: nothing else about the node changed.
    await _place(session, yard, HEATER)
    await _charge(session, constants, yard, constants[R.FROST_HEATER_DRAW])
    assert await craft.start(session, constants, catalog, body, MAKE, 1) is not None


async def test_a_batch_waiting_in_a_frozen_node_does_not_break_waking(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A frozen bench refuses **work**, not the world around it.

    Resuming a batch happens inside waking and inside the arrival job, and a
    refusal thrown out of there is not a refusal to the player: the body would
    fail to wake up and the arrival job would retry for ever, leaving it in
    transit until the cold finished it.
    """
    _, yard = await _town(session)
    session.add(Building(node_id=yard.id, area_m2=200))
    await _place(session, yard, BENCH)
    await _place(session, yard, HEATER)
    await _charge(session, constants, yard, constants[R.FROST_HEATER_DRAW])
    body = await _dweller(session, yard)
    master = await session.get(Identity, body.identity_id)
    assert master is not None
    await world.learn(session, master, MAKE)
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, "wood", amount=50, quality=60, origin="тест")

    #: The work starts in the warm and freezes when the body lies down.
    await craft.start(session, constants, catalog, body, MAKE, 1)
    body.stamina = Decimal("1")
    await session.flush()
    await rest.sleep(session, constants, body)

    #: The city lets its heat go out while the body sleeps.
    await _charge(session, constants, yard, 0)
    assert not await frost.is_warm(session, constants, yard)

    #: Waking must work. The batch stays waiting: the bench is frozen, not lost.
    await rest.wake(session, constants, body)
    assert body.sleeping_since is None
    assert await craft.running(session, body) is None


async def test_a_frozen_node_does_not_print_a_body(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The printer is a machine like any other: the door is there, but the city
    let its heat go out, and whoever wants through it learns that -- refused,
    not hidden."""
    _, yard = await _town(session)
    await _place(session, yard, "bioprinter")
    identity = await world.create_identity(session, f"Облако-{uuid.uuid4().hex[:6]}")

    with pytest.raises(frost.Frozen):
        await death.order(session, constants, catalog, identity, yard)


async def test_what_burns_works_in_any_frost(session: AsyncSession, constants: Constants) -> None:
    """Without this rule a frozen city could never be lit: the generator that
    must give the first heat would itself be standing frozen."""
    _, yard = await _town(session)
    assert not await frost.is_warm(session, constants, yard)
    assert await frost.works_here(session, constants, yard, BRAZIER)
    assert await frost.works_here(session, constants, yard, "coal_plant")
    assert not await frost.works_here(session, constants, yard, BENCH)


async def test_a_frozen_node_silences_the_terminal_and_the_office(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """«администрация закрыта, терминал молчит» (D-231).

    Both are in the decision by name, and both had the gate in the engine and
    nothing pinning it: a refactor could have dropped either and every test
    would still have passed.
    """
    from src.engine import market
    from src.engine.city import founding, hall, office

    delegate, yard = await _town(session)
    settlement = await founding.found(session, catalog, delegate, f"Мерид-{uuid.uuid4().hex[:4]}")
    session.add(Building(node_id=yard.id, area_m2=200))
    await _place(session, yard, TERMINAL)
    await _place(session, yard, HALL)
    body = await _dweller(session, yard)
    yard.owner_city_id = settlement.id
    await session.flush()

    with pytest.raises(frost.Frozen):
        await market.terminal(session, yard)
    #: By the key, not by the sentence: the wording is the locale's (D-251 III).
    with pytest.raises(office.NotAllowed) as shut:
        await hall.require_at_hall(session, body, settlement)
    assert shut.value.key == "city-hall-frozen"

    #: Heat it, and both open. Nothing else about the node changed.
    await _place(session, yard, HEATER)
    await _charge(session, constants, yard, constants[R.FROST_HEATER_DRAW])
    assert await market.terminal(session, yard) is not None
    await hall.require_at_hall(session, body, settlement)


async def test_nothing_is_sown_in_the_open_ground_of_a_climate(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """«земледелие на мерзлоте не работает в открытом грунте» (D-231).

    Aurora's plots want greenhouses and Pyroxis wants nothing at all; until
    that mechanic exists, the food of both arrives by ship. Refused at the
    **marking out** rather than at the sowing: a plot is estate, and a plot
    nobody could ever sow would be a thing sold to a player for nothing.

    Not a rule about the cold but about the climate: the scorching planet is
    refused by the same line, and a heated node does not lift it -- a stove
    warms a workshop, not a field under the open sky.
    """
    from src.engine import farm

    for planet, weather in ((Planet.AURORA, frost.FROST), (Planet.PYROXIS, frost.HEAT)):
        _, yard = await _town(session, planet=planet, climate=weather)
        body = await _dweller(session, yard)
        yard.owner_identity_id = body.identity_id
        await session.flush()
        #: Asserted by the key and the climate it carries, not by the sentence:
        #: the wording belongs to the locale now (D-251 wave III).
        with pytest.raises(farm.FarmError) as refused:
            await farm.mark(session, constants, body, name="Поле", area=100)
        assert refused.value.key == "farm-no-open-ground"
        assert refused.value.params["weather"] == weather

        #: And heating the node changes nothing: the field is still outdoors.
        #: Only where a stove means anything -- on the scorching planet there
        #: are no shelters at all, and a heater there is not even wrong.
        if weather == frost.FROST:
            await _place(session, yard, HEATER)
            await _charge(session, constants, yard, constants[R.FROST_HEATER_DRAW])
            assert await frost.is_warm(session, constants, yard)
            with pytest.raises(farm.FarmError) as warmed:
                await farm.mark(session, constants, body, name="Поле", area=100)
            assert warmed.value.key == "farm-no-open-ground"
            assert warmed.value.params["weather"] == weather

    #: On Terra the same call marks the plot out: the rule is the climate's.
    _, home = await _town(session, planet=Planet.TERRA, climate=None)
    settler = await _dweller(session, home)
    home.owner_identity_id = settler.identity_id
    await session.flush()
    assert await farm.mark(session, constants, settler, name="Поле", area=100) is not None


async def test_a_bed_in_the_cold_is_not_a_home(session: AsyncSession, constants: Constants) -> None:
    """Furniture does not work in a frozen node either: sleep there is the
    mistake the planet kills for, not a night at home."""
    _, yard = await _town(session)
    await _place(session, yard, "bed")
    body = await _dweller(session, yard)
    body.stamina = Decimal("1")
    await session.flush()

    await rest.sleep(session, constants, body)
    assert body.sleeping_home is False
