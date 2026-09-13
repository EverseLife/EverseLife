# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A flyby from the console to the mooring (D-341), through the rows.

The console quotes a passage bent round Pyroxis with the world named beside
the price; the order carries the pass it was quoted; the tick flies it past
Pyroxis onto Aurora's circle within the promise; and an order for a point the
slider does not offer -- a flyby the sky does not have at those hours, or the
direct arc of an hour the slider offers as a flyby -- is refused rather than
flown as something the console would not offer.
"""

from __future__ import annotations

from datetime import timedelta
from itertools import pairwise

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
from src.api.commands import transport
from src.api.registry import Refused
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import ship, world
from src.engine.ship import course, flyby, slider
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
    #: One point an hour, fastest first and each cheaper than the last (D-341).
    assert all(a["hours"] < b["hours"] and a["dv"] > b["dv"] for a, b in pairwise(samples))
    bent = [one for one in samples if one.get("via") == Planet.PYROXIS.value]
    assert bent, "в этот день ползунок предлагает пролёт мимо Пироксиса"
    assert all(one.get("via") in (None, Planet.PYROXIS.value) for one in samples)
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
    #: The slider's fast end, a direct arc: no pass round Pyroxis is offered
    #: at its hours.
    straight = forecast["samples"][0]
    assert "via" not in straight
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


async def test_an_order_a_minute_after_the_console_flies_what_it_showed(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The console reads the slider, the owner presses the button a minute
    later: the hull has moved on along its circle, and the order still flies
    the pass the console quoted, at its price -- the slider this hull laid in
    the sky's ten minutes is found again, and only the wait is counted anew
    from where the hull now is (D-341, D-316)."""
    vessel, far = await _moored_over_terra(session, constants, catalog)
    epoch = await world.epoch(session)
    assert epoch is not None
    moment = epoch + timedelta(days=SWING_DAY)
    forecast = await ship.forecast(session, constants, catalog, vessel, Planet.AURORA, now=moment)
    bent = next(one for one in forecast["samples"] if one.get("via") == Planet.PYROXIS.value)
    later = moment + timedelta(minutes=1)
    body = await _body_of(session, vessel)

    def no_pool() -> None:
        raise AssertionError("приказ пересчитал ползунок вместо памяти")

    #: Found in memory, not laid again: the sky's pool is not asked at all.
    monkeypatch.setattr(flyby, "_pool", no_pool)
    arrives = await ship.fly(
        session,
        constants,
        catalog,
        body,
        vessel,
        far,
        hours=bent["hours"],
        via=Planet.PYROXIS.value,
        now=later,
    )
    course = vessel.course
    assert course is not None and course["via"] == Planet.PYROXIS.value
    assert course["hours"] == bent["hours"]
    assert course["dv"] == pytest.approx(bent["dv"], abs=0.01)
    assert timedelta(hours=bent["hours"]) <= arrives - later < timedelta(hours=bent["hours"] + 24)


async def test_an_order_off_the_slider_is_refused(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The order flies a point of the slider as the sky offers it this hull
    at the order's moment, and nothing else (D-341): the hours of a flyby
    named without its world ask for the direct arc of that hour, which the
    slider does not offer -- refused with the world the hour bends round, and
    nothing is laid."""
    vessel, far = await _moored_over_terra(session, constants, catalog)
    epoch = await world.epoch(session)
    assert epoch is not None
    moment = epoch + timedelta(days=SWING_DAY)
    forecast = await ship.forecast(session, constants, catalog, vessel, Planet.AURORA, now=moment)
    bent = next(one for one in forecast["samples"] if one.get("via") == Planet.PYROXIS.value)
    body = await _body_of(session, vessel)
    with pytest.raises(ship.NoArc) as refused:
        await ship.fly(
            session, constants, catalog, body, vessel, far, hours=bent["hours"], now=moment
        )
    assert refused.value.key == "ship-hours-are-a-flyby"
    assert refused.value.params["planet"] == Planet.PYROXIS.value
    assert vessel.course is None and vessel.docked_node_id is not None


async def test_an_hour_of_the_direct_grid_with_no_arc_is_said_so(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An order for an hour of the direct slider the sky has no arc for at all
    is refused as that -- no arc -- and not as an hour off the slider (D-341)."""
    vessel, far = await _moored_over_terra(session, constants, catalog)
    epoch = await world.epoch(session)
    assert epoch is not None
    moment = epoch + timedelta(days=SWING_DAY)

    async def no_arcs(*args: object, **kwargs: object) -> list[object]:
        return []

    #: Every arc of the hour cutting the corona, put in by hand.
    monkeypatch.setattr(slider, "arcs", no_arcs)
    first = course.grid(constants)[0]
    body = await _body_of(session, vessel)
    with pytest.raises(ship.NoArc) as refused:
        await ship.fly(session, constants, catalog, body, vessel, far, hours=first, now=moment)
    assert refused.value.key == "ship-no-arc"


def test_the_order_names_a_planet_or_is_refused_on_the_wire() -> None:
    """`ship.fly {via}` takes a planet's key and nothing else (D-341)."""
    assert transport._via({}) is None
    assert transport._via({"via": "pyroxis"}) == Planet.PYROXIS.value
    with pytest.raises(Refused) as refused:
        transport._via({"via": "nowhere"})
    assert "cmd-no-such-planet" in str(refused.value)
