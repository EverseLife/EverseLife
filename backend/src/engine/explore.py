# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Exploration: the map grows on foot, not by patch (D-152).

The world was set by the seed and did not grow: a plot could be taken only
where a node was already drawn, and new veins never appeared. Exploration
answers where the world beyond the walls comes from, and the answer "the
developers drew it" contradicts the design.

## Three search goals, and they differ

One seeks not "something" but what is needed. The goal is chosen before
leaving, and what ends up on the map depends on it:

| Goal | Where sought | What is found |
|---|---|---|
| `lot` | on the city layer | a free plot for building -- civic land (D-089) |
| `site` | on the planet | a place for a future city: a wild node with properties |
| `vein` | on the planet | a vein; the species can be named in advance |

**A named species is found worse than an unnamed one.** The chance is
multiplied by its share in the mining pace (`harvest.rates`): copper is rarer
than iron, and aiming at the rare means coming back empty more often.
Otherwise everyone would seek only the most expensive, and exploration would
become a faucet.

## How a run works

A run is an ordinary journal job: it goes offline, survives a restart and fires
exactly once. At the deadline a roll against the chance; for a vein without a
named species `explore.vein_share` applies as well.

**An empty run is normal.** Without it the map would grow by click, and
exploration would become a formality.

## The run's price is a property of the place, not the player (D-156)

Every node has a count of finds made when leaving from it. While the
surroundings are untrodden a run lasts `explore.attempt_minutes` -- minutes --
and the chance `explore.find_chance` is close to certain. Each find from this
node multiplies the duration by `explore.effort_growth` and the chance by
`explore.find_decay`, until the duration hits the ceiling
`explore.attempt_hours` and the chance the floor `explore.find_floor`.

**Stamina is charged by time in the field:** `explore.attempt_stamina` is the
price of a full-length run; a one-minute one costs correspondingly less.
Otherwise stamina would lock early runs instead of hours, and the fix would
amount to swapping one lock for another.

The count lives on the node, not on the player: an exploration level would be
character progress and turn the world into a backdrop for grinding. A trodden
neighbourhood grows poorer for everyone at once, and a run from a fresh find is
cheap again -- so the map grows in breadth, not as a star from the birthplace.

## Crowding turns a city outwards (D-207)

Depletion is about the neighbourhood; crowding is about the **shape** of the map.
A find is an edge, and edges pile up where everybody wants to be: at the
bioprinter, because the centre is where one wants to live, and at the city gate,
because everything wild couples to it (D-206). Left alone that grows a star of
thirty edges -- a place one can neither walk through nor look at.

So the chance is multiplied by the crowding of the node the find will **hang
on**: its own edges plus its neighbours' extra ones, `explore.crowding_decay` per
edge over `explore.crowding_free`, never below `explore.crowding_floor`. The
centre saturates first, and the next plot is sought where edges are few -- in the
outer rings. The city grows in rings because searching the centre stops paying,
not because a rule forbids it.

**The chance is promised at departure, not at return.** It is computed at the
moment of leaving and travels in the job: while the scout is in the field the
neighbours may tread the area, but the price is already named, and changing it
retroactively is dishonest.

## What exactly is found

The vein's species is chosen from what is mined at all -- the `gives` list of
the "Mining" operation in the vault. A species' weight equals its pace in
`harvest.rates`: the rare is mined slower, so it also turns up rarer. The
engine keeps no list of "which ores exist": add a fifth species in the vault
and it starts being found without a code change (D-151).

A place's merits are rolled under a common budget `site.quality_budget`: a
place good in everything never drops (D-126). A river eats part of the budget,
and the more water, the less is left for fertility.

**What is found belongs to nobody.** The finder gets the right of first night,
not ownership: a plot is taken in person, like any wild land.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants, current, current_catalog
from src.constants import registry as R
from src.engine import city as town
from src.engine import (
    craft,
    events,
    food,
    frost,
    luck,
    occupation,
    ruins,
    transport,
    travel,
    world,
)
from src.engine import ship as vessels
from src.engine.errors import Refusal
from src.engine.jobs import enqueue, handler
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.job import Job, JobKind, JobState
from src.models.world import Edge, Layer, Node, Planet, Surface
from src.units import MINUTES_PER_HOUR, PERCENT

