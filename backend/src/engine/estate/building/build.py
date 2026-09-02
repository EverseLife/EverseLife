# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Raising and reshaping: the composition and the bill of a type, the
minutes it takes, storeys opened and closed on a standing frame, and the
construction itself -- materials written off at once, the building rising
on schedule.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import craft, events, goods, travel, world
from src.engine.estate._base import (
    GROUND_FLOOR,
    STOREY,
    EstateError,
    NoRoom,
    TooSmall,
    UnknownKind,
    storey_of,
)
from src.engine.estate.building.frame import (
    built_area,
    free_ground,
    height_of,
    hold_ground,
    planned_footprint,
    storey_area_for,
    storeys_of,
)
from src.engine.jobs import enqueue
from src.models.estate import Building
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.job import Job, JobKind
from src.models.travel import Travel, TravelState
from src.models.works import WorkOrderKind
from src.models.world import Edge, Layer, Node, Planet, Surface
from src.units import (
    MINUTES_PER_HOUR,
)


def kinds(constants: Constants) -> list[str]:
    """The building types there are, in the vault's own order (D-218).

    The order matters: it is the ladder from a log hut to an all-metal house,
    and the shop window shows it as the vault wrote it -- cheapest first.
    """
    return list(constants[R.BUILD_TYPES])


def composition(constants: Constants, kind: str) -> dict[str, float]:
    """What one square metre of this type's floor is made of.

    Refuses an unknown name rather than falling back to a default: silently
    building a log house where an all-metal one was ordered would be a swindle
    the player only discovers at the collapse.
    """
    types = constants[R.BUILD_TYPES]
    if kind not in types:
        #: Ids joined, not words: the message says them with `KINDS()`, and how
        #: a list is punctuated is the language's business, not ours (D-251).
        raise UnknownKind(key="estate-unknown-kind", kind=kind, kinds=", ".join(types))
    return types[kind]


def floor_growth(constants: Constants, kind: str) -> float:
    """How much dearer each next floor of this type is than the one below it."""
    composition(constants, kind)  # -- the name is checked in one place only
    return float(constants[R.BUILD_FLOOR_GROWTH][kind])


def estimate(constants: Constants, *, footprint: float, floors: int, kind: str) -> dict[str, float]:
    """The bill of materials for a house, by the vault formula `build.cost_per_area`.

    The type gives the composition per square metre of floor, and each next
    floor costs `build.floor_growth_by_type` times more than the one below:
    height grows geometrically, and so does the price of it. Reinforcement is
    not a separate line any more -- it lives inside that growth, which is why a
    timber house pays double per floor and an all-metal one thirteen per cent
    (D-218).
    """
    norms = composition(constants, kind)
    growth = floor_growth(constants, kind)

    #: The sum over floors, not "area times a coefficient": the eighth floor is
    #: expensive by itself, and averaging would hide exactly that.
    per_footprint = sum(growth ** (floor - 1) for floor in range(1, floors + 1))
    return {name: float(qty) * footprint * per_footprint for name, qty in norms.items()}


def bill(constants: Constants, *, footprint: float, floors: int, kind: str) -> dict[str, float]:
    """The same bill, in the amounts the world can actually hand over (D-212).

    A counted material goes into the wall whole: two and a half boards is
    three boards. `estimate` stays as the formula wrote it -- the labour ratio
    in `build_minutes` is about how heavy the lot is, not about what the saw
    could not halve -- and this is what is shown and what is written off.
    """

    return {
        name: goods.whole(name, qty, up=True)
        for name, qty in estimate(constants, footprint=footprint, floors=floors, kind=kind).items()
    }


