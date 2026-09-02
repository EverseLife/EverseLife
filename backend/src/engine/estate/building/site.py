# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The construction site: materials by contribution, the house by time and body (D-266).

A house used to be bought in one motion -- every material of the bill written
off the builder's hands at once (`construct`) -- and nothing bigger than a
shed fit in those hands: a house of two hundred metres on three floors is
thousands of units of stone and iron against a carry limit of thirty to a
hundred and ten kilograms. The playtest of 2026-09-02 ran into that wall on
the first big house.

So the build goes in three phases, and matter is carried by parts:

* **lay** -- the owner names the footprint, the floors and the type, as
  before, but brings nothing; the ground is spoken for from this moment;
* **contribute** -- anybody standing here brings any amount of any material
  on the bill, in as many trips as it takes; the site never takes more than
  the bill says, under its own row lock;
* **start** -- when every line of the bill is full, the owner starts the
  build: the term is the old one, and the body pays `build.start_stamina_per_m2`
  for every metre of usable area;
* **finish** -- the term out, the site stands ready and the owner raises the
  house by hand. A job ripens the site; it never raises the house itself.

The one-motion `construct` stays the city's door (D-248): a city order is
built of the treasury's materials, and there is nothing for the builder to
carry.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import craft, events, goods, travel, world
from src.engine.estate._base import EstateError, NoRoom, TooSmall, storey_of
from src.engine.estate.building.build import (
    bill,
    build_minutes,
    composition,
    kinds,
    raise_house,
)
from src.engine.estate.building.frame import free_ground, hold_ground, planned_footprint
from src.engine.jobs import enqueue
from src.models.estate import Building, BuildSite, SiteState
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.job import Job, JobKind
from src.models.world import Node, Planet
from src.units import AMOUNT_SCALE

#: Below a thousandth a gap in the bill is rounding, not a missing brick.
_DUST = 1 / AMOUNT_SCALE


class SiteError(EstateError):
    """The site refuses: the wrong phase, the wrong hands, the wrong thing brought."""


class NoStrength(EstateError):
    """The owner has not the stamina the build takes to start."""


async def sites_of(session: AsyncSession, node: Node) -> list[BuildSite]:
    """The sites of this plot that still hold ground: everything but the finished."""
    rows = await session.execute(
        select(BuildSite)
        .where(BuildSite.node_id == node.id, BuildSite.state != SiteState.DONE)
        .order_by(BuildSite.laid_at)
    )
    return list(rows.scalars().all())


async def site_of(session: AsyncSession, site_id: uuid.UUID) -> BuildSite:
    site = await session.get(BuildSite, site_id)
    if site is None:
        raise SiteError(key="estate-site-nowhere", site=str(site_id))
    return site


def short_of(site: BuildSite) -> dict[str, float]:
    """What the bill still waits for, goods key to units. Empty -- the site is full."""
    gaps: dict[str, float] = {}
    for name, need in site.needed.items():
        gap = float(need) - float(site.brought.get(name, 0.0))
        if gap > _DUST:
            gaps[name] = gap
    return gaps


async def _at(session: AsyncSession, body: Body, site: BuildSite) -> Node:
    """The body stands alive at the site's plot -- every phase begins so."""
    if body.state is not BodyState.ALIVE:
        raise EstateError(key="estate-build-dead")
    await travel.require_here(session, body)
    if body.node_id != site.node_id:
        raise SiteError(key="estate-site-not-here")
    node = await session.get(Node, site.node_id)
    if node is None:  # pragma: no cover -- a site without a plot is a bug
        raise SiteError(key="estate-site-nowhere", site=str(site.id))
    return node


