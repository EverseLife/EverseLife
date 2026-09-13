# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Oxygen aboard: a hull breathes its crew off the life support's line
(D-233, D-234, D-288).

Checked:

* what is breathed is what stands on the line -- a vessel **installed**
  aboard that the line names; no system, and nothing aboard is breathed; the
  line runs dry, and the crew has one settling of grace before it dies;
* a tick at a time the crew breathes `oxygen.crew_draw`, and not a fifth more
  as it did at 0.1 an hour a head and a one-minute `time.tick`: what a
  thousandth cannot hold is carried on the hull, not rounded;
* two settlings of one hull drink its line once and carry its breath once;
* the bridge and the journal name one quantity off the line.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import _slow
from oxygen_kit import (
    AIR,
    CANISTER,
    CHEST,
    WATER,
    _held,
    _hull,
    _in_canister,
    _in_tank,
    _person,
    _plumb,
    _port,
    _sphere,
    _system,
)
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import oxygen, ship, storage, world
from src.models.event import Event, EventKind
from src.models.identity import BodyState
from src.models.inventory import Item
from src.models.ship import Ship
from src.models.world import Planet
from src.units import AMOUNT_SCALE, ROUND_REMAINDER

# --- the hull breathes off the life support's line -----------------------------


async def test_the_life_support_breathes_the_crew_off_its_line(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The system drinks and makes nothing (D-288): an hour aboard costs the
    crew's draw out of the vessels on its line, and a tank of water beside
    them is not so much as looked at -- air is the electrolyser's to make.
    """
    await _sphere(session, Planet.TERRA, airless=False)
    port = await _port(session)
    vessel, body, connector = await _hull(session, constants, port)
    system = await _system(session, connector)
    air = await _in_tank(session, connector, AIR, 10)
    water = await _in_tank(session, connector, WATER, 5000)
    await _plumb(session, system, air)

    vessel.docked_node_id = None
    vessel.air_at = datetime.now(UTC) - timedelta(hours=1)
    await session.flush()

    breathed, dead = await oxygen.tick_ships(session, constants, catalog)
    draw = constants[R.OXYGEN_CREW_DRAW]
    assert dead == 0
    assert breathed == pytest.approx(draw, abs=0.01), "час на одного — расход одного"
    assert await oxygen.reserve(session, constants, catalog, vessel) == pytest.approx(
        10 - draw, abs=0.01
    )
    assert body.choking_since is None
    assert await _held(session, water) == pytest.approx(5000), (
        "вода не тронута: система не электролизёр"
    )


async def test_without_a_system_nothing_aboard_is_breathed(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A hull with oxygen in its tanks and no life support breathes none of it.

    The system is the one thing aboard that breathes for people (D-288), and
    casting off without one is refused for exactly this (`flight._leaving`);
    a hull sealed anyway -- down on Pyroxis, unmoored by a test -- suffocates
    its crew beside full tanks.
    """
    await _sphere(session, Planet.TERRA, airless=False)
    port = await _port(session)
    vessel, body, connector = await _hull(session, constants, port)
    tank = await _in_tank(session, connector, AIR, 10)
    vessel.docked_node_id = None
    vessel.air_at = datetime.now(UTC) - timedelta(hours=1)
    await session.flush()

    assert await oxygen.reserve(session, constants, catalog, vessel) == 0
    _, dead = await oxygen.tick_ships(session, constants, catalog)
    assert dead == 0, "первый счёт только ставит отсчёт"
    assert body.choking_since is not None

    vessel.air_at = datetime.now(UTC) - timedelta(hours=1)
    await session.flush()
    _, dead = await oxygen.tick_ships(session, constants, catalog)
    assert dead == 1
    assert await _held(session, tank) == pytest.approx(10), (
        "баллоны полны: без системы их никто не пьёт"
    )


async def test_when_the_line_runs_dry_the_crew_dies_after_one_settling_of_grace(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A drop on the line, then nothing: the reserve is all there is, and after it, death.

    One settling of grace on purpose: a tick landing a second after the last
    unit was spent must not be indistinguishable from suffocation.
    """
    await _sphere(session, Planet.TERRA, airless=False)
    port = await _port(session)
    vessel, body, connector = await _hull(session, constants, port)
    system = await _system(session, connector)
    await _plumb(session, system, await _in_tank(session, connector, AIR, 0.05))

    vessel.docked_node_id = None
    #: A minute past the hour on purpose: the stretch's breath is off the
    #: thousandths grid, so a carry that was not dropped would show.
    moment = datetime.now(UTC)
    vessel.air_at = moment - timedelta(hours=2, minutes=1)
    await session.flush()

    _, dead = await oxygen.tick_ships(session, constants, catalog, now=moment)
    assert dead == 0, "первый счёт только опустошает линию"
    assert await oxygen.reserve(session, constants, catalog, vessel) == pytest.approx(0, abs=0.01)
    assert body.choking_since is not None, "отсчёт до удушья пошёл"
    assert vessel.air_owed == 0, "за удушье не должают: долг не копится поверх него"

    vessel.air_at = datetime.now(UTC) - timedelta(hours=1)
    await session.flush()
    _, dead = await oxygen.tick_ships(session, constants, catalog)
    assert dead == 1
    await session.refresh(body)
    assert body.state is BodyState.DEAD


async def test_an_empty_hull_spends_nothing(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Nobody aboard breathes nothing: a hull in flight arrives as it left."""
    await _sphere(session, Planet.TERRA, airless=False)
    port = await _port(session)
    vessel, body, connector = await _hull(session, constants, port)
    system = await _system(session, connector)
    await _plumb(session, system, await _in_tank(session, connector, AIR, 20))
    body.node_id = port.id
    vessel.docked_node_id = None
    vessel.air_at = datetime.now(UTC) - timedelta(hours=10)
    await session.flush()

    await oxygen.tick_ships(session, constants, catalog)
    assert await oxygen.reserve(session, constants, catalog, vessel) == pytest.approx(20, abs=0.01)


async def test_the_gauge_reads_the_line_and_the_sky(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The level is what stands on the line, the rate is the crew's draw --
    and only under a sealed hull: at a Terran pier the hatch is open and
    nothing is spent (D-233). Oxygen no line reaches is not on the gauge,
    because it is not what the crew dies by.
    """
    await _sphere(session, Planet.TERRA, airless=False)
    port = await _port(session)
    vessel, body, connector = await _hull(session, constants, port)
    system = await _system(session, connector)
    await _plumb(session, system, await _in_tank(session, connector, AIR, 10))
    await _in_canister(session, connector, AIR, 4, installed=False)

    open_hatch = await oxygen.gauge(session, constants, catalog, vessel, crew=1)
    assert open_hatch["sealed"] is False
    assert open_hatch["per_hour"] == 0
    assert open_hatch["units"] == pytest.approx(10), "канистра на полу — не запас"

    vessel.docked_node_id = None
    await session.flush()
    shut = await oxygen.gauge(session, constants, catalog, vessel, crew=2)
    assert shut["sealed"] is True
    assert shut["per_hour"] == pytest.approx(-2 * constants[R.OXYGEN_CREW_DRAW])


async def test_two_hulls_settling_together_do_not_drink_one_cylinder_twice(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """The tick and a second tick land on one hull at once.

    The air on the line is a quantity of a shared thing: read without a lock,
    both passes would breathe the same units and the crew would live through
    an hour it did not live through.
    """
    async with factory() as session, session.begin():
        await _sphere(session, Planet.TERRA, airless=False)
        port = await _port(session)
        vessel, _, connector = await _hull(session, constants, port)
        system = await _system(session, connector)
        #: Air for exactly one hour of one person, and no more: the second
        #: pass must find the line dry rather than the reading it started from.
        await _plumb(
            session, system, await _in_tank(session, connector, AIR, constants[R.OXYGEN_CREW_DRAW])
        )
        vessel.docked_node_id = None
        vessel.air_at = datetime.now(UTC) - timedelta(hours=1)
        await session.flush()
        ship_id = vessel.id

    ready = asyncio.Barrier(2)

    async def breathe() -> float:
        async with factory() as db, db.begin():
            #: Both transactions open and looking at the same hull before either
            #: writes -- that is the window an unlocked settling drinks the line
            #: twice in. Nothing is written before the barrier on purpose: two
            #: writes to one row before it would simply deadlock and prove
            #: nothing about the code under test.
            await db.get(Ship, ship_id)
            await ready.wait()
            breathed, _ = await oxygen.tick_ships(db, constants, catalog)
            return breathed

    breathed = sum(await asyncio.gather(breathe(), breathe()))

    async with factory() as session:
        vessel = await session.get(Ship, ship_id)
        left = await oxygen.reserve(session, constants, catalog, vessel)
    assert left == pytest.approx(0, abs=0.01), "линия выпита вся"
    assert breathed == pytest.approx(constants[R.OXYGEN_CREW_DRAW], abs=0.01), (
        f"воздуха отчитано {breathed:.3f} при запасе {constants[R.OXYGEN_CREW_DRAW]:.3f}"
    )


async def test_the_line_reaches_an_installed_canister_as_readily_as_a_tank(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """What stands on a line is a vessel **installed** aboard (D-288), whatever
    its shape: a canister put up in the room is breathed like a tank, one
    lying in it is luggage -- and the word for the difference is on the
    thing itself, not in the depth of the stowage.
    """
    await _sphere(session, Planet.TERRA, airless=False)
    port = await _port(session)
    vessel, body, connector = await _hull(session, constants, port)
    system = await _system(session, connector)
    #: Not one tank aboard: everything is in canisters, one put up, one lying.
    standing = await _in_canister(session, connector, AIR, 4)
    lying = await _in_canister(session, connector, AIR, 9, installed=False)
    #: Both on the line: the lying one is skipped by the line, not by the list.
    await _plumb(session, system, standing, lying)
    vessel.docked_node_id = None
    await session.flush()

    assert await oxygen.reserve(session, constants, catalog, vessel) == pytest.approx(4, abs=0.01)

    vessel.air_at = datetime.now(UTC) - timedelta(hours=2)
    await session.flush()
    await oxygen.tick_ships(session, constants, catalog)
    assert await _held(session, standing) == pytest.approx(
        4 - 2 * constants[R.OXYGEN_CREW_DRAW], abs=0.01
    ), "система пьёт установленную канистру"
    assert await _held(session, lying) == pytest.approx(9), "лежащая — груз, её не пьют"


async def test_a_canister_packed_into_a_chest_is_stowed_cargo(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A vessel is on the line only when it stands installed in a room.

    Packed into a chest it is luggage, and lying on the floor it is luggage
    too -- pinned here because it is exactly the kind of boundary a crew finds
    out about by dying: the spare oxygen was put away tidily.
    """
    await _sphere(session, Planet.TERRA, airless=False)
    port = await _port(session)
    vessel, _, connector = await _hull(session, constants, port)
    system = await _system(session, connector)
    yard = await world.node_container(session, connector)

    chest = await world.grant_item(session, yard, CHEST, quality=60, origin="тест")
    packed = await storage.inside(session, chest)
    can = await world.grant_item(session, packed, CANISTER, quality=60, origin="тест")
    #: On the line from the start: the line is obeyed only where the can stands.
    await _plumb(session, system, can)
    await world.grant_item(
        session, await storage.inside(session, can), AIR, amount=9, quality=60, origin="тест"
    )

    assert await oxygen.reserve(session, constants, catalog, vessel) == 0, (
        "убранное в сундук — груз, а не запас"
    )

    #: Out of the chest and onto the floor: still luggage.
    can.container_id = yard.id
    await session.flush()
    assert await oxygen.reserve(session, constants, catalog, vessel) == 0, (
        "лежащее на полу — тоже груз"
    )

    #: Put up in the room, it stands on the line.
    can.installed = True
    await session.flush()
    assert await oxygen.reserve(session, constants, catalog, vessel) == pytest.approx(9, abs=0.01)


async def test_the_gauge_tells_a_dry_hull_from_an_unplumbed_one(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The bridge is owed the difference (D-288).

    A hull that casts off with a full bottle nobody drew a line to suffocates
    exactly as one with no bottle at all -- and the two are not the same
    trouble: the first is a line to draw, the second is a bottle to find. So
    the reading names both what the line reaches and what stands aboard past
    it, and the console can say which of the two it is looking at.
    """
    await _sphere(session, Planet.TERRA, airless=False)
    port = await _port(session)
    vessel, _, connector = await _hull(session, constants, port)
    system = await _system(session, connector)
    #: One canister put up and plumbed, one put up and forgotten, one lying.
    lined = await _in_canister(session, connector, AIR, 4)
    spare = await _in_canister(session, connector, AIR, 9)
    await _in_canister(session, connector, AIR, 5, installed=False)
    await _plumb(session, system, lined)

    reading = await oxygen.gauge(session, constants, catalog, vessel, crew=1)
    assert reading["units"] == pytest.approx(4, abs=0.01), "дышат только линией"
    assert reading["off_line"] == pytest.approx(9, abs=0.01), (
        "стоящее мимо линии названо, лежащее — груз и в счёт не идёт"
    )

    #: Plumbed as well, it stops being a warning and becomes reserve.
    await _plumb(session, system, lined, spare)
    plumbed = await oxygen.gauge(session, constants, catalog, vessel, crew=1)
    assert plumbed["units"] == pytest.approx(13, abs=0.01)
    assert plumbed["off_line"] == pytest.approx(0, abs=0.01)

    #: And a hull with no system at all breathes nothing, yet still knows
    #: what stands in it: that is the "put a system in" case, not "draw a line".
    await session.delete(system)
    await session.flush()
    bare = await oxygen.gauge(session, constants, catalog, vessel, crew=1)
    assert bare["units"] == pytest.approx(0, abs=0.01)
    assert bare["off_line"] == pytest.approx(13, abs=0.01)


async def test_the_journal_and_the_bridge_count_the_same_oxygen_off_the_line(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """D-288 names **one** quantity, so there is one of it.

    The journal's `SHIP_AIRLESS` and the console's gauge used to answer it
    apart: the event counted every vessel in the rooms, standing or lying,
    while the gauge counted the standing alone. A crew read one number in the
    journal and another on the bridge about the same hull -- and only the
    narrow one is what the decision says ("сколько кислорода стоит мимо
    линии") and what one can act on: a line is drawn to a vessel that stands,
    and a canister on the floor is luggage until it is put up.
    """
    await _sphere(session, Planet.TERRA, airless=False)
    port = await _port(session)
    vessel, body, connector = await _hull(session, constants, port)
    #: A system aboard with nothing plumbed to it, one canister up and
    #: forgotten, one lying: this is the "draw a line" trouble, not the
    #: "put a system in" one.
    await _system(session, connector)
    await _in_canister(session, connector, AIR, 6)
    await _in_canister(session, connector, AIR, 4, installed=False)

    reading = await oxygen.gauge(session, constants, catalog, vessel, crew=1)
    assert reading["off_line"] == pytest.approx(6, abs=0.01), "лежащее не в счёт"

    vessel.docked_node_id = None
    vessel.air_at = datetime.now(UTC) - timedelta(hours=2)
    await session.flush()
    await oxygen.tick_ships(session, constants, catalog)

    told = (
        (await session.execute(select(Event).where(Event.kind == EventKind.SHIP_AIRLESS)))
        .scalars()
        .all()
    )
    assert len(told) == 1
    assert told[0].payload["off_line"] == pytest.approx(reading["off_line"], abs=0.01), (
        "журнал и рубка говорят о борте одно число"
    )
    assert body.choking_since is not None, "и дышать всё равно нечем"


# --- a minute at a time, the crew breathes its rate ----------------------------


def test_the_crew_debt_is_kept_at_the_scale_it_is_written_with() -> None:
    """`ROUND_REMAINDER` and the hull's debt column are one number in two places."""
    assert Ship.__table__.c.air_owed.type.scale == ROUND_REMAINDER


#: A crew of one over ten hours is the case that was measured; a crew of two
#: rounds the other way, and an hour of it is already a hundred times the
#: tolerance off -- each tick is a dozen statements, and the suite is shared.
@pytest.mark.parametrize(("crew", "minutes"), [(1, 600), (2, 60)], ids=["one", "two"])
async def test_a_minute_at_a_time_the_crew_breathes_its_rate(
    session: AsyncSession, constants: Constants, catalog: Catalog, crew: int, minutes: int
) -> None:
    """Hours of minute ticks cost those hours of `oxygen.crew_draw`, no more.

    The tick settles a hull every `time.tick`, and a crew's tick is not a
    whole number of thousandths. Each tick's draw used to be rounded to the
    nearest: at a minute and 0.1 an hour a head, a crew of one breathed a
    fifth more than the rate and a crew of two a tenth less. The sliver is
    carried on the hull now.
    """
    await _sphere(session, Planet.TERRA, airless=False)
    port = await _port(session)
    vessel, _, connector = await _hull(session, constants, port)
    for _ in range(crew - 1):
        await _person(session, connector)
    system = await _system(session, connector)
    tank = await _in_tank(session, connector, AIR, 10)
    await _plumb(session, system, tank)
    start = datetime.now(UTC)
    vessel.docked_node_id = None
    vessel.air_at = start
    await session.flush()

    breathed = 0.0
    for minute in range(1, minutes + 1):
        drawn, dead = await oxygen.tick_ships(
            session, constants, catalog, now=start + timedelta(minutes=minute)
        )
        breathed += drawn
        assert dead == 0

    rate = crew * constants[R.OXYGEN_CREW_DRAW] * minutes / 60
    drunk = 10 - await _held(session, tank)
    owed = float(vessel.air_owed)
    #: Drunk and owed are the rate, to the carry's own grid (a billionth a
    #: tick); owed is under a thousandth, so drunk is the rate within one. Not
    #: `drunk == rate ± 0.001` itself: a whole number of hours of a whole
    #: number of thousandths is a knife's edge, where a float a hair below
    #: leaves the last thousandth on the hull for the next tick to take.
    assert drunk + owed == pytest.approx(rate, abs=1e-6), (
        f"выпито {drunk:.3f} и в долгу {owed:.6f} при норме {rate:.3f}"
    )
    assert 0 <= owed < 0.001, "невыпитое ждёт на борту меньше тысячной"
    assert breathed == pytest.approx(drunk, abs=1e-9), "тик отчитывается выпитым"


async def test_a_stretch_too_short_for_a_thousandth_decides_nothing(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Seconds that cannot ask the line for a whole thousandth are carried --
    and say nothing about the line either: a choking crew's countdown stands.
    """
    await _sphere(session, Planet.TERRA, airless=False)
    port = await _port(session)
    vessel, body, connector = await _hull(session, constants, port)
    await _system(session, connector)
    start = datetime.now(UTC)
    body.choking_since = start
    vessel.docked_node_id = None
    vessel.air_at = start
    await session.flush()

    #: A tenth of a thousandth's worth of breath, on a line that has nothing.
    seconds = 0.1 / AMOUNT_SCALE / constants[R.OXYGEN_CREW_DRAW] * 3600
    drawn, dead = await oxygen.tick_ships(
        session, constants, catalog, now=start + timedelta(seconds=seconds)
    )
    assert (drawn, dead) == (0.0, 0)
    assert float(vessel.air_owed) == pytest.approx(0.1 / AMOUNT_SCALE, abs=1e-6), (
        "дыхание не забыто"
    )
    assert body.choking_since == start, "отсчёт не сброшен и не продвинут"


async def test_two_settlings_of_one_hull_carry_its_breath_once(
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two ticks a minute apart land on one hull at once.

    The carried sliver is written beside the stamp, and read without the
    hull's lock both passes would start from the same stamp and the same
    sliver: the line would pay the first minute twice and the sliver of one
    of them would be lost. Under the lock the second waits and settles only
    what the first left.
    """
    async with factory() as session, session.begin():
        await _sphere(session, Planet.TERRA, airless=False)
        port = await _port(session)
        vessel, _, connector = await _hull(session, constants, port)
        system = await _system(session, connector)
        tank = await _in_tank(session, connector, AIR, 10)
        await _plumb(session, system, tank)
        start = datetime.now(UTC)
        vessel.docked_node_id = None
        vessel.air_at = start
        await session.flush()
        ship_id, tank_id = vessel.id, tank.id

    ready = asyncio.Barrier(2)
    #: The pass that holds the hull holds it long enough for the other to
    #: arrive: without the lock both would be past their reading by then.
    _slow(monkeypatch, ship, "crew_of")

    async def breathe(minute: int) -> None:
        async with factory() as db, db.begin():
            await db.get(Ship, ship_id)
            await ready.wait()
            #: The earlier tick to the lock first, so the later one is the
            #: pass that must read the sliver the earlier one left. Well
            #: inside the hold `_slow` gives: unlocked, it still reads stale.
            await asyncio.sleep(0.05 * (minute - 1))
            await oxygen.tick_ships(db, constants, catalog, now=start + timedelta(minutes=minute))

    await asyncio.gather(breathe(1), breathe(2))

    async with factory() as session:
        hull = await session.get(Ship, ship_id)
        left = await _held(session, await session.get(Item, tank_id))
    assert hull is not None
    owed = constants[R.OXYGEN_CREW_DRAW] * 2 / 60
    assert (10 - left) + float(hull.air_owed) == pytest.approx(owed, abs=1e-6), (
        f"списано {10 - left:.3f} и в долгу {float(hull.air_owed):.6f} за {owed:.6f}"
    )
    assert hull.air_at == start + timedelta(minutes=2), "штамп не откатился назад"
