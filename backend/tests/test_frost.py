# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Warmth: the node is warm or cold, the body carries hours (D-231).

Checked is what the mechanic was made for, and nothing about degrees:

* a planet without a climate costs nothing at all -- Terra is warm everywhere,
  and no reserve of anybody standing there is ever touched;
* on the permafrost the node is warm only while something heats it, and what
  heats it eats the city pool round the clock: an empty pool is a cold city;
* the plant reaches its neighbours, the heater does not, the brazier needs no
  grid but does need fuel;
* a frozen node stops machines -- **except the ones that burn**, without which
  a dead city could never be lit again;
* the reserve melts hour by hour, a suit multiplies it, a warmer adds to it,
  and a body whose stamina ran out in the cold dies where it lies, offline
  and asleep included.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import craft, death, energy, frost, gear, rest, travel, world
from src.models.estate import Building
from src.models.identity import Body, BodyState, Identity
from src.models.world import Layer, Node, Planet
from src.units import SECONDS_PER_HOUR

BENCH = "workbench"
TERMINAL = "market_terminal"
HALL = "administration"
#: Made at the workbench out of wood alone -- the simplest honest batch.
MAKE = "handle"
COAL = "coal"
PLANT = "heat_plant"
HEATER = "heater"
BRAZIER = "brazier"
SUIT = "insulated_suit"
WARMER = "warmer"


async def _sphere(session: AsyncSession, planet: Planet, climate: str | None) -> Node:
    """The planet's node on the space layer -- the seed lays exactly this (D-231)."""
    return await world.create_node(
        session,
        planet.value,
        planet.value,
        planet=planet,
        area_m2=1,
        layer=Layer.SPACE,
        properties={} if climate is None else {climate: True},
    )


async def _town(
    session: AsyncSession, *, planet: Planet = Planet.AURORA, climate: str | None = frost.FROST
) -> tuple[Node, Node]:
    """A city on the planet: a delegate node with one built-up node under it."""
    sphere = await _sphere(session, planet, climate)
    stamp = uuid.uuid4().hex[:8]
    city = await world.create_node(
        session,
        f"{planet.value}.city.{stamp}",
        "Город",
        planet=planet,
        area_m2=1,
        layer=Layer.PLANET,
        parent=sphere,
    )
    yard = await world.create_node(
        session,
        f"{planet.value}.city.{stamp}.yard",
        "Двор",
        planet=planet,
        area_m2=200,
        layer=Layer.CITY,
        parent=city,
    )
    return city, yard


async def _dweller(session: AsyncSession, node: Node) -> Body:
    identity = await world.create_identity(session, f"Житель-{uuid.uuid4().hex[:6]}")
    return await world.print_body(session, identity, node)


async def _place(session: AsyncSession, node: Node, what: str, qty: float = 1) -> None:
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, what, amount=qty, quality=60, origin="тест")


async def _charge(session: AsyncSession, constants: Constants, node: Node, stored: float) -> None:
    """Put energy into the city pool by hand: generation is another test's business."""
    pool = await energy.pool_of(session, constants, node)
    assert pool is not None
    pool.stored = Decimal(str(stored))
    pool.counted_at = datetime.now(UTC)
    await session.flush()


def _ago(hours: float) -> datetime:
    return datetime.now(UTC) - timedelta(hours=hours)


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
    from src.engine.city import polity

    delegate, yard = await _town(session)
    settlement = await polity.found(session, catalog, delegate, f"Мерид-{uuid.uuid4().hex[:4]}")
    session.add(Building(node_id=yard.id, area_m2=200))
    await _place(session, yard, TERMINAL)
    await _place(session, yard, HALL)
    body = await _dweller(session, yard)
    yard.owner_city_id = settlement.id
    await session.flush()

    with pytest.raises(frost.Frozen):
        await market.terminal(session, yard)
    #: By the key, not by the sentence: the wording is the locale's (D-251 III).
    with pytest.raises(polity.NotAllowed) as shut:
        await polity.require_at_hall(session, body, settlement)
    assert shut.value.key == "city-hall-frozen"

    #: Heat it, and both open. Nothing else about the node changed.
    await _place(session, yard, HEATER)
    await _charge(session, constants, yard, constants[R.FROST_HEATER_DRAW])
    assert await market.terminal(session, yard) is not None
    await polity.require_at_hall(session, body, settlement)


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


# --- the body's reserve -------------------------------------------------------


