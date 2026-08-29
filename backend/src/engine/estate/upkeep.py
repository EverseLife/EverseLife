# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""estate: decay, repair and collapse (D-218).

Split out of `engine/estate.py` along its sections (review 2026-08-23, wave 3).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants, current, current_catalog
from src.constants import registry as R
from src.engine import craft, events, goods, occupation, storage, travel, world
from src.engine.estate._base import EstateError, Ruined
from src.engine.estate.building import (
    _equipment,
    build_minutes,
    buildings_of,
    close_storeys,
    composition,
    estimate,
    hold_ground,
    spare_storeys,
)
from src.engine.jobs import enqueue, handler
from src.engine.ship import ABOARD
from src.models.estate import Building
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Container, ContainerKind, Item
from src.models.job import Job, JobKind, JobState
from src.models.works import WorkOrderKind
from src.models.world import Node
from src.units import (
    SCALE_MAX,
    SCALE_MIN,
    SECONDS_PER_MINUTE,
    amount_float,
)


def decay_per_day(constants: Constants, kind: str) -> float:
    """How much condition a house of this type loses in a day.

    This is what dear materials actually buy: not a stronger wall but a rarer
    repair. A log hut wants mending twice a year, an all-metal house about once
    in a lifetime -- and pays for that up front, in iron and glass.
    """
    composition(constants, kind)
    return float(constants[R.BUILD_DECAY][kind])


def missing_share(houses: list[Building]) -> float:
    """How much of full condition the plot's houses have lost, as a share of one.

    Weighted by nothing: houses on one plot are mended in one go, and the share
    is taken from the worst of them. Mending the sound one along with it costs
    materials it did not need, and the player would rightly call that theft.
    """
    if not houses:
        return 0.0
    worst = min(float(house.condition) for house in houses)
    return (SCALE_MAX - worst) / SCALE_MAX


def repair_bill(constants: Constants, houses: list[Building]) -> dict[str, float]:
    """What mending these houses back to full costs in materials.

    What a house is built of is what it is mended with (D-145): the bill is
    recomputed from the type rather than stored, and taken in the share of
    condition actually missing. `build.repair_materials_k` is the price of
    lifting a house from nothing to full -- a part of building it, never all,
    because the walls are still standing.
    """

    share = constants[R.BUILD_REPAIR_MATERIALS_K] * missing_share(houses)
    wanted: dict[str, float] = {}
    for house in houses:
        lot = estimate(
            constants,
            footprint=float(house.footprint_m2),
            floors=house.floors,
            kind=house.kind,
        )
        for name, qty in lot.items():
            wanted[name] = wanted.get(name, 0.0) + qty * share
    #: Whole pieces, upwards (D-212): half a board does not go into a wall.
    return {name: goods.whole(name, qty, up=True) for name, qty in wanted.items() if qty > 0}


def repair_minutes(constants: Constants, houses: list[Building]) -> float:
    """The term of mending: `build.repair_labor_k` of the raising labour, by the gap."""
    share = constants[R.BUILD_REPAIR_LABOR_K] * missing_share(houses)
    return share * sum(
        build_minutes(
            constants,
            footprint=float(house.footprint_m2),
            floors=house.floors,
            kind=house.kind,
        )
        for house in houses
    )


async def repairing(session: AsyncSession, node: Node) -> bool:
    """Whether a repair is already under way here.

    The same reason as for demolition: the materials are written off at the
    order, and two orders would take twice for one set of walls.
    """

    rows = (
        (
            await session.execute(
                select(Job).where(
                    Job.kind == JobKind.BUILD_REPAIR.value,
                    Job.state == JobState.PENDING,
                )
            )
        )
        .scalars()
        .all()
    )
    return any(job.payload.get("node") == str(node.id) for job in rows)


