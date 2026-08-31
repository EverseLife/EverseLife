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

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants, current, current_catalog
from src.constants import current_catalog as _catalog
from src.engine import craft, events, gear, travel, world
from src.engine.market._base import (
    TERMINAL,
    MarketError,
    NoGoods,
    NoTerminal,
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
from src.units import amount_float


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
    await terminal(session, node)
    inventory = await body_container(session, body)
    into = await stall(session, node, body.identity_id, lock=True)

    moved = await _move(
        session,
        inventory,
        into,
        type_key,
        _volume(_catalog(), type_key, quantity),
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

    #: No more than the limit is taken in hand: for the rest come with a wagon (D-146).

    await gear.check_carry(
        session,
        constants,
        current_catalog(),
        body,
        split_key(type_key)[0],
        amount_float(want),
    )

    moved = await _move(session, stock, inventory, type_key, want, tier=tier, constants=constants)
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


def _carrier() -> tuple[str, ...]:

    return craft.carrier_names()
