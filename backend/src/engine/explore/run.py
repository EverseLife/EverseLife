# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""explore: the run itself -- setting out, coming back, and calling it off.

Split out of `engine/explore.py` along its sections. A run is an ordinary
journal job: it goes offline, survives a restart and fires exactly once. What
it costs is `explore.odds`, what it leaves behind is `explore.site`; this file
is the walk between them.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants, current, current_catalog
from src.constants import registry as R
from src.engine import city as town
from src.engine import craft, events, food, frost, luck, occupation, ruins, transport, travel, world
from src.engine import ship as vessels
from src.engine.explore import odds as forecast
from src.engine.explore import site
from src.engine.explore._base import (
    FOUND_HERE,
    GOALS,
    LOT,
    ROOM,
    SITE,
    VEIN,
    WORDS,
    AlreadyOut,
    ExploreError,
    NotOut,
    mineable,
)
from src.engine.jobs import enqueue, handler
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.job import Job, JobKind, JobState
from src.models.world import Node, Surface
from src.units import MINUTES_PER_HOUR


async def pending(session: AsyncSession, body: Body) -> Job | None:
    """This body's ongoing run, if any."""
    return (
        (
            await session.execute(
                select(Job).where(
                    Job.kind == JobKind.EXPLORE_SURVEY.value,
                    Job.body_id == body.id,
                    Job.state == JobState.PENDING,
                )
            )
        )
        .scalars()
        .first()
    )