async def test_the_reserve_melts_hour_by_hour(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, yard = await _town(session)
    body = await _dweller(session, yard)
    body.warmth_at = _ago(2)
    await session.flush()

    left = await frost.settle(session, constants, catalog, body)
    assert left == pytest.approx(constants[R.FROST_RESERVE_MAX] - 2, abs=0.05)


async def test_a_warm_node_fills_the_reserve_back(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Coming back is faster than going -- and never above the ceiling."""
    _, yard = await _town(session)
    await _place(session, yard, HEATER)
    await _charge(session, constants, yard, constants[R.FROST_HEATER_DRAW])
    body = await _dweller(session, yard)
    body.warmth = Decimal("0")
    body.warmth_at = _ago(1)
    await session.flush()

    left = await frost.settle(session, constants, catalog, body)
    assert left == pytest.approx(constants[R.FROST_WARM_RATE], abs=0.05)

    body.warmth_at = _ago(10)
    await session.flush()
    assert await frost.settle(session, constants, catalog, body) == pytest.approx(
        constants[R.FROST_RESERVE_MAX], abs=0.05
    )


async def test_the_road_is_the_cold_itself(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A body between nodes is out on the ice: the node it left does not go on
    heating it, or a walk from a warm city would cost nothing at all."""
    city, yard = await _town(session)
    await _place(session, yard, HEATER)
    await _charge(session, constants, yard, constants[R.FROST_HEATER_DRAW])
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
    body = await _dweller(session, yard)
    await travel.depart(session, constants, body, door)

    body.warmth = Decimal(str(constants[R.FROST_RESERVE_MAX]))
    body.warmth_at = _ago(1)
    await session.flush()
    left = await frost.settle(session, constants, catalog, body)
    assert left == pytest.approx(constants[R.FROST_RESERVE_MAX] - 1, abs=0.05)


async def test_the_suit_multiplies_the_reserve(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, yard = await _town(session)
    body = await _dweller(session, yard)
    bare = await frost.limit_of(session, constants, catalog, body)

    pocket = await world.body_container(session, body)
    suit = await world.grant_item(session, pocket, SUIT, quality=60, origin="тест")
    await gear.equip(session, constants, catalog, body, suit)

    assert await frost.limit_of(session, constants, catalog, body) == pytest.approx(
        bare * constants[R.FROST_SUIT_K][SUIT]
    )


async def test_a_warmer_adds_hours_and_is_gone(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The thing one walks into the cold with. Above the ceiling it is not kept."""
    _, yard = await _town(session)
    body = await _dweller(session, yard)
    body.warmth = Decimal("0")
    body.warmth_at = datetime.now(UTC)
    await session.flush()

    pocket = await world.body_container(session, body)
    warmer = await world.grant_item(session, pocket, WARMER, quality=60, origin="тест")
    gained = await frost.use_warmer(session, constants, catalog, body, warmer)

    assert gained == pytest.approx(constants[R.FROST_WARMER_HOURS])
    assert float(body.warmth) == pytest.approx(constants[R.FROST_WARMER_HOURS])
    assert await world.contents(session, pocket) == ()


async def test_a_warmer_that_would_give_nothing_is_refused(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A thing that vanishes for no effect is a silent sink of matter: on a full
    reserve, and on a planet where nobody freezes at all, the warmer stays whole."""
    _, yard = await _town(session)
    await _place(session, yard, HEATER)
    await _charge(session, constants, yard, constants[R.FROST_HEATER_DRAW])
    body = await _dweller(session, yard)
    body.warmth = Decimal(str(constants[R.FROST_RESERVE_MAX]))
    body.warmth_at = _ago(1)
    await session.flush()
    pocket = await world.body_container(session, body)
    warmer = await world.grant_item(session, pocket, WARMER, quality=60, origin="тест")

    with pytest.raises(frost.FrostError):
        await frost.use_warmer(session, constants, catalog, body, warmer)
    assert len(await world.contents(session, pocket)) == 1

    _, terra = await _town(session, planet=Planet.TERRA, climate=None)
    other = await _dweller(session, terra)
    theirs = await world.body_container(session, other)
    spare = await world.grant_item(session, theirs, WARMER, quality=60, origin="тест")
    with pytest.raises(frost.FrostError):
        await frost.use_warmer(session, constants, catalog, other, spare)
    assert len(await world.contents(session, theirs)) == 1


async def test_the_look_carries_the_hand_and_not_the_hour(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The client is given the stamp, the rate and the ceiling and draws the
    hand itself (D-226); on a planet without a climate it is given nothing."""
    _, yard = await _town(session)
    body = await _dweller(session, yard)
    view = await frost.view(session, constants, catalog, body, yard)
    assert view is not None
    assert view["climate"] == frost.FROST
    assert view["warm"] is False
    assert view["per_hour"] == -1.0
    assert view["max"] == constants[R.FROST_RESERVE_MAX]
    #: A body that has never been cold is a full reserve **as of now**: an old
    #: stamp would have the client count down from the day it was printed.
    assert view["hours"] == constants[R.FROST_RESERVE_MAX]
    assert datetime.fromisoformat(view["at"]) >= _ago(1)

    #: Once it has been settled, the stamp is the settling's own.
    await frost.settle(session, constants, catalog, body)
    settled = await frost.view(session, constants, catalog, body, yard)
    assert settled is not None
    assert settled["at"] == body.warmth_at.isoformat()
    #: Catalog constants are read from `/public/*`, never sent per look (D-225).
    assert "frozen_stamina" not in view
    assert "frozen_drain_k" not in view

    _, terra = await _town(session, planet=Planet.TERRA, climate=None)
    other = await _dweller(session, terra)
    assert await frost.view(session, constants, catalog, other, terra) is None


# --- the world's own hours ----------------------------------------------------


async def test_the_frozen_burn_stamina_and_die(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A night in the frost is exactly the mistake the planet kills for, and
    there is no offline mercy: the tick does it without a login."""
    _, yard = await _town(session)
    body = await _dweller(session, yard)
    body.warmth = Decimal("0")
    body.warmth_at = _ago(1)
    body.stamina = Decimal(str(constants[R.FROST_FROZEN_STAMINA] * 2))
    await session.flush()

    dead = await frost.tick_bodies(session, constants, catalog)
    assert dead == 0
    assert float(body.stamina) == pytest.approx(constants[R.FROST_FROZEN_STAMINA], abs=0.5)

    body.warmth_at = _ago(2)
    await session.flush()
    assert await frost.tick_bodies(session, constants, catalog) == 1
    assert body.state is BodyState.DEAD


async def test_the_cold_is_paid_by_whoever_counts_it(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """An active frozen body must not outrun the cold.

    Every command settles the reserve, and a settling that only moved the stamp
    would hand the hour to nobody: the tick would find a minute where an hour
    had passed, and a player who keeps clicking would pay a sixth of what a
    sleeping one pays. D-231 charges for time, not for idleness.
    """
    _, yard = await _town(session)
    body = await _dweller(session, yard)
    body.warmth = Decimal("0")
    body.warmth_at = _ago(1)
    body.stamina = Decimal(str(constants[R.BODY_STAMINA_MAX]))
    await session.flush()

    #: One command's worth of settling -- and the hour is paid.
    assert (
        await frost.drain_multiplier(session, constants, body) == constants[R.FROST_FROZEN_DRAIN_K]
    )
    paid = constants[R.BODY_STAMINA_MAX] - float(body.stamina)
    assert paid == pytest.approx(constants[R.FROST_FROZEN_STAMINA], rel=0.05)

    #: And the tick right behind it takes nothing a second time.
    await frost.tick_bodies(session, constants, catalog)
    assert constants[R.BODY_STAMINA_MAX] - float(body.stamina) == pytest.approx(paid, rel=0.05)


async def test_an_hour_is_never_charged_twice(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A settling does not run backwards.

    The tick carries the nominal moment of its tick and can reach a body a
    second after the body's own command settled it. Writing that older stamp
    back would leave the seconds between them to be counted -- and charged --
    all over again.
    """
    _, yard = await _town(session)
    body = await _dweller(session, yard)
    body.warmth = Decimal("0")
    body.warmth_at = _ago(1)
    body.stamina = Decimal(str(constants[R.BODY_STAMINA_MAX]))
    await session.flush()

    now = datetime.now(UTC)
    await frost.settle(session, constants, catalog, body, now=now)
    paid = constants[R.BODY_STAMINA_MAX] - float(body.stamina)
    stamp = body.warmth_at

    #: The tick, running a little behind the player's own command.
    await frost.tick_bodies(session, constants, catalog, now=now - timedelta(seconds=20))
    assert body.warmth_at == stamp
    assert constants[R.BODY_STAMINA_MAX] - float(body.stamina) == pytest.approx(paid)


async def test_the_warm_are_left_alone_by_the_tick(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A body on Terra costs the world no writes: the sweep does not see it."""
    _, terra = await _town(session, planet=Planet.TERRA, climate=None)
    body = await _dweller(session, terra)
    body.warmth = Decimal("0")
    stamp = _ago(100)
    body.warmth_at = stamp
    await session.flush()

    assert await frost.tick_bodies(session, constants, catalog) == 0
    assert body.warmth_at == stamp
    assert float(body.stamina) == constants[R.BODY_STAMINA_MAX]


async def test_the_cold_makes_every_step_dearer(
    session: AsyncSession, constants: Constants
) -> None:
    """The frozen body pays more for any work -- and a warm one pays as before."""
    _, yard = await _town(session)
    body = await _dweller(session, yard)
    assert await frost.drain_multiplier(session, constants, body) == 1.0

    body.warmth = Decimal("0")
    await session.flush()
    assert (
        await frost.drain_multiplier(session, constants, body) == constants[R.FROST_FROZEN_DRAIN_K]
    )


async def test_a_body_on_terra_is_never_read_as_frozen(
    session: AsyncSession, constants: Constants
) -> None:
    """The stamp of a body that has never been cold says nothing, and the
    multiplier must not read it as death by frost on the capital's square."""
    _, terra = await _town(session, planet=Planet.TERRA, climate=None)
    body = await _dweller(session, terra)
    body.warmth = Decimal("0")
    body.warmth_at = _ago(1000)
    await session.flush()
    assert await frost.drain_multiplier(session, constants, body) == 1.0


async def test_a_body_that_has_never_been_cold_arrives_with_a_full_reserve(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Empty means never measured -- every body printed before the frost existed
    is one, and the ice must not kill it in the first minute for that."""
    _, yard = await _town(session)
    body = await _dweller(session, yard)
    body.warmth = None
    body.warmth_at = _ago(1)
    await session.flush()

    left = await frost.settle(session, constants, catalog, body)
    assert left == pytest.approx(constants[R.FROST_RESERVE_MAX] - 1, abs=0.05)


# --- two sessions at once -----------------------------------------------------


async def test_the_tick_and_the_player_do_not_spend_one_stamina_twice(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stamina is a quantity of the body (CLAUDE.md): the frost tick takes it
    off while the player is spending it on the road.

    Without the row lock both read the same stamina, both subtract from what
    they read, and one of the two write-offs disappears -- the body walks out of
    the cold richer than it went in.
    """
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
    #: A long road on purpose: the two write-offs must be of one order, or a
    #: lost update would hide inside the rounding of the bigger one.
    await travel.connect(session, yard, door, base_seconds=SECONDS_PER_HOUR * 7.5)
    body = await _dweller(session, yard)
    #: Frozen an hour ago and rested: enough stamina for both the road and the
    #: cold, so neither is refused and both must be paid.
    body.warmth = Decimal("0")
    body.warmth_at = _ago(1)
    body.stamina = Decimal(str(constants[R.BODY_STAMINA_MAX]))
    await session.flush()
    body_id = body.id
    await session.commit()

    #: Hold each transaction between reading the stamina and writing it back:
    #: a local database answers too fast for the window to open by itself.
    original = frost.limit_of

    async def held(*args, **kwargs):
        result = await original(*args, **kwargs)
        await asyncio.sleep(0.2)
        return result

    monkeypatch.setattr(frost, "limit_of", held)

    async def tick() -> None:
        async with factory() as db, db.begin():
            await frost.tick_bodies(db, constants, catalog)

    async def walk() -> None:
        async with factory() as db, db.begin():
            found = (await db.execute(select(Body).where(Body.id == body_id))).scalars().one()
            target = await db.get(Node, door.id)
            assert target is not None
            await travel.depart(db, constants, found, target)

    await asyncio.gather(tick(), walk())

    async with factory() as db:
        again = (await db.execute(select(Body).where(Body.id == body_id))).scalars().one()
        burnt = constants[R.BODY_STAMINA_MAX] - float(again.stamina)
        #: Both write-offs are in: the cold's hour and the road, and the road at
        #: the frozen body's rate.
        cold = constants[R.FROST_FROZEN_STAMINA]
        road = (
            travel.stamina_cost(constants, SECONDS_PER_HOUR * 7.5, transport=False)
            * constants[R.FROST_FROZEN_DRAIN_K]
        )
        assert burnt == pytest.approx(cold + road, rel=0.05)
