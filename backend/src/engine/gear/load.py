# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The load and the limit (D-146, D-268, D-313): what the hands carry and
how the body feels it, what the worn exoskeleton lifts, and the two doors a
pick-up asks -- goods by name and a thing that moves whole.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import battery, world
from src.engine.gear._base import INSIDE_KINDS, Overloaded, holds_things, mass_of
from src.engine.gear.worn import equipped
from src.models.identity import Body
from src.models.inventory import Container, Item
from src.units import amount_float


async def inner_mass(session: AsyncSession, catalog: Catalog, things: Sequence[Item]) -> float:
    """The mass riding inside the things themselves, kg (D-313).

    A full canister weighs its fill (D-230), and so do a full chest and a
    loaded barrow: what is carried is carried, it only lives a container
    deeper, and a lid is not a way out of the limit. Answered for a whole list
    at once -- the inventory asks it of every hand at every `look`, and the
    carry limit at every pick-up.

    Containers nest -- a chest into a chest, a chest into a barrow -- so the
    reading walks down until a layer holds nothing. Each layer is two queries,
    not two per thing; hands holding no container at all cost none; and only
    the three columns the mass needs are read, because a chest of two hundred
    stacks would otherwise be hydrated whole at every `look`.

    Containers visited are remembered. The doors cannot build a cycle -- to be
    put into a box a thing must be in the hands, and a box that holds it lies
    in a node -- but a walk that trusts that would hang a request inside a
    transaction if one ever appeared, and the answer to "how would it get
    there" must not be the only thing standing between a read and a hang.
    """
    total = 0.0
    layer = [
        (thing.id, thing.type_key) for thing in things if holds_things(catalog, thing.type_key)
    ]
    seen: set[uuid.UUID] = set()
    while layer:
        holds = [
            hold
            for hold in (
                (
                    await session.execute(
                        select(Container.id).where(
                            Container.kind.in_(INSIDE_KINDS),
                            Container.owner_id.in_([owner for owner, _ in layer]),
                        )
                    )
                )
                .scalars()
                .all()
            )
            if hold not in seen
        ]
        if not holds:
            break
        seen.update(holds)
        rows = (
            await session.execute(
                select(Item.id, Item.type_key, Item.amount).where(Item.container_id.in_(holds))
            )
        ).all()
        total += sum(mass_of(catalog, key, amount_float(amount)) for _, key, amount in rows)
        layer = [(found, key) for found, key, _ in rows if holds_things(catalog, key)]
    return total


async def carried_mass(session: AsyncSession, catalog: Catalog, body: Body) -> float:
    """The matter in the hands, kg -- before a pack lightens any of it.

    What is worn counts along with everything else. This is the figure a pack
    is applied to, and the one in which kilograms may be added at all: the
    felt load is not additive, so two readings of it must never be summed.

    What those things hold inside counts too (D-313): a full canister weighs
    its fill, a full chest weighs what is stacked in it, a loaded barrow its
    load.
    """
    things = await world.contents(session, await world.body_container(session, body))
    own = sum(mass_of(catalog, thing.type_key, amount_float(thing.amount)) for thing in things)
    fill = await inner_mass(session, catalog, things)
    return own + fill


async def load_of(
    session: AsyncSession, constants: Constants, catalog: Catalog, body: Body
) -> float:
    """How much the body carries now, kg. What is worn counts along with everything."""
    return packed(
        constants,
        catalog,
        await equipped(session, body),
        await carried_mass(session, catalog, body),
    )


def _pack_of(
    constants: Constants, catalog: Catalog, worn: dict[str, Item]
) -> dict[str, float] | None:
    """The worn pack, or nothing. One back, one pack -- the first found is it."""
    packs = constants[R.INVENTORY_PACK]
    for thing in worn.values():
        pack = packs.get(catalog.recipes.resolve(thing.type_key))
        if pack:
            return pack
    return None


def packed(constants: Constants, catalog: Catalog, worn: dict[str, Item], mass: float) -> float:
    """The load as the body feels it: the pack lightens what fits in it (D-268).

    A pack holds its first `capacity` kilograms of the load and multiplies
    them by its `factor`; packing is implied -- the first kilograms ride in
    it. The rest is carried as it is. The pack raises no limit: that is the
    exoskeleton's business, and the two never compete.

    The line is bent, not scaled: below the capacity a kilogram weighs
    `factor` of itself, above it a whole one. So the reading of a load is not
    the sum of the readings of its parts, and a question about a load that is
    about to change is a question about the whole of it -- `check_carry` and
    `matter_over` ask it in the two directions.
    """
    pack = _pack_of(constants, catalog, worn)
    if pack is None:
        return mass
    inside = min(mass, float(pack["capacity"]))
    return mass - inside * (1 - float(pack["factor"]))


def matter_over(
    constants: Constants, catalog: Catalog, worn: dict[str, Item], mass: float, limit: float
) -> float:
    """How much matter must leave a raw load of `mass` for it to fit `limit`, kg.

    `packed` read backwards, and it has to be read backwards rather than
    subtracted: with a pack the felt excess and the matter that removes it are
    different kilograms. While the load sits inside the pack's capacity, a
    kilogram out of the hands lightens the body by `factor` of itself, so
    putting down the felt excess would leave the body over the limit still.
    Past the capacity the two agree -- the only case the vault's packs have
    ever produced, and a tripwire in the tests says so the day one of them
    grows roomier than the limit.

    Matter, hence the name: `overload.shed` is the door that drops it (D-306),
    this is only the measure it drops by.
    """
    if packed(constants, catalog, worn, mass) <= limit:
        return 0.0
    return mass - matter_within(constants, catalog, worn, limit)


