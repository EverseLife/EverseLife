# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""ship: the slider one hull is offered (D-271, D-289, D-341), and the point
of it an order names.

Above `sim`, beside the order: the console's slider (`card.forecast`) and
summary (`card.profile`) read here, and the crossing (`crossing.fly`) asks
here what it may fly -- the slider as the sky offers the hull at the order's
moment, and the one point of it the order names, or the refusal that says
what is true of the hours asked for. What is laid is the sky's (`sky.routes`,
`sky.preview`), where it runs is `flyby`'s, and the rows it is laid from are
`sim`'s; this module reads them together and writes nothing.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src import sky
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine.ship import course, flyby, sim
from src.engine.ship._base import NoArc, NotEnoughThrust
from src.engine.ship.physics import sky_days
from src.models.ship import Ship
from src.units import (
    HOURS_PER_DAY,
    ROUND_DV,
    ROUND_HOURS,
    SKY_CURVE_MEMO,
    SKY_MEMO_PER_DAY,
)


async def offers(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    ship: Ship,
    target: sky.Target,
    *,
    now: datetime,
    thrust_ratio: float,
) -> list[sky.Sample]:
    """The slider this hull is offered from where it is (D-271, D-289, D-341):
    to a planet, the direct arcs and the flybys cut to the choices -- what
    its engines deliver, only real choices, the largest group -- fastest
    first, so the cheap end is the last; to a drifter, the one quote of the
    approach profile (wave 3). Empty for a hull not in the sky, and for one
    whose engines deliver nothing the sky has.

    Laid in a process of its own from the hull's place and remembered there
    (`flyby.offered`, `_basis`): seconds a planet, and asked only where the
    slider itself is read or an order is given. The wait for the ejection
    window is the reader's own, counted from where the hull is at `now`. The
    console's summary of every route (`card.profile`) stays with the direct
    arcs (`arcs`)."""
    found = await sim.state_at(session, constants, ship, now=now)
    if found is None:
        return []
    r, v, t = found
    world = await sim.system(session, constants)
    if isinstance(target, sky.Drifter):
        #: A hull as the target (wave 3): one price, the approach profile's
        #: own -- the helm flies that profile and no arc, so a slider of
        #: arcs would quote hours and delta-v nobody flies.
        a_max = thrust_ratio * float(constants[R.ORBIT_THRUST_SCALE])
        return [sky.approach_quote(r, v, t, target, a_max)]
    laid = await flyby.offered(
        constants,
        world,
        target,
        sim.leaving_of(world, t, r, v),
        r,
        v,
        t,
        reach=course.reach(constants, thrust_ratio),
        basis=_basis(ship),
    )
    return _waited(world, target, laid, r, v, t)


async def arcs(
    session: AsyncSession, constants: Constants, ship: Ship, target: sky.Body, *, now: datetime
) -> list[sky.Sample]:
    """The direct arcs to a planet from where the hull is (D-271, D-289): every
    hour of the direct grid the sky has an arc for, priced, before any cut --
    the summary's routes and an order's reason for a refusal. Empty for a
    hull not in the sky.

    Memoised as the slider is (`_basis`), on the sky's ten-minute bucket:
    forty Lambert solutions a planet on every reread of `ship.view` were a
    tenth of a second each in the event loop. What is not remembered is
    solved off the loop; the wait is the reader's own."""
    found = await sim.state_at(session, constants, ship, now=now)
    if found is None:
        return []
    r, v, t = found
    world = await sim.system(session, constants)
    leaving = sim.leaving_of(world, t, r, v)
    key = (
        constants.digest,
        tuple(one.key for one in world.bodies),
        target.key,
        None if leaving is None else leaving.key,
        round(t * SKY_MEMO_PER_DAY),
        _basis(ship),
    )
    hit = _PREVIEWS.get(key)
    if hit is None:
        hit = await asyncio.to_thread(
            sky.preview, world, constants, r, v, t, target, course.grid(constants), leaving=leaving
        )
        _PREVIEWS[key] = hit
        while len(_PREVIEWS) > SKY_CURVE_MEMO:
            _PREVIEWS.popitem(last=False)
    return _waited(world, target, hit, r, v, t)


