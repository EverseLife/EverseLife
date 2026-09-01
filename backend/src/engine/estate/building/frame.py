# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""What stands on the plot, read but not raised: the buildings and their
storeys, the built and the marked ground, the mass on the floors and in
the yard, the slots a workshop offers and the space a home gives.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants, current_catalog
from src.constants import registry as R
from src.constants.catalog import ItemKind
from src.db.base import forget, remember
from src.engine import gear, storage, world
from src.engine.estate._base import (
    STOREY,
    storey_of,
)
from src.models.estate import Building
from src.models.farm import Plot
from src.models.inventory import Item
from src.models.job import Job, JobKind, JobState
from src.models.world import Node
from src.units import (
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