async def repair(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    node: Node,
    *,
    tiers: dict[str, str] | None = None,
    now: datetime | None = None,
) -> Job:
    """Mend the houses on a plot. Materials at once, the condition on schedule.

    Whose house may be mended follows whose house may be built (D-089, D-198):
    one's own plot, and any nobody's land beyond the walls. The whole plot is
    mended in one order, as the whole plot is taken apart in one -- houses stand
    together and rot together.
    """
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        raise EstateError("мёртвое тело не чинит")
    await travel.require_here(session, body)
    if body.node_id != node.id:
        raise EstateError("чинят руками: дойдите до участка")
    nobodys = node.owner_identity_id is None and node.owner_city_id is None
    if not nobodys and node.owner_identity_id != body.identity_id:
        #: An open city repair order is a licence (D-248): the city posted it
        #: holding the TREASURY power, and withdrawing it closes the plot again.
        from src.engine import works_city  # noqa: PLC0415 -- lazy: works_city imports estate

        allowed = node.owner_city_id is not None and await works_city.licensed(
            session, WorkOrderKind.BUILDING_REPAIR, node
        )
        if not allowed:
            raise EstateError("участок не ваш: чинят у себя")
    if await repairing(session, node):
        raise EstateError("ремонт уже идёт: второй раз его не заказывают")
    #: Mending is an occupation like any other (D-211): one pair of hands does
    #: one thing, and a body ploughing a strip is not also mending a wall.
    await occupation.require_free(session, body)

    houses = await buildings_of(session, node)
    if not houses:
        raise Ruined("чинить нечего: на участке нет здания")
    if missing_share(houses) <= 0:
        raise Ruined("дом целёхонек: чинить в нём нечего")

    #: A repair this body walked away from is **resumed**, not started again:
    #: the materials went into the walls at the first order, and charging them
    #: twice would make leaving the node a fine rather than an interruption.
    frozen = await _frozen(session, node, body)
    needed = {} if frozen is not None else repair_bill(constants, houses)
    if needed:
        pocket = await world.body_container(session, body)
        stock = await craft._stock(session, pocket, tuple(needed), tiers=tiers)  # noqa: SLF001
        for pick in craft._pick(stock, needed):  # noqa: SLF001
            if pick.item.amount > pick.take:
                pick.item.amount -= pick.take
            else:
                await session.delete(pick.item)
        await session.flush()

    left = repair_minutes(constants, houses) if frozen is None else frozen
    term = moment + timedelta(minutes=left)
    event = await events.record(
        session,
        EventKind.CRAFT_STARTED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        work="repair",
        spent=needed,
        ready_at=term.isoformat(),
    )
    job = await enqueue(
        session,
        JobKind.BUILD_REPAIR,
        term,
        payload={"node": str(node.id), "identity": str(body.identity_id)},
        dedup_key=f"repair:{node.id}:{event.id}",
        cause_event_id=event.id,
        body_id=body.id,
    )
    if job is None:  # pragma: no cover -- the key is unique per event
        raise EstateError("ремонт уже поставлен")
    return job


async def _frozen(session: AsyncSession, node: Node, body: Body) -> float | None:
    """Minutes left on a repair this body walked away from, if there is one.

    Written down by `pause` and consumed here: the row is marked spent, so a
    remainder is resumed once and not once per order.
    """
    rows = (
        (
            await session.execute(
                select(Job)
                .where(
                    Job.kind == JobKind.BUILD_REPAIR.value,
                    Job.state == JobState.CANCELLED,
                    Job.body_id == body.id,
                )
                .order_by(Job.run_at.desc())
            )
        )
        .scalars()
        .all()
    )
    for job in rows:
        payload = job.payload or {}
        if payload.get("node") != str(node.id) or payload.get("resumed"):
            continue
        left = payload.get("left_minutes")
        if left is None:
            continue
        job.payload = {**payload, "resumed": True}
        await session.flush()
        return float(left)
    return None


