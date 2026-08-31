# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""estate: building (D-106, D-125).

Split out of `engine/estate.py` along its sections (review 2026-08-23, wave 3).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants, current_catalog
from src.constants import registry as R
from src.constants.catalog import ItemKind
from src.db.base import forget, remember
from src.engine import craft, events, gear, goods, storage, travel, world
from src.engine.estate._base import (
    GROUND_FLOOR,
    STOREY,
    EstateError,
    NoRoom,
    TooSmall,
    UnknownKind,
    storey_of,
)
from src.engine.jobs import enqueue
from src.models.estate import Building
from src.models.event import EventKind
from src.models.farm import Plot
from src.models.identity import Body, BodyState
from src.models.inventory import Item
from src.models.job import Job, JobKind, JobState
from src.models.travel import Travel, TravelState
from src.models.works import WorkOrderKind
from src.models.world import Edge, Layer, Node, Planet, Surface
from src.units import (
    MINUTES_PER_HOUR,
    amount_float,
)


async def buildings_of(session: AsyncSession, node: Node) -> list[Building]:
    return list(
        (await session.execute(select(Building).where(Building.node_id == node.id))).scalars().all()
    )


async def built_area(session: AsyncSession, node: Node, *, ground: bool = False) -> float:
    """Built area of the node: usable by default, the footprint when asked.

    Since storeys arrived (D-125) these are two different numbers: a two-storey
    house of ten metres takes ten metres of the plot and gives twenty of floor.
    Whatever is measured against the plot must ask for `ground`; machines,
    cargo and upkeep go by the usable area.
    """

    async def measure() -> float:
        column = Building.footprint_m2 if ground else Building.area_m2
        total = await session.scalar(
            select(func.coalesce(func.sum(column), 0)).where(Building.node_id == node.id)
        )
        return float(total or 0)

    #: The plot's own screen asks for this three times over -- usable area,
    #: footprint, and again usable from `slots` (`db.base.remember`).
    return await remember(session, ("built_area", node.id, ground), measure)


async def height_of(session: AsyncSession, node: Node) -> int:
    """How many floors the plot reaches: the tallest house standing on it (D-247).

    The **tallest**, not the sum: two houses on one plot are two roofs over each
    floor they both reach, exactly as they have always been two roofs over one
    ground floor.
    """
    return max((house.floors for house in await buildings_of(session, node)), default=0)


async def storey_area(session: AsyncSession, node: Node) -> float:
    """The floor one stands on here, in metres (D-125, D-247).

    A house of ten metres in four storeys gives forty metres of floor, and they
    are **four floors, not one of forty**: three of them are nodes of their own,
    and the ground floor is the plot. So the indoor surface of any one node is a
    single storey.

    One rule covers both, and it is the rule the ground floor always had: the
    area of a floor is the footprint of everything that reaches it. On the plot
    every house reaches the ground, so that is the sum of the footprints; on the
    third floor only the houses of three storeys and more do. A floor nothing
    reaches any more has no metres at all -- the walls that held it are gone,
    and the room waits empty until something is built up to it again.

    `built_area` stays the whole house: the bill, the meter and the plot screen
    all ask about the building, and the building is all of it.
    """
    floor = storey_of(node)
    if floor is None:
        return await built_area(session, node, ground=True)
    if node.parent_id is None:  # pragma: no cover -- a storey without a plot is a defect
        return 0.0
    place = await session.get(Node, node.parent_id)
    if place is None:  # pragma: no cover
        return 0.0
    return sum(
        float(house.footprint_m2)
        for house in await buildings_of(session, place)
        if house.floors >= floor
    )


async def storeys_of(session: AsyncSession, node: Node) -> list[Node]:
    """The floors standing over this plot, lowest first (D-247).

    Open and closed alike: a floor nothing holds up any more keeps its node --
    nothing in this world is deleted (D-007) -- and is told apart by having no
    metres and no way in.
    """
    rows = (
        (
            await session.execute(
                select(Node).where(Node.parent_id == node.id, Node.properties.has_key(STOREY))
            )
        )
        .scalars()
        .all()
    )
    return sorted(rows, key=lambda room: storey_of(room) or 0)


