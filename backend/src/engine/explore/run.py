# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A run: the scout goes, the field is read, a node is born (D-321).

The run is priced by the road: aiming at a point *d* metres away costs the
walk of *d* metres over wild ground, in time and in stamina -- **one way**,
because the scout walks there and stays (D-327). A run is an occupation
(D-211) and a journal job. When the job fires the cell is **materialised**:
the field is read at its centre, the node is created with the cell's key, a
wild way is laid back to the node the scout left from -- and only there
(D-326) -- and, by the vault's chance, a whole complex is laid round it. Then
the body takes the find as the node it stands in, the way an arrival does.

A run can be turned back from (`stop`), and turning back returns the scout to
where they set out: there is no half of a way in this world (D-194).

The body stays in its origin node for the whole run all the same -- the map
draws the scout on the way, but `node_id` does not move until the job fires
(D-327 says so out loud). Real absence (D-107) wants a transit row with an end
that is not in the graph yet, and that is a piece of work, not a line. What
holds the two together meanwhile is that a scout cannot walk off: `travel.depart`
refuses a run under way (`travel-scouting`).

Nothing is rolled that the field can answer. The dice that remain are seeded
by the cell's key, so a run repeated after a failure lays the same node with
the same veins and the same stores, and two servers lay one world.
"""

from __future__ import annotations

import logging
import math
import random
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src import globe
from src.constants import Catalog, Constants, current, current_catalog
from src.constants import registry as R
from src.engine import (
    biome,
    chat,
    climate,
    craft,
    customs,
    events,
    facet,
    ground,
    memory,
    occupation,
    places,
    ruins,
    terrain,
    transport,
    travel,
    world,
)
from src.engine.explore import aim as aiming
from src.engine.explore._base import (
    Aim,
    AlreadyJoined,
    AlreadyOut,
    Cell,
    ExploreError,
    Harnessed,
    NotFromHere,
    NotOut,
    ScoutGone,
    key_of,
    point_of,
)
from src.engine.jobs import enqueue, handler
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.job import Job, JobKind, JobState
from src.models.world import ABOARD, Layer, Node, Planet, Surface
from src.units import PERCENT

log = logging.getLogger(__name__)

#: The properties a complex writes on its nodes: the scheme's role, and a ford.
ROLE = "role"
FORD = aiming.FORD_MARK
#: The sign of a vein on the node, for the map's glyph: the rows are in `vein`.
VEIN = "vein"
#: A find has no name: the map shows the sign of its kind (the owner, 2026-09-06).
NAMELESS = ""


def _wild_seconds(constants: Constants, metres: float, snow: float) -> float:
    """A run over the wild: the metres at the walk, the wild's factor and the
    season's snow where the scout sets out from (D-338)."""
    return (
        travel.walk_seconds(constants, metres)
        * float(constants[R.ROAD_WILD_MULTIPLIER])
        * travel.snow_multiplier(constants, Surface.WILD, snow)
    )


