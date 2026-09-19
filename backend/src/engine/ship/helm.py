# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The helm at the tick (D-289): every hull in the sky moved up to now.

The floor above `sim`, which owns the state: here the ordered hulls are
flown step by step -- the helm decides, the engines burn what the tanks can
pay, the sky pulls -- and come to their ends: moored on a planet's circle,
come to rest beside another hull (`hold`, wave 3), or adrift with the tanks
dry (`fate`). A coasting hull has its stamp moved along and its coast ahead
counted. Who came into sight while a hull moved is told here too.
"""

from __future__ import annotations

import asyncio
import math
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src import sky
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import events, stock
from src.engine.ship import fate, flyby, hold
from src.engine.ship.physics import (
    _FUEL_EPS,
    _sphere,
    engine_class,
    fuel_energy,
    fuel_stacks,
    mass,
    ratio,
    sky_days,
    spend_fuel,
)
from src.engine.ship.sim import (
    _DV_EPS,
    _forecast_stale,
    _keep_forecast,
    _row,
    _state_of,
    _write_state,
    dv_aboard,
    fuel_for_dv,
    into_orbit,
    meetable,
    states_at,
    system,
)
from src.models.event import EventKind
from src.models.ship import Ship
from src.models.world import Planet
from src.units import HOURS_PER_DAY, MINUTES_PER_HOUR, ROUND_DV, ROUND_MASS, amount_float


async def tick_sky(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    *,
    now: datetime | None = None,
) -> dict[str, float | int]:
    """Move every hull in space up to `now`: fly the ordered, restamp the
    coasting. Returns what happened, for the tick's telemetry."""
    moment = now or datetime.now(UTC)
    #: Which hulls have work this tick, read without a lock; then each one
    #: is taken for update by itself, and one somebody is ordering right now
    #: is skipped until the next minute. A single lock over every hull in
    #: the sky held all of `ship.fly` and `ship.recall` behind the slowest
    #: forecast (review of this wave).
    stale = timedelta(hours=float(constants[R.ORBIT_RESTAMP_HOURS]))
    released = await hold.sweep(session, constants, now=moment)
    wanted = (
        (
            await session.execute(
                select(Ship.id)
                .where(
                    Ship.docked_node_id.is_(None),
                    Ship.sky_at.isnot(None),
                    Ship.lost_at.is_(None),
                    Ship.held_ship_id.is_(None),
                    (Ship.course.isnot(None)) | (Ship.sky_at <= moment - stale),
                )
                .order_by(Ship.id)
            )
        )
        .scalars()
        .all()
    )
    if not wanted:
        return {
            "flown": 0,
            "moored": 0,
            "adrift": released,
            "held": 0,
            "circled": 0,
            "struck": 0,
            "fuel": 0.0,
        }
    world = await system(session, constants)
    flown = moored = adrift = held = circled = struck = 0
    fuel = 0.0
    moved: list[Ship] = []
    for ship_id in wanted:
        ship = await session.get(
            Ship, ship_id, with_for_update={"skip_locked": True}, populate_existing=True
        )
        if ship is None or ship.docked_node_id is not None or ship.lost_at is not None:
            continue
        done = ""
        if ship.course:
            done, burnt = await _fly(session, constants, catalog, world, ship, now=moment)
            flown += 1
            fuel += burnt
            moored += done == "moored"
            adrift += done == "adrift"
            struck += done == "struck"
            held += done == "held"
            circled += done == "circled"
        elif moment - ship.sky_at >= stale:
            if await _restamp(session, constants, world, ship, now=moment):
                struck += 1
                continue
        else:
            continue
        if done != "struck":
            #: A struck hull is off the water: its row is already lost and
            #: flushed, so it is not among `afloat` and has nothing to sight.
            moved.append(ship)
    #: And who came into sight while they moved (wave 3): every hull in the
    #: sky placed once, the pairs read off that one table.
    if moved:
        afloat = (
            (
                await session.execute(
                    select(Ship).where(Ship.sky_at.isnot(None), Ship.lost_at.is_(None))
                )
            )
            .scalars()
            .all()
        )
        table = await states_at(session, constants, afloat, now=moment)
        for ship in moved:
            await _sight(session, world, ship, afloat, table)
    await session.flush()
    return {
        "flown": flown,
        "moored": moored,
        "adrift": adrift + released,
        "held": held,
        "circled": circled,
        #: Hulls the ground took under an order (OQ-120) or on a coast
        #: between two restamps (D-354).
        "struck": struck,
        "fuel": round(fuel, ROUND_MASS),
    }


