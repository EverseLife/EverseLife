# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The sky step against a hand over the vessels on the engines' fuel line.

One of the race files (see `test_races.py` for the family's method). Here the
contended rows are the fuel standing on a hull's engine line and the rows of
the crew that flies with it, and the order the two are taken in. Everybody who
acts takes their body first and the things after -- `_alive` is the prologue of
every command (D-211) -- so a sweep that will write a crew row takes the crew
before it touches anything the hull holds (`ship.belonging.lock_crew`,
`ship.fate._lose`). The sky step took them the other way round: the tanks
under `FOR UPDATE` for the whole stretch, and the crew only once the ground
had taken the hull (OQ-120, as closed by D-316).

* a crew member pours fuel out of a tank on the engines' line in the same
  second the ground takes the hull: the pour holds the body and reaches for
  the stack, the step holds the stack and reaches for the body;
* the line is emptied between the step's reading of it and its lock: the
  minute was flown on a budget the tanks turned out not to have, so the
  engines are out from there and the hull drifts;
* the line is **filled** in that same window, under a step whose reading said
  the tanks were spent: the drift is the tanks' word and the tanks are asked
  under the lock, so the order stands;
* and an emptied line under a hull the ground has already taken: it is lost
  this tick all the same, because a strike is not put off to the next minute.

The handshake is `automat_kit._until_blocked_by`: the pour keeps its
transaction open and commits only once the step provably waits on it. On the
code the first race catches, the database finds the knot instead and kills one
of the two -- the player's own command as readily as the tick.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import _until_blocked_by
from ship_kit import (
    CONSOLE,
    ENGINE,
    FUEL,
    LIFE,
    TANK,
    _equip,
    _fast_sample,
    _fuel,
    _in_orbit,
    _laid,
    _orbit,
    _port,
    _shipwright,
)
from src.api.commands.things import _liquid_pour
from src.constants import Catalog, Constants
from src.engine import liquid, ship, storage
from src.engine.ship import helm, physics, sim
from src.models.event import Event, EventKind
from src.models.identity import Body
from src.models.inventory import Container, Item
from src.models.ship import Ship
from src.models.world import Node, Planet

#: Units of fuel the hand pours off the line into the tank beside it. Small
#: against what the hull carries: the first race is about the order the rows
#: are taken in, and a stretch that ran short of fuel on top of it would be
#: two stories in one test.
POURED = 2.0
#: What stands on the engines' line, units. One tankful (`fuel_tank` holds two
#: tonnes, and a unit of rocket fuel weighs 2.7 kg), so that the hand can pour
#: the whole of it into the empty tank beside it -- which the other two races
#: do.
ABOARD = 700.0
#: What is left standing on the line for the race that fills it: little enough
#: that the helm asks for more thrust than it buys in the very first minute, so
#: the reading's verdict on that minute is "the tanks are spent".
LEFT = 0.02
#: And what the hand pours back into the line in that same minute.
BACK = 100.0


async def _under_order(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> tuple[Ship, Body, Item, Item, datetime]:
    """A crewed hull on the fast arc to Aurora, at the hour its burn begins.

    Gives back the hull, its whole crew, the one tank on the engines' line, the
    empty tank beside it to pour into, and the moment the arc starts at (the
    order waits for its ejection window first -- D-316).

    One tank on the line and not the two a fitted-out hull carries
    (`ship_kit._flightworthy`), so that "the line" and "that tank" are the same
    thing and one pour can empty it.
    """
    home = await _port(session, name="Космодром столицы")
    await _port(session, name="Космодром Мерида", planet=Planet.AURORA)
    aurora = await _orbit(session, Planet.AURORA)
    _, owner = await _shipwright(session, home)
    vessel = await _laid(session, constants, owner, home)
    connector = await session.get(Node, vessel.connector_node_id)
    assert connector is not None
    for part in (ENGINE, LIFE, CONSOLE):
        await _equip(session, connector, part)
    await _fuel(session, connector, ABOARD)
    #: Off the line and empty: what the hand pours over goes where the engines
    #: do not draw from, so none of it comes back to them.
    spare = await _equip(session, connector, TANK)
    owner.node_id = connector.id
    await session.flush()
    await _in_orbit(session, constants, catalog, owner, vessel)

    moment = datetime.now(UTC)
    fast = await _fast_sample(session, constants, catalog, vessel, Planet.AURORA)
    await ship.fly(
        session, constants, catalog, owner, vessel, aurora, hours=fast["hours"], now=moment
    )
    assert vessel.course is not None, "the order is on the row, the hull under the autopilot"
    tank = await _tank_of(session, constants, catalog, vessel)
    await session.flush()
    return vessel, owner, tank, spare, moment + timedelta(hours=float(fast["wait"]))


async def _past_the_edge(
    session: AsyncSession, constants: Constants, vessel: Ship, *, at: datetime
) -> None:
    """Put the hull just outside `orbit.system_radius` by hand, still running.

    The edge and not a planet's ground: `ground_of` answers both the same way
    (OQ-120), and a hull put here is taken by the first step the integrator
    takes, with no centre to fly through.
    """
    system = await sim.system(session, constants)
    sim._write_state(vessel, (system.edge * 1.01, 0.0), (system.edge, 0.0), at=at)
    await session.flush()


async def _drain_to(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    owner: Body,
    tank: Item,
    spare: Item,
    left: float,
) -> None:
    """Move all but `left` units off the line into the tank beside it.

    By the same door a player would use, in the setup and not in the race: the
    hull is to set out on a line the reading will call spent.
    """
    have = await _units(session, tank.id)
    _, poured = await liquid.pour(
        session, constants, catalog, owner, tank, spare, FUEL, have - left
    )
    assert poured == pytest.approx(have - left), poured
    await session.flush()


async def _tank_of(
    session: AsyncSession, constants: Constants, catalog: Catalog, vessel: Ship
) -> Item:
    """The one vessel standing on the engines' fuel line."""
    stacks = await physics.fuel_stacks(session, constants, catalog, vessel)
    holders = {stack.container_id for stack in stacks}
    assert len(holders) == 1, holders
    hold = await session.get(Container, holders.pop())
    assert hold is not None
    tank = await session.get(Item, hold.owner_id)
    assert tank is not None
    return tank


def _pouring(body: Body, source: Item, target: Item, *, units: float | None):
    """The player's own pour, as the socket runs it: `_alive` first, the
    vessels and the stacks in them after. `units` of `None` is the whole of
    what stands in the source."""
    identity_id, source_id, target_id = body.identity_id, source.id, target.id

    async def pour(db: AsyncSession) -> float:
        message: dict = {"from": str(source_id), "to": str(target_id), "goods": FUEL}
        if units is not None:
            message["amount"] = units
        answer = await _liquid_pour({"identity_id": identity_id}, db, message)
        return float(answer["poured"])

    return pour


async def _units(db: AsyncSession, vessel_id: uuid.UUID) -> float:
    """How much liquid stands in this vessel."""
    vessel = await db.get(Item, vessel_id)
    assert vessel is not None
    return sum(float(one.amount) for one in await storage.content(db, vessel)) / 1000


def _emptying(
    monkeypatch: pytest.MonkeyPatch,
    factory: async_sessionmaker[AsyncSession],
    pouring,
) -> list[float]:
    """Land the pour in the window the step leaves between its reading of the
    line and its lock on it. Returns the list the pour records itself in.

    The step reads the line for the budget and locks it for the write-off, both
    through `fuel_stacks`; the first of the two calls is the reading, and a
    pour committed from it is a hand that emptied the line in between.
    """
    poured: list[float] = []
    read = helm.fuel_stacks

    async def reading(*args, **kwargs) -> list[Item]:
        stacks = await read(*args, **kwargs)
        if not poured:
            async with factory() as db, db.begin():
                poured.append(await pouring(db))
        return stacks

    monkeypatch.setattr(helm, "fuel_stacks", reading)
    return poured


async def test_a_pour_off_the_line_while_the_ground_takes_the_hull_does_not_knot(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crew member pours the engines' fuel over as the hull leaves the system.

    The pour holds its own row (`_alive`) and has not reached the vessels yet;
    the step walks in between. Taken the way the step took them, it held the
    stack on the line for the whole stretch and reached for the body at the end
    of it, while the pour held the body and reached for the stack -- and the
    database untied that by killing one of the two, which the player is shown
    as a database error instead of an answer. Taken crew first, the step waits
    on a pour holding nothing it wants, and both land: the fuel poured over is
    gone from the line, and the edge takes the hull with the crew aboard.
    """
    vessel, owner, tank, spare, at = await _under_order(session, constants, catalog)
    await _past_the_edge(session, constants, vessel, at=at + timedelta(minutes=1))
    now = at + timedelta(minutes=3)
    pouring = _pouring(owner, tank, spare, units=POURED)
    vessel_id, owner_id, tank_id, spare_id = vessel.id, owner.id, tank.id, spare.id
    await session.commit()

    steps: list[asyncio.Future[dict]] = []
    waited: list[bool] = []
    reach = liquid.within_reach

    async def flying() -> dict:
        async with factory() as db, db.begin():
            return await helm.tick_sky(db, constants, catalog, now=now)

    async def reaching(db: AsyncSession, *args, **kwargs) -> None:
        await reach(db, *args, **kwargs)
        if not steps:
            steps.append(asyncio.ensure_future(flying()))
            waited.append(await _until_blocked_by(factory, db, unless=steps[0]))

    monkeypatch.setattr(liquid, "within_reach", reaching)

    async def acting() -> float:
        async with factory() as db, db.begin():
            return await pouring(db)

    (poured,) = await asyncio.gather(acting(), return_exceptions=True)
    (report,) = await asyncio.gather(*steps, return_exceptions=True)

    assert not isinstance(poured, BaseException), poured
    assert not isinstance(report, BaseException), report
    assert waited == [True], "the step did not wait for the pour"
    assert poured == pytest.approx(POURED)
    assert report["struck"] == 1, "the tick counted the hull the edge took"
    assert report["fuel"] > 0, "and the stretch was paid for"
    async with factory() as db:
        lost = await db.get(Ship, vessel_id)
        assert lost is not None and lost.lost_at is not None and lost.course is None
        body = await db.get(Body, owner_id)
        assert body is not None and body.died_at is not None, "the crew went with the hull"
        assert await _units(db, spare_id) == pytest.approx(POURED), "what was poured over is over"
        #: And the line paid for the stretch out of what the pour left it, not
        #: out of what the step read before it.
        assert await _units(db, tank_id) < ABOARD - POURED


async def test_the_line_emptied_between_the_reading_and_the_lock_strands_the_hull(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The line covered the minute as the step read it and did not as it locked it.

    The step prices the stretch off a reading -- the lock is the write-off's,
    not the arithmetic's -- so a hand emptying the line between the two leaves
    it holding a minute the tanks never paid for. The minute stands, because it
    happened; the engines are out from there, which is what pouring the fuel
    from under them means. The hull drifts, and that ends it: a drifter carries
    no order to fly the trick again on, so the minute cannot be bought twice.
    """
    vessel, owner, tank, spare, at = await _under_order(session, constants, catalog)
    now = at + timedelta(minutes=1)
    vessel_id, owner_id, tank_id = vessel.id, owner.id, tank.id
    was = (vessel.sky_x, vessel.sky_y)
    await session.commit()

    poured = _emptying(monkeypatch, factory, _pouring(owner, tank, spare, units=None))

    async with factory() as db, db.begin():
        report = await helm.tick_sky(db, constants, catalog, now=now)

    assert len(poured) == 1 and poured[0] > 0, "the hand poured the line over"
    assert report["adrift"] == 1 and report["struck"] == 0
    assert report["fuel"] == 0.0, "there was nothing left on the line to burn"
    async with factory() as db:
        afloat = await db.get(Ship, vessel_id)
        assert afloat is not None and afloat.lost_at is None
        assert afloat.course is None, "the order is off: the engines are out"
        assert afloat.sky_at == now, "the minute stands"
        assert (afloat.sky_x, afloat.sky_y) != was, "and the hull is where it flew to"
        body = await db.get(Body, owner_id)
        assert body is not None and body.died_at is None
        assert await _units(db, tank_id) == pytest.approx(0)
        told = await db.scalar(select(Event.id).where(Event.kind == EventKind.SHIP_ADRIFT))
        assert told is not None, "everybody aboard is told the engines fell silent"

    #: And there is nothing left to repeat it on: the next minute finds a
    #: drifter, not a hull under an order.
    async with factory() as db, db.begin():
        again = await helm.tick_sky(db, constants, catalog, now=now + timedelta(minutes=1))
    assert again["flown"] == 0


@pytest.mark.parametrize("left", [LEFT, 0.0])
async def test_the_line_filled_between_the_reading_and_the_lock_keeps_the_order(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
    left: float,
) -> None:
    """The other way round: a hand fills the line while the step flies on a
    reading that called the tanks spent.

    The step read a line with next to nothing on it, so the helm got less
    thrust than it asked for and the stretch ended "the tanks are dry". Then
    the hand poured a hundred units back in. Whether the engines are out is the
    tanks' word, and the tanks are asked under the lock: the order stands,
    nobody is told the hull is adrift, and the hull flew that stretch on less
    thrust than it could have -- which is a minute of the arc, not the end of
    it.

    Without the second half of that rule, the reading's stale verdict stood:
    the course was dropped, the crew was told the tanks had failed, and the
    loss was booked -- on a hull with a full tank on its line. And it could not
    happen before this change at all, because the tanks were locked from the
    top of the stretch and the pour waited for the tick.

    `left` of nothing is the same story with the reading finding the line bare,
    and it is the sharper half: a stretch off an empty line buys no thrust at
    all, so it burns nothing -- and a correction that only ran for stretches
    that burnt would miss exactly the case where the stale "dry" comes from.
    """
    vessel, owner, tank, spare, at = await _under_order(session, constants, catalog)
    await _drain_to(session, constants, catalog, owner, tank, spare, left)
    now = at + timedelta(minutes=1)
    vessel_id, owner_id, tank_id = vessel.id, owner.id, tank.id
    await session.commit()

    poured = _emptying(monkeypatch, factory, _pouring(owner, spare, tank, units=BACK))

    async with factory() as db, db.begin():
        report = await helm.tick_sky(db, constants, catalog, now=now)

    assert poured == [pytest.approx(BACK)], "the hand poured the line full again"
    assert report["flown"] == 1 and report["adrift"] == 0, "the tanks were not dry after all"
    async with factory() as db:
        afloat = await db.get(Ship, vessel_id)
        assert afloat is not None and afloat.lost_at is None
        assert afloat.course is not None, "the order stands"
        assert afloat.sky_at == now
        body = await db.get(Body, owner_id)
        assert body is not None and body.died_at is None
        #: The stretch cost what the stale budget bought and no more -- the
        #: burn is the stretch's, not the line's (the telemetry rounds to whole
        #: kilograms, so the tank is what says it).
        assert BACK <= await _units(db, tank_id) <= BACK + left
        told = await db.scalar(select(Event.id).where(Event.kind == EventKind.SHIP_ADRIFT))
        assert told is None, "nobody was told the tanks had failed"


async def test_the_ground_takes_the_hull_the_same_tick_though_the_line_paid_nothing(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hull the edge has taken is lost now, whatever the tanks turn out to hold.

    The same window as above, on a hull that is already off the edge of the
    system. The stretch was flown on fuel that was poured away meanwhile -- but
    the ground is not a matter of budget, and D-316 says the hull dies on the
    tick that reaches it rather than on a later one. Put off instead, a strike
    would be put off again by the next pour, and a hull could be kept from
    dying a minute at a time.
    """
    vessel, owner, tank, spare, at = await _under_order(session, constants, catalog)
    await _past_the_edge(session, constants, vessel, at=at + timedelta(minutes=1))
    now = at + timedelta(minutes=3)
    vessel_id, owner_id, tank_id = vessel.id, owner.id, tank.id
    await session.commit()

    poured = _emptying(monkeypatch, factory, _pouring(owner, tank, spare, units=None))

    async with factory() as db, db.begin():
        report = await helm.tick_sky(db, constants, catalog, now=now)

    assert len(poured) == 1 and poured[0] > 0, "the hand poured the line over"
    assert report["struck"] == 1, "the edge took the hull this tick, not the next one"
    assert report["fuel"] == 0.0, "and there was nothing left on the line to take for it"
    async with factory() as db:
        lost = await db.get(Ship, vessel_id)
        assert lost is not None and lost.lost_at is not None and lost.course is None
        body = await db.get(Body, owner_id)
        assert body is not None and body.died_at is not None
        assert await _units(db, tank_id) == pytest.approx(0)
