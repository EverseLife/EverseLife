# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The hydroponics breathes (D-288, D-340).

Checked:

* a growing bed in a bay with a hydroponic unit breathes
  `oxygen.hydroponics_rate` an hour per square metre into the vessels on the
  unit's oxygen line of a sealed hull;
* a ripe bed, a dead one, an unsown one and a bed with no unit in its
  compartment breathe nothing; neither does a hull whose hatch opens on air;
* what a bay gave this stretch is air the crew may breathe in it, even into
  a cylinder that was empty when the stretch began;
* two units in one bay do not breathe the bay twice;
* a minute at a time adds up to the hour: the thousandths are carried, not
  rounded;
* the beds and a hand pouring into the same cylinder do not overfill it.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import _slow
from lines_kit import CYLINDER, _empty, _held, _hull, _room
from ship_kit import LIFE, _equip
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import liquid, oxygen, ship, storage, world
from src.models.farm import Plot, PlotState
from src.models.identity import Body
from src.models.inventory import Item
from src.models.ship import Ship
from src.models.world import Node

AIR = "oxygen"
UNIT = "hydroponic_unit"
CULTURE = "spelt"


async def _bay(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    *,
    area: float = 10,
    growth: float = 40,
    health: float = 80,
    state: PlotState = PlotState.SOWN,
    units: int = 1,
    unit_in_bay: bool = True,
    undock: bool = True,
):
    """A hull with a bay: a sown bed, the unit beside it on a line to an empty
    cylinder, nobody aboard (so the crew breathes nothing of it), an hour to
    settle."""
    vessel, body, connector = await _hull(session, constants, foundations=2)
    bay = await _room(session, constants, body, vessel)
    bottles = []
    for _ in range(units):
        unit = await _equip(session, bay if unit_in_bay else connector, UNIT)
        bottle = await _empty(session, bay)
        await ship.set_lines(session, constants, catalog, body, vessel, unit, AIR, [bottle])
        bottles.append(bottle)
    session.add(
        Plot(
            node_id=bay.id,
            owner_identity_id=body.identity_id,
            name="Грядка",
            area_m2=Decimal(str(area)),
            state=state,
            fertility=Decimal(50),
            culture_id=CULTURE if state is PlotState.SOWN else None,
            growth=Decimal(str(growth)),
            health=Decimal(str(health)),
            settled_at=datetime.now(UTC),
        )
    )
    body.node_id = vessel.docked_node_id
    if undock:
        vessel.docked_node_id = None
    moment = datetime.now(UTC)
    vessel.air_at = moment - timedelta(hours=1)
    await session.flush()
    return vessel, body, bottles, moment