async def _fly(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    world: sky.System,
    ship: Ship,
    *,
    now: datetime,
) -> tuple[str, float]:
    """One hull's stretch under its order: step by step, the helm decides, the
    engines burn what the tanks can pay, the sky pulls. Ends moored, adrift,
    or still under way."""
    t0 = await sky_days(session, ship.sky_at)
    t1 = await sky_days(session, now)
    if t1 <= t0:
        return "flying", 0.0
    order = dict(ship.course or {})
    arrive = await sky_days(session, datetime.fromisoformat(str(order["arrive_at"])))
    #: The target: a planet on its circle, or a hull on its forecast (wave 3).
    #: A hull that is no longer there to be met -- lost, moored since, under
    #: an order of its own, or on somebody's hold -- voids the order: the
    #: chaser coasts from where it is, and its owner is told as of a drift.
    other: Ship | None = None
    target: sky.Target
    if order.get("ship"):
        other_id = uuid.UUID(str(order["ship"]))
        if await session.get(Ship, other_id) is None:
            return await _void(session, constants, world, ship, now=now, t0=t0, t1=t1)
        #: The target's row, locked for the stretch: an order given to it in
        #: the same second must not slip in between the reading and the
        #: hold. Locked by somebody else right now -- the next minute will do.
        other = await session.get(
            Ship, other_id, with_for_update={"skip_locked": True}, populate_existing=True
        )
        if other is None:
            return "flying", 0.0
        if not meetable(other) or other.id == ship.id:
            return await _void(session, constants, world, ship, now=now, t0=t0, t1=t1)
        found_goal = await _dense_drifter(session, constants, world, other, t0=t0, t1=t1)
        if found_goal is None:
            return "flying", 0.0
        target = found_goal
    elif order.get("target") == sky.STAR.key:
        #: The circle round the star (2026-09-04): nothing to chase, only a
        #: velocity to match where the hull is.
        target = sky.STAR
    else:
        target = world.body(str(order["planet"]))
    #: A flyby (D-341): the order carries the pass, and the helm's place in it.
    route = await _route_of(session, constants, world, order, target, arrive)
    leg = flyby.leg_of(order.get("leg"))

    weight = await mass(session, constants, catalog, ship)
    klass = await engine_class(session, constants, ship)
    a_max = (await ratio(session, constants, catalog, ship)) * float(
        constants[R.ORBIT_THRUST_SCALE]
    )
    #: The tanks, **read**: what they hold is the budget of speed the helm may
    #: ask for over the stretch. Read and not locked -- the lock belongs to the
    #: write-off and is taken with it, at the end (`_paid`). Taken here, it
    #: held the heaviest arithmetic in the project under a row lock, and, worse,
    #: had the stretch reach for a crew row with a thing of the hull's already
    #: in hand.
    seen = await fuel_stacks(session, constants, catalog, ship)
    worth = sum(amount_float(one.amount) * fuel_energy(constants, one.type_key) for one in seen)
    budget = dv_aboard(constants, worth, weight, klass)

    r, v = _state_of(ship)
    hit: str | None = None
    left = False
    #: And whatever the helm means, the ground is where it is (OQ-120): a
    #: hull under an order used to pass through a planet, the corona or the
    #: edge of the system without noticing, because D-289 wrote the deaths
    #: of a drift alone. Asked of every step the integrator takes rather
    #: than of the step's two ends -- a planet is small and a tick catching
    #: up after an idle worker flies through one between samples.
    ground: dict[str, object] = {}

    def watch(tt: np.ndarray, rr: sky.Rows, vv: sky.Rows) -> None:
        if ground:
            return
        body, gone = sky.ground_of(world, tt, rr)
        if body is not None or gone:
            ground.update(
                at=float(tt[0]),
                body=body,
                gone=gone,
                r=(float(rr[0, 0]), float(rr[0, 1])),
                v=(float(vv[0, 0]), float(vv[0, 1])),
            )

    step = float(constants[R.ORBIT_STEP_MINUTES]) / MINUTES_PER_HOUR / HOURS_PER_DAY
    t = t0
    spent = 0.0
    phase = str(order.get("phase", sky.BURN))
    outcome = "flying"
    while t < t1 - sky.TIME_EPS:
        dt = min(step, t1 - t)
        if route is None:
            helm = sky.steer(world, target, t, r, v, arrive=arrive, a_max=a_max, dt=dt)
        else:
            assert isinstance(target, sky.Body)
            helm, leg, want = sky.steer_pass(world, target, route, leg, t, r, v, a_max=a_max, dt=dt)
            if want is not None:
                #: A correction: a shooting through the whole sky, seconds at
                #: worst -- off the loop, so the tick does not stall the
                #: worker's other jobs behind one hull's pass.
                leg = await asyncio.to_thread(sky.correct, world, target, route, leg, want, t, r, v)
                helm, leg, _ = sky.steer_pass(
                    world, target, route, leg, t, r, v, a_max=a_max, dt=dt
                )
        if helm.captured:
            outcome = "moored"
            break
        thrust = np.array(helm.thrust)
        wanted = float(np.hypot(*thrust)) * dt
        if wanted > _DV_EPS and wanted > budget - spent:
            #: The tanks run out mid-step: burn what is left, then the coast.
            share = max(0.0, budget - spent) / wanted
            thrust = thrust * share
            wanted *= share
            outcome = "adrift"
        rr, vv = sky.advance(
            world,
            np.array([t]),
            np.array([t + dt]),
            np.array([r]),
            np.array([v]),
            dt_max=dt,
            thrust=thrust[None, :],
            watch=watch,
        )
        spent += wanted
        if ground:
            r, v = ground["r"], ground["v"]  # type: ignore[assignment]
            t, hit, left = float(ground["at"]), ground["body"], bool(ground["gone"])  # type: ignore[arg-type,assignment]
            outcome = "struck"
            break
        r, v = _row(rr), _row(vv)
        phase = helm.phase
        t += dt
        if outcome == "adrift":
            break
    if outcome == "adrift" and t < t1 - sky.TIME_EPS:
        #: The rest of the stretch with the tanks dry is still flown through
        #: the same sky, and at the coaster's pace rather than the helm's:
        #: hours in one call, so the ground is watched step by step here too.
        r, v, t, hit, left = sky.coast_to(
            world,
            t,
            t1,
            r,
            v,
            dt_max=float(constants[R.ORBIT_PLAN_STEP_MINUTES]) / MINUTES_PER_HOUR / HOURS_PER_DAY,
        )
        if hit is not None or left:
            outcome = "struck"
    if outcome == "moored" and isinstance(target, sky.Body):
        #: The last burn of an arrival (D-354): the helm caught the hull near
        #: the circle, and rounding its orbit off at the height it is at costs
        #: the speed the two differ by -- paid like any other burn. The tanks
        #: short of it, the hull keeps the ellipse it was caught on: the
        #: capture window made that a closed one, and nothing is set down on a
        #: circle for free any more.
        rounded, trim = sky.rounded(target, t, r, v)
        if trim <= max(budget - spent, 0.0):
            v = rounded
            spent += trim

    #: **The crew, and then the hull's things** -- the order of the two the
    #: world keeps (`belonging.lock_crew`). A crew member is a pair of hands
    #: that can reach anything aboard, and every command they act through holds
    #: their body first and the thing after (`_alive`, D-211): a pour off a
    #: tank, a sack off the floor. So a sweep that will write a crew row takes
    #: the crew before it touches anything the hull holds, the way the loss of
    #: a hull does (`fate._lose`).
    #:
    #: Here only the ground writes one (OQ-120, as closed by D-316), and
    #: whether the ground came is what the arithmetic above has just worked out
    #: -- so the strike goes first and the tanks after it, and a stretch that
    #: ends any other way queues nobody's hands behind the tick. The tanks used
    #: to be held from the top of the stretch, which put them the wrong side of
    #: `fate._lose`'s crew and knotted the tick against a crew member's own
    #: pour (`tests/test_races_ship_fuel.py`).
    #:
    #: **Only the ground, and only this hull's crew.** A loss reaches into the
    #: crew of whoever flies as one with the hull as well (`fate._lose`), and
    #: the whole tick is one transaction, so a companion's rows are taken with
    #: an earlier hull's tanks still held. Never that companion's tanks,
    #: though: a hull still under an order is not lost with another
    #: (`fate._lose` passes over one with a course), and a hull that is held or
    #: docked is not flown at all -- the sweep above skips a held row, and a
    #: hull may only be come to rest beside if it carries no order of its own
    #: (`sim.meetable`), so it is restamped rather than flown, and a restamp
    #: locks no tanks. And a crew's hands reach their own hull only: what is
    #: within reach is the pocket and the yard of the node one stands in
    #: (`liquid.within_reach`, D-315), so nobody but this crew can be holding
    #: a body and waiting for these tanks. D-289 promises a line across a
    #: gangway for refuelling a rescued hull, and that promise is what would
    #: take the condition away: it is written down as OQ-176, and this is the
    #: paragraph it points at.
    #:
    #: **These two rows, and no others.** The hull's own row is taken earlier
    #: still, at the top of the tick (`tick_sky`), and how that one stands
    #: against a command's `_alive` -- which takes the body first and the hull
    #: after -- is a rule of its own and a fix of its own. Nothing below is
    #: about it, and reading this paragraph as settling it would be reading it
    #: too widely.
    stamp = now if outcome not in ("moored", "struck") else _moment_of(now, t1, t)
    _write_state(ship, r, v, at=stamp)

    if outcome == "struck":
        await fate.strike(session, constants, ship, now=stamp, t=t, r=r, body=hit, gone=left)
        #: The tanks after the crew, and after the hull is lost: a wreck still
        #: paid for its way down, and the ground is no matter of budget -- what
        #: the line turns out to hold changes nothing here. D-316 has the hull
        #: die on the tick that reaches it, not on a later one. The one case
        #: this swallows: a stretch the reading called dry coasts the rest of
        #: itself ballistically, and the ground may take it there when a line
        #: the lock would have found full would have carried it past. A
        #: stretch's worth of thrust either way, and a hull that has hit the
        #: ground has hit it.
        return outcome, (await _paid(session, constants, catalog, ship, weight, spent, klass))[0]

    #: The tanks are asked whenever there is anything to ask them: a stretch
    #: that burnt, and one the reading called dry -- the second without the
    #: first being exactly the stale "dry" the correction below is for. A
    #: reading of an empty line buys no thrust at all, so `spent` is nought and
    #: nothing is written off; what the lock finds still has the last word.
    burnt, aboard, need = await _paid(
        session, constants, catalog, ship, weight, spent, klass, weigh=outcome == "adrift"
    )
    if spent > _DV_EPS or outcome == "adrift":
        #: **Whether the engines are out is the tanks' word, and the tanks are
        #: asked under the lock** -- the budget the stretch was flown on was a
        #: reading, and a hand may have poured either way since. Poured out
        #: from under a burning engine, the line cannot pay for the stretch just
        #: flown: the stretch stands, because it happened, and the engines are
        #: out from here -- the same drift as tanks that ran dry, which is what
        #: they did. Poured in, the line holds more than the stretch cost, and a
        #: hull the stale reading called dry is not stranded for it: it flew on
        #: less thrust than it could have, and carries on.
        #:
        #: Corrected rather than put off to the next stretch: one put off once
        #: is put off again by the next pour, and a hull could be kept from
        #: arriving -- or from dying -- a stretch at a time. What a hand can buy
        #: that way is bounded by the line as the reading saw it, and it is
        #: bought once: an unpaid stretch leaves a drifter, and a drifter has no
        #: order left to fly the trick on. A stretch is `now - sky_at` and not a
        #: minute, so after an idle worker that bound is hours of thrust rather
        #: than one minute's -- still one stretch, still once.
        #:
        #: A stretch that **ended** -- moored, held, on the star's circle --
        #: keeps its end the way a strike does, short line or not: there is no
        #: "from here" for a hull that has arrived, and the last stretch of a
        #: crossing is not an order anybody can fly the trick on twice.
        if aboard + _FUEL_EPS < need:
            outcome = "adrift" if outcome == "flying" else outcome
        elif outcome == "adrift" and aboard > need + _FUEL_EPS:
            outcome = "flying"

    if outcome == "moored" and other is not None:
        await hold.begin(session, constants, catalog, ship, other, r, v, now=stamp)
        return "held", burnt
    if outcome == "moored" and isinstance(target, sky.Star):
        #: On the circle round the star: no order any more, a coast that is
        #: stable by construction -- counted all the same, so the console
        #: and the map read it like any drifter's -- and the owner told.
        ship.course = None
        verdict = await fate.book_loss(session, constants, ship, world, now=stamp, t=t, r=r, v=v)
        _keep_forecast(ship, verdict, now=stamp, t=t)
        await events.record(
            session,
            EventKind.SHIP_STAR_ORBIT,
            actor_identity_id=ship.owner_identity_id,
            node_id=ship.connector_node_id,
            ship_id=str(ship.id),
            name=ship.name,
        )
        await session.flush()
        return "circled", burnt
    if outcome == "moored" and isinstance(target, sky.Body):
        sphere = await _sphere(session, target_planet(target))
        if sphere is None:  # pragma: no cover -- the sky runs only the planets laid
            outcome = "flying"
        else:
            #: In orbit (D-354): no node to moor to, the order done, the coast
            #: counted -- a closed orbit, stable by arithmetic.
            await into_orbit(session, ship, sphere, r=r, v=v, now=stamp)
            verdict = await fate.book_loss(
                session, constants, ship, world, now=stamp, t=t, r=r, v=v
            )
            _keep_forecast(ship, verdict, now=stamp, t=t)
            await events.record(
                session,
                EventKind.SHIP_IN_ORBIT,
                actor_identity_id=ship.owner_identity_id,
                node_id=ship.connector_node_id,
                ship_id=str(ship.id),
                name=ship.name,
                planet=sphere.planet.value,
            )
            await session.flush()
            return outcome, burnt
    if outcome == "adrift":
        ship.course = None
        await fate._adrift(session, constants, ship, world, now=now, t=t1, r=r, v=v)
        return outcome, burnt
    order["phase"] = phase
    order["spent"] = round(float(order.get("spent", 0.0)) + spent, ROUND_DV)
    if route is not None:
        order["leg"] = flyby.leg_row(leg)
    ship.course = order
    #: "If the engines fell silent now": the coast ahead of a hull under an
    #: order, refreshed at the coaster's cadence rather than every minute --
    #: ninety days of five-body arithmetic is the tick's to spend, not a
    #: reading's, and not every tick's either.
    if _forecast_stale(constants, ship, now):
        _keep_forecast(ship, await fate.fate_of(session, constants, world, t1, r, v), now=now, t=t1)
    await session.flush()
    return outcome, burnt


