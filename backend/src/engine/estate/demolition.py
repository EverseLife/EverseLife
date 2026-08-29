# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""estate: demolition (D-205).

Split out of `engine/estate.py` along its sections (review 2026-08-23, wave 3).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants, current
from src.constants import registry as R
from src.engine import events, goods, travel, world
from src.engine.estate._base import EstateError, NoBuilding, NoRoom, NotOwner
from src.engine.estate.building import (
    build_minutes,
    buildings_of,
    built_area,
    close_storeys,
    estimate,
    floor_mass,
    hold_ground,
    kinds,
    open_storeys,
    slots,
    spare_storeys,
    storeys_of,
    under_construction,
    yard_mass,
)
from src.engine.jobs import enqueue, handler
from src.models.estate import Building
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Item
from src.models.job import Job, JobKind, JobState
from src.models.world import Node
from src.units import amount as to_amount


def demolish_minutes(constants: Constants, houses: list[Building]) -> float:
    """The term of taking apart: `build.demolish_labor_k` of the raising labour."""
    return constants[R.BUILD_DEMOLISH_LABOR_K] * sum(
        build_minutes(
            constants,
            footprint=float(house.footprint_m2),
            floors=house.floors,
            kind=house.kind,
        )
        for house in houses
    )


def salvage(constants: Constants, houses: list[Building]) -> dict[str, float]:
    """Materials coming back: `build.demolish_salvage` of what the houses cost.

    Counted from the bill of the very same houses rather than from their area:
    height and type made the bill non-linear (D-125, D-218), and a demolition
    that returned by area would pay for a tower as for a shed -- and would give
    back timber for a house of iron and glass.
    """
    share = constants[R.BUILD_DEMOLISH_SALVAGE]
    back: dict[str, float] = {}
    for house in houses:
        lot = estimate(
            constants,
            footprint=float(house.footprint_m2),
            floors=house.floors,
            kind=house.kind,
        )
        for name, qty in lot.items():
            back[name] = back.get(name, 0.0) + qty * share
    #: What comes back comes back whole, and downwards (D-212): taking a house
    #: apart must not mint a board that was not in it.

    return {name: goods.whole(name, qty) for name, qty in back.items()}


async def demolishing(session: AsyncSession, node: Node) -> bool:
    """Whether a demolition is already under way here.

    Without this check a second order would be taken while the first is still
    running, and each of them carries its own salvage in the payload: the house
    is one, the materials would come back twice. Matter does not multiply.
    """

    rows = (
        (
            await session.execute(
                select(Job).where(
                    Job.kind == JobKind.BUILD_DEMOLISH.value,
                    Job.state == JobState.PENDING,
                )
            )
        )
        .scalars()
        .all()
    )
    return any(job.payload.get("node") == str(node.id) for job in rows)


async def demolish_blockers(session: AsyncSession, constants: Constants, node: Node) -> list[str]:
    """What stands in the way of demolition, in words -- before the work, not after.

    The yard empties **first**: after the demolition the machines have nowhere
    to stand and the cargo has no room on the ground. Refusing up front is the
    only honest order -- possessions in this world are lost to an eruption
    (D-197), not to a button.
    """
    reasons: list[str] = []
    #: Every floor of the house, not the ground one alone (D-247): the storeys
    #: are nodes of their own and hold their own machines and their own cargo,
    #: and all of it comes down into this one yard.
    upstairs = await storeys_of(session, node)
    _, occupied = await slots(session, constants, node)
    for room in upstairs:
        _, above = await slots(session, constants, room)
        occupied += above
    if occupied > 0:
        reasons.append(
            f"в здании стоит оборудование ({occupied}): рабочие станции и мебель "
            "забирают до сноса — после него им негде стоять"
        )
    #: Both surfaces against the whole plot (D-244): the roof goes, and what
    #: was under it comes to lie beside what was already out in the yard. Asking
    #: only about the floor let a demolition through that left the ground
    #: overloaded, and the message quoted a capacity nobody was measured against.
    lying = await floor_mass(session, node)
    for room in upstairs:
        lying += await floor_mass(session, room)
    outside = await yard_mass(session, node)
    yard = float(node.area_m2) * constants[R.BUILD_FLOOR_PER_M2]
    if lying + outside > yard:
        reasons.append(
            f"на полу {lying:.1f} кг и во дворе {outside:.1f} кг, а участок держит "
            f"{yard:.1f} кг: лишнее увезите или уложите в сундук"
        )
    if await under_construction(session, node):
        reasons.append("здесь идёт стройка: сначала дождитесь её конца")
    if await demolishing(session, node):
        reasons.append("снос уже идёт: второй раз его не заказывают")
    return reasons