async def survey(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    *,
    goal: str = SITE,
    resource: str | None = None,
    now: datetime | None = None,
) -> Job:
    """Go exploring from this node for the named goal. The find arrives on schedule.

    The scout **leaves in person**: while the run goes, the body is in the field
    and unavailable for everything in-person -- as in sleep
    (`travel.require_here`). One can return before the deadline with `cancel`,
    but then the find does not happen.

    Duration and chance depend on how trodden the surroundings already are
    (D-156): the first run from here is minutes and an almost certain find, the
    sixth is hours and a roll. Stamina is written off up front, like batch
    materials -- but a shortage does not lock the run: what was missing the
    scout sleeps off in the field, and the run simply lasts longer -- by the
    sleep time per `body.hibernation_rate`.
    """
    moment = now or datetime.now(UTC)
    if goal not in GOALS:
        raise ExploreError(f"неизвестная цель поиска: {goal}")
    if body.state is not BodyState.ALIVE:
        raise ExploreError("мёртвое тело не разведывает")
    if resource is not None and resource not in mineable(current_catalog()):
        raise ExploreError(f"такой породы в этом мире не добывают: {resource}")
    await travel.require_here(session, body)
    #: A run is an occupation (D-211): the scout leaves in person, and a body
    #: with a plot under the plough or a batch at the bench has no hands to
    #: leave with.

    await occupation.require_free(session, body, besides=frozenset({occupation.FIELD}))

    origin = await session.get(Node, body.node_id)
    if origin is None:  # pragma: no cover -- a body always stands in a node
        raise ExploreError("разведка идёт из узла, а тело стоит в никуда")

    #: Not from aboard a ship (D-201). A find comes with an edge from the node
    #: one left from, and an edge out of a ship node would be a second way in
    #: -- the connector must stay the only one, or the inspection at the
    #: gangway is walked around. There is no land under a hull to explore anyway.

    if vessels.is_aboard(origin):
        raise ExploreError(
            "с борта не разведывают: под кораблём земли нет. Сойдите в порту и идите от него"
        )

    #: The refusal must come at once, not on return: an impossible goal is
    #: visible before leaving, and the player must not spend stamina on it.
    if goal == LOT and await town.of_node(session, origin) is None:
        raise ExploreError("участок ищут в городе: за стенами городской застройки нет")
    #: The goal must be one this very node offers (D-232). `possible` is what
    #: the client draws its buttons from, and the door must agree with the
    #: advice: a socket takes a goal from anybody, agents included, and
    #: "search for city ground" from a room deep inside Merid would hang a
    #: frozen city on a corridor and walk around the gate rule (D-206).
    offers = await forecast.possible(session, origin)
    if goal not in offers:
        raise ExploreError(
            f"отсюда так не ищут: здесь ищут "
            f"{', '.join(WORDS.get(one, one) for one in offers) if offers else 'ничего'}"
        )
    ruined = await ruins.city_of(session, origin)
    if goal == ROOM and ruined is not None and ruins.exhausted(constants, ruined):
        raise ExploreError(f"«{ruined.name}» выработан: всё, что можно было вскрыть, уже вскрыто")
    if await pending(session, body) is not None:
        raise AlreadyOut("заход уже идёт: дождитесь возвращения")

    minutes = forecast.minutes_of(constants, origin, random.Random())
    spend = (
        forecast.stamina_for(constants, minutes)
        * food.drain_multiplier(constants, body, moment)
        * await frost.drain_multiplier(session, constants, body)
    )
    #: A shortage of strength does not lock the run but lengthens it: what was
    #: missing the scout sleeps off in the field per `body.hibernation_rate` and continues.
    have = float(body.stamina)
    if spend > have:
        deficit = spend - have
        minutes += deficit / constants[R.BODY_HIBERNATION_RATE] * MINUTES_PER_HOUR
        body.stamina = Decimal("0")
    else:
        body.stamina = Decimal(str(have - spend))
    await session.flush()

    #: The chance is named at departure and travels in the job: while the scout
    #: is in the field the neighbours may tread the area, but that does not change the promised
    #: price.
    #:
    #: Three things multiply into it, and they answer different questions: how
    #: trodden the surroundings are (D-156), how rare the sought species is
    #: (D-151), and how crowded the place the find will hang on already is (D-207).
    hangs_on = await forecast.anchor_of(session, origin, goal)
    press = await forecast.crowding(session, constants, hangs_on)
    aimed = forecast.aim_at(constants, current_catalog(), goal, resource)
    odds = forecast.chance(constants, origin) * aimed * press
    #: And a city of the Forerunners is worked out like a vein (D-232): the more
    #: of its rooms are open, the oftener the next door leads nowhere.
    odds *= await forecast.wear_of(session, constants, origin, goal)
    will_return = moment + timedelta(minutes=minutes)
    #: In the field the scout is not at the machine: the running batch
    #: freezes and waits for the return (D-209).

    await craft.freeze(session, body, now=moment)
    event = await events.record(
        session,
        EventKind.EXPLORE_STARTED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        stamina=spend,
        goal=goal,
        resource=resource,
        minutes=minutes,
        chance=odds,
        explored=forecast.found_here(origin),
        crowding=press,
        returns_at=will_return.isoformat(),
    )
    job = await enqueue(
        session,
        JobKind.EXPLORE_SURVEY,
        will_return,
        payload={
            "body": str(body.id),
            "from": str(body.node_id),
            "goal": goal,
            "resource": resource,
            "chance": odds,
        },
        dedup_key=f"explore.survey:{body.id}:{event.id}",
        cause_event_id=event.id,
        body_id=body.id,
    )
    if job is None:  # pragma: no cover -- the key is unique per event
        raise AlreadyOut("заход уже поставлен")
    return job