async def pause(session: AsyncSession, body: Body, *, now: datetime | None = None) -> float | None:
    """The mason left the wall: the repair stops with the time left in it.

    Mending is done **by hand and on the spot**: a wall does not mend itself
    while its owner is a planet away. So leaving the node stops the work rather
    than letting it finish by itself -- the same rule the bench keeps
    (`craft.freeze`), and for the same reason.

    What is **not** lost is the materials: they went into the walls at the
    order, and the remainder waits here for whoever comes back to it. Returns
    the minutes left, or None if nothing was running.
    """
    moment = now or datetime.now(UTC)
    job = (
        (
            await session.execute(
                select(Job)
                .where(
                    Job.kind == JobKind.BUILD_REPAIR.value,
                    Job.state == JobState.PENDING,
                    Job.body_id == body.id,
                )
                .with_for_update()
            )
        )
        .scalars()
        .first()
    )
    if job is None:
        return None
    left = max(0.0, (job.run_at - moment).total_seconds() / SECONDS_PER_MINUTE)
    job.state = JobState.CANCELLED
    job.payload = {**(job.payload or {}), "left_minutes": left}
    await session.flush()
    return left


@handler(JobKind.BUILD_REPAIR)
async def finish_repair(session: AsyncSession, job: Job) -> None:
    """The mending is over: the houses stand as new.

    Whatever they lost while the work ran is written off with the rest: the
    materials were paid at the order, and charging the days of the repair
    itself would make a long repair unfinishable.
    """
    node = await session.get(Node, uuid.UUID(job.payload["node"]))
    if node is None:  # pragma: no cover
        raise EstateError(f"ремонт {job.id} ссылается в никуда")
    houses = await buildings_of(session, node)
    #: The house may have fallen while the work ran -- then there is nothing to
    #: mend and nothing to complain about: the journal keeps both records.
    for house in houses:
        house.condition = Decimal(str(SCALE_MAX))
    await session.flush()
    await events.record(
        session,
        EventKind.BUILDING_REPAIRED,
        actor_identity_id=uuid.UUID(job.payload["identity"]),
        node_id=node.id,
        houses=len(houses),
    )
    #: A mending the city ordered collects its pay (D-248): the houses are
    #: whole again, the engine saw it in its own data.
    from src.engine import works_city  # noqa: PLC0415 -- lazy: works_city imports estate

    await works_city.pay_repair_order(
        session,
        current(),
        node,
        uuid.UUID(job.payload["identity"]),
        now=job.run_at,
    )