async def demolish(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    node: Node,
    *,
    now: datetime | None = None,
) -> Job:
    """Take a house apart. The work goes by time, the materials come back at its end.

    Whose house may be taken apart follows exactly whose house may be built
    (`construct`): one's own plot -- and any nobody's land, where work is open to
    everyone and always will be (D-198). A homestead beyond the walls is put up
    by whoever came and taken down by whoever came: there is no title there to
    make one of them the owner.

    Somebody else's **civic** plot is another matter: there the land has a paper,
    and a house on it is demolished by a court order (D-095), not by this button.
    """
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        raise EstateError("мёртвое тело не сносит")
    await travel.require_here(session, body)
    if body.node_id != node.id:
        raise EstateError("сносят ногами: дойдите до участка")
    nobodys = node.owner_identity_id is None and node.owner_city_id is None
    if not nobodys and node.owner_identity_id != body.identity_id:
        raise NotOwner(
            "участок не ваш: сносят своё, а чужую городскую застройку "
            "разбирают по решению суда, а не кнопкой"
        )

    houses = await buildings_of(session, node)
    if not houses:
        raise NoBuilding("сносить нечего: здания на участке нет")
    blocking = await demolish_blockers(session, constants, node)
    if blocking:
        raise NoRoom("; ".join(blocking))

    back = salvage(constants, houses)
    minutes = demolish_minutes(constants, houses)
    term = moment + timedelta(minutes=minutes)
    event = await events.record(
        session,
        EventKind.CRAFT_STARTED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        work="demolish",
        area=await built_area(session, node),
        back=back,
        ready_at=term.isoformat(),
    )
    job = await enqueue(
        session,
        JobKind.BUILD_DEMOLISH,
        term,
        payload={
            "node": str(node.id),
            "back": back,
            "identity": str(body.identity_id),
        },
        dedup_key=f"demolish:{node.id}:{event.id}",
        cause_event_id=event.id,
        body_id=body.id,
    )
    if job is None:  # pragma: no cover -- the key is unique per event
        raise EstateError("снос уже поставлен")
    return job


@handler(JobKind.BUILD_DEMOLISH)
async def finish_demolish(session: AsyncSession, job: Job) -> None:
    """The house is taken apart: the plot is empty again, part of the material is back.

    Where the salvage goes is decided the same way as with a batch at a machine:
    into the hands of whoever is standing here, and onto the floor if they left or
    died. Matter does not vanish with whoever ordered the work.
    """

    node = await session.get(Node, uuid.UUID(job.payload["node"]))
    if node is None:  # pragma: no cover
        raise EstateError(f"снос {job.id} ссылается в никуда")

    houses = await buildings_of(session, node)
    if not houses:
        #: Nothing left to take apart -- the work has already been done, and the
        #: salvage with it. A repeated job must not hand out the materials twice.
        return
    #: The plot's row first, and for the same reason building takes it
    #: (`estate.hold_ground`, D-246): the floors that stay are read off what
    #: stands here, and a build finishing in this same second would leave its
    #: storeys standing with no stair to any of them.
    await hold_ground(session, node)

    for house in houses:
        await session.delete(house)
    await session.flush()

    #: The floors come down with the walls (D-247): what stood and lay on them
    #: goes into the yard, which was checked for room before the work began, and
    #: the way up is cut. The rooms themselves stay -- nothing here is deleted
    #: (D-007) -- and a house built up to that height again walks back into them.
    yard = await world.node_container(session, node)
    rooms = await spare_storeys(session, node)
    for room in rooms:
        for thing in await world.contents(session, await world.node_container(session, room)):
            thing.container_id = yard.id
            #: Out under the sky: the roof it stood under has been taken apart.
            thing.outdoors = True
        await session.flush()
    await close_storeys(session, node, rooms)

    body = (
        None if job.body_id is None else await session.get(Body, job.body_id, with_for_update=True)
    )
    at_hand = body is not None and body.state is BodyState.ALIVE and body.node_id == node.id
    where = (
        await world.body_container(session, body)
        if at_hand and body is not None
        else await world.node_container(session, node)
    )
    for name, qty in (job.payload.get("back") or {}).items():
        given = to_amount(float(qty))
        if given <= 0:
            continue
        salvage = Item(container_id=where.id, type_key=name, amount=given)
        session.add(salvage)
        await world.stack_up(session, salvage)
    await session.flush()

    await events.record(
        session,
        EventKind.BUILDING_DEMOLISHED,
        actor_identity_id=uuid.UUID(job.payload["identity"]),
        node_id=node.id,
        back=job.payload.get("back") or {},
        to_hands=at_hand,
    )


@handler(JobKind.BUILD_FINISH)
async def finish_build(session: AsyncSession, job: Job) -> None:
    """Construction is over: the building stands on the plot."""
    node = await session.get(Node, uuid.UUID(job.payload["node"]))
    if node is None:  # pragma: no cover
        raise EstateError(f"стройка {job.id} ссылается в никуда")

    #: Old jobs from before storeys carry no `floors`, and those from before
    #: types (D-218) name a tier instead of a type. Either way such a site
    #: finishes as the plainest house there is -- a single floor of the
    #: cheapest kind, which is what those tiers were built of anyway.
    footprint = float(job.payload["area"])
    floors = int(job.payload.get("floors", 1))
    kind = str(job.payload.get("kind") or kinds(current())[0])
    building = Building(
        node_id=node.id,
        #: Usable area is the sum of the floors; the ground taken is the footprint.
        area_m2=footprint * floors,
        footprint_m2=footprint,
        floors=floors,
        kind=kind,
    )
    session.add(building)
    await session.flush()

    #: The floors above the ground open with the house (D-247): each is a node
    #: of its own, and a stair leads from the one below. The ground floor is the
    #: plot itself, so a one-storey house opens nothing. Floors are the plot's,
    #: so a second house that reaches higher simply carries them further up.
    await open_storeys(session, current(), node)

    await events.record(
        session,
        EventKind.BUILDING_BUILT,
        actor_identity_id=uuid.UUID(job.payload["identity"]),
        node_id=node.id,
        building_id=str(building.id),
        area=float(building.area_m2),
        floors=floors,
        built_of=kind,
    )
    #: A house the city ordered collects its pay (D-248): the engine just put
    #: the building on the plot itself -- there is nothing left to verify.
    from src.engine import works_city  # noqa: PLC0415 -- lazy: works_city imports estate

    await works_city.pay_build_order(
        session,
        current(),
        node,
        building,
        uuid.UUID(job.payload["identity"]),
        now=job.run_at,
    )