async def _paid(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    ship: Ship,
    weight: float,
    dv: float,
    klass: int | None,
    *,
    weigh: bool = False,
) -> tuple[float, float, float]:
    """Write off what a stretch of `dv` burnt. Returns what was burnt, what the
    **lock** found on the line, and what the stretch asked of it -- all three in
    reference units (D-252), because the caller's verdict is the difference.

    `weigh` asks for the line to be locked and weighed even when the stretch
    burnt nothing. A reading that found the line empty buys no thrust at all,
    so the stretch spends nought -- and that is the very stretch whose "the
    tanks are dry" may be a hand's doing rather than the tanks'.

    The tanks are locked here and nowhere earlier: this is the only place a
    stretch changes an amount, and an amount changes under the row lock and
    nothing else does (the quality bar). The stretch was priced off a reading
    taken before the arithmetic, so the lock may find more or less than that
    reading promised -- a hand poured into the line, or out of it, in between.
    Which of the two it was, and what to do about it, is `_fly`'s to say; what
    is written off here is what was **there**, never what the reading promised.

    The two ends are exact inverses (`sim.dv_aboard`, `sim.fuel_for_dv`: both
    linear in the same factor), so `aboard` against `need` is the same question
    as the budget the reading gave against the budget the lock would have
    given, to the thousandth a representation may cost (`physics._FUEL_EPS`).

    The line is read a second time here, rather than the reading above being
    locked: a vessel joined to the line meanwhile belongs to it, and a list
    gathered before the arithmetic would lock the wrong rows. Two walks of the
    hold a minute for a hull that burns, against a row lock held through the
    whole of the sky's arithmetic -- which is what the one walk used to cost.

    The departure asks it the other way round (`flight._burn`, through
    `physics.burn_checked`): it has a player in front of it, so it weighs first
    and refuses in words rather than burning a tank it cannot fly the leg out
    of. The tick has nobody to speak to, and a stretch already flown to speak
    about: it drinks the line to the bottom and reports.
    """
    if dv <= _DV_EPS and not weigh:
        #: A stretch that asked for no thrust and is not being second-guessed
        #: has nothing to write off and nothing to weigh: the hull under an
        #: order that is coasting this minute locks no tanks.
        return 0.0, 0.0, 0.0
    stacks = await stock.lock_items(
        session, await fuel_stacks(session, constants, catalog, ship), ordered=True
    )
    aboard = sum(amount_float(one.amount) * fuel_energy(constants, one.type_key) for one in stacks)
    need = fuel_for_dv(constants, weight, dv, klass) if dv > _DV_EPS else 0.0
    return await spend_fuel(session, constants, catalog, ship, need, stacks=stacks), aboard, need