async def survey(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    target: globe.Geo,
    *,
    now: datetime | None = None,
) -> Job:
    """Send the body to explore the point: refused by the landscape, paid by the road."""
    moment = now or datetime.now(UTC)
    await travel.require_here(session, body)
    await occupation.require_free(session, body)
    origin = await session.get(Node, body.node_id)
    if origin is None:  # pragma: no cover -- a body always stands in a node
        raise NotFromHere(key="explore-not-from-here")
    if (origin.properties or {}).get(ABOARD):
        raise NotFromHere(key="explore-not-from-here")
    #: On one's own feet, and that is not a taste: a run goes over wild ground
    #: by definition, and neither the wild nor a trail carries a cart (D-157,
    #: `transport.passable`). D-185 had the convoy come along behind, from the
    #: days before that rule; leaving it would also make the run **free** --
    #: `transport.stamina_k` is nought, because a cart carries instead of legs
    #: -- and a run that costs nothing is not a walk at all (D-321 item 7).
    if await transport.harnessed(session, body) is not None:
        raise Harnessed(key="explore-harnessed")
    aim = await aiming.check(session, constants, current_catalog(), origin, target, body=body)
    if aim.existing is not None and await travel.edge_between(session, origin, aim.existing):
        #: The node's word, not its name: a find has none, and the sentence
        #: would end on a hole (`peek` says it with the same word).
        raise AlreadyJoined(
            key="explore-already-joined", node=aiming.word_of(constants, aim.existing)
        )
    #: The border is settled **before** setting out, exactly as a leg settles
    #: it (D-123, `travel.depart`): a duty that cannot be paid must refuse the
    #: run in words, not turn up when the scout is already outside the walls.
    #: Both sides are known here -- the far one is the node already standing in
    #: the cell, or, for a find still to be made, nobody's land in no city.
    from src.engine.city import lookup as town  # noqa: PLC0415 -- lazy: city reaches back here

    await customs.between(
        session,
        constants,
        current_catalog(),
        body,
        origin,
        await town.of_node(session, origin),
        None if aim.existing is None else await town.of_node(session, aim.existing),
        now=moment,
    )
    snow = climate.snow_on(constants, origin, await world.epoch(session), moment)
    seconds = _wild_seconds(constants, aim.metres, snow)
    await travel.pay_for_road(session, constants, body, seconds, moment=moment)
    started = await events.record(
        session,
        EventKind.EXPLORE_STARTED,
        actor_identity_id=body.identity_id,
        node_id=origin.id,
        cell=key_of(aim.planet, aim.cell),
        metres=round(aim.metres),
    )
    job = await enqueue(
        session,
        JobKind.EXPLORE_SURVEY,
        moment + timedelta(seconds=seconds),
        payload={
            "body": str(body.id),
            "origin": str(origin.id),
            "planet": aim.planet.value,
            "cell": list(aim.cell),
            #: When the walk began. The client draws the scout moving along
            #: the way to the find (D-327), and a share of the way needs both
            #: ends of the clock: `run_at` is the far one. Written down rather
            #: than read off the row's `created_at`, so that the share is
            #: measured against the very moment the price was reckoned from.
            "started": moment.isoformat(),
        },
        dedup_key=f"explore:{body.id}:{started.id}",
        cause_event_id=started.id,
        body_id=body.id,
    )
    if job is None:  # pragma: no cover -- `require_free` refused the second run first
        raise AlreadyOut(key="explore-already-out")
    return job


@handler(JobKind.EXPLORE_SURVEY)
async def returned(session: AsyncSession, job: Job) -> None:
    """The scout is back: the cell becomes a node, or the ground turned out taken."""
    constants = current()
    body = await session.get(Body, uuid.UUID(job.payload["body"]), with_for_update=True)
    origin = await session.get(Node, uuid.UUID(job.payload["origin"]))
    if body is None or origin is None:  # pragma: no cover -- both outlive a run
        raise ExploreError(key="explore-run-dangling", job=str(job.id))
    planet = Planet(job.payload["planet"])
    cell: Cell = (int(job.payload["cell"][0]), int(job.payload["cell"][1]))
    point = point_of(constants, planet, cell)
    #: The cell is held for the transaction before it is read: two scouts back
    #: in the same second would otherwise both find it empty, and the second
    #: would lose its run to the unique key rather than to a refusal.
    await _hold_cell(session, key_of(planet, cell))
    try:
        if body.state is not BodyState.ALIVE or body.node_id != origin.id:
            raise ScoutGone(key="explore-scout-gone")
        aim = await aiming.check(session, constants, current_catalog(), origin, point, body=body)
    except ExploreError as why:
        #: The ground was free when the scout left and is taken now -- a
        #: neighbour's find came first. The run is spent; the journal says why.
        await events.record(
            session,
            EventKind.EXPLORE_EMPTY,
            actor_identity_id=body.identity_id,
            node_id=origin.id,
            cell=key_of(planet, cell),
            why=why.key,
        )
        return
    if aim.existing is not None:
        #: Found before us: the cell is one node for the world (D-237), and the
        #: second scout brings home a way to it rather than a second node.
        await travel.connect(session, origin, aim.existing, surface=Surface.WILD)
        await memory.remember(
            session, constants, body.identity_id, [aim.existing.key], at=job.run_at
        )
        #: Walked there all the same, and the walk ends where it went (D-327):
        #: the cell had a node already, so what the run bought is the way and
        #: the standing, not a find.
        await _stand_on(session, constants, body, aim.existing, origin=origin, at=job.run_at)
        await events.record(
            session,
            EventKind.EXPLORE_FOUND,
            actor_identity_id=body.identity_id,
            node_id=aim.existing.id,
            cell=aim.existing.key,
            known=True,
            **_told(constants, aim.existing),
        )
        return
    node, scheme = await materialise(
        session, constants, current_catalog(), aim, origin, who=body.identity_id
    )
    #: The scout was there: the find is remembered like a place arrived at.
    await memory.remember(session, constants, body.identity_id, [node.key], at=job.run_at)
    await _stand_on(session, constants, body, node, origin=origin, at=job.run_at)
    await events.record(
        session,
        EventKind.EXPLORE_FOUND,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        cell=node.key,
        complex=scheme,
        known=False,
        **_told(constants, node),
    )


