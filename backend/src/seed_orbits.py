# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The one-off catch-up of D-354: a planet's orbital node goes.

Until D-354 every planet had a hidden node above it, marked `orbit_node`
and named after the planet's orbit, and a hull in orbit was moored to it by
a gangway, on an analytic circle. A world laid before keeps those nodes;
this step takes them away and finds a place for what they held:

* a hull moored there is put into orbit -- a state in the sky -- over the
  meridian of the pier it last left on this planet, as a climb puts one
  there now, and over the planet's nought meridian if it came from another;
* a walk under way across a gangway to or from the node ends at its end
  aboard, and its arrival job with it;
* a body standing there goes aboard its own hull moored there, or the
  first hull moored there; with none it dies of the void, as D-245 said a
  body left there would;
* a thing lying there goes aboard with it, or is gone with the node;
* the journal of walks there, the talk and the circles once talked in
  there, and the lists of who may enter go with the node; a post written
  there is remembered as written over the planet;
* a leg bound there -- a climb, a descent turned back -- is bound for the
  planet instead.

Idempotent by reading the world: a world with no orbital node left has
nothing to do, and a world laid today never had one. Anything else found
still pointing at such a node stops the seed with its name: that is a world
this step was not written for, and a guess would lose what it holds.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src import sky
from src.constants import Constants
from src.db.base import Base
from src.engine import death, travel
from src.engine.oxygen import ASPHYXIA
from src.engine.ship import fate, sim
from src.engine.ship.flight import meridian
from src.engine.ship.physics import sky_days
from src.engine.world.gone import destroy
from src.engine.world.things import node_container, node_things, node_yard
from src.models.chat import ChatGroup, ChatMessage
from src.models.identity import Body
from src.models.job import Job, JobKind, JobState
from src.models.net import NetPost
from src.models.ship import Ship
from src.models.travel import Travel, TravelState
from src.models.world import Edge, Node, NodePass

log = logging.getLogger(__name__)

#: The mark the seed laid on a planet's orbital node before D-354
#: (`ship.ORBIT_NODE` then). Read here alone, to find the nodes going away.
_ORBIT_MARK = "orbit_node"


async def orbits_gone(
    session: AsyncSession, constants: Constants, *, now: datetime | None = None
) -> dict[str, int]:
    """Take every orbital node out of the world, and put what it held
    somewhere true. Returns how much of each there was, for the log."""
    moment = now or datetime.now(UTC)
    orbits = (
        (
            await session.execute(
                select(Node).where(Node.properties.contains({_ORBIT_MARK: True})).order_by(Node.key)
            )
        )
        .scalars()
        .all()
    )
    report = {"orbits": 0, "hulls": 0, "aboard": 0, "died": 0, "things": 0, "legs": 0}
    if not orbits:
        return report
    gone = {orbit.id: orbit for orbit in orbits}
    await _legs_bound_there(session, gone, report)
    for orbit in orbits:
        await _empty(session, constants, orbit, now=moment, report=report)
    #: A hull that last left an orbit remembers the node as the pier it
    #: left (D-242): the memory goes with the node, and a turn-back finds
    #: nowhere rather than a node that is not there.
    for hull in (
        (await session.execute(select(Ship).where(Ship.left_node_id.in_(list(gone)))))
        .scalars()
        .all()
    ):
        hull.left_node_id = None
    await session.flush()
    for orbit in orbits:
        await _still_pointed_at(session, orbit)
        await session.delete(orbit)
        report["orbits"] += 1
    await session.flush()
    log.info("orbital nodes taken out of the world (D-354): %s", report)
    return report