async def under_construction(session: AsyncSession, node: Node) -> list[dict]:
    """Houses being built here right now: what and by when (D-125).

    Materials are written off at the start, and until this list existed the
    yard looked empty right after them -- as if the timber had vanished.
    """

    #: Filtered in SQL by the node inside the payload, not in Python over
    #: every pending build in the world (review 2026-08-23); the partial
    #: index `ix_job_due` narrows it to pending jobs first.
    rows = (
        (
            await session.execute(
                select(Job)
                .where(
                    Job.kind == JobKind.BUILD_FINISH.value,
                    Job.state == JobState.PENDING,
                    Job.payload["node"].astext == str(node.id),
                )
                .order_by(Job.run_at)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "area": float(job.payload.get("area", 0)),
            "floors": int(job.payload.get("floors", 1)),
            "kind": job.payload.get("kind"),
            "ready_at": job.run_at.isoformat(),
        }
        for job in rows
    ]


async def floor_mass(session: AsyncSession, node: Node) -> float:
    """How many kilograms lie on the node's floor (D-192).

    Only what lies loose: goods inside a chest are counted by the chest, not by
    the floor -- that is what a chest is for (D-181).
    """

    catalog = current_catalog()
    inside, _ = await split(session, node)
    return sum(
        gear.mass_of(catalog, thing.type_key, amount_float(thing.amount)) for thing in inside
    )


async def split(session: AsyncSession, node: Node) -> tuple[list[Item], list[Item]]:
    """What lies loose here, in two heaps: indoors and out (D-244).

    Two ways to be outdoors, and the second is what keeps the rest of the
    engine free of the question:

    * the thing was put on the ground on purpose (`item.outdoors`);
    * or there is no building on the node at all, and then there is no floor to
      be on -- everything lying here is outside whatever the mark says.

    The second is why loot from a death, cargo spilt by a broken cart and
    materials back from a demolition need to know nothing about surfaces: they
    put things in the node, and on a bare plot the node **is** the open sky.

    Machines, furniture and chests are in neither heap: they stand rather than
    lie, and pay for their place by slots (D-106, D-181).
    """
    catalog = current_catalog()
    #: The storey one stands on, not the whole house (D-247): upstairs the roof
    #: is this floor's own, and `built_area` there is nought -- the building
    #: record lives on the plot below.
    roofed = await storey_area(session, node) > 0
    inside: list = []
    outside: list = []
    #: Through `node_things`, not `node_container`: the estimates and the looks
    #: come this way, and a read must not make a yard row (CLAUDE.md).
    for thing in await world.node_things(session, node):
        if _equipment(catalog, thing.type_key) or storage.is_storage(catalog, thing.type_key):
            continue
        (inside if roofed and not thing.outdoors else outside).append(thing)
    return inside, outside


async def yard_mass(session: AsyncSession, node: Node) -> float:
    """The weight of what lies on the open ground. Chests carry their own."""
    catalog = current_catalog()
    _, outside = await split(session, node)
    return sum(
        gear.mass_of(catalog, thing.type_key, amount_float(thing.amount)) for thing in outside
    )


def _equipment(catalog, type_key: str) -> bool:
    """Machines and furniture pay for their place by slots, not by weight."""

    try:
        return catalog.recipes.recipe(type_key).kind in (
            ItemKind.STATION,
            ItemKind.FURNITURE,
        )
    except Exception:  # noqa: BLE001 -- raw material has no recipe, and that is normal
        return False


async def space(session: AsyncSession, constants: Constants, node: Node) -> dict[str, float]:
    """The **indoor** surface: the floor of the house and what stands on it (D-192).

    A building is the roof over the goods, and since D-244 it is a place of its
    own rather than a mood the yard is in: the plot outside its footprint is a
    second surface with its own metres (`yard`). Equipment pays
    `build.slots_per_area` per piece, loose cargo pays by weight through
    `build.floor_per_m2`.

    No building -- no indoors: the area is nought, and everything the node
    holds is out under the sky. The keys stay as they were so that a client
    reading a roofless node sees an honest empty floor rather than a missing
    one.
    """
    total_slots, taken_slots = await slots(session, constants, node)
    roofed = await storey_area(session, node)
    lying = await floor_mass(session, node)
    by_cargo = lying / constants[R.BUILD_FLOOR_PER_M2]
    by_equipment = taken_slots * constants[R.BUILD_SLOTS_PER_AREA]
    #: `roofed` is gone: it was the same number as `area` from the day this
    #: became the indoor surface, and the client tells "is there a house" by
    #: whether the area is nought (D-225).
    return {
        "area": roofed,
        "used": by_equipment + by_cargo,
        "cargo_mass": lying,
        "free": max(0.0, roofed - by_equipment - by_cargo),
        "slots": float(total_slots),
        "slots_used": float(taken_slots),
    }


async def yard(session: AsyncSession, constants: Constants, node: Node) -> dict[str, float]:
    """The **open ground**: the plot outside the building's footprint (D-244).

    Only what lies is counted here -- a machine is placed into a building and
    never reaches the open ground (D-106). A house that covers the whole plot
    leaves no yard at all, and then the area is nought: there is nowhere to put
    anything down, and the client shows no such list.

    Measured against the **footprint**, not against the usable area: a house of
    two storeys gives twenty metres of floor off ten metres of plot (D-125), and
    it is the ten the yard loses.
    """
    #: Upstairs there is no ground at all (D-247): under a storey is a floor,
    #: and a floor is the other surface. Without this the yard of the third
    #: floor would be as wide as the footprint, and things would be dropped on
    #: open ground that is somebody's ceiling.
    under = (
        float(node.area_m2)
        if storey_of(node) is not None
        else await built_area(session, node, ground=True)
    )
    capacity = max(0.0, float(node.area_m2) - under)
    lying = await yard_mass(session, node)
    by_cargo = lying / constants[R.BUILD_FLOOR_PER_M2]
    #: The plot and the footprint are **not** repeated here: the client already
    #: has both -- the node's area and the building's ground -- and a third
    #: copy of a subtraction is a third thing to keep in step (D-225).
    return {
        "area": capacity,
        "used": by_cargo,
        "cargo_mass": lying,
        "free": max(0.0, capacity - by_cargo),
    }


async def slots(session: AsyncSession, constants: Constants, node: Node) -> tuple[int, int]:
    """Capacity and occupancy: (total places, occupied places).

    A place is `build.slots_per_area` square metres of the building; it is
    taken by machines and furniture standing in the node.
    """

    #: One storey's worth of places, not the whole house's (D-247): the floors
    #: above the ground are nodes of their own and carry their own.
    area = await storey_area(session, node)
    in_total = int(area // constants[R.BUILD_SLOTS_PER_AREA])

    book = current_catalog().recipes
    things = await world.node_things(session, node)
    occupied = 0
    for thing in things:
        try:
            recipe = book.recipe(thing.type_key)
        except Exception:  # noqa: BLE001 -- raw material at the machine has no recipe
            continue
        if recipe.kind in (ItemKind.STATION, ItemKind.FURNITURE):
            occupied += 1
    return in_total, occupied


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


async def planned_footprint(session: AsyncSession, node: Node) -> float:
    """Ground already promised to sites started here but not yet finished.

    Counting only what stands would let a queue of orders walk straight past
    the plot check: five hundred metres of house on a hundred-metre plot, and
    every order lawful on its own, because none of them had arrived yet
    (D-218). The materials for them are written off and the houses are coming
    -- that ground is spoken for.
    """
    return sum(float(work["area"]) for work in await under_construction(session, node))


async def hold_ground(session: AsyncSession, node: Node) -> None:
    """Take the plot's row for the transaction before spending its metres.

    The plot's area is a remainder like money and grain (CLAUDE.md), and three
    commands spend it against the same sum (D-246): a house takes its footprint,
    a strip takes its metres, and what is left is the empty land the foraging
    walks. Every one of them reads `free_ground` and then writes, so without the
    lock two of them read the same hundred metres and both take sixty -- and the
    plot goes into a minus that nothing afterwards can notice, because nothing
    afterwards ever re-adds the parts.

    The **plot**, always: a storey is spent by nothing, and a house on it is
    spoken for by the ground it stands on.

    The whole row rather than its id, and `populate_existing` with it: whoever
    held the lock before us may have written the very fields we are about to
    read -- the holder, above all -- and a locked read that left a stale copy in
    the session would be a lock taken for nothing. The command's memory goes the
    same way and for the same reason: `remember` keeps its answers until a write
    throws them away (`db.base`), and a wait is not a write -- so a footprint
    counted before the lock would be handed back after it, from before the very
    change we waited out.
    """
    await session.execute(
        select(Node)
        .where(Node.id == node.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    forget(session)


async def marked_ground(session: AsyncSession, node: Node) -> float:
    """Ground already cut into strips here (D-118).

    A bed stands in the yard and takes it: the plot's metres are spent by three
    things and only three -- the footprint of the houses, the strips marked out
    of the land, and whatever is left empty. Counting the strips out of that sum
    let a hundred-metre plot carry a fifty-metre house and a hundred metres of
    beds at once, and then the empty land the foraging is measured against came
    out negative and was clamped to nought (D-210, D-246).

    Everybody's strips, not the asker's: the ground is one, whoever ploughed it.
    """

    async def measure() -> float:
        total = await session.scalar(
            select(func.coalesce(func.sum(Plot.area_m2), 0)).where(Plot.node_id == node.id)
        )
        return float(total or 0)

    #: The plot's own screen asks for this from both ends -- the yard's spare
    #: metres and the foraging's empty land (`db.base.remember`).
    return await remember(session, ("marked_ground", node.id), measure)


async def spare_ground(session: AsyncSession, node: Node) -> float:
    """The plot's empty land: neither built on nor cut into strips (D-210, D-246).

    The footprint, not the usable area: a two-storey house of ten metres takes
    ten from the yard, not twenty (D-125). Sites under way are **not** counted
    here -- this is the ground as it lies today, and what the foraging walks.
    """
    #: A storey is not land (D-247): nothing is built on it, nothing is marked
    #: out of it and nothing is gathered from it. Its metres are floor.
    if storey_of(node) is not None:
        return 0.0
    taken = await built_area(session, node, ground=True) + await marked_ground(session, node)
    return float(node.area_m2) - taken


async def free_ground(session: AsyncSession, node: Node) -> float:
    """What is left to spend: the empty land, minus what is already on the way.

    This is the number a new house and a new strip are both measured against:
    ground promised to a started site is ground gone, even though nothing
    stands on it yet.
    """
    return await spare_ground(session, node) - await planned_footprint(session, node)


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


async def storey_area_for(session: AsyncSession, node: Node, floor: int) -> float:
    """Metres of this plot's `floor`-th storey: the footprint of what reaches it."""
    return sum(
        float(house.footprint_m2)
        for house in await buildings_of(session, node)
        if house.floors >= floor
    )


async def spare_storeys(session: AsyncSession, node: Node) -> list[Node]:
    """Floors standing higher than anything on the plot now reaches (D-247).

    What is left after a house comes down: the rooms are still there, and
    nothing holds them up any more. The caller decides what happens to what was
    on them -- a demolition brings it into the yard, a collapse buries it -- and
    then hands them to `close_storeys`.
    """
    height = await height_of(session, node)
    return [room for room in await storeys_of(session, node) if (storey_of(room) or 0) > height]


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