def _told(constants: Constants, node: Node) -> dict[str, str]:
    """What the line of a run says of the node it reached: its biome, named or
    not -- what was found is part of the record -- and besides it what
    every line of the journal names a node by (`facet.told_of`: its name, or
    the keys of its ground, never the vault's word), so that the digest names
    it in the reader's language."""
    here = biome.of_node(constants, node)
    return {**({biome.BIOME: here} if here else {}), **facet.told_of(constants, node)}


async def _stand_on(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    node: Node,
    *,
    origin: Node,
    at: datetime,
) -> None:
    """The run ends with the scout standing on the find (D-327).

    A run was always priced as the walk **one way** -- `_wild_seconds` of the
    distance, no return leg in it (D-321 item 7) -- and until 2026-09-08 the
    body paid that and stayed where it set out from, as though the walk had
    been there and back for the price of half. The owner asked for the honest
    end: "после окончания разведки игрок должен оказаться на точке которую он
    разведал". So the find is where the scout is.

    This is an arrival, and it does what `travel.arrive` does for a leg -- no
    more. The reserve is settled **before** the new node is taken, or the hours
    on the road would be counted by the warmth of the place walked to (D-231);
    the way just laid is trodden by the feet that laid it (D-319); a work
    frozen at the find wakes (D-209). What it does not do is a convoy: a scout
    goes into the wild on their own feet, and `pay_for_road` already priced
    the run without one.
    """
    #: Lazy for the same reason as in `travel.walk`: `frost` and `road` both
    #: import travel, which imports this package's neighbours in turn.
    from src.engine import frost, road  # noqa: PLC0415 -- lazy: breaks the cycles

    await frost.settle(session, constants, current_catalog(), body, now=at)
    #: Left the node -- left the conversation: the circle does not follow
    #: (D-043), and a scout a kilometre out was still talking in the workshop.
    await chat.leave_groups(session, body.identity_id)
    edge = await travel.edge_between(session, origin, node)
    body.node_id = node.id
    #: Chat horizon: before arriving the body heard nothing here (D-043).
    body.node_since = at
    await session.flush()
    if edge is not None:
        await road.tread(session, constants, edge.id, node_id=node.id)
    await craft.wake(session, body, now=at)