async def _route_of(
    session: AsyncSession,
    constants: Constants,
    world: sky.System,
    order: dict,
    target: sky.Target,
    arrive: float,
) -> sky.Route | None:
    """The flyby an order carries, in sky days (D-341), or nothing for a
    crossing that goes straight."""
    if not order.get("via") or not isinstance(target, sky.Body):
        return None
    via = world.body(str(order["via"]))
    return sky.Route(
        via=via,
        home=world.body(str(order["from"])) if order.get("from") else None,
        at=await sky_days(session, datetime.fromisoformat(str(order["pass_at"]))),
        rp=float(order["rp"]),
        aim=(float(order["aim"][0]), float(order["aim"][1])),
        burn=float(order["burn"]),
        arrive=arrive,
        floor=float(constants[R.ORBIT_FLYBY_FLOOR_RADII]) * via.radius,
    )


def target_planet(body: sky.Body) -> Planet:
    return Planet(body.key)


async def _dense_drifter(
    session: AsyncSession,
    constants: Constants,
    world: sky.System,
    other: Ship,
    *,
    t0: float,
    t1: float,
) -> sky.Drifter | None:
    """The target hull's line for the stretch being flown, laid densely.

    The forecast on its row is the chart's line -- a couple of dozen points
    over months, coarse enough to miss a hull by units between two of them
    -- so the helm is given the same coast propagated afresh **from the
    hull's own stamp**, a point an hour, to a while past the stretch's end
    (`_meet` wants the hull's speed as well as its place). From the stamp
    and not from the stretch's start: the target may have been restamped
    this very tick, and a state asked for before a stamp is the stamp's --
    an hour's shift that read as the target jumping a unit.

    A target in orbit round a planet is read by Kepler instead (D-354,
    wave 3, `sky.Orbiter`): a lap there is hours long, and a line of hourly
    points is chords across it -- and a meeting in orbit aims at where the
    other will be at the order's hour, days past any stretch.
    """
    if other.sky_at is None or other.held_ship_id is not None:
        return None
    r, v = _state_of(other)
    start = await sky_days(session, other.sky_at)
    step = float(constants[R.ORBIT_PLAN_STEP_MINUTES]) / MINUTES_PER_HOUR / HOURS_PER_DAY
    held = sky.bound_to(world, start, r, v)
    if held is not None:
        if t0 > start and not sky.kepler_reads(world, held, t0 - start):
            #: Where Kepler does not read the orbit over the stamp's age --
            #: Pyroxis -- the target is flown to the stretch's start under the
            #: whole sky, so that it and the chaser are aimed from one moment.
            #: Read off a stamp hours old it jumped at every restamp by what
            #: the tide had done since, and the chaser paid to follow the jumps
            #: (measured 2026-09-19 round Pyroxis: 1.6 times the price of a
            #: thirteen-hour meeting, and 1.01 read afresh).
            rr, vv = await asyncio.to_thread(
                sky.advance,
                world,
                np.array([start]),
                np.array([t0]),
                np.array([r]),
                np.array([v]),
                dt_max=step,
            )
            held = sky.bound_to(world, t0, _row(rr), _row(vv)) or held
        return sky.orbiter(f"ship:{other.id}:{held.t0}", held)
    horizon = max(t1 - start, step) + max(t1 - t0, step) + step
    points = int(math.ceil(horizon * HOURS_PER_DAY)) + 1 + 1
    path = sky.sample(
        world,
        start,
        np.array([r]),
        np.array([v]),
        np.array([horizon]),
        dt_max=step,
        points=points,
    )[0]
    return sky.Drifter(
        key=f"ship:{other.id}:{start}",
        t0=start,
        t1=start + horizon,
        trace=tuple((float(x), float(y)) for x, y in path),
    )


