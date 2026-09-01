# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The body in the cold (D-231).

The reserve of warm hours: how it melts on the road and fills back under a
roof, what the suit and the warmer buy, how the tick charges the hours
exactly once and what happens when the last of them is spent. The places
and the machines live in `test_frost.py`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from frost_kit import HEATER, _ago, _charge, _dweller, _place, _town
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import frost, gear, travel, world
from src.models.identity import Body, BodyState
from src.models.world import Layer, Node, Planet
from src.units import SECONDS_PER_HOUR

SUIT = "insulated_suit"

WARMER = "warmer"

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
    #: Patched where the name is looked up (the frost split): `hours._burn`
    #: binds `limit_of` into its own globals, so slowing the package
    #: re-export would slow nobody.
    from src.engine.frost import hours as frost_hours

    original = frost_hours.limit_of

    async def held(*args, **kwargs):
        result = await original(*args, **kwargs)
        await asyncio.sleep(0.2)
        return result

    monkeypatch.setattr(frost_hours, "limit_of", held)

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
