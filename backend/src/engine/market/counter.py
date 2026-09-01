# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The terminal and the cells behind it: where the goods physically lie.

Matter moves only physically (D-044): `load` and `take` demand a present body,
and everything else in the market moves stacks between cells of one terminal
through `_move`. The cell is the unit of locking -- `sell`, `load` and `take`
all read "what is free" and then move it, so `stall(lock=True)` queues them
(review 2026-08-23).
"""

from __future__ import annotations

import math
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants, current, current_catalog
from src.constants import current_catalog as _catalog
from src.constants import registry as R
from src.engine import craft, events, gear, liquid, stock, storage, travel, world
from src.engine.market._base import (
    TERMINAL,
    MarketError,
    NoGoods,
    NoRoom,
    NoTerminal,
    TankFull,
    _volume,
    split_key,
    tier_of,
)
from src.engine.world import body_container, node_container, station_names
from src.models.event import EventKind
from src.models.identity import Body
from src.models.inventory import Container, ContainerKind, Item
from src.models.market import Order, OrderSide, OrderState
from src.models.world import Node
from src.units import AMOUNT_SCALE, amount_float


async def terminal(session: AsyncSession, node: Node) -> Item:
    """The node's terminal. No terminal -- no trade, as there is none in an open field."""
    where = await node_container(session, node)

    found = (
        await session.execute(
            select(Item)
            .where(
                Item.container_id == where.id,
                Item.type_key.in_(station_names(TERMINAL)),
            )
            #: Deterministic (D-215 allows a second terminal): the capacity
            #: check locks THE terminal row, and two loads locking two
            #: different terminals of one node would both see the same room.
            .order_by(Item.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if found is None:
        raise NoTerminal(key="market-no-terminal", node=node.key)
    #: A terminal in a frozen node is silent (D-231): the machine is here, the
    #: heat is not, and the rule is the same one that stops the workbench.
    from src.engine import frost  # noqa: PLC0415 -- lazy: breaks the import cycle with frost

    await frost.require_working(session, current(), node, found.type_key)
    return found


async def stall(
    session: AsyncSession,
    node: Node,
    identity_id: uuid.UUID,
    *,
    create: bool = True,
    lock: bool = False,
) -> Container | None:
    """The identity's cell in the node's terminal: its loaded goods and its purchases.

    With `create=False` a missing cell is None, not a new row: reads
    (`look`) must not write. With `lock=True` the cell's row is locked for
    the transaction: `sell`, `load` and `take` all read "what is free" and
    then move it, and two of them at once on one cell must queue (review
    2026-08-23). Lock order on the market: cell -> orders -> accounts."""
    stmt = select(Container).where(
        Container.kind == ContainerKind.MARKET,
        Container.owner_id == identity_id,
        Container.node_id == node.id,
    )
    if lock:
        stmt = stmt.with_for_update()
    container = (await session.execute(stmt)).scalar_one_or_none()
    if container is None and create:
        container = Container(kind=ContainerKind.MARKET, owner_id=identity_id, node_id=node.id)
        session.add(container)
        await session.flush()
    return container


async def load(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    type_key: str,
    quantity: float,
    *,
    tier: str | None = None,
) -> float:
    """Load goods into the terminal. In person: goods are carried on foot.

    `tier` names which stacks go: without it the worst go first, and the good
    ore stays in the sack for the smelt it was mined for (D-058).
    """
    node = await _node_of(session, body)
    machine = await terminal(session, node)
    inventory = await body_container(session, body)
    into = await stall(session, node, body.identity_id, lock=True)

    want = _volume(_catalog(), type_key, quantity)
    if _catalog().recipes.is_liquid(split_key(type_key)[0]):
        #: A liquid is poured, not put (D-255): out of the vessels in the
        #: hands into the terminal's tank -- the cells behind the counter are
        #: its inside, and that is the whole reason a liquid may lie there at
        #: all (D-230). The tank is finite, and the check runs under the
        #: terminal's own lock: two sellers pouring at once must not both
        #: see the same room.
        moved = await _pour_in(
            session, constants, node, machine, inventory, into, type_key, want, tier
        )
    else:
        moved = await _move(
            session,
            inventory,
            into,
            type_key,
            want,
            tier=tier,
            constants=constants,
        )
    await events.record(
        session,
        EventKind.MARKET_LOADED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        type_key=type_key,
        amount=amount_float(moved),
    )
    return amount_float(moved)


async def take(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    type_key: str,
    quantity: float,
    *,
    tier: str | None = None,
) -> float:
    """Take your own from the terminal. What is committed to an order is not given twice."""
    node = await _node_of(session, body)
    await terminal(session, node)
    stock = await stall(session, node, body.identity_id, lock=True)
    inventory = await body_container(session, body)

    free = await _free(session, constants, node, body.identity_id, type_key, tier)
    want = min(_volume(_catalog(), type_key, quantity), free)
    if want <= 0:
        raise NoGoods(key="market-nothing-free", goods=type_key)

    if current_catalog().recipes.is_liquid(split_key(type_key)[0]):
        #: Poured, not handed (D-255): into the vessels the buyer carries, by
        #: their room, and what has no room stays in the tank waiting -- the
        #: carry limit judges the vessel, a full canister weighs its fill.
        moved = await _pour_out(session, constants, stock, inventory, type_key, want, tier)
    else:
        #: No more than the limit is taken in hand: for the rest come with a wagon (D-146).

        await gear.check_carry(
            session,
            constants,
            current_catalog(),
            body,
            split_key(type_key)[0],
            amount_float(want),
        )

        moved = await _move(
            session, stock, inventory, type_key, want, tier=tier, constants=constants
        )
    await events.record(
        session,
        EventKind.MARKET_TAKEN,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        type_key=type_key,
        amount=amount_float(moved),
    )
    return amount_float(moved)


async def _node_of(session: AsyncSession, body: Body) -> Node:
    """The node the body **stands** in. In transit it is nowhere (D-107)."""
    await travel.require_here(session, body)
    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise MarketError(key="market-body-off-node")
    return node


async def _free(
    session: AsyncSession,
    constants: Constants,
    node: Node,
    identity_id: uuid.UUID,
    type_key: str,
    tier: str | None,
) -> int:
    """How much of the goods in the terminal is not committed to orders.

    Locks the cell: `sell` reads this and then commits the goods to an order,
    and two sells at once must not both see the same free stock."""
    stock = await stall(session, node, identity_id, lock=True)
    items = await _stacks(session, stock, type_key, tier, constants)
    have = sum(item.amount for item in items)

    stmt = select(func.coalesce(func.sum(Order.amount_left), 0)).where(
        Order.node_id == node.id,
        Order.identity_id == identity_id,
        Order.type_key == type_key,
        Order.side == OrderSide.SELL,
        Order.state == OrderState.ACTIVE,
    )
    if tier is not None:
        stmt = stmt.where(Order.tier == tier)
    reserved = int(await session.scalar(stmt) or 0)
    return max(0, have - reserved)


async def _stacks(
    session: AsyncSession,
    container: Container,
    type_key: str,
    tier: str | None,
    constants: Constants,
    *,
    floor: int = 0,
) -> list[Item]:
    """Stacks of the needed goods, worst first: the good ones are saved.

    `floor` drops what is worse than a buyer agreed to take (D-239). A thing
    without quality at all -- energy, money -- has no way to clear a floor
    above zero, and is dropped by the same rule rather than by a name in code.
    """
    kind, recipe = split_key(type_key)
    stmt = select(Item).where(Item.container_id == container.id, Item.type_key == kind)
    if recipe is not None:
        stmt = stmt.where(Item.recipe_key == recipe)
    elif kind in _carrier():
        #: A bare "Рецепт" on the counter is a blank one -- a written carrier
        #: is always named together with what is on it.
        stmt = stmt.where(Item.recipe_key.is_(None))
    #: The stacks themselves are locked: `_move` splits and re-parents them,
    #: and two trades off one stack must see each other's decrement.
    rows = (
        (
            await session.execute(
                stmt.order_by(
                    Item.quality.asc().nulls_first(), Item.created_at.asc()
                ).with_for_update()
            )
        )
        .scalars()
        .all()
    )
    fitting = list(rows)
    if tier is not None:
        fitting = [item for item in fitting if tier_of(constants, _quality(item)) == tier]
    if floor > 0:
        good_enough = []
        for item in fitting:
            quality = _quality(item)
            if quality is not None and quality >= floor:
                good_enough.append(item)
        fitting = good_enough
    return fitting


def _quality(item: Item) -> float | None:
    return None if item.quality is None else float(item.quality)


async def _move(
    session: AsyncSession,
    source: Container,
    target: Container,
    type_key: str,
    quantity: int,
    *,
    tier: str | None,
    constants: Constants,
    floor: int = 0,
) -> int:
    """Move goods from container to container, splitting stacks as needed.

    `floor` is the buyer's quality floor (D-239): below it nothing is taken,
    and above it the worst still goes first -- the seller keeps the better.
    """
    left = quantity
    for item in await _stacks(session, source, type_key, tier, constants, floor=floor):
        if left <= 0:
            break
        take = min(left, item.amount)
        if take == item.amount:
            item.container_id = target.id
            await world.stack_up(session, item)
        else:
            #: The split-off part is the same thing: same mark, shelf life, dish
            #: kind and fineness. Losing them when splitting a stack would
            #: depersonalise the goods on the counter.

            item.amount -= take
            sold = Item(
                container_id=target.id,
                type_key=item.type_key,
                amount=take,
                quality=item.quality,
                condition=item.condition,
                condition_cap=item.condition_cap,
                maker_identity_id=item.maker_identity_id,
                made_at=item.made_at,
                made_node_id=item.made_node_id,
                spoils_at=item.spoils_at,
                flavor=item.flavor,
                roles_filled=item.roles_filled,
                fineness=item.fineness,
                recipe_key=item.recipe_key,
            )
            session.add(sold)
            await world.stack_up(session, sold)
        left -= take
    await session.flush()
    return quantity - left


async def _pour_in(
    session: AsyncSession,
    constants: Constants,
    node: Node,
    machine: Item,
    inventory: Container,
    into: Container,
    type_key: str,
    want: int,
    tier: str | None,
) -> int:
    """Canister -> tank, capacity-checked under the terminal's lock (D-255)."""
    catalog = _catalog()
    kind = split_key(type_key)[0]
    #: The terminal row serializes the capacity check: two sellers pouring at
    #: once would otherwise both read the same room (the quality bar).
    await session.execute(select(Item.id).where(Item.id == machine.id).with_for_update())
    unit = catalog.recipes.mass_of(kind)
    stored = await _tank_mass(session, catalog, node)
    room_kg = max(0.0, constants[R.MARKET_TANK_CAPACITY] - stored)
    #: Floored: a capacity check that itself rounds past the capacity is not
    #: one. Half-even `amount()` could go half a thousandth over.
    room = want if unit <= 0 else min(want, _units_floor(room_kg / unit))
    if room <= 0:
        raise TankFull(key="market-tank-full", goods=kind)

    #: The vessels are locked before their stacks are read, in id order --
    #: the one rule every pour keeps (`liquid._lock`): `_stacks` below locks
    #: by quality order, and without this first lock a `market.load` against
    #: a batch draining the same canister is a deadlock.
    vessels = await liquid.vessels_in(session, catalog, inventory)
    await stock.lock_items(session, vessels)
    moved = 0
    for vessel in vessels:
        if moved >= room:
            break
        inside = await storage.inside(session, vessel)
        moved += await _move(
            session, inside, into, type_key, room - moved, tier=tier, constants=constants
        )
    if moved <= 0:
        raise NoGoods(key="market-nothing-loaded", goods=kind)
    return moved


async def _pour_out(
    session: AsyncSession,
    constants: Constants,
    cell: Container,
    inventory: Container,
    type_key: str,
    want: int,
    tier: str | None,
) -> int:
    """Tank -> the buyer's vessels, by their room (D-255). No room -- it waits."""
    catalog = _catalog()
    kind = split_key(type_key)[0]
    unit = catalog.recipes.mass_of(kind)
    vessels = await liquid.vessels_in(session, catalog, inventory)
    #: The vessels are locked before their room is read, in id order -- the
    #: same rule every pour keeps (`liquid._lock`).
    await stock.lock_items(session, vessels)
    rooms: list[int] = []
    for vessel in vessels:
        room_kg = await liquid.free_in(session, catalog, vessel)
        rooms.append(want if unit <= 0 else _units_floor(room_kg / unit))
    planned = min(want, sum(rooms))
    if planned <= 0:
        raise NoRoom(key="market-liquid-no-room", goods=kind)
    #: The carry limit judges the pour, exactly as `liquid.pour` does into a
    #: carried vessel: a full canister weighs its fill, and the door must
    #: refuse rather than let the body walk out overloaded (D-146).
    body = await session.get(Body, inventory.owner_id)
    if body is not None:
        await gear.check_carry(session, current(), catalog, body, kind, amount_float(planned))
    moved = 0
    for vessel, room in zip(vessels, rooms, strict=True):
        if moved >= planned:
            break
        take_units = min(planned - moved, room)
        if take_units <= 0:
            continue
        inside = await storage.inside(session, vessel)
        moved += await _move(
            session, cell, inside, type_key, take_units, tier=tier, constants=constants
        )
    if moved <= 0:
        raise NoRoom(key="market-liquid-no-room", goods=kind)
    return moved


async def _tank_mass(session: AsyncSession, catalog, node: Node) -> float:
    """Kilograms of liquid across every cell of this node's terminal.

    Summed by the database: a busy counter holds hundreds of stacks, and a
    capacity check must not haul them all out to weigh one kind of thing.
    """
    book = catalog.recipes
    liquids = tuple(book.liquid)
    if not liquids:
        return 0.0
    rows = (
        await session.execute(
            select(Item.type_key, func.sum(Item.amount))
            .join(Container, Item.container_id == Container.id)
            .where(
                Container.kind == ContainerKind.MARKET,
                Container.node_id == node.id,
                Item.type_key.in_(liquids),
            )
            .group_by(Item.type_key)
        )
    ).all()
    return sum(amount_float(int(total)) * book.mass_of(kind) for kind, total in rows)


def _units_floor(value: float) -> int:
    """Units for a room, floored: past-the-brim is not a rounding mode."""
    return max(0, math.floor(value * AMOUNT_SCALE))


def _carrier() -> tuple[str, ...]:

    return craft.carrier_names()