def build_minutes(constants: Constants, *, footprint: float, floors: int, kind: str) -> float:
    """The term: assembly labour, with the same correction for height and type.

    Effort follows the bill: what is dearer in materials is longer in hands.
    The yardstick is the cheapest type at one floor -- the plain hut everything
    else is heavier than.
    """
    lot = estimate(constants, footprint=footprint, floors=floors, kind=kind)
    plain = estimate(constants, footprint=footprint, floors=1, kind=kinds(constants)[0])
    total = sum(plain.values())
    heaviness = (sum(lot.values()) / total) if total else 1.0
    return footprint * constants[R.BUILD_LABOR_PER_M2] * MINUTES_PER_HOUR * heaviness


async def open_storeys(session: AsyncSession, constants: Constants, node: Node) -> list[Node]:
    """Open the plot's floors up to its tallest house and cut the stairs (D-247).

    The ground floor is the plot itself -- the door, the yard and the way in are
    all there -- so only the floors above it become nodes. They stand in a row,
    not in a star: one climbs to the fifth through the four below it, and that
    is what makes height a decision rather than a free widening of the ground.

    **Floors belong to the plot, not to a particular house.** Two houses on one
    plot are two roofs over each floor they both reach, exactly as they have
    always been two roofs over one ground floor -- and a floor keyed by the plot
    is a floor that can be **reopened**: a house taken down and put up again
    walks back into the same rooms, with whatever names their owner gave them.

    Idempotent, and that is what makes it the one entry point: called after a
    build, after a demolition, after a collapse, it brings the plot's floors to
    what stands on it now. A one-storey plot opens nothing, which is why the
    world as it was needs no rewriting.
    """
    #: The same lock the ground is spent under (`hold_ground`), and for the
    #: neighbouring reason: a plot changing hands in another session reads the
    #: floors it must carry with it, and a build finishing here writes them. One
    #: of the two has to wait, or the buyer gets the yard and the seller keeps
    #: the workshop upstairs.
    await hold_ground(session, node)
    height = await height_of(session, node)
    standing = {storey_of(room): room for room in await storeys_of(session, node)}
    rooms: list[Node] = []
    below = node
    for floor in range(GROUND_FLOOR + 1, height + 1):
        room = standing.get(floor)
        if room is None:
            room = await world.create_node(
                session,
                f"{node.key}.floor.{floor}",
                f"{floor}-й этаж",
                planet=node.planet,
                #: One floor is the footprint, however many stand on it (D-125).
                #: Kept in step below, on every opening: `storey_area` is the
                #: answer, and the node's own metres must not become a second one.
                area_m2=await storey_area_for(session, node, floor),
                layer=Layer.LOCATION,
                parent=node,
                #: On the plot's own map a floor stands next to the one below it.
                anchor=below,
                properties={STOREY: floor},
            )
        #: The floor is held by whoever holds the plot: a storey is not bought,
        #: not sold and not fenced on its own (D-247). Land outside a city has
        #: no holder, and neither has a floor of a house standing on it.
        room.owner_identity_id = node.owner_identity_id
        #: And it is as wide as what reaches it **now**: a room reopened under a
        #: narrower house than the one that first cut it would otherwise carry
        #: the old number for ever -- a second opinion about a figure the engine
        #: computes anyway, and one that has already drifted.
        room.area_m2 = Decimal(str(await storey_area_for(session, node, floor)))
        await session.flush()
        #: Idempotent (`travel.connect`): a reopened floor keeps the stair it had.
        await travel.connect(
            session,
            below,
            room,
            base_seconds=constants[R.BUILD_STAIR_SECONDS],
            surface=Surface.PAVED,
        )
        rooms.append(room)
        below = room
    return rooms


