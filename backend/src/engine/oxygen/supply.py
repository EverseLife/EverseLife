# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""What a hull holds and a body carries: breathable stacks, the reserve, the
water and the charge aboard, the suit and the cylinders on the belt.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, ConstantError, Constants
from src.constants import registry as R
from src.engine import ship as vessels
from src.engine import world
from src.engine.oxygen._base import AIR, ENERGY, SUIT, WATER
from src.models.gear import Equipped
from src.models.identity import Body
from src.models.inventory import Container, ContainerKind, Item
from src.models.ship import Ship
from src.units import (
    amount_float,
)

# --- what a hull holds ---------------------------------------------------------


async def breathable_stacks(
    session: AsyncSession, ship: Ship, *what: str, things: list[Item] | None = None
) -> list[Item]:
    """The named liquids in any vessel **standing in a compartment**.

    Wider than the fuel a passage burns, and narrower than "everything aboard".
    Both edges are meant:

    * fuel goes from the tanks and nowhere else (D-230), because the engines are
      plumbed to them: a canister in the hold weighs and does not burn. Air and
      the water it is made of are plumbed nowhere -- the life support is a
      machine standing in a room, and what a crew carries to it, it uses. A crew
      suffocating beside a hold full of oxygen because the bottles were the
      wrong shape is not a rule, it is a bug with an explanation;
    * a vessel **packed into a chest** is stowed cargo, and the system does not
      reach into somebody's luggage for it. It is the same rule one step along,
      so it is said out loud here and in D-240 rather than left to be discovered
      by a crew that put the spare oxygen away tidily.

    Hence exactly one level: what stands in the room, and what is inside it.
    `things` is that reading when the caller already has it (`ship._things`
    walks precisely those two levels).
    """
    hold = things if things is not None else await vessels._things(session, ship)
    wanted = set(what)
    return sorted((one for one in hold if one.type_key in wanted), key=lambda one: str(one.id))


async def reserve(session: AsyncSession, ship: Ship) -> float:
    """Oxygen the crew can actually breathe: what lies in the vessels aboard."""
    stacks = await breathable_stacks(session, ship, AIR)
    return sum(amount_float(stack.amount) for stack in stacks)


async def water_aboard(
    session: AsyncSession, ship: Ship, *, things: list[Item] | None = None
) -> float:
    """Water aboard: what the life support turns into air."""
    stacks = await breathable_stacks(session, ship, WATER, things=things)
    return sum(amount_float(stack.amount) for stack in stacks)


async def _liquids(
    session: AsyncSession, ship: Ship, *, things: list[Item] | None = None
) -> tuple[float, float]:
    """Air and water at once, in **one** reading of the hold.

    The console asks both of every hull it lists, and the walk into a vessel is
    three joins: asking twice was the same fan-out `profile` was cut down for
    once already (review 2026-08-23).
    """
    stacks = await breathable_stacks(session, ship, AIR, WATER, things=things)
    air = sum(amount_float(one.amount) for one in stacks if one.type_key == AIR)
    water = sum(amount_float(one.amount) for one in stacks if one.type_key == WATER)
    return air, water


def _per_unit(catalog: Catalog, what: str) -> float:
    """How much of `what` one unit of air costs, by the vault's recipe.

    Read from the catalog rather than written here: the electrolysis line is
    content (D-065), and the life support is that line running by itself.
    """
    try:
        made = catalog.recipes.recipe(AIR)
    except ConstantError:  # pragma: no cover -- the vault always knows the air
        return 0.0
    return float(made.amounts.get(what, 0.0))


def hull_draw(constants: Constants, crew: int) -> float:
    """What a crew of this size breathes an hour aboard."""
    return crew * constants[R.OXYGEN_CREW_DRAW]


async def hull_output(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    ship: Ship,
    *,
    things: list[Item] | None = None,
    water: float | None = None,
) -> float:
    """What the life support can actually make an hour, here and now.

    Three ceilings, and the reading must respect all three or it is a lie about
    the one refusal it exists for:

    * **the systems** -- `life_support` already says how many people they hold
      (D-202), and a system breathes for exactly as many. A crew too big for
      them starts to suffocate for the very reason such a ship may not cast off;
    * **the water** in the tanks, and
    * **the charge** in the batteries of the ship's rooms.

    A hull with a system and empty water tanks makes nothing, and if this said
    otherwise the console would draw a full bar and a calm rate right up to the
    tick that starts killing -- which is exactly the death by surprise the whole
    module is built against.
    """
    holds = await vessels.life_support(session, constants, ship, things=things)
    made = holds * constants[R.OXYGEN_CREW_DRAW]
    if made <= 0:
        return 0.0
    per_water = _per_unit(catalog, WATER)
    if per_water > 0:
        have = water if water is not None else await water_aboard(session, ship)
        made = min(made, have / per_water)
    per_energy = _per_unit(catalog, ENERGY)
    if per_energy > 0:
        made = min(made, await _charge_aboard(session, constants, ship) / per_energy)
    return max(0.0, made)


async def _charge_aboard(session: AsyncSession, constants: Constants, ship: Ship) -> float:
    """How much charge stands in the ship's rooms. A **read**: nothing is spent.

    Read straight off the stacks rather than through `energy.batteries_in`:
    that one creates the room's yard where there is none, and a reading may not
    write (CLAUDE.md).
    """
    from src.engine import energy  # noqa: PLC0415 -- lazy: breaks the cycle with energy

    rooms = await vessels.nodes_of(session, ship)
    if not rooms:  # pragma: no cover -- a ship always has its connector
        return 0.0
    cells = (
        (
            await session.execute(
                select(Item)
                .join(Container, Container.id == Item.container_id)
                .where(
                    Container.kind == ContainerKind.NODE,
                    Container.owner_id.in_([room.id for room in rooms]),
                    Item.type_key.in_(world.station_names(energy.BATTERY)),
                    Item.installed.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    return sum(energy.charge_of(constants, cell) * amount_float(cell.amount) for cell in cells)


# --- what a body carries -------------------------------------------------------


async def suited(session: AsyncSession, catalog: Catalog, body: Body) -> bool:
    """Whether a suit is worn. Not carried -- worn: the suit is the connection."""
    worn = (
        (
            await session.execute(
                select(Item)
                .join(Equipped, Equipped.item_id == Item.id)
                .where(Equipped.body_id == body.id)
            )
        )
        .scalars()
        .all()
    )
    suits = world.station_names(SUIT)
    return any(catalog.recipes.resolve(thing.type_key) in suits for thing in worn)


async def cylinders(session: AsyncSession, body: Body) -> list[Item]:
    """The oxygen a body can actually breathe: what lies inside vessels in its hands.

    Inside, not among: a liquid exists only in a vessel (D-230), so this is the
    stacks of air in the storages of the things in the pocket -- and a vessel
    standing in the node is somebody's property of the place, not this body's
    breath.
    """
    pocket = await world.body_container(session, body)
    carried = select(Item.id).where(Item.container_id == pocket.id)
    insides = select(Container.id).where(
        Container.kind == ContainerKind.STORAGE, Container.owner_id.in_(carried)
    )
    rows = await session.execute(
        select(Item).where(Item.container_id.in_(insides), Item.type_key == AIR).order_by(Item.id)
    )
    return list(rows.scalars().all())


async def carried(session: AsyncSession, body: Body) -> float:
    return sum(amount_float(stack.amount) for stack in await cylinders(session, body))