#: The vault operation from which the engine learns what is mined in this world at all.
MINING_OPERATION = "Добыча"

#: Count of finds made from this node. Lives in the node's properties:
#: depletion is a property of the place, not the player, and needs no migration (D-156).
FOUND_HERE = "разведано"

#: Search goals. As strings, not an enumeration: the list grows with the map,
#: and the client names the goal with the same word as the engine.
LOT = "lot"
SITE = "site"
VEIN = "vein"
#: Woods to fell (D-191). The find is an ordinary wild node -- what makes it a
#: forest is the same place property the felling reads (D-177).
FOREST = "forest"
#: A room of a Forerunner city (D-232). The one goal that **reveals** instead of
#: creating: the city stood before anybody came, and the search opens its next
#: door (`engine.ruins`).
ROOM = ruins.ROOM
GOALS = (LOT, SITE, VEIN, FOREST, ROOM)

#: The goals in the player's own words, for a refusal that names what **is**
#: possible here rather than only what is not.
_WORDS = {
    LOT: "участок",
    SITE: "новое место",
    VEIN: "жилу",
    FOREST: "лес",
    ROOM: "помещения Предтеч",
}

#: The place property both the search and the felling operation look at.
WOODS = "лес"
#: Stony ground and meadow (D-196): stone and wild flax are gathered by hand,
#: and that is the first step of the whole ladder.
STONES = "камни"
MEADOW = "луг"


class ExploreError(Refusal):
    pass


class AlreadyOut(ExploreError):
    """A run is already going. One body cannot explore in two directions."""