async def leg_of(session: AsyncSession, constants: Constants, body: Body) -> dict | None:
    """The run under way as the map draws it: where from, where to, and when (D-327).

    The scout walks to a point that is **not a node yet**, so this leg cannot
    be said the way a road is -- with two keys. Its far end is a place, and
    nothing already on the wire lets the client work it out (D-225): the cell
    is the engine's lattice, and after a reload the client does not even know
    what was aimed at.

    Where the scout is right now is not sent and must not be: that is the two
    stamps and a straight line, and the client draws it frame by frame (D-226).
    """
    job = await session.scalar(
        select(Job)
        .where(
            Job.body_id == body.id,
            Job.kind == JobKind.EXPLORE_SURVEY.value,
            Job.state.in_((JobState.PENDING, JobState.RUNNING)),
        )
        .limit(1)
    )
    if job is None:
        return None
    origin = await session.get(Node, uuid.UUID(job.payload["origin"]))
    lat, lon = point_of(constants, Planet(job.payload["planet"]), tuple(job.payload["cell"]))
    return {
        "from_key": "" if origin is None else origin.key,
        "place": {"lat": lat, "lon": lon},
        #: A run begun before the stamp was written down (D-327) still draws:
        #: the row's own birthday is the same moment to within the transaction.
        "started_at": str(job.payload.get("started") or job.created_at.isoformat()),
        "arrives_at": job.run_at.isoformat(),
    }