def _basis(ship: Ship) -> tuple:
    """What a remembered slider from this hull is keyed on besides the sky's
    bucket (D-341): the hull's stamp -- the moment and the state the tick last
    wrote, which place it in the sky at any moment of the bucket -- so a
    slider laid a minute ago from this very hull is found again, and an order
    given after the console read it flies what the console showed. The hull's
    own: no two hulls share a slider unless they share a state. The place it
    is read at moves every second a hull goes round a planet, and was no key
    for anything since D-354 put every hull at a planet into the sky."""
    return (
        None if ship.sky_at is None else ship.sky_at.isoformat(),
        ship.sky_x,
        ship.sky_y,
        ship.sky_vx,
        ship.sky_vy,
        ship.held_ship_id,
    )


def _waited(
    world: sky.System,
    target: sky.Body,
    samples: Sequence[sky.Sample],
    r: tuple[float, float],
    v: tuple[float, float],
    t: float,
) -> list[sky.Sample]:
    """Remembered samples with the wait for the ejection window counted anew
    from where the hull is at `t` (D-316): a slider laid minutes ago from this
    hull still names the passage right, and the hour before it is the
    reader's."""
    if not samples:
        return []
    waits = sky.eject_waits(world, target, t, r, v, [one.v1 for one in samples])
    return [
        replace(one, wait=float(wait) * HOURS_PER_DAY)
        for one, wait in zip(samples, waits, strict=True)
    ]


async def point(
    session: AsyncSession,
    constants: Constants,
    ship: Ship,
    target: Ship | None,
    goal: sky.Target,
    offered: Sequence[sky.Sample],
    *,
    hours: float,
    via: str | None,
    now: datetime,
    thrust_ratio: float,
) -> sky.Sample:
    """The point of the slider an order names (D-341), or its refusal: only a
    point of `offered` -- the slider as the sky offers this hull at the order's
    moment -- is flown, never a route the console would not offer then.

    To a hull (`target`, wave 3) there is one price and no choice among
    prices: the quote of the order's own moment, whatever hours the console
    read minutes ago -- the profile's hours move with the geometry, and it is
    laid within the thrust by construction.

    To a planet each refusal says what is true of the hours asked for: a
    flyby not among the points is `ship-no-flyby`, whether the sky turned
    under it since the console read it or it was never there; the direct arc
    of an hour the slider offers as a flyby is `ship-hours-are-a-flyby`; a
    direct arc the engines cannot deliver in its hours is
    `ship-too-fast-for-thrust`; an hour of the direct grid with no arc at all
    is `ship-no-arc`; any other hour off the slider -- off the grid, slower
    and no cheaper than a faster point, out of the group offered -- is
    `ship-hours-out-of-range`. The console rereads the slider on the answer.
    """
    if isinstance(goal, sky.Drifter):
        assert target is not None
        (quote,) = offered
        if sim.gone_by(goal, await sky_days(session, now), quote.hours):
            raise NoArc(key="ship-target-gone-by-then", other=target.name)
        return quote
    named = round(hours, ROUND_HOURS)
    at = [one for one in offered if one.hours in (named, hours)]
    for one in at:
        if (None if one.via is None else one.via.via) == via:
            return one
    if via is not None:
        raise NoArc(key="ship-no-flyby", hours=named, planet=via)
    for one in at:
        if one.via is not None:
            #: The hour is on the slider, as a flyby: the order asked for its
            #: direct arc, which the slider does not offer.
            raise NoArc(key="ship-hours-are-a-flyby", hours=named, planet=one.via.via)
    can = course.deliverable(constants, thrust_ratio, hours)
    direct = await arcs(session, constants, ship, goal, now=now)
    for arc in direct:
        if arc.hours in (named, hours) and arc.dv > can:
            raise NotEnoughThrust(
                key="ship-too-fast-for-thrust",
                hours=named,
                need=round(arc.dv, ROUND_DV),
                have=round(can, ROUND_DV),
            )
    if named in course.grid(constants) and all(arc.hours != named for arc in direct):
        #: An hour of the direct slider the sky has no arc for at all.
        raise NoArc(key="ship-no-arc", hours=named)
    raise NoArc(key="ship-hours-out-of-range", hours=named)


#: The direct previews remembered across commands (see `arcs`).
_PREVIEWS: OrderedDict[tuple, list[sky.Sample]] = OrderedDict()