async def test_a_growing_bed_breathes_into_its_units_line(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    vessel, _, (bottle,), moment = await _bay(session, constants, catalog, area=10)
    await oxygen.tick_ships(session, constants, catalog, now=moment)
    rate = constants[R.OXYGEN_HYDROPONICS_RATE]
    assert await _held(session, bottle) == pytest.approx(rate * 10, abs=0.001)


@pytest.mark.parametrize(
    "bed",
    [
        {"growth": 100},
        {"health": 0},
        {"state": PlotState.IDLE, "growth": 0},
        {"unit_in_bay": False},
        {"undock": False},
    ],
    ids=["ripe", "dead", "unsown", "no-unit-in-the-bay", "hatch-open-on-air"],
)
async def test_what_does_not_grow_breathes_nothing(
    session: AsyncSession, constants: Constants, catalog: Catalog, bed: dict
) -> None:
    """A ripe bed has done its growing, a dead one is gone, an unsown one is
    soil; a bed with no unit beside it has nothing to collect with; and under
    a sky that has air the bays breathe into the planet's."""
    vessel, _, (bottle,), moment = await _bay(session, constants, catalog, **bed)
    await oxygen.tick_ships(session, constants, catalog, now=moment)
    assert await _held(session, bottle) == 0
    assert vessel.air_at == moment, "штамп ушёл всё равно"


async def test_the_crew_breathes_what_the_bays_gave_this_stretch(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The beds breathe first, and the crew breathes what they gave (D-340).

    The cylinder the life support drinks from is the one the bay fills, and it
    was **empty** when the stretch began: what the bay poured into it is a
    stack that did not exist when the hull's air was first read. A stretch
    that went by that first reading would have the crew choking beside a
    cylinder filled a moment earlier -- a stretch late, every stretch.
    """
    vessel, body, (bottle,), moment = await _bay(session, constants, catalog, area=10)
    connector = await session.get(Node, vessel.connector_node_id)
    assert connector is not None
    #: The owner stays aboard this time, and the life support hangs on the
    #: very cylinder the bay pours into.
    body.node_id = connector.id
    system = await _equip(session, connector, LIFE)
    await ship.set_lines(session, constants, catalog, body, vessel, system, AIR, [bottle])
    await session.flush()

    drawn, dead = await oxygen.tick_ships(session, constants, catalog, now=moment)
    grown = constants[R.OXYGEN_HYDROPONICS_RATE] * 10
    draw = constants[R.OXYGEN_CREW_DRAW]
    assert dead == 0 and body.choking_since is None, "задохнулся у только что наполненного баллона"
    assert drawn == pytest.approx(draw, abs=0.001)
    assert await _held(session, bottle) == pytest.approx(grown - draw, abs=0.001)


async def test_two_units_in_one_bay_do_not_breathe_it_twice(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """How much a unit serves is not decided (OQ-171): the bay breathes once,
    into the union of its units' lines."""
    _, _, bottles, moment = await _bay(session, constants, catalog, area=10, units=2)
    await oxygen.tick_ships(session, constants, catalog, now=moment)
    held = [await _held(session, one) for one in bottles]
    assert sum(held) == pytest.approx(constants[R.OXYGEN_HYDROPONICS_RATE] * 10, abs=0.001)


async def test_minutes_add_up_to_the_hour(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The smallest bed breathes less than a thousandth a minute; carried, not
    rounded, sixty of them are the hour's breath and not a fifth more."""
    vessel, _, (bottle,), moment = await _bay(session, constants, catalog, area=5)
    start = moment - timedelta(hours=1)
    for minute in range(1, 61):
        await oxygen.tick_ships(session, constants, catalog, now=start + timedelta(minutes=minute))
    hour = constants[R.OXYGEN_HYDROPONICS_RATE] * 5
    assert await _held(session, bottle) == pytest.approx(hour, abs=0.0011)
    assert 0 <= float(vessel.air_grown) < 0.001


async def test_the_beds_and_a_hand_do_not_overfill_one_cylinder(
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bays pour under the vessel's lock, as a hand does: two pours into
    the last room of one cylinder queue, and it holds what it holds."""
    async with factory() as session, session.begin():
        vessel, body, (bottle,), moment = await _bay(
            session, constants, catalog, area=10_000, undock=True
        )
        inside = await storage.inside(session, bottle)
        await world.grant_item(session, inside, AIR, amount=5.5, quality=60, origin="тест")
        #: The hand stands aboard with a cylinder of its own, in the bay.
        body.node_id = (await ship.nodes_of(session, vessel))[-1].id
        pocket = await world.body_container(session, body)
        can = await world.grant_item(session, pocket, CYLINDER, quality=60, origin="тест")
        await world.grant_item(
            session, await storage.inside(session, can), AIR, amount=1, quality=60, origin="тест"
        )
        #: The crew is the hand alone, and breathes off no line: only the beds
        #: and the hand fill the cylinder.
        vessel.air_at = moment - timedelta(minutes=10)
        ids = (vessel.id, body.id, can.id, bottle.id)

    _slow(monkeypatch, liquid, "free_in")

    async def beds() -> None:
        async with factory() as db, db.begin():
            await oxygen.tick_ships(db, constants, catalog, now=moment)

    async def hand() -> None:
        async with factory() as db, db.begin():
            me = await db.get(Body, ids[1])
            source = await db.get(Item, ids[2])
            target = await db.get(Item, ids[3])
            with contextlib.suppress(liquid.LiquidError):
                await liquid.pour(db, constants, catalog, me, source, target, AIR)

    await asyncio.gather(beds(), hand())
    async with factory() as session:
        held = await _held(session, await session.get(Item, ids[3]))
        hull = await session.get(Ship, ids[0])
    assert held <= 6 + 0.001, "баллон не переполнен"
    assert hull is not None and hull.air_at == moment