async def _void(
    session: AsyncSession,
    constants: Constants,
    world: sky.System,
    ship: Ship,
    *,
    now: datetime,
    t0: float,
    t1: float,
) -> tuple[str, float]:
    """The order's target is gone: the hull coasts on from where it is."""
    r0, v0 = _state_of(ship)
    step = float(constants[R.ORBIT_PLAN_STEP_MINUTES]) / MINUTES_PER_HOUR / HOURS_PER_DAY
    #: Watched like any other stretch (OQ-120): the target being gone is no
    #: reason for the hull to fall through a planet on the way to finding out.
    r, v, t, hit, left = sky.coast_to(world, t0, t1, r0, v0, dt_max=step)
    if hit is not None or left:
        stamp = _moment_of(now, t1, t)
        _write_state(ship, r, v, at=stamp)
        await fate.strike(session, constants, ship, now=stamp, t=t, r=r, body=hit, gone=left)
        return "struck", 0.0
    _write_state(ship, r, v, at=now)
    ship.course = None
    await fate._adrift(session, constants, ship, world, now=now, t=t1, r=r, v=v, why="target")
    return "adrift", 0.0


async def _sight(
    session: AsyncSession,
    world: sky.System,
    ship: Ship,
    afloat: Sequence[Ship],
    table: dict[uuid.UUID, tuple[tuple[float, float], tuple[float, float]]],
) -> None:
    """Who is within the sight radius of a hull that just moved (D-289, wave 3):
    a foreign hull newly in sight is told of to both owners, once, and a hull
    gone out of sight may be sighted again. `table` is every hull's place
    this tick (`sim.states_at`).

    The memory of a sighting is on the rows of hulls that move: only this
    hull's row is written -- the other's is not locked, and an update to it
    would queue this tick behind that hull's own command -- and the pair is
    known if either row lists it, so it is told once whichever moves first.
    A hull gone out of sight takes itself off the other's row if that row is
    free, so that a return is a sighting again; a row under a hand this
    second is left for the next move, and nothing waits on it. A hull that
    stops moving -- moored, held -- keeps the list of the tick it stopped in
    (it is in `moved` that tick), and the hulls leaving its sight keep it
    clean from then on.
    """
    mine = table.get(ship.id)
    if mine is None:  # pragma: no cover -- the tick just wrote the state
        return
    seen: list[str] = []
    for other in afloat:
        if other.id == ship.id or other.owner_identity_id == ship.owner_identity_id:
            continue
        theirs = table.get(other.id)
        if theirs is None:
            continue
        if math.hypot(mine[0][0] - theirs[0][0], mine[0][1] - theirs[0][1]) <= world.sight_radius:
            seen.append(str(other.id))
    before = set(ship.sightings or [])
    for other in afloat:
        if other.id == ship.id or other.owner_identity_id == ship.owner_identity_id:
            continue
        theirs_too = str(ship.id) in (other.sightings or [])
        if str(other.id) in seen:
            if str(other.id) in before or theirs_too:
                continue
            for teller, seer, seen_one in (
                (ship.owner_identity_id, ship, other),
                (other.owner_identity_id, other, ship),
            ):
                await events.record(
                    session,
                    EventKind.SHIP_SIGHTED,
                    actor_identity_id=teller,
                    node_id=seer.connector_node_id,
                    ship_id=str(seer.id),
                    name=seer.name,
                    other_ship_id=str(seen_one.id),
                    other=seen_one.name,
                )
        elif theirs_too:
            #: Out of sight, and the other's row still lists this hull from
            #: its own last move: taken off it if the row is free.
            row = await session.get(
                Ship, other.id, with_for_update={"skip_locked": True}, populate_existing=True
            )
            if row is not None:
                row.sightings = sorted(set(row.sightings or []) - {str(ship.id)})
    if sorted(seen) != sorted(before):
        ship.sightings = sorted(seen)