async def _bury(
    session: AsyncSession,
    store: Container,
    lost: dict[str, float],
    *,
    filtered: bool,
) -> None:
    """Destroy what a fallen roof was over, counting it into `lost`.

    `filtered` is the ground floor's question and only its (D-244): a plot has
    two surfaces, and what lay in the yard was rained on all along -- a house
    falling on the far side does not crush it. A storey has one surface and it
    is all indoors, so nothing there is spared.

    The mark is read raw rather than through `estate.split`: the house is
    deleted around this call, so asking "is there a building" would answer no
    and spare everything. What the mark says is what the thing was standing
    under a minute ago, and that is the question.

    For **cargo**, that is. Equipment and chests are indoors by what they are --
    placed into a building, counted against its slots, worked at (D-106, D-181)
    -- and their mark is not to be trusted: carrying a bench out and putting it
    down in the yard leaves it marked, and it keeps that mark when carried back
    in. Two ordinary commands would otherwise make every machine and every chest
    in a house proof against its collapse, losing neither its slot nor its use.
    """
    catalog = current_catalog()
    things = [
        thing
        for thing in (
            (await session.execute(select(Item).where(Item.container_id == store.id)))
            .scalars()
            .all()
        )
        if not filtered
        or not thing.outdoors
        or _equipment(catalog, thing.type_key)
        or storage.is_storage(catalog, thing.type_key)
    ]
    for thing in things:
        lost[thing.type_key] = lost.get(thing.type_key, 0.0) + amount_float(thing.amount)
        #: A chest goes down with its contents: the inside is a container of
        #: its own, and left behind it would be goods in no place at all.
        inside = (
            (
                await session.execute(
                    select(Container).where(
                        Container.kind == ContainerKind.STORAGE,
                        Container.owner_id == thing.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        for box in inside:
            stored = (
                (await session.execute(select(Item).where(Item.container_id == box.id)))
                .scalars()
                .all()
            )
            for held in stored:
                lost[held.type_key] = lost.get(held.type_key, 0.0) + amount_float(held.amount)
                await session.delete(held)
            await session.delete(box)
        await session.delete(thing)
    await session.flush()


async def collapse(session: AsyncSession, node: Node, house: Building) -> None:
    """Condition ran out: the house falls, and what it sheltered falls with it.

    A collapse is not a demolition: nothing is salvaged, and no warning comes at
    the last moment -- the warning was every day of the condition dropping.

    **What perishes is what stood under the roof, and only that** (D-244). The
    node keeps two surfaces: the floor of the house and the open ground beside
    it. What lay in the yard was rained on all along and is rained on still --
    a house falling on the far side of the plot does not crush it. What was
    indoors goes down with the roof: machines, furniture and the chests along
    with what was inside them.

    While another house still stands on the plot the goods move under it and
    survive: the indoor surface is one for the node, not one per building, and
    that is deliberate -- two houses on a plot are two roofs over one floor,
    not two floors.

    **The floors nothing else holds up fall too** (D-247), and they fall whole:
    a storey has no yard to be rained on, so everything on it is under the roof.
    A neighbouring house that reaches as high keeps them standing: floors belong
    to the plot, and two houses are two roofs over each floor they both reach.
    """

    #: The plot's row first, and for the same reason building takes it
    #: (`estate.hold_ground`, D-246): what the floors are is read off what
    #: stands here, and a build finishing in another session in this same second
    #: would leave a four-storey house with no stair to any of its floors.
    await hold_ground(session, node)

    await session.delete(house)
    await session.flush()

    #: The floors nothing holds up any more fall with the walls (D-247), and
    #: everything on them goes down: upstairs there is no yard to be rained on,
    #: every metre of a storey is under the roof. A neighbouring house that
    #: reaches as high keeps them standing -- floors belong to the plot, and two
    #: houses are two roofs over each floor they both reach.
    lost: dict[str, float] = {}
    rooms = await spare_storeys(session, node)
    for room in rooms:
        await _bury(session, await world.node_container(session, room), lost, filtered=False)
    await close_storeys(session, node, rooms)

    last = not await buildings_of(session, node)
    if last:
        #: The ground floor loses only what was under the roof (D-244) -- see
        #: `_bury`. While another house still stands here the goods move under
        #: it and survive.
        await _bury(session, await world.node_container(session, node), lost, filtered=True)

    await events.record(
        session,
        EventKind.BUILDING_COLLAPSED,
        node_id=node.id,
        area=float(house.area_m2),
        floors=house.floors,
        built_of=house.kind,
        last=last,
        lost=lost,
    )


async def decay(session: AsyncSession, constants: Constants) -> tuple[int, int]:
    """Daily decay of every house in the world. Returns (worn, collapsed).

    One step per daily tick, as a road overgrows and gear wears (D-129): the
    step is the type's own (`build.decay_by_type`), which is the whole point of
    paying for iron instead of timber.
    """

    rows = (
        await session.execute(select(Building, Node).join(Node, Node.id == Building.node_id))
    ).all()
    worn = 0
    fallen: list[Building] = []
    for house, node in rows:
        #: A ship's compartment is a building for counting area alone (D-202).
        #: It is kept up by its own repairs, and the weather over a yard has no
        #: say aboard -- a hull that rotted away in a year would be a defect.
        if (node.properties or {}).get(ABOARD):
            continue
        step = decay_per_day(constants, house.kind)
        left = max(SCALE_MIN, float(house.condition) - step)
        house.condition = Decimal(str(left))
        worn += 1
        await events.record(
            session,
            EventKind.BUILDING_WORN,
            node_id=house.node_id,
            building_id=str(house.id),
            built_of=house.kind,
            spent=step,
            condition=left,
        )
        if left <= SCALE_MIN:
            fallen.append(house)
    await session.flush()

    for house in fallen:
        node = await session.get(Node, house.node_id)
        if node is None:  # pragma: no cover -- a building without a node is a defect
            continue
        await collapse(session, node, house)
    return worn, len(fallen)