async def lay(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    node: Node,
    area: float,
    *,
    floors: int = 1,
    kind: str | None = None,
    now: datetime | None = None,
) -> BuildSite:
    """Lay out a site on your own plot -- or on nobody's land (D-198).

    The same rules the one-motion build had for the ground and the height
    (D-218, D-247), and none for the hands: nothing is brought yet. The
    footprint is spent from the plot here, under the plot's lock, so that
    a second house cannot be laid on ground this one has been promised.
    """
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        raise EstateError(key="estate-build-dead")
    await travel.require_here(session, body)
    if body.node_id != node.id:
        raise EstateError(key="estate-build-on-foot")
    if storey_of(node) is not None:
        raise EstateError(key="estate-build-not-on-storey")
    nobodys = node.owner_identity_id is None and node.owner_city_id is None
    if not nobodys and node.owner_identity_id != body.identity_id:
        raise EstateError(key="estate-build-not-yours")
    if floors < 1:
        raise EstateError(key="estate-build-no-floors")
    if node.planet is Planet.PYROXIS:
        raise EstateError(key="estate-build-not-on-pyroxis")
    kind = kind or kinds(constants)[0]
    composition(constants, kind)
    smallest = constants[R.BUILD_AREA_MIN]
    if area < smallest:
        raise TooSmall(key="estate-build-too-small", smallest=smallest, area=area)

    await hold_ground(session, node)
    free = await free_ground(session, node)
    if area > free:
        going = await planned_footprint(session, node)
        raise NoRoom(
            key="estate-build-no-room",
            plot=float(node.area_m2),
            free=max(free, 0),
            started="true" if going > 0 else "false",
            going=going,
            area=area,
        )

    needed = bill(constants, footprint=area, floors=floors, kind=kind)
    site = BuildSite(
        node_id=node.id,
        owner_identity_id=body.identity_id,
        footprint_m2=area,
        floors=floors,
        kind=kind,
        needed={name: float(value) for name, value in needed.items()},
        brought={},
        state=SiteState.GATHERING,
        laid_at=moment,
    )
    session.add(site)
    await session.flush()
    await events.record(
        session,
        EventKind.ESTATE_SITE_LAID,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        site=str(site.id),
        area=area,
        floors=floors,
        built_of=kind,
        needed=site.needed,
    )
    return site


async def contribute(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    site: BuildSite,
    goods_key: str,
    quantity: float,
    *,
    tier: str | None = None,
) -> float:
    """Bring a material from the hands to the site. Returns what the site took.

    Anybody standing here may bring; the site takes no more than the bill
    still wants of that thing, and a counted thing goes in whole pieces
    (D-212). The row is taken before the gap is read: two contributions at
    once must not both fill the last of it.
    """
    node = await _at(session, body, site)
    await session.refresh(site, with_for_update=True)
    if site.state is not SiteState.GATHERING:
        raise SiteError(key="estate-site-not-gathering")
    name = catalog.recipes.resolve(goods_key)
    if name not in site.needed:
        raise SiteError(key="estate-site-not-needed", goods=name)
    left = float(site.needed[name]) - float(site.brought.get(name, 0.0))
    if left <= _DUST:
        raise SiteError(key="estate-site-material-full", goods=name)
    take = goods.whole(name, min(float(quantity), left), catalog=catalog)
    if take <= 0:
        raise SiteError(key="estate-site-nothing-to-add", goods=name)

    #: The body's row before its stacks are read (CLAUDE.md): a contribution
    #: and a drop, or two contributions to two sites, must not both write off
    #: the same stack. The site's row is already held -- site, then body,
    #: the same order `start` takes.
    await session.execute(select(Body.id).where(Body.id == body.id).with_for_update())
    pocket = await world.body_container(session, body)
    #: Which stacks go into the wall is the bringer's choice by tier (D-058).
    stock = await craft._stock(  # noqa: SLF001
        session, pocket, (name,), tiers={name: tier} if tier else None
    )
    for pick in craft._pick(stock, {name: take}):  # noqa: SLF001
        if pick.item.amount > pick.take:
            pick.item.amount -= pick.take
        else:
            await session.delete(pick.item)
    #: A new dict, not a key set in place: the JSON column notices the
    #: former and not the latter.
    site.brought = {**site.brought, name: float(site.brought.get(name, 0.0)) + take}
    await session.flush()
    await events.record(
        session,
        EventKind.ESTATE_SITE_CONTRIBUTED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        site=str(site.id),
        type_key=name,
        amount=take,
        brought=site.brought[name],
        needed=float(site.needed[name]),
        #: The owner is a party to the contribution: told of it wherever they are.
        owner_identity_id=str(site.owner_identity_id),
    )
    return take