async def _empty(
    session: AsyncSession,
    constants: Constants,
    orbit: Node,
    *,
    now: datetime,
    report: dict[str, int],
) -> None:
    hulls = (
        (
            await session.execute(
                select(Ship).where(Ship.docked_node_id == orbit.id).order_by(Ship.id)
            )
        )
        .scalars()
        .all()
    )
    refuge = None if not hulls else await session.get(Node, hulls[0].connector_node_id)

    #: Walks under way across a gangway end at their end aboard -- the only
    #: end that will still be there.
    walking = (
        (
            await session.execute(
                select(Travel).where(
                    Travel.state == TravelState.GOING,
                    or_(Travel.from_node_id == orbit.id, Travel.to_node_id == orbit.id),
                )
            )
        )
        .scalars()
        .all()
    )
    for leg in walking:
        body = await session.get(Body, leg.body_id)
        aboard = leg.to_node_id if leg.from_node_id == orbit.id else leg.from_node_id
        if body is not None:
            body.node_id = aboard
        leg.state = TravelState.ARRIVED
        leg.arrived_at = now
        leg.plan = None
    #: And their arrival jobs: the legs are gone with the node below, and a
    #: job left for one fails on it (`travel-job-no-leg`) and is retried.
    if walking:
        for job in (
            (
                await session.execute(
                    select(Job).where(
                        Job.kind == JobKind.TRAVEL_LEG.value,
                        Job.state.in_((JobState.PENDING, JobState.RUNNING)),
                        Job.payload["travel"].astext.in_([str(leg.id) for leg in walking]),
                    )
                )
            )
            .scalars()
            .all()
        ):
            job.state = JobState.DONE
            job.finished_at = now
    await session.flush()

    #: Bodies standing in the void: aboard the first hull that was there, or
    #: dead of it. The dead ones lying there are carried along with them.
    standing = (
        (await session.execute(select(Body).where(Body.node_id == orbit.id).order_by(Body.id)))
        .scalars()
        .all()
    )
    for body in standing:
        #: Its own hull first: a body out on the node came off one, and the
        #: one it owns is the likeliest; failing that, the first there --
        #: aboard a stranger's hull rather than dead of a node taken away.
        own = next((hull for hull in hulls if hull.owner_identity_id == body.identity_id), None)
        haven = refuge if own is None else await session.get(Node, own.connector_node_id)
        if haven is not None:
            body.node_id = haven.id
            report["aboard"] += body.died_at is None
            continue
        if body.died_at is None:
            await death.die(session, constants, body, cause=ASPHYXIA, now=now)
            report["died"] += 1
    await session.flush()
    #: Whatever is left pointing at the void -- a body dead here before, or
    #: one just now -- lies on the planet's own node: the void has no floor.
    sphere = None if orbit.parent_id is None else await session.get(Node, orbit.parent_id)
    if sphere is not None:
        for body in (
            (await session.execute(select(Body).where(Body.node_id == orbit.id))).scalars().all()
        ):
            body.node_id = sphere.id

    #: Things lying there: aboard with the bodies, or gone with the node.
    lying = await node_things(session, orbit)
    if lying:
        if refuge is not None:
            hold = await node_container(session, refuge)
            for thing in lying:
                thing.container_id = hold.id
                thing.outdoors = False
        else:
            await destroy(session, list(lying))
        report["things"] += len(lying)
    yard = await node_yard(session, orbit)
    if yard is not None:
        await session.delete(yard)

    #: The hulls: off the gangway and into the sky.
    world_sky = await sim.system(session, constants)
    for hull in hulls:
        connector = await session.get(Node, hull.connector_node_id)
        if connector is not None:
            await travel.disconnect(session, orbit, connector)
        hull.docked_node_id = None
        hull.berth = None
        if sphere is None or sphere.planet.value not in {one.key for one in world_sky.bodies}:
            continue
        body = world_sky.body(sphere.planet.value)
        pier = None if hull.left_node_id is None else await session.get(Node, hull.left_node_id)
        if pier is not None and (
            pier.planet != sphere.planet or (pier.properties or {}).get(_ORBIT_MARK)
        ):
            #: A hull that came from another world last left a pier there, or
            #: an orbital node: no meridian of this planet to hang over, so
            #: its own nought meridian.
            pier = None
        t = await sky_days(session, now)
        angle = await meridian(session, constants, body, pier, t=t, at=now)
        r, v = sky.parking(world_sky, body, t, angle)
        here = (float(r[0, 0]), float(r[0, 1]))
        speed = (float(v[0, 0]), float(v[0, 1]))
        await sim.into_orbit(session, hull, sphere, r=here, v=speed, now=now)
        verdict = await fate.book_loss(
            session, constants, hull, world_sky, now=now, t=t, r=here, v=speed
        )
        sim._keep_forecast(hull, verdict, now=now, t=t)
        report["hulls"] += 1

    #: The record of the place: walks that went there, circles talked in.
    await session.execute(
        delete(Travel).where(or_(Travel.from_node_id == orbit.id, Travel.to_node_id == orbit.id))
    )
    await session.execute(delete(ChatGroup).where(ChatGroup.node_id == orbit.id))
    await session.execute(delete(ChatMessage).where(ChatMessage.node_id == orbit.id))
    await session.execute(delete(NodePass).where(NodePass.node_id == orbit.id))
    if sphere is not None:
        await session.execute(
            update(NetPost).where(NetPost.node_id == orbit.id).values(node_id=sphere.id)
        )
    await session.execute(
        delete(Edge).where(or_(Edge.node_a_id == orbit.id, Edge.node_b_id == orbit.id))
    )


async def _legs_bound_there(
    session: AsyncSession, gone: dict[uuid.UUID, Node], report: dict[str, int]
) -> None:
    """A climb in the air, or a descent turned back, bound for an orbital node:
    bound for the planet instead, over the pier the hull left (D-354)."""
    legs = (
        (
            await session.execute(
                select(Job).where(
                    Job.kind == JobKind.SHIP_FLIGHT,
                    Job.state.in_((JobState.PENDING, JobState.RUNNING)),
                )
            )
        )
        .scalars()
        .all()
    )
    for job in legs:
        to = job.payload.get("to")
        orbit = None if to is None else gone.get(uuid.UUID(str(to)))
        if orbit is None or orbit.parent_id is None:
            continue
        hull = await session.get(Ship, uuid.UUID(str(job.payload["ship"])))
        payload = dict(job.payload)
        payload["to"] = str(orbit.parent_id)
        if (
            hull is not None
            and hull.left_node_id is not None
            and hull.left_node_id not in gone
            and "over" not in payload
        ):
            #: Over the pier it left; a descent turned back left the orbit
            #: itself, which is going, and hangs over the planet's own
            #: nought meridian instead.
            payload["over"] = str(hull.left_node_id)
        job.payload = payload
        report["legs"] += 1
    await session.flush()


async def _still_pointed_at(session: AsyncSession, orbit: Node) -> None:
    """Refuse to delete a node something the step does not know still points
    at: every foreign key to `node.id`, asked by name."""
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            if not any(key.column.table.name == "node" for key in column.foreign_keys):
                continue
            children = await session.scalar(
                select(func.count()).select_from(table).where(column == orbit.id)
            )
            if children:
                raise RuntimeError(
                    f"orbital node {orbit.key} is still pointed at by "
                    f"{table.name}.{column.name} ({children}): the catch-up that "
                    "takes the orbital nodes away does not know what to do with it"
                )