async def close_storeys(session: AsyncSession, node: Node, rooms: list[Node]) -> None:
    """Cut the way to floors nothing holds up any more (D-247).

    **The nodes stay.** Nothing in this world is deleted (D-007), and a node
    least of all: a floor somebody has walked to is written into the journal of
    transits, the chat of the place, the orders made in it and a dozen tables
    besides, and none of them is a thing to throw away because a wall fell. So
    the stairs go instead -- and a place with no way in is off the map by the
    same rule a ship in flight is (D-201): the graph simply does not reach it.
    Build up to that height again and the room comes back, name and all.

    **What was on them is the caller's business** and is dealt with before this
    runs. What is not the caller's business is where the people go: a floor
    losing its walls under somebody standing on it must not leave them shut in,
    so they come down into the yard.
    """
    if not rooms:
        return
    upstairs = [room.id for room in rooms]
    #: Whoever is **on the way up** turns back before the stairs are cut (D-194,
    #: pillar P6). `travel.disconnect` refuses to take an edge out from under a
    #: walker, and here refusing is not on the table -- the walls are falling --
    #: so the walk is ended instead. Left alone, the leg would fire on schedule
    #: and put the body down on a floor with no metres and no way out: a node
    #: with no edges is a node nothing leads out of, and this world does not
    #: have places one can be stuck in.
    walking = (
        (
            await session.execute(
                select(Body)
                .join(Travel, Travel.body_id == Body.id)
                .where(Travel.state == TravelState.GOING, Travel.to_node_id.in_(upstairs))
            )
        )
        .scalars()
        .all()
    )
    for walker in walking:
        #: Forced: the door's rule holds against a decision, and a collapse is
        #: not one -- there is no end of the passage left to walk to.
        await travel.turn_back(session, walker, forced=True)
    for room in rooms:
        for body in (
            (await session.execute(select(Body).where(Body.node_id == room.id))).scalars().all()
        ):
            body.node_id = node.id
        await session.flush()
        #: The stairs go with the floor. Removed by hand rather than through
        #: `travel.disconnect`: that one guards a gangway somebody is walking,
        #: and here the walls are already down -- there is nothing left to wait
        #: for and nowhere to arrive.
        await session.execute(
            delete(Edge).where((Edge.node_a_id == room.id) | (Edge.node_b_id == room.id))
        )
    await session.flush()


async def _city_ordered_build(
    session: AsyncSession, node: Node, kind: str, area: float, floors: int
) -> bool:
    """Whether an open city order licenses this exact build here (D-248).

    The order is the permission, and only for the house it names: kind and
    floors to the letter, the footprint at least the ordered one -- otherwise
    the licence to build the granary would cover a shed on the city square.
    """
    if node.owner_city_id is None:
        return False
    from src.engine import works_city  # noqa: PLC0415 -- lazy: works_city imports estate

    order = await works_city.open_city_order(session, WorkOrderKind.BUILDING_BUILD, node)
    return (
        order is not None
        and order.payload.get("building_kind") == kind
        and int(order.payload.get("floors", 0)) == floors
        and area >= float(order.payload.get("footprint", 0))
    )


async def raise_house(
    session: AsyncSession,
    constants: Constants,
    node: Node,
    *,
    footprint: float,
    floors: int,
    kind: str,
    identity_id: uuid.UUID,
) -> Building:
    """The house stands on the plot: the record, the storeys above, the word.

    Shared by the old one-motion build's job and the site's own finish
    (D-266): what a house is once it is up does not depend on how the
    materials got there.
    """
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
    #: plot itself, so a one-storey house opens nothing. One house per plot
    #: (D-279): its height is the height the plot has, until the house is taken
    #: down and another is raised -- how a standing house grows is OQ-115.
    await open_storeys(session, constants, node)

    await events.record(
        session,
        EventKind.BUILDING_BUILT,
        actor_identity_id=identity_id,
        node_id=node.id,
        building_id=str(building.id),
        area=float(building.area_m2),
        floors=floors,
        built_of=kind,
    )
    return building