async def stop(session: AsyncSession, body: Body, *, now: datetime | None = None) -> Job:
    """Turn back from a run: the scout stays where they set out from (D-327).

    The road's rule, word for word (`travel.turn_back`, D-194): there is no
    half of a way in this world -- a node is the unit of place -- so cancelling
    returns rather than stops midway. What was spent is not returned: the
    stamina went up front and the hours have passed.

    The cell is not touched. It was never held: two scouts may aim at one cell
    and the second brings home a way rather than a second node (D-321 item 3),
    so a run given up leaves nothing behind to release.

    The row is taken `FOR UPDATE SKIP LOCKED`, and that settles the one race
    here: a turn-back sent in the very second the job fires. A locked row is a
    run the worker is **already running** (`jobs._claim` holds it for the whole
    of the handler's transaction), and there is nothing left to turn back from,
    so skipping it is the right answer and not merely the cheap one. Waiting
    for the lock instead would be worse than slow: this command already holds
    the body's row (`_alive`) and the handler takes the body's row after the
    job's, so the two would meet head to head and one would die of a deadlock
    rather than of a refusal. The other order needs nothing: the worker's claim
    skips a locked row, and by the time it looks again the state is `cancelled`.
    """
    moment = now or datetime.now(UTC)
    job = await session.scalar(
        select(Job)
        .where(
            Job.body_id == body.id,
            Job.kind == JobKind.EXPLORE_SURVEY.value,
            Job.state.in_((JobState.PENDING, JobState.RUNNING)),
        )
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job is None:
        raise NotOut(key="explore-not-out")
    job.state = JobState.CANCELLED
    job.finished_at = moment
    await session.flush()
    await events.record(
        session,
        EventKind.EXPLORE_STOPPED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        cell=key_of(Planet(job.payload["planet"]), tuple(job.payload["cell"])),
    )
    return job


async def _hold_cell(session: AsyncSession, key: str) -> None:
    """Take the cell for the transaction: an advisory lock on its key, cheap and
    released with the commit (`places._hold` does the same for a planet)."""
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": key})


async def _sphere_of(session: AsyncSession, planet: Planet) -> Node | None:
    return await session.scalar(
        select(Node).where(Node.key == planet.value, Node.layer == Layer.SPACE)
    )


async def _found_node(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    dice: random.Random,
    planet: Planet,
    cell: Cell,
    point: globe.Geo,
    *,
    sphere: Node | None,
    area: float,
    extra: dict | None = None,
    vein: bool | None = None,
    who: uuid.UUID | None,
) -> Node:
    """One node of the surface out of the field: properties read at the point,
    the biome's marks and swing on it, a vein by the biome's chance.

    **Nameless** (the owner, 2026-09-06): a find is known by the sign of its
    kind on the map -- the vein, the river, the forest, the desert -- not by a
    word; the word is the locale's, drawn by the client off the signs. What
    the engine writes is the signs, and `vein` is one of them.
    """
    here = biome.classify(constants, planet, *point)
    if here is None:  # pragma: no cover -- `aim.check` refused water already
        raise ExploreError(key="explore-not-land")
    field = terrain.field_of(constants, planet)
    #: The face this ground wears (landscape plan, wave 7): read once here and
    #: written on the node, so the thicket a find was made in stays a thicket.
    face = facet.at(constants, catalog, planet, *point, here=here)
    if vein is None:
        #: The biome's chance, times the facet's and the province's (waves 3
        #: and 7): an ore ridge gives colour every second pit, a rotten lowland
        #: never, and a boulder field oftener than the meadow beside it.
        chance = (
            float(constants[R.GROUND_VEIN_SHARE])
            / PERCENT
            * facet.vein_k(constants, here, face)
            * field.province_vein_k_at(*point)
        )
        vein = dice.random() < chance
    properties = await ground.properties(
        session,
        constants,
        dice,
        vein=vein,
        at=(planet, point),
        shares=facet.marks(constants, here, face),
    )
    properties |= {
        biome.BIOME: here,
        biome.TEMPERATURE_SWING: facet.swing_c(constants, here, face),
        places.PLACE: {places.PLACE_LAT: point[0], places.PLACE_LON: point[1]},
    }
    if face is not None:
        properties[facet.FACET] = face.id
    #: The province, stamped once like the biome (D-237): the field's word
    #: at the point, and only where the planet has provinces at all.
    province = field.province_at(*point)
    if province:
        properties[biome.PROVINCE] = province
    if vein:
        properties[VEIN] = True
    if extra:
        properties |= extra
    node = await world.create_node(
        session,
        key_of(planet, cell),
        NAMELESS,
        planet=planet,
        area_m2=area,
        layer=Layer.PLANET,
        parent=sphere,
        properties=properties,
    )
    if vein:
        richness = constants[R.GROUND_VEIN_RICHNESS]
        stock = constants[R.GROUND_VEIN_STOCK]
        await world.create_vein(
            session,
            node,
            await ground.species_of(session, constants, catalog, dice, planet=planet, who=who),
            richness=dice.uniform(richness.min, richness.max),
            remaining=dice.uniform(stock.min, stock.max),
        )
    await session.flush()
    return node


async def materialise(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    aim: Aim,
    origin: Node,
    *,
    who: uuid.UUID | None,
) -> tuple[Node, str | None]:
    """The cell becomes a node of the world, with its ways and -- by chance -- its complex.

    Returns the node and the name of the complex laid round it, if any.
    """
    planet = aim.planet
    dice = random.Random(key_of(planet, aim.cell))
    sphere = await _sphere_of(session, planet)
    node = await _found_node(
        session,
        constants,
        catalog,
        dice,
        planet,
        aim.cell,
        aim.point,
        sphere=sphere,
        area=aim.area,
        who=who,
    )
    #: One way, and it is the one the scout walked (D-326). The find used to be
    #: sewn to every neighbour within reach as well -- `knit`, D-321's "the
    #: graph is sewn, not grown as a thread" -- and two points scouted apart
    #: joined themselves with a road nobody had made. A road is somebody's
    #: labour, like the node itself: the scout spends time and a place appears,
    #: and a way appears the same way or not at all.
    await travel.connect(session, origin, node, surface=Surface.WILD)
    scheme = await _complex(session, constants, catalog, dice, node, aim.point, who=who)
    return node, scheme


def _schemes_for(constants: Constants, planet: Planet, here: str) -> dict[str, dict]:
    schemes: dict[str, dict] = constants[R.COMPLEX_SCHEMES]
    return {
        name: scheme
        for name, scheme in schemes.items()
        if scheme.get("planet") == planet.value and scheme.get("biome") == here
    }


async def _complex(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    dice: random.Random,
    node: Node,
    point: globe.Geo,
    *,
    who: uuid.UUID | None,
) -> str | None:
    """By the vault's chance, a scheme of nodes round the find (D-321, point 6).

    The chance is rolled by dice of its own, seeded by the cell alone
    (`complex_roll`): whether a cell hides a complex is a fact of the map that
    does not depend on how many dice the node's own properties happened to
    use, and a test can ask it before the run.
    """
    here = str((node.properties or {}).get(biome.BIOME) or "")
    chance = float(constants[R.COMPLEX_CHANCE].get(node.planet.value, {}).get(here, 0)) / PERCENT
    if chance <= 0 or complex_roll(node.key) >= chance:
        return None
    offered = _schemes_for(constants, node.planet, here)
    if not offered:
        return None
    names = sorted(offered)
    picked = dice.choices(names, weights=[float(offered[n].get("weight", 1)) for n in names])[0]
    scheme = offered[picked]
    if scheme.get("city"):
        where = _beside(constants, catalog, node, point, 0)
        try:
            city_aim = await aiming.check(session, constants, catalog, node, where)
        except ExploreError:
            return None
        if city_aim.existing is not None:
            return None
        pier = await ruins.lost_city(session, constants, node, who=who, at=where)
        await ruins.open_all(session, constants, pier, dice)
        return picked
    sphere = await _sphere_of(session, node.planet)
    for number, part in enumerate(scheme.get("nodes", [])):
        where = _beside(constants, catalog, node, point, number)
        cell = aiming.cell_of(constants, node.planet, where)
        where = point_of(constants, node.planet, cell)
        try:
            part_aim = await aiming.check(session, constants, catalog, node, where)
        except ExploreError:
            #: The scheme yields to the ground: a part with no room is not laid.
            continue
        if part_aim.existing is not None:
            #: Somebody's find already stands in the cell: the scheme joins it.
            await travel.connect(session, node, part_aim.existing, surface=Surface.WILD)
            continue
        extra: dict = {ROLE: str(part.get("role", ""))}
        if part.get("water"):
            extra[world.WATER] = str(part["water"])
        if part.get("woods"):
            extra[ground.WOODS] = True
        if part.get("ford"):
            extra[FORD] = True
        member = await _found_node(
            session,
            constants,
            catalog,
            dice,
            node.planet,
            cell,
            where,
            sphere=sphere,
            area=part_aim.area,
            extra=extra,
            vein=bool(part.get("vein", False)) or None,
            who=who,
        )
        await travel.connect(session, node, member, surface=Surface.WILD)
        if part.get("finds"):
            await ruins.stock(session, constants, dice, member, str(part["finds"]), who=who)
    return picked


def complex_roll(key: str) -> float:
    """The cell's own roll for a complex, in [0, 1): the same for the world and for a test."""
    return random.Random(f"{key}:complex").random()


def _beside(
    constants: Constants, catalog: Catalog, node: Node, point: globe.Geo, number: int
) -> globe.Geo:
    """Where the number-th part of a scheme stands: a fan round the find, one
    reach out, so the parts are neighbours and not a heap: the middle of the
    ring a part may lawfully take, from where the find's own land and the
    room a part needs end (`facet.room_m`) out to the find's far reach
    (`facet.band_m`). The middle of the band from the centre put a wide
    find's parts inside itself, and a narrow one's short of the room -- the
    room rule refused them and the scheme was laid without them."""
    here = str((node.properties or {}).get(biome.BIOME) or "")
    near, far = facet.band_m(constants, catalog, node, here)
    clear = aiming.radius_of(float(node.area_m2)) + facet.room_m(constants)
    step = globe.midpoint(max(near, clear), far)
    radius = globe.radius_m(constants, node.planet)
    angle = globe.GOLDEN_ANGLE * (number + 1)
    return globe.offset(radius, point, step * math.cos(angle), step * math.sin(angle))
