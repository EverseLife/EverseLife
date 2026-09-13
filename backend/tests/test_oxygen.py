# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Oxygen: the second scale of survival, and only where there is no air
(D-233, D-234).

Checked is what the whole mechanic rests on:

* the question exists only where the **planet** says so. On Terra the reading is
  empty and nothing is ever spent -- the same shape the cold has;
* a body outside breathes a **cylinder through a suit**, and neither half alone
  is worth anything: a bare body dies with a full bag;
* the step into a place with nothing to breathe is refused **before** it is
  taken, so nobody dies of a door;
* two settlings of one body in the same second spend one cylinder's worth, not
  two: the reserve is a quantity of a shared thing, and locks are the whole
  reason it stays one (CLAUDE.md).

A hull and its crew are in `test_oxygen_hull.py`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from oxygen_kit import _cylinder, _ground, _hull, _person, _port, _sphere, _suited
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import oxygen, travel
from src.models.identity import Body
from src.models.world import Planet, Surface
from src.units import AMOUNT_SCALE, ROUND_AMOUNT, ROUND_REMAINDER

# --- where the question arises at all -----------------------------------------


async def test_terra_breathes_and_pyroxis_does_not(session: AsyncSession) -> None:
    """The planet decides, and it decides in the world rather than in a constant."""
    terra = await _sphere(session, Planet.TERRA, airless=False)
    pyroxis = await _sphere(session, Planet.PYROXIS, airless=True)
    field = await _ground(session, Planet.TERRA, terra)
    rock = await _ground(session, Planet.PYROXIS, pyroxis, name="Чёрное поле")

    assert await oxygen.free_air(session, field) is True
    assert await oxygen.free_air(session, rock) is False


async def test_a_hull_is_sealed_in_flight_and_open_in_port(
    session: AsyncSession, constants: Constants
) -> None:
    """In port under a sky that has air the hatch may as well be open (D-233)."""
    await _sphere(session, Planet.TERRA, airless=False)
    port = await _port(session)
    vessel, _, connector = await _hull(session, constants, port)

    assert await oxygen.sealed(session, vessel) is False
    assert await oxygen.free_air(session, connector) is True

    vessel.docked_node_id = None
    await session.flush()
    assert await oxygen.sealed(session, vessel) is True
    assert await oxygen.free_air(session, connector) is False