async def construct(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    node: Node,
    area: float,
    *,
    floors: int = 1,
    kind: str | None = None,
    tiers: dict[str, str] | None = None,
    now: datetime | None = None,
) -> Job:
    """Build a house on your own plot. Materials at once, the building on schedule.

    `area` is the **footprint**: the ground one floor takes. Storeys stand on
    it, and the usable area is their sum -- that is how a plot stops being a
    hard limit on a workshop (D-125).

    **The footprint is bounded and the height is not** (D-218). Ground is
    finite: the footprint may be no larger than the plot's free remainder --
    sites already started included -- and no smaller than `build.area_min`,
    below which the thing is a lean-to. Height is bounded by the bill alone:
    the type decides how much dearer each next floor is, and a twenty-storey
    log house is allowed exactly because nobody will pay for one.

    Civic land is built by the city -- here only your own (D-089); land outside
    a city has no owner and is open to all (D-198).
    """
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        raise EstateError(key="estate-build-dead")
    await travel.require_here(session, body)
    if body.node_id != node.id:
        raise EstateError(key="estate-build-on-foot")
    #: A storey is a floor of a house, not ground under one (D-247). Left to
    #: the room check below it would refuse with "nothing free on the plot",
    #: which is true of a third floor and explains nothing.
    if storey_of(node) is not None:
        raise EstateError(key="estate-build-not-on-storey")
    #: One house per plot (05-domain-model, D-279): no second beside the first.
    if await built_area(session, node) > 0 or await planned_footprint(session, node) > 0:
        raise EstateError(key="estate-build-house-stands")
    nobodys = node.owner_identity_id is None and node.owner_city_id is None
    if (
        not nobodys
        and node.owner_identity_id != body.identity_id
        and not await _city_ordered_build(session, node, kind or kinds(constants)[0], area, floors)
    ):
        raise EstateError(key="estate-build-not-yours")
    if floors < 1:
        raise EstateError(key="estate-build-no-floors")
    #: Pyroxis does not get built on (D-230): the ground shakes too often for
    #: a wall to outlive its builder, and what stands there arrived by ship.
    if node.planet is Planet.PYROXIS:
        raise EstateError(key="estate-build-not-on-pyroxis")

    #: Unnamed, the house is of the plainest type there is: that is what the
    #: world was built of before types arrived, and the default must not silently
    #: become the expensive one.
    kind = kind or kinds(constants)[0]
    composition(constants, kind)

    smallest = constants[R.BUILD_AREA_MIN]
    if area < smallest:
        raise TooSmall(key="estate-build-too-small", smallest=smallest, area=area)

    #: The plot's metres are a remainder, and this is where they are spent.
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

    #: Materials come from the vault, per metre of floor. Written off at once:
    #: construction has started, and the timber is already in the wall, not in the sack.

    needed = bill(constants, footprint=area, floors=floors, kind=kind)
    pocket = await world.body_container(session, body)
    #: Which stacks go into the wall is the builder's choice by tier (D-058).
    stock = await craft._stock(session, pocket, tuple(needed), tiers=tiers)  # noqa: SLF001
    for pick in craft._pick(stock, needed):  # noqa: SLF001
        if pick.item.amount > pick.take:
            pick.item.amount -= pick.take
        else:
            await session.delete(pick.item)
    await session.flush()

    minutes = build_minutes(constants, footprint=area, floors=floors, kind=kind)
    term = moment + timedelta(minutes=minutes)
    event = await events.record(
        session,
        EventKind.CRAFT_STARTED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        work="build",
        area=area,
        floors=floors,
        built_of=kind,
        spent=needed,
        ready_at=term.isoformat(),
    )
    job = await enqueue(
        session,
        JobKind.BUILD_FINISH,
        term,
        payload={
            "node": str(node.id),
            "area": area,
            "floors": floors,
            "kind": kind,
            "identity": str(body.identity_id),
        },
        dedup_key=f"build:{node.id}:{event.id}",
        cause_event_id=event.id,
        body_id=body.id,
    )
    if job is None:  # pragma: no cover -- the key is unique per event
        raise EstateError(key="estate-build-already-queued")
    return job