@handler(JobKind.EXPLORE_SURVEY)
async def returned(session: AsyncSession, job: Job) -> None:
    """The scout returned. One roll, seeded by the job: a retry gives the same."""
    body = await session.get(Body, uuid.UUID(job.payload["body"]), with_for_update=True)
    origin = await session.get(Node, uuid.UUID(job.payload["from"]))
    if body is None or origin is None:  # pragma: no cover
        raise ExploreError(f"заход {job.id} ссылается в никуда")

    constants, catalog = current(), current_catalog()
    dice = random.Random(str(job.id))
    goal = str(job.payload.get("goal") or SITE)
    requested = job.payload.get("resource")

    #: The chance was named at departure (D-156). Old jobs do not carry it --
    #: for them we compute by place, as it was computed at departure.
    odds = job.payload.get("chance")
    if odds is None:  # pragma: no cover -- runs queued before D-156
        odds = forecast.chance(constants, origin) * forecast.aim_at(
            constants, catalog, goal, requested
        )
    #: The chance has a memory (D-213): it grows with every empty run and
    #: resets on a find, so the announced percent stays the mean and the
    #: twelve-run drought stops happening.

    if not await luck.hit(session, body.identity_id, luck.EXPLORE_FIND, float(odds), dice=dice):
        await _empty(session, body, origin, goal=goal, resource=requested, now=job.run_at)
        return

    #: For a vein without a named species the old share `explore.vein_share`
    #: applies: sought "anything" -- got whatever turned up.
    with_vein = goal == VEIN and (
        requested is not None
        or await luck.hit(
            session,
            body.identity_id,
            luck.EXPLORE_VEIN,
            constants[R.EXPLORE_VEIN_SHARE],
            dice=dice,
        )
    )
    #: Two finds reveal instead of creating (D-232): a room of a city that
    #: stood before anybody came, and another city of the Forerunners beyond
    #: the ice. Everything else is a place the world did not have until
    #: somebody walked to it.
    if goal == ROOM:
        #: The city may have been worked out while this scout was in the field
        #: -- somebody else took its last room. That is an **empty run**, not a
        #: broken job: the roll simply found nothing, and the scout comes back
        #: the way anybody comes back empty (D-232).
        #: Under the lock, and before `open_room` takes it: two scouts coming
        #: back at 23 rooms of 24 must both end their runs, one with the room
        #: and one with nothing. Read without the lock, the loser would meet a
        #: refusal thrown out of the job instead -- a retry, a backoff, and the
        #: honest "empty" only on the second attempt.
        city = await ruins.city_of(session, origin, lock=True)
        if city is None or ruins.exhausted(constants, city):
            await _empty(session, body, origin, goal=goal, resource=requested, now=job.run_at)
            return
        found = await ruins.open_room(session, constants, dice, origin, who=body.identity_id)
    elif goal == SITE and await ruins.left_by_precursors(session, origin):
        found = await ruins.lost_city(session, constants, origin, who=body.identity_id)
    else:
        found = await site.lay(
            session,
            constants,
            dice,
            origin,
            goal=goal,
            vein=with_vein,
            who=body.identity_id,
        )

    species = None
    if with_vein:
        species = requested or await site.species_of(
            session, constants, catalog, dice, planet=origin.planet, who=body.identity_id
        )
        richness = constants[R.EXPLORE_VEIN_RICHNESS]
        stock = constants[R.EXPLORE_VEIN_STOCK]
        await world.create_vein(
            session,
            found,
            species,
            richness=dice.uniform(richness.min, richness.max),
            remaining=dice.uniform(stock.min, stock.max),
        )
        found.name = f"Жила: {species.lower()}"
        await session.flush()

    #: A plot in the city is a step across the quarter; a find beyond the wall
    #: is a trail, and its length is set by the find's distance (D-180): the
    #: farther from the city, the pricier the step.
    if goal in (LOT, ROOM):
        #: A plot is a step across the quarter, and a room a step along a
        #: corridor: both are inside the built-up area, and the Forerunners
        #: laid their floors better than anybody has since.
        step = constants[R.TRAVEL_CITY_STEP]
        seconds = dice.uniform(step.min, step.max)
        coverage = Surface.PAVED
        minutes = seconds / MINUTES_PER_HOUR
    else:
        seconds = travel.frontier_seconds(constants, travel.reach_of(found))
        minutes = seconds / MINUTES_PER_HOUR
        #: Snow is walked, not driven (D-232), and `Surface.TRAIL` is exactly
        #: that: two to three times longer than a road, and no vehicle passes.
        #: It is the slowest surface the world has, and the walk to a city
        #: found beyond the ice is the brake on colonising the planet.
        coverage = Surface.TRAIL
    #: A plot is found inside the built-up area and hangs on the node it was
    #: sought from; a find beyond the walls hangs on the city's **gate** (D-206).
    #: Otherwise a scout who set out from the trading yard would leave a trail
    #: from it into the steppe, and the market would quietly become a second gate
    #: -- which is exactly how the capital ended up with two ways out. The same
    #: node the chance was measured against at departure (D-207).
    anchor = await forecast.anchor_of(session, origin, goal)
    await travel.connect(session, anchor, found, base_seconds=seconds, surface=coverage)

    #: The surroundings became one find poorer -- for everyone who leaves from
    #: here next (D-156). Only luck counts: an empty run depletes nothing,
    #: otherwise bad luck would punish twice.
    origin.properties = {**(origin.properties or {}), FOUND_HERE: forecast.found_here(origin) + 1}
    await session.flush()

    #: Found means you stand there (D-185): the scout reached the place on foot,
    #: and returning them to the exit node would cancel the path walked. The way
    #: back is their decision, and they have already laid themselves a trail.
    body.node_id = found.id
    body.node_since = job.run_at
    await session.flush()

    #: The convoy follows, as in an ordinary transit (D-157): otherwise it would
    #: stay standing in the exit node, and the body would be "harnessed" to a
    #: wagon half a map away.

    convoy = await transport.harnessed(session, body)
    if convoy is not None:
        await transport.follow(session, convoy, found)

    await events.record(
        session,
        EventKind.EXPLORE_FOUND,
        actor_identity_id=body.identity_id,
        node_id=found.id,
        from_node=origin.key,
        #: Where the trail actually starts: from the city it is the gate rather
        #: than the node the scout set out from (D-206).
        tied_to=anchor.key,
        found=found.key,
        name=found.name,
        goal=goal,
        resource=species,
        minutes=minutes,
        explored=forecast.found_here(origin),
    )
    #: The scout stands in the find now, not at the machine they left: what
    #: waited there stays frozen until they walk back (D-209). Whatever of
    #: theirs waited **here** -- unlikely, but possible -- goes on.

    await craft.wake(session, body, now=job.run_at)