def matter_within(
    constants: Constants, catalog: Catalog, worn: dict[str, Item], limit: float
) -> float:
    """How much raw matter a body may hold before `limit` is felt, kg.

    `packed` inverted, and the one place it is inverted. Both directions need
    the same arithmetic -- `matter_over` counts down to the limit from above
    (D-306), `room_for` counts up to it from below (D-314) -- and two copies of
    a bent line are two chances to disagree with `check_carry`.
    """
    pack = _pack_of(constants, catalog, worn)
    if pack is None:
        return limit
    room, factor = float(pack["capacity"]), float(pack["factor"])
    #: Inside the pack the limit buys `limit / factor` kilograms of matter;
    #: past it the pack's whole discount is spent and the rest weighs itself.
    return limit / factor if factor > 0 and limit <= room * factor else limit + room * (1 - factor)


async def capacity(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    worn: dict[str, Item] | None = None,
) -> float:
    """The carry limit with worn gear in mind, kg.

    An exoskeleton raises it (D-268) -- while a charged battery rides in the
    hands. Drained, it is a frame that weighs and lifts nothing.

    `worn` is for a caller that has already read it: the load and the limit
    are two questions about the same slots, and `look` asks both on every
    glance. Left out, they are read here.
    """
    if worn is None:
        worn = await equipped(session, body)
    lift = exo_bonus(constants, catalog, worn)
    if lift > 0 and not await battery.charged_carried(session, constants, body):
        lift = 0.0
    return constants[R.INVENTORY_CARRY_MASS] + lift


def exo_bonus(constants: Constants, catalog: Catalog, worn: dict[str, Item]) -> float:
    """What the worn exoskeleton would add, kg -- powered or not."""
    bonuses = constants[R.INVENTORY_EXO_BONUS]
    return sum(bonuses.get(catalog.recipes.resolve(thing.type_key), 0.0) for thing in worn.values())


async def check_carry(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    type_key: str,
    quantity: float,
    *,
    inside: float = 0.0,
) -> None:
    """Whether this fits in the hands. Did not fit -- not taken, and that is not an error but
    weight.

    Weighed as the load **will be**, not as it was plus raw kilograms: a pack
    that lightens the first kilograms lightens the ones about to arrive too
    (D-268). Adding the thing's own mass to a load already read through the
    pack charged full weight for what the pack was going to carry at
    `factor`, and refused a pickup the body could make.

    `inside` is what rides within the thing itself (D-313): a chest arrives
    with its contents, and weighing the lid alone would let a ton through a
    door that asked about four kilograms. Doors that mint goods by name --
    a harvest, a face, a poured litre -- have nothing inside and say nothing.
    """
    bonus = mass_of(catalog, type_key, quantity) + inside
    if bonus <= 0:
        return
    worn = await equipped(session, body)
    mass = await carried_mass(session, catalog, body)
    carries = packed(constants, catalog, worn, mass)
    after = packed(constants, catalog, worn, mass + bonus)
    limit = await capacity(session, constants, catalog, body, worn)
    if after > limit:
        #: The refusal names what the body would feel, not what the thing
        #: weighs on the ground: the three figures in the message have to add
        #: up for whoever reads it.
        raise Overloaded(key="gear-overloaded", carries=carries, limit=limit, extra=after - carries)


async def moved_inside(
    session: AsyncSession, catalog: Catalog, item: Item, quantity: float
) -> float:
    """What rides inside a thing when it moves, kg (D-313).

    Only a whole row brings its contents along: splitting a stack hands over a
    bare thing, and what was inside stays with the row that keeps it. Every
    door into something bounded by mass -- the hands, a chest, a hold -- adds
    this to what it weighs, or it weighs the lid and lets the load through.
    """
    if quantity < amount_float(item.amount):
        return 0.0
    return await inner_mass(session, catalog, [item])


async def check_carry_thing(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    item: Item,
    quantity: float,
) -> None:
    """Whether this **thing** fits in the hands, contents and all (D-313).

    The door for a row that moves whole -- off the floor, out of a chest, out
    of a hold, from another's hands.

    The row is taken for the transaction first, and that is the point rather
    than a precaution: what is inside it is read here and moved a moment
    later, and between the two a second session may fill it. Read a chest
    holding five kilograms, have three hundred put in, walk off with all of
    it -- the very hole this door exists to shut, one window over. `put`
    takes the same row, so the two serialise on it (CLAUDE.md, the remainder
    rule); `world.move_stack` takes it again below, which costs nothing.
    """
    await session.execute(select(Item.id).where(Item.id == item.id).with_for_update())
    inside = await moved_inside(session, catalog, item, quantity)
    await check_carry(session, constants, catalog, body, item.type_key, quantity, inside=inside)


async def room_for(
    session: AsyncSession, constants: Constants, catalog: Catalog, body: Body, type_key: str
) -> float:
    """How much of this the hands still have room for, in units of the thing.

    `check_carry` from the other end: that door answers "does this fit", this
    one "how much of it fits". A door that hands over what it can rather than
    refusing the lot needs the figure (`rig.empty_hopper`, D-314).

    Counted in **matter**, through `matter_within`, and not by subtracting the
    felt load from the limit: with a pack the two are different kilograms
    (D-268), and the difference is what a hopper would hand over wrongly. A
    weightless thing has no bound at all.
    """
    per = catalog.recipes.mass_of(type_key)
    if per <= 0:
        return float("inf")
    worn = await equipped(session, body)
    mass = await carried_mass(session, catalog, body)
    limit = await capacity(session, constants, catalog, body, worn)
    return max(0.0, (matter_within(constants, catalog, worn, limit) - mass) / per)