def _moment_of(now: datetime, t1: float, t: float) -> datetime:
    """The clock moment of sky day `t`, counted back from `now` at `t1`."""
    return now - timedelta(days=t1 - t)


async def _restamp(
    session: AsyncSession, constants: Constants, world: sky.System, ship: Ship, *, now: datetime
) -> bool:
    """Move a coasting hull's stamp along, so a reading never propagates weeks.
    Flown under the whole sky, not read by Kepler: the stamp is the truth the
    readings until the next restamp are drawn from (D-354). Returns whether
    the coast ended on the way."""
    t0 = await sky_days(session, ship.sky_at)
    t1 = await sky_days(session, now)
    r0, v0 = _state_of(ship)
    step = float(constants[R.ORBIT_PLAN_STEP_MINUTES]) / MINUTES_PER_HOUR / HOURS_PER_DAY
    #: Watched like any stretch (OQ-120): a coast that meets the ground
    #: between two restamps is a hull lost there, not one flown through the
    #: planet and found on its far side.
    #: Off the loop, as the forecast is (`fate.fate_of`): hours of steps
    #: near a planet, with the ground asked at every one of them.
    r, v, t, hit, left = await asyncio.to_thread(
        sky.coast_to, world, t0, max(t0, t1), r0, v0, dt_max=step
    )
    if hit is not None or left:
        stamp = _moment_of(now, t1, t)
        _write_state(ship, r, v, at=stamp)
        await fate.strike(session, constants, ship, now=stamp, t=t, r=r, body=hit, gone=left)
        return True
    was = (ship.forecast or {}).get("kind")
    _write_state(ship, r, v, at=now)
    #: And the coast ahead, from the new stamp: what the console and the map
    #: read as the drifter's line and verdict. A coast that was stable and is
    #: not any more -- a wide ellipse the star's tide has pumped toward the
    #: ground -- has its hour booked here; one already booked keeps the job
    #: it has, which books itself again at its hour if the hour moved
    #: (`fate.lost`).
    verdict = await fate.fate_of(session, constants, world, t1, r, v)
    if verdict.kind != sky.STABLE and was in (None, sky.STABLE):
        await fate.book_loss(session, constants, ship, world, now=now, t=t1, r=r, v=v, fate=verdict)
    _keep_forecast(ship, verdict, now=now, t=t1)
    return False