class NotOut(ExploreError):
    """The body is not exploring: nowhere to return from."""


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
    offers = await possible(session, origin)
    if goal not in offers:
        raise ExploreError(
            f"отсюда так не ищут: здесь ищут "
            f"{', '.join(_WORDS.get(one, one) for one in offers) if offers else 'ничего'}"
        )
    ruined = await ruins.city_of(session, origin)
    if goal == ROOM and ruined is not None and ruins.exhausted(constants, ruined):
        raise ExploreError(f"«{ruined.name}» выработан: всё, что можно было вскрыть, уже вскрыто")
    if await pending(session, body) is not None:
        raise AlreadyOut("заход уже идёт: дождитесь возвращения")

    minutes = _minutes(constants, origin, random.Random())
    spend = (
        _stamina(constants, minutes)
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
    press = await crowding(session, constants, await anchor_of(session, origin, goal))
    odds = chance(constants, origin) * _aim(constants, current_catalog(), goal, resource) * press
    #: And a city of the Forerunners is worked out like a vein (D-232): the more
    #: of its rooms are open, the oftener the next door leads nowhere.
    odds *= await _wear(session, constants, origin, goal)
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
        explored=found_here(origin),
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
        odds = chance(constants, origin) * _aim(constants, catalog, goal, requested)
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
        found = await _place(
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
        species = requested or await species_of(
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
    anchor = await anchor_of(session, origin, goal)
    await travel.connect(session, anchor, found, base_seconds=seconds, surface=coverage)

    #: The surroundings became one find poorer -- for everyone who leaves from
    #: here next (D-156). Only luck counts: an empty run depletes nothing,
    #: otherwise bad luck would punish twice.
    origin.properties = {**(origin.properties or {}), FOUND_HERE: found_here(origin) + 1}
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
        explored=found_here(origin),
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


def found_here(node: Node) -> int:
    """How many finds have already been made from this node (D-156)."""
    return int((node.properties or {}).get(FOUND_HERE, 0))


def chance(constants: Constants, node: Node) -> float:
    """The chance of a run from here, in percent. Falls with each find down to the floor.

    The floor exists so that a trodden place grows poorer rather than locked: a
    node one can no longer go into the field from is a dead end, and the map is
    eternal (D-007).
    """
    decline = constants[R.EXPLORE_FIND_DECAY] ** found_here(node)
    return max(constants[R.EXPLORE_FIND_FLOOR], constants[R.EXPLORE_FIND_CHANCE] * decline)


async def possible(session: AsyncSession, node: Node) -> tuple[str, ...]:
    """Which goals make sense in this node at all.

    The client draws its buttons from this rather than guessing by map layer,
    and `survey` refuses anything not in it: a goal that would be refused must
    not be offered, and one that is offered must not be refused.

    Inside a city of the Forerunners one opens their next room -- and at its
    **door** one may also set out for the ice, or there would be no way off the
    three cities the seed lays (D-232). Inside a city of people a lot is added
    to the open world rather than replacing it: a find beyond the walls ties
    itself to the gate wherever it was sought from (D-206).
    """
    #: Nothing grows where the ground bakes (D-231, D-233): a grove found on
    #: Pyroxis would be a place property nobody could explain, and felling it
    #: reads the same property the search would have written.
    beyond = (
        (SITE, VEIN)
        if await frost.climate_of(session, node) == frost.HEAT
        else (
            SITE,
            VEIN,
            FOREST,
        )
    )
    #: Inside a city of the Forerunners the search is for their next room
    #: (D-232): nothing is founded here and nothing is felled, and a frozen city
    #: hung on a corridor would walk around the gate rule (D-206).
    #:
    #: **Except at the door.** A city's pier is where the ice plains begin, and
    #: without this the whole of Aurora would end at three cities: the seed lays
    #: no wild node on the planet, a ship lands only at a pier, and a search for
    #: new cities would have nowhere to start from. From the door one goes out
    #: onto the ice; from a corridor one goes deeper in.
    if await ruins.city_of(session, node) is not None:
        return (ROOM, *beyond) if await travel.is_exit(session, node) else (ROOM,)
    #: Everywhere else the world beyond the walls is open from anywhere: a find
    #: made from inside a city ties itself to the gate, not to the node one set
    #: out from (D-206). A lot is the one goal that needs a city around it.
    if node.layer is Layer.CITY and await town.of_node(session, node) is not None:
        return (LOT, *beyond)
    return beyond


async def anchor_of(session: AsyncSession, origin: Node, goal: str) -> Node:
    """The node a find from here will hang on -- and whose crowding decides the chance.

    A plot lands inside the built-up area, on the very node it was sought from; a
    find beyond the walls hangs on the city's gate (D-206). So "how crowded is
    it here" is a question about the gate for the second case, and measuring the
    node one set out from would miss exactly the star the gate is growing.
    """
    if goal in (LOT, ROOM):
        return origin
    gate = await travel.gate_of(session, origin)
    return gate if gate is not None else origin


async def crowding(session: AsyncSession, constants: Constants, node: Node) -> float:
    """Chance multiplier for the crowding of the graph around this node (D-207).

    A find is an edge, and edges pile up where everybody wants to be: at the
    bioprinter, at the city gate. Thirty edges on one node is a place one can
    neither walk through nor look at, so the search there gets worse -- by the
    node's own degree and by its neighbours' extra edges.

    Neighbours are counted **without** the edge back here: a chain of nodes
    creates no crowding, a cluster does. Below `explore.crowding_floor` the
    multiplier does not fall: the map is eternal (D-007) and has no place one can
    never search from again.
    """
    edges = (
        (
            await session.execute(
                select(Edge).where(or_(Edge.node_a_id == node.id, Edge.node_b_id == node.id))
            )
        )
        .scalars()
        .all()
    )
    neighbours = {edge.node_b_id if edge.node_a_id == node.id else edge.node_a_id for edge in edges}
    degree = len(edges)

    #: The neighbours' degrees, in one query: every endpoint that falls inside the
    #: set is one edge of somebody in it.
    around = 0
    if neighbours:
        rows = (
            (
                await session.execute(
                    select(Edge).where(
                        or_(
                            Edge.node_a_id.in_(neighbours),
                            Edge.node_b_id.in_(neighbours),
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
        incidences = sum(
            (edge.node_a_id in neighbours) + (edge.node_b_id in neighbours) for edge in rows
        )
        #: Minus the edges leading back here: those are already counted as `degree`.
        around = max(0, incidences - len(neighbours))

    crowd = degree + constants[R.EXPLORE_CROWDING_NEIGHBOUR_K] * around
    over = max(0.0, crowd - constants[R.EXPLORE_CROWDING_FREE])
    floor = constants[R.EXPLORE_CROWDING_FLOOR] / PERCENT
    return max(floor, constants[R.EXPLORE_CROWDING_DECAY] ** over)


def _cap(constants: Constants) -> float:
    """The run duration ceiling in minutes: depletion grows it no further."""
    return constants[R.EXPLORE_ATTEMPT_HOURS] * MINUTES_PER_HOUR


def _minutes(constants: Constants, node: Node, dice: random.Random) -> float:
    """How long a run from here takes. Each find lengthens the next."""
    run = constants[R.EXPLORE_ATTEMPT_MINUTES]
    depletion = constants[R.EXPLORE_EFFORT_GROWTH] ** found_here(node)
    return min(_cap(constants), dice.uniform(run.min, run.max) * depletion)


def _stamina(constants: Constants, minutes: float) -> float:
    """The run's price in stamina: by time in the field, not per piece.

    `explore.attempt_stamina` is the price of a full-length run. A per-piece
    price would lock early runs with stamina exactly where D-156 unlocks them
    with time.
    """
    return constants[R.EXPLORE_ATTEMPT_STAMINA] * minutes / _cap(constants)


async def outlook(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    *,
    goal: str = SITE,
    resource: str | None = None,
) -> dict | None:
    """What a run from here will cost -- before leaving.

    The price of exploration changes from place to place (D-156), and a price
    that cannot be seen in advance reads as engine randomness. Aiming is
    computed right here: a requested species is found the worse the rarer it is
    (D-151), and showing "90% chance" to someone going for gold would be a lie.

    Crowding is shown apart from the chance for the same reason (D-207): "here it
    is cramped" is a fact about the place the player can act on -- by walking a
    day out and setting off from the frontier -- and hiding it inside one number
    would leave only bad luck to blame.
    """
    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover -- a body always stands in a node
        return None
    run = constants[R.EXPLORE_ATTEMPT_MINUTES]
    depletion = constants[R.EXPLORE_EFFORT_GROWTH] ** found_here(node)
    short = min(_cap(constants), run.min * depletion)
    long_ = min(_cap(constants), run.max * depletion)
    aim = _aim(constants, current_catalog(), goal, resource)
    anchor = await anchor_of(session, node, goal)
    press = await crowding(session, constants, anchor)
    #: A worked-out city is the fourth thing that narrows the chance, and it
    #: narrows it to nothing (D-232). Left out, the button would promise ninety
    #: percent in a city where the true answer is none -- exactly the price in
    #: advance that D-156 exists for.
    wear = await _wear(session, constants, node, goal)
    return {
        "explored": found_here(node),
        "minutes": {"min": short, "max": long_},
        #: The largest possible: the player must know the ceiling, not the average.
        "stamina": _stamina(constants, long_),
        "chance": chance(constants, node) * aim * press * wear,
        #: How much of the city is already open: a fact of the place the player
        #: can act on -- by walking to the next city (D-232).
        "worked_out": wear,
        #: By how much the species request narrowed the chance: the player sees
        #: not only "little" but why little (D-151).
        "aim": aim,
        #: And by how much the crowding of the place narrowed it (D-207), plus the
        #: node the find will hang on -- from the city that is the gate, not here.
        "crowding": press,
        "anchor": anchor.name if anchor.id != node.id else None,
        "resource": resource,
    }


async def _wear(session: AsyncSession, constants: Constants, node: Node, goal: str) -> float:
    """How much a city already opened narrows the search in it (D-232).

    One place, asked by both the forecast and the departure: a promise and a
    price that disagreed would be worse than either.
    """
    if goal != ROOM:
        return 1.0
    city = await ruins.city_of(session, node)
    return 1.0 if city is None else ruins.worked_out(constants, city)


def mineable(catalog: Catalog) -> tuple[str, ...]:
    """What is mined in this world at all -- the `gives` list of the "Mining" operation.

    The engine keeps no species list: add a fifth in the vault and it appears
    both in the goal choice and in finds, without a code change (D-151).
    """
    operation = next((op for op in catalog.recipes.operations if op.name == MINING_OPERATION), None)
    return tuple(operation.gives) if operation is not None else ()


def _aim(constants: Constants, catalog: Catalog, goal: str, requested: str | None) -> float:
    """Chance multiplier for aiming.

    A named species is found worse than an unnamed one, and exactly as many
    times worse as it is rarer: the share of its pace in `harvest.rates`
    relative to the fastest. No second rarity table -- it would diverge from
    the first (D-151).
    """
    #: Woods asked for are found as often as the world is wooded (D-191): one
    #: number rules both the chance random finds carry a forest and the price
    #: of aiming for one.
    if goal == FOREST:
        return constants[R.EXPLORE_FOREST_SHARE] / PERCENT
    if goal != VEIN or requested is None:
        return 1.0
    paces = constants[R.HARVEST_RATES]
    mining_ = [name for name in mineable(catalog) if float(paces.get(name, 0)) > 0]
    if requested not in mining_:
        return 1.0
    most_common = max(float(paces[name]) for name in mining_)
    return float(paces[requested]) / most_common


async def _place(
    session: AsyncSession,
    constants: Constants,
    dice: random.Random,
    origin: Node,
    *,
    goal: str,
    vein: bool,
    who: uuid.UUID | None = None,
) -> Node:
    """Create the found node next to the one we left from.

    A city plot lands **in the city** and belongs to it: civic land is not
    taken, the authority hands it out (D-089). Everything else hangs on the
    planet and stays unowned -- the finder gets the right of first night, not
    ownership (D-152).
    """

    #: The node key must be stable and unique forever: the map is eternal,
    #: there are no wipes (D-007), and "wild plot 3" will sooner or later
    #: collide. Named after the planet it is actually on: keys are read by
    #: people -- in the admin, in a migration, in a log line -- and a field of
    #: Pyroxis called `terra.wild.*` is a lie told to whoever reads it next.
    key = f"{origin.planet.value}.wild.{uuid.uuid4().hex}"

    if goal == LOT:
        city = await town.of_node(session, origin)
        if city is None:
            raise ExploreError("участок ищут в городе: за стенами застройки нет")
        delegate = await session.get(Node, city.node_id)
        ring = constants[R.LAND_AREA_RING1]
        plot = await world.create_node(
            session,
            key,
            "Свободный участок",
            area_m2=dice.uniform(ring.min, ring.max),
            layer=Layer.CITY,
            parent=delegate,
            planet=origin.planet,
            #: On the built-up map the plot lies where it was found: beside the
            #: very node the scout set out from (D-237).
            anchor=origin,
            properties={"участок": True, "кольцо": origin.properties.get("кольцо", 0)},
        )
        plot.owner_city_id = city.id
        await session.flush()
        return plot

    root = await _planet_root(session, origin)
    area = constants[R.EXPLORE_NODE_AREA]
    names = {SITE: "Место под город", FOREST: "Роща"}
    return await world.create_node(
        session,
        key,
        names.get(goal, "Дикий участок"),
        area_m2=dice.uniform(area.min, area.max),
        layer=Layer.PLANET,
        parent=root,
        planet=origin.planet,
        #: And it stands next to it on the map as well: sought from inside a
        #: city, the find lies beside that city, because on the planet's map
        #: the whole city is one point (D-206, D-237).
        anchor=origin,
        #: Distance grows by a step from the node we left from (D-180): the
        #: frontier recedes by itself as it is pushed.
        properties=await _properties(
            session, constants, dice, vein=vein, woods=goal == FOREST, who=who
        )
        | {travel.REACH: travel.reach_of(origin) + 1},
    )


async def _properties(
    session: AsyncSession,
    constants: Constants,
    dice: random.Random,
    *,
    vein: bool,
    woods: bool = False,
    who: uuid.UUID | None = None,
) -> dict:
    """Place properties under a common merit budget (D-126).

    There is no perfect place: a river eats part of the budget, and the more
    water, the less is left for fertility.

    Woods grow by themselves on `explore.forest_share` of finds (D-191), and
    always where the woods are what the scout went looking for: the world gets
    forested without anybody asking, and timber becomes geography.
    """

    budget = constants[R.SITE_QUALITY_BUDGET]
    #: Each of the place's signs is a chance with a memory (D-213): a scout
    #: who never once found a river is the same complaint as one who never
    #: found anything.
    river = await luck.hit(session, who, luck.SITE_RIVER, constants[R.SITE_RIVER_SHARE], dice=dice)
    for_water = dice.uniform(0, budget) if river else 0.0
    for_land = max(0.0, budget - for_water)

    temperature = constants[R.SITE_TEMP_RANGE]
    rainfall = constants[R.SITE_RAIN_RANGE]
    return {
        "вода": "река" if river else "нет",
        #: On a vein find arable land is beside the point: rock bears no bread.
        "плодородие": 0 if vein else round(PERCENT * for_land / budget),
        "температура": round(dice.uniform(temperature.min, temperature.max)),
        "осадки": round(dice.uniform(rainfall.min, rainfall.max)),
        WOODS: woods
        or await luck.hit(
            session, who, luck.SITE_WOODS, constants[R.EXPLORE_FOREST_SHARE], dice=dice
        ),
        #: Stones and meadow fall out on their own, like woods (D-196): one
        #: goes for stone and for flax in different directions.
        STONES: await luck.hit(
            session, who, luck.SITE_STONES, constants[R.EXPLORE_STONES_SHARE], dice=dice
        ),
        MEADOW: await luck.hit(
            session, who, luck.SITE_MEADOW, constants[R.EXPLORE_MEADOW_SHARE], dice=dice
        ),
        "дикий": True,
    }


async def species_of(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    dice: random.Random,
    *,
    planet: Planet = Planet.TERRA,
    who: uuid.UUID | None = None,
) -> str:
    """Which species. The list is from the vault, the weight from the mining pace (D-151).

    The rare is mined slower, so it also turns up rarer. No second rarity table:
    it would diverge from the first.

    A planet bends those weights and does not replace them (D-232): Aurora is
    generous with coal and poor in iron, and that one line is the whole economy
    of the place -- fuel underfoot, metal brought in by ship.
    """
    paces = dict(constants[R.HARVEST_RATES])
    bend: dict[str, float] = constants[R.HARVEST_PLANET_WEIGHTS].get(planet.value, {})
    for name, weight in bend.items():
        if name in paces:
            paces[name] = paces[name] * weight
    operation = next((op for op in catalog.recipes.operations if op.name == MINING_OPERATION), None)
    if_missing = "Камень"
    if operation is None:  # pragma: no cover -- the mining operation exists by construction
        return if_missing
    species = [name for name in operation.gives if float(paces.get(name, 0)) > 0]
    if not species:  # pragma: no cover
        return if_missing
    #: Dealt from a deck by the same weights (D-213): the rare stays rare, but
    #: "six iron veins and never a copper one" is no longer a thing.

    return await luck.draw(
        session,
        who,
        #: A deck per planet (D-213, D-232): the species are the same names
        #: everywhere and only the weights differ, so one shared deck would go
        #: on dealing Terra's iron on Aurora -- exactly where "coal here, iron
        #: brought in" is the first thing a player should feel.
        f"{luck.EXPLORE_SPECIES}:{planet.value}",
        {name: float(paces[name]) for name in species},
        dice=dice,
    )


async def _planet_root(session: AsyncSession, node: Node) -> Node | None:
    """The planet the node stands on: walk up the display hierarchy."""
    current = node
    while current.parent_id is not None:
        parent = await session.get(Node, current.parent_id)
        if parent is None:  # pragma: no cover
            return None
        if parent.layer is Layer.SPACE:
            return parent
        current = parent
    return None