async def test_a_terran_reading_is_empty(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """No air scale where there is air: no key in the look, as with the cold."""
    terra = await _sphere(session, Planet.TERRA, airless=False)
    field = await _ground(session, Planet.TERRA, terra)
    body = await _person(session, field)
    assert await oxygen.view(session, constants, catalog, body, field) is None


# --- a body outside breathes a cylinder through a suit -------------------------


async def test_a_bare_body_breathes_nothing_however_many_cylinders(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The suit is the connection (D-234): without one the bag is luggage."""
    pyroxis = await _sphere(session, Planet.PYROXIS, airless=True)
    rock = await _ground(session, Planet.PYROXIS, pyroxis)
    body = await _person(session, rock)
    await _cylinder(session, body, 100)
    body.air_at = datetime.now(UTC) - timedelta(hours=1)
    await session.flush()

    breath = await oxygen.settle(session, constants, catalog, body)
    assert breath.uncovered > 0, "дышать нечем: скафандра нет"
    assert await oxygen.carried(session, body) == pytest.approx(100, abs=0.01), "баллон не тронут"


def test_the_air_grid_is_one_number_in_two_places() -> None:
    """`AMOUNT_SCALE` and `ROUND_AMOUNT` say the same grid, one as a scale and
    one as places. Move either alone and every figure floored to the amount
    grid is floored to the wrong one -- silently, and always downwards.
    """
    assert AMOUNT_SCALE == 10**ROUND_AMOUNT


def test_the_air_debt_is_kept_at_the_scale_it_is_written_with() -> None:
    """`ROUND_REMAINDER` and the debt column are one number in two places."""
    assert Body.__table__.c.air_owed.type.scale == ROUND_REMAINDER


async def test_breathing_often_costs_the_same_air_as_breathing_once(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A body settled every second on Pyroxis spends the hour it stood there.

    Air is split into thousandths, and at `oxygen.body_draw` a stretch under
    seven seconds cannot be taken out of a cylinder. The breath used to be
    asked for, rounded away and forgotten, and every step settles the
    breathing -- the gangway off a landed ship is seven tenths of a second --
    so a suited body could stand on an airless world for ever on a drop.
    """
    pyroxis = await _sphere(session, Planet.PYROXIS, airless=True)
    rock = await _ground(session, Planet.PYROXIS, pyroxis)
    often = await _person(session, rock)
    once = await _person(session, rock)
    started = datetime.now(UTC)
    for who in (often, once):
        await _cylinder(session, who, 6)
        await _suited(session, constants, catalog, who)
        who.air_at = started
    await session.flush()

    #: Two seconds apart, well under the seven a thousandth of air buys.
    steps, every = 60, timedelta(seconds=2)
    for tick in range(1, steps + 1):
        await oxygen.settle(session, constants, catalog, often, now=started + every * tick)
    await oxygen.settle(session, constants, catalog, once, now=started + every * steps)

    spent = constants[R.OXYGEN_BODY_DRAW] * (steps * every) / timedelta(hours=1)
    left_once = await oxygen.carried(session, once)
    left_often = await oxygen.carried(session, often)
    #: The two minutes really cost something, or the test proves nothing.
    assert 6 - left_once == pytest.approx(spent, abs=0.001)
    #: And the busy body paid exactly what the quiet one paid.
    assert left_often == pytest.approx(left_once, abs=0.001)


async def test_a_body_out_of_air_cannot_step_onto_airless_ground(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """What keeps a walker from outrunning suffocation is the door, not the debt.

    Every step settles the breathing, and the tick settles it again -- and the
    tick clears the mark of choking whenever its own stretch came up covered.
    A step's stretch is far too short to come up short, so a walker could in
    principle keep the reaper at bay by walking. It cannot, but not for the
    reason the debt suggests: `require_air` refuses the step outright when the
    bottle is empty, so a body with nothing to breathe cannot take one. This
    pins that door, since removing it would make the hole real.
    """
    pyroxis = await _sphere(session, Planet.PYROXIS, airless=True)
    rock = await _ground(session, Planet.PYROXIS, pyroxis)
    beyond = await _ground(session, Planet.PYROXIS, pyroxis, name="Дальше")
    await travel.connect(session, rock, beyond, base_seconds=1, surface=Surface.PAVED)
    body = await _person(session, rock)
    await _suited(session, constants, catalog, body)
    await _cylinder(session, body, 0.001)
    body.air_at = datetime.now(UTC)
    await session.flush()

    #: Breathe the drop away, then try to walk.
    await oxygen.settle(session, constants, catalog, body, now=body.air_at + timedelta(minutes=1))
    #: By the key, not merely by the class: `require_air` has three refusals,
    #: and the one this pins is the empty bottle. The other two would satisfy
    #: the same assertion while leaving the hole open -- and which of them
    #: answers depends on the length of the road, which is this test's choice.
    with pytest.raises(oxygen.NoAir) as refused:
        await travel.depart(session, constants, body, beyond)
    assert refused.value.key == "oxygen-tanks-empty"


async def test_stepping_aboard_does_not_forgive_the_air_owed_outside(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The gangway is not a way of breathing free.

    A ship landed on bare ground makes an edge of seven tenths of a second,
    and every step settles the breathing. If what the ground breathed but
    could not be charged for rode on the stamp, arriving in air would move
    that stamp to now and forgive it -- so a body stepping off and back on
    would pay nothing, for ever. The debt is the body's own, and it survives
    the crossing.
    """
    pyroxis = await _sphere(session, Planet.PYROXIS, airless=True)
    rock = await _ground(session, Planet.PYROXIS, pyroxis)
    #: The other side of the gangway is simply somewhere with air: `settle`
    #: asks the node it stands on and nothing else.
    terra = await _sphere(session, Planet.TERRA, airless=False)
    inside = await _ground(session, Planet.TERRA, terra, name="Палуба")
    body = await _person(session, rock)
    await _cylinder(session, body, 6)
    await _suited(session, constants, catalog, body)
    started = datetime.now(UTC)
    body.air_at = started
    await session.flush()

    #: Off the ship and back, twenty times: two seconds on the ground each
    #: time, and two aboard, where breathing is free.
    moment = started
    for _ in range(20):
        moment += timedelta(seconds=2)
        body.node_id = rock.id
        await oxygen.settle(session, constants, catalog, body, now=moment)
        moment += timedelta(seconds=2)
        body.node_id = inside.id
        await oxygen.settle(session, constants, catalog, body, now=moment)
    body.node_id = rock.id

    #: Forty seconds on the rock, and every one of them paid for.
    spent = constants[R.OXYGEN_BODY_DRAW] * timedelta(seconds=40) / timedelta(hours=1)
    left = await oxygen.carried(session, body)
    assert 6 - left == pytest.approx(spent, abs=0.001)


async def test_a_suited_body_spends_its_cylinder(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Outside the draw is `oxygen.body_draw`, and it comes out of the bottle."""
    pyroxis = await _sphere(session, Planet.PYROXIS, airless=True)
    rock = await _ground(session, Planet.PYROXIS, pyroxis)
    body = await _person(session, rock)
    await _cylinder(session, body, 6)
    await _suited(session, constants, catalog, body)
    body.air_at = datetime.now(UTC) - timedelta(hours=2)
    await session.flush()

    breath = await oxygen.settle(session, constants, catalog, body)
    spent = 2 * constants[R.OXYGEN_BODY_DRAW]
    assert breath.uncovered == 0
    assert breath.left == pytest.approx(6 - spent, abs=0.01)


async def test_the_step_into_vacuum_is_refused_before_it_is_taken(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Death by ignorance in one click is not this world's way (D-233)."""
    terra = await _sphere(session, Planet.TERRA, airless=False)
    pyroxis = await _sphere(session, Planet.PYROXIS, airless=True)
    one = await _ground(session, Planet.PYROXIS, pyroxis, name="Плато")
    other = await _ground(session, Planet.PYROXIS, pyroxis, name="Чёрное поле")
    await travel.connect(session, one, other, base_seconds=60, surface=Surface.TRAIL)
    body = await _person(session, one)

    with pytest.raises(oxygen.NoAir):
        await travel.depart(session, constants, body, other)

    await _suited(session, constants, catalog, body)
    await _cylinder(session, body, 6)
    assert await travel.depart(session, constants, body, other) is not None
    assert terra is not None


# --- the reserve is a quantity, and two hands must not spend it twice ----------


async def test_two_settlings_in_one_second_spend_one_cylinder_once(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """The tick and a command land together on one body.

    Without the row lock both read the same stamp, both charge the same stretch
    and the cylinder loses twice what an hour costs -- systematically in the
    world's favour, and invisible until somebody suffocates early.
    """
    async with factory() as session, session.begin():
        pyroxis = await _sphere(session, Planet.PYROXIS, airless=True)
        rock = await _ground(session, Planet.PYROXIS, pyroxis)
        body = await _person(session, rock)
        await _cylinder(session, body, 6)
        await _suited(session, constants, catalog, body)
        body.air_at = datetime.now(UTC) - timedelta(hours=2)
        await session.flush()
        body_id = body.id

    ready = asyncio.Barrier(2)

    async def settle() -> None:
        async with factory() as db, db.begin():
            mine = await db.get(Body, body_id)
            await ready.wait()
            await oxygen.settle(db, constants, catalog, mine)

    await asyncio.gather(settle(), settle())

    async with factory() as session:
        body = await session.get(Body, body_id)
        left = await oxygen.carried(session, body)
    spent = 2 * constants[R.OXYGEN_BODY_DRAW]
    assert left == pytest.approx(6 - spent, abs=0.01), (
        f"два счёта списали {6 - left:.2f} вместо {spent:.2f}"
    )


async def test_refilling_gives_the_grace_back(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A body that ran dry once and breathed again is not marked for death.

    Without this the mark stays for ever, and the next stretch the cylinder only
    half covers kills on the spot -- with none of the settling of warning the
    module promises.
    """
    pyroxis = await _sphere(session, Planet.PYROXIS, airless=True)
    rock = await _ground(session, Planet.PYROXIS, pyroxis)
    body = await _person(session, rock)
    await _suited(session, constants, catalog, body)
    body.air_at = datetime.now(UTC) - timedelta(hours=1)
    await session.flush()

    assert await oxygen.tick_bodies(session, constants, catalog) == 0
    assert body.choking_since is not None, "первый пустой счёт ставит отсчёт"

    await _cylinder(session, body, 6)
    body.air_at = datetime.now(UTC) - timedelta(hours=1)
    await session.flush()
    assert await oxygen.tick_bodies(session, constants, catalog) == 0
    assert body.choking_since is None, "заправился — отсрочка вернулась"
