# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A flyby from the console to the mooring (D-341), through the rows.

The console quotes a passage bent round Pyroxis with the world named beside
the price; the order carries the pass it was quoted; the tick flies it past
Pyroxis onto Aurora's circle within the promise; and an order for a flyby the
sky does not have at those hours is refused rather than flown as an arc.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ship_kit import (
    LATE_HOURS,
    _body_of,
    _flightworthy,
    _flown,
    _fuel,
    _in_orbit,
    _laid,
    _orbit,
    _port,
    _shipwright,
)
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import ship, world
from src.models.ship import Ship
from src.models.world import Node, Planet

#: The sky day a Terra-to-Aurora passage bends round Pyroxis at the fast end
#: of the slider, a little over two days, in a world of these three planets
#: (found by the survey of 2026-09-13; `test_flyby` pins the arithmetic).
SWING_DAY = 16.0


async def _moored_over_terra(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> tuple[Ship, Node]:
    """A hull on Terra's circle with fuel to spare, Aurora's orbit to go to,
    and Pyroxis in the sky to bend round."""
    here = await _port(session)
    await _port(session, name="Порт Авроры", planet=Planet.AURORA)
    far = await _orbit(session, Planet.AURORA)
    await _orbit(session, Planet.PYROXIS)
    _, owner = await _shipwright(session, here)
    vessel = await _laid(session, constants, owner, here)
    await _flightworthy(session, constants, catalog, vessel)
    connector = await session.get(Node, vessel.connector_node_id)
    assert connector is not None
    await _fuel(session, connector, 5000)
    owner.node_id = connector.id
    await session.flush()
    await _in_orbit(session, constants, catalog, owner, vessel)
    return vessel, far


async def test_the_console_quotes_a_flyby_and_the_helm_flies_it(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The slider names the world a passage bends round; the order carries
    the pass; the tick flies it onto Aurora's circle past Pyroxis, never under
    the floor, and no later than the promise."""
    vessel, far = await _moored_over_terra(session, constants, catalog)
    epoch = await world.epoch(session)
    assert epoch is not None
    moment = epoch + timedelta(days=SWING_DAY)
    forecast = await ship.forecast(session, constants, catalog, vessel, Planet.AURORA, now=moment)
    samples = forecast["samples"]
    #: One point an hour, and the cheaper passage of that hour.
    assert len({one["hours"] for one in samples}) == len(samples)
    bent = [one for one in samples if one["via"] == Planet.PYROXIS.value and one["ok"]]
    assert bent, "в этот день ползунок предлагает пролёт мимо Пироксиса"
    assert all(one["via"] in (None, Planet.PYROXIS.value) for one in samples)
    pick = min(bent, key=lambda one: one["hours"])
    assert len(pick["trace"]) >= 2

    body = await _body_of(session, vessel)
    arrives = await ship.fly(
        session,
        constants,
        catalog,
        body,
        vessel,
        far,
        hours=pick["hours"],
        via=Planet.PYROXIS.value,
        now=moment,
    )
    course = vessel.course
    assert course is not None
    assert course["via"] == Planet.PYROXIS.value and course["from"] == Planet.TERRA.value
    assert course["leg"]["stage"] == "depart" and course["pass_at"]
    sky_ = await ship.sim.system(session, constants)
    floor = float(constants[R.ORBIT_FLYBY_FLOOR_RADII]) * sky_.body(Planet.PYROXIS.value).radius
    assert abs(course["rp"]) >= floor, "перицентр не ниже пола"
    assert course["dv"] == pytest.approx(pick["dv"], rel=0.01)
    #: The promise is a crossing's: the slider's hours, the wait, the braking.
    promised = timedelta(hours=pick["hours"] + pick["wait"])
    assert promised <= arrives - moment < promised + timedelta(hours=24)
    summary = await ship.profile(session, constants, catalog, vessel)
    assert summary["flight"]["via"] == Planet.PYROXIS.value

    at = await _flown(session, constants, catalog, vessel, since=moment, until=arrives)
    assert vessel.lost_at is None, "корпус не разбился о Пироксис"
    assert vessel.docked_node_id == far.id, "пролёт кончился на круге Авроры"
    assert at - arrives <= timedelta(hours=LATE_HOURS)


async def test_a_flyby_the_sky_does_not_have_is_refused(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Asked for a pass round Pyroxis at an hour the slider has none, the
    order is refused with its own reason, not flown as the direct arc."""
    vessel, far = await _moored_over_terra(session, constants, catalog)
    epoch = await world.epoch(session)
    assert epoch is not None
    moment = epoch + timedelta(days=SWING_DAY)
    forecast = await ship.forecast(session, constants, catalog, vessel, Planet.AURORA, now=moment)
    straight = next(one for one in forecast["samples"] if one["via"] is None and one["ok"])
    body = await _body_of(session, vessel)
    with pytest.raises(ship.NoArc) as refused:
        await ship.fly(
            session,
            constants,
            catalog,
            body,
            vessel,
            far,
            hours=straight["hours"],
            via=Planet.PYROXIS.value,
            now=moment,
        )
    assert "ship-no-flyby" in str(refused.value)
    assert vessel.course is None and vessel.docked_node_id is not None