def start_stamina(constants: Constants, site: BuildSite) -> float:
    """What the owner's body pays to start: per metre of usable area."""
    return constants[R.BUILD_START_STAMINA_PER_M2] * float(site.footprint_m2) * site.floors


async def start(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    site: BuildSite,
    *,
    now: datetime | None = None,
) -> Job:
    """The bill is full: the owner starts the build with time and the body.

    The term is the one the one-motion build had (`build_minutes`); the
    stamina is the site's own price. Both are read and spent under the rows'
    locks -- the site's, so that one start is one start; the body's, so that
    two works cannot spend the same strength.
    """
    moment = now or datetime.now(UTC)
    node = await _at(session, body, site)
    await session.refresh(site, with_for_update=True)
    if site.owner_identity_id != body.identity_id:
        raise SiteError(key="estate-site-not-yours")
    if site.state is not SiteState.GATHERING:
        raise SiteError(key="estate-site-not-gathering")
    gaps = short_of(site)
    if gaps:
        name, short = next(iter(gaps.items()))
        raise SiteError(key="estate-site-short", goods=name, short=short)

    cost = start_stamina(constants, site)
    await session.refresh(body, with_for_update=True)
    have = float(body.stamina)
    if have < cost:
        raise NoStrength(key="estate-site-no-strength", need=cost, have=have)
    body.stamina -= Decimal(str(cost))

    footprint = float(site.footprint_m2)
    minutes = build_minutes(constants, footprint=footprint, floors=site.floors, kind=site.kind)
    term = moment + timedelta(minutes=minutes)
    event = await events.record(
        session,
        EventKind.ESTATE_SITE_STARTED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        site=str(site.id),
        area=footprint,
        floors=site.floors,
        built_of=site.kind,
        stamina=cost,
        ready_at=term.isoformat(),
    )
    job = await enqueue(
        session,
        JobKind.BUILD_FINISH,
        term,
        payload={
            "node": str(node.id),
            "site": str(site.id),
            "area": footprint,
            "floors": site.floors,
            "kind": site.kind,
            "identity": str(body.identity_id),
        },
        dedup_key=f"site:{site.id}",
        cause_event_id=event.id,
        body_id=body.id,
    )
    if job is None:  # pragma: no cover -- the key is unique per site
        raise SiteError(key="estate-site-already-started")
    site.state = SiteState.BUILDING
    site.started_at = moment
    site.ready_at = term
    await session.flush()
    return job


async def ripen(session: AsyncSession, site_id: uuid.UUID, *, now: datetime | None = None) -> None:
    """The term is out: the site stands ready for the owner's hand.

    Called by the build job. Idempotent: a job run twice finds the site ripe
    already and leaves it so.
    """
    site = await session.get(BuildSite, site_id, with_for_update=True)
    if site is None or site.state is not SiteState.BUILDING:
        return
    site.state = SiteState.READY
    site.ready_at = now or datetime.now(UTC)
    await session.flush()
    await events.record(
        session,
        EventKind.ESTATE_SITE_READY,
        actor_identity_id=site.owner_identity_id,
        node_id=site.node_id,
        site=str(site.id),
        area=float(site.footprint_m2),
        floors=site.floors,
        built_of=site.kind,
    )


async def finish(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    site: BuildSite,
    *,
    now: datetime | None = None,
) -> Building:
    """The owner raises the house: the site is done, the building stands."""
    moment = now or datetime.now(UTC)
    node = await _at(session, body, site)
    await session.refresh(site, with_for_update=True)
    if site.owner_identity_id != body.identity_id:
        raise SiteError(key="estate-site-not-yours")
    if site.state is not SiteState.READY:
        raise SiteError(key="estate-site-not-ready")
    building = await raise_house(
        session,
        constants,
        node,
        footprint=float(site.footprint_m2),
        floors=site.floors,
        kind=site.kind,
        identity_id=body.identity_id,
    )
    site.state = SiteState.DONE
    site.finished_at = moment
    await session.flush()
    return building
