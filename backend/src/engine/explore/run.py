# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A run: the scout goes, the field is read, a node is born (D-321).

The run is priced by the road: aiming at a point *d* metres away costs the
walk of *d* metres over wild ground, in time and in stamina, and the body
stands in its node meanwhile -- a run is an occupation (D-211) and a journal
job. When the job fires the cell is **materialised**: the field is read at
its centre, the node is created with the cell's key, a wild way is laid back
to the node the scout left from and to every found node the biome's reach
allows with a clear line, and -- by the vault's chance -- a whole complex is
laid round it.

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
from src.engine import biome, events, ground, memory, occupation, places, ruins, travel, world
from src.engine.explore import aim as aiming
from src.engine.explore._base import (
    Aim,
    AlreadyJoined,
    AlreadyOut,
    Cell,
    ExploreError,
    NotFromHere,
    ScoutGone,
    key_of,
    point_of,
)
from src.engine.jobs import enqueue, handler
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.job import Job, JobKind
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


def _wild_seconds(constants: Constants, metres: float) -> float:
    return travel.walk_seconds(constants, metres) * float(constants[R.ROAD_WILD_MULTIPLIER])


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
    aim = await aiming.check(session, constants, origin, target)
    if aim.existing is not None and await travel.edge_between(session, origin, aim.existing):
        raise AlreadyJoined(key="explore-already-joined", node=aim.existing.name)
    seconds = _wild_seconds(constants, aim.metres)
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
        aim = await aiming.check(session, constants, origin, point)
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
        await events.record(
            session,
            EventKind.EXPLORE_FOUND,
            actor_identity_id=body.identity_id,
            node_id=aim.existing.id,
            node=aiming.word_of(constants, aim.existing),
            cell=aim.existing.key,
            biome=biome.of_node(constants, aim.existing),
            known=True,
        )
        return
    node, scheme = await materialise(
        session, constants, current_catalog(), aim, origin, who=body.identity_id
    )
    #: The scout was there: the find is remembered like a place arrived at.
    await memory.remember(session, constants, body.identity_id, [node.key], at=job.run_at)
    await events.record(
        session,
        EventKind.EXPLORE_FOUND,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        node=aiming.word_of(constants, node),
        cell=node.key,
        biome=(node.properties or {}).get(biome.BIOME),
        complex=scheme,
        known=False,
    )


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
    if vein is None:
        chance = float(constants[R.GROUND_VEIN_SHARE]) / PERCENT * biome.vein_k(constants, here)
        vein = dice.random() < chance
    properties = await ground.properties(
        session,
        constants,
        dice,
        vein=vein,
        at=(planet, point),
        shares=biome.marks(constants, here),
    )
    properties |= {
        biome.BIOME: here,
        biome.TEMPERATURE_SWING: biome.swing_c(constants, here),
        places.PLACE: {places.PLACE_LAT: point[0], places.PLACE_LON: point[1]},
    }
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
    await travel.connect(session, origin, node, surface=Surface.WILD)
    await knit(session, constants, node, aim.point, except_for={origin.id})
    scheme = await _complex(session, constants, catalog, dice, node, aim.point, who=who)
    return node, scheme


async def knit(
    session: AsyncSession,
    constants: Constants,
    node: Node,
    point: globe.Geo,
    *,
    except_for: set[uuid.UUID],
) -> None:
    """Ways from a found node to every found neighbour within the biome's reach
    that a straight, dry, uncrossed line joins: the graph is sewn, not grown as a thread."""
    here = biome.of_node(constants, node)
    if here is None:  # pragma: no cover
        return
    _, far = biome.reach_m(constants, here)
    radius = globe.radius_m(constants, node.planet)
    for other, where in await aiming._surface(session, constants, node.planet, point):
        if other.id == node.id or other.id in except_for:
            continue
        if globe.distance_m(radius, where, point) > far:
            continue
        if aiming.crosses_water(constants, node.planet, point, where, ford=aiming._is_ford(node)):
            continue
        try:
            await aiming.check(session, constants, other, point)
        except ExploreError:
            continue
        await travel.connect(session, node, other, surface=Surface.WILD)


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
        where = _beside(constants, node, point, 0)
        try:
            city_aim = await aiming.check(session, constants, node, where)
        except ExploreError:
            return None
        if city_aim.existing is not None:
            return None
        pier = await ruins.lost_city(session, constants, node, who=who, at=where)
        await ruins.open_all(session, constants, pier, dice)
        return picked
    sphere = await _sphere_of(session, node.planet)
    for number, part in enumerate(scheme.get("nodes", [])):
        where = _beside(constants, node, point, number)
        cell = aiming.cell_of(constants, node.planet, where)
        where = point_of(constants, node.planet, cell)
        try:
            part_aim = await aiming.check(session, constants, node, where)
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


def _beside(constants: Constants, node: Node, point: globe.Geo, number: int) -> globe.Geo:
    """Where the number-th part of a scheme stands: a fan round the find, one
    reach out, so the parts are neighbours and not a heap."""
    here = str((node.properties or {}).get(biome.BIOME) or "")
    near, far = biome.reach_m(constants, here)
    step = globe.midpoint(near, far)
    radius = globe.radius_m(constants, node.planet)
    angle = globe.GOLDEN_ANGLE * (number + 1)
    return globe.offset(radius, point, step * math.cos(angle), step * math.sin(angle))
