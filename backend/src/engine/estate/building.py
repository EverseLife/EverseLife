# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""estate: building (D-106, D-125).

Split out of `engine/estate.py` along its sections (review 2026-08-23, wave 3).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants, current_catalog
from src.constants import registry as R
from src.constants.catalog import ItemKind
from src.db.base import remember
from src.engine import craft, events, gear, goods, storage, travel, world
from src.engine.estate._base import EstateError, NoRoom, TooSmall, UnknownKind
from src.engine.jobs import enqueue
from src.models.estate import Building
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Item
from src.models.job import Job, JobKind, JobState
from src.models.world import Node, Planet
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
    roofed = await built_area(session, node) > 0
    inside: list = []
    outside: list = []
    for thing in await world.contents(session, await world.node_container(session, node)):
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
    roofed = await built_area(session, node)
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
    under = await built_area(session, node, ground=True)
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

    area = await built_area(session, node)
    in_total = int(area // constants[R.BUILD_SLOTS_PER_AREA])

    book = current_catalog().recipes
    things = await world.contents(session, await world.node_container(session, node))
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
        raise UnknownKind(f"«{kind}» — не тип здания; строят из: {', '.join(types)}")
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


async def free_ground(session: AsyncSession, node: Node) -> float:
    """The plot's unbuilt remainder: the yard, minus what is already on the way."""
    taken = await built_area(session, node, ground=True) + await planned_footprint(session, node)
    return float(node.area_m2) - taken


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
        raise EstateError("мёртвое тело не строит")
    await travel.require_here(session, body)
    if body.node_id != node.id:
        raise EstateError("строят ногами: дойдите до участка")
    nobodys = node.owner_identity_id is None and node.owner_city_id is None
    if not nobodys and node.owner_identity_id != body.identity_id:
        raise EstateError("участок не ваш: строят у себя")
    if floors < 1:
        raise EstateError("дом без этажей — это яма")
    #: Pyroxis does not get built on (D-230): the ground shakes too often for
    #: a wall to outlive its builder, and what stands there arrived by ship.
    if node.planet is Planet.PYROXIS:
        raise EstateError(
            "на Пироксисе не строят: землетрясения рушат постройки быстрее, "
            "чем их ставят. Жильё здесь — борт корабля"
        )

    #: Unnamed, the house is of the plainest type there is: that is what the
    #: world was built of before types arrived, and the default must not silently
    #: become the expensive one.
    kind = kind or kinds(constants)[0]
    composition(constants, kind)

    smallest = constants[R.BUILD_AREA_MIN]
    if area < smallest:
        raise TooSmall(
            f"пятно меньше {smallest:.0f} м² — это навес, а не здание: просят {area:.0f}"
        )

    free = await free_ground(session, node)
    if area > free:
        going = await planned_footprint(session, node)
        started = f", в стройке {going:.0f}" if going > 0 else ""
        raise NoRoom(
            f"на участке {float(node.area_m2):.0f} м², свободно {max(free, 0):.0f}"
            f"{started}: ещё {area:.0f} не помещается"
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
        raise EstateError("стройка уже поставлена")
    return job