async def _empty(
    session: AsyncSession,
    body: Body,
    origin: Node,
    *,
    goal: str,
    resource: str | None,
    now: datetime,
) -> None:
    """Came back with nothing. An empty run is normal (D-152), and it is the
    only honest ending for a search that found no place to find one."""
    await events.record(
        session,
        EventKind.EXPLORE_EMPTY,
        actor_identity_id=body.identity_id,
        node_id=origin.id,
        goal=goal,
        resource=resource,
    )
    #: Back at the exit node with empty hands: the frozen work goes on (D-209).
    await craft.wake(session, body, now=now)


async def cancel(session: AsyncSession, body: Body) -> Job:
    """Turn back: the run is cancelled, the body is in the exit node again.

    Spent stamina does not come back -- the legs are already walked -- and the
    find will not happen: the roll was scheduled for the return time, and the
    scout did not reach it. The body's node has not changed since departure, so
    "return" means cancelling the job, and the body is free at once.
    """
    run = await pending(session, body)
    if run is None:
        raise NotOut("тело не в разведке: возвращаться неоткуда")
    run.state = JobState.CANCELLED
    run.finished_at = datetime.now(UTC)
    await session.flush()

    await events.record(
        session,
        EventKind.EXPLORE_CANCELLED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        goal=str(run.payload.get("goal") or SITE),
        resource=run.payload.get("resource"),
    )
    #: Turned back: the body is at the machine again, the frozen work goes on (D-209).

    await craft.wake(session, body, now=run.finished_at)
    return run
