# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Two transactions at once over one vehicle standing in a node (D-157).

One of the race files (see `test_races.py` for the family's method): here the
contended row is a vehicle lying in the yard of nobody's land (D-198, D-204),
and the two questions the harness asks of it -- is it standing here, and is
anybody pulling it already. Both are asked of the row after its lock. The
wheelbarrow is the vehicle to race over where a hand is one of the two sides:
empty, it weighs less than the hands carry (D-146), so `storage.pick` lifts it
like any cargo -- the hole the harness and the lift meet in.

* a lift goes first -- the harness must find the barrow gone from the yard,
  not put a harness on a barrow now in somebody's hands, which the carter's
  first leg would then pull out of those hands (`transport.follow`);
* a harness goes first -- the lift must find the barrow pulled, not carry
  off a vehicle somebody is harnessed to;
* a fire goes first (`plates._burn`) -- the harness must find the cart
  gone, in words, not crash on a row that is no longer there;
* two harnesses at once -- one is pulled and the other is told so. The
  unique constraint on `harness.item_id` already kept a second harness from
  landing; what went wrong was the answer: the second insert died on the
  constraint, and the player read "the server failed" instead of the world's
  refusal.

The handshake is `conftest._until_blocked_by`: the side that went first
keeps its transaction open, holding the vehicle's row, and commits only once
the other side has provably walked into it.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import _until_blocked_by
from gone_kit import _burning, _lifting
from src.constants import Catalog, Constants
from src.engine import gear, storage, transport, world
from src.models.identity import Body
from src.models.inventory import Item
from src.models.travel import Harness
from src.models.world import Node

BARROW = "wheelbarrow"
CART = "cart"


async def _yard_with(session: AsyncSession, constants: Constants, catalog: Catalog, vehicle: str):
    """A vehicle in the yard of nobody's node and two empty-handed bodies beside it.

    Returns the node, the two bodies and the vehicle.
    """
    #: What the race needs of the vault, asserted rather than assumed: the
    #: vehicle is one, and the barrow fits in empty hands.
    assert transport.is_vehicle(catalog, vehicle)
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.yoke.{stamp}", "Yard", area_m2=200)
    first = await world.print_body(
        session, await world.create_identity(session, f"First-{stamp}"), node
    )
    second = await world.print_body(
        session, await world.create_identity(session, f"Second-{stamp}"), node
    )
    if vehicle == BARROW:
        room = await gear.capacity(session, constants, catalog, first)
        assert gear.mass_of(catalog, BARROW, 1) < room
    thing = await world.grant_item(
        session, await world.node_container(session, node), vehicle, quality=60, origin="test"
    )
    await session.flush()
    return node, first, second, thing


async def _harnesses_of(factory: async_sessionmaker[AsyncSession], item_id) -> list[Harness]:
    async with factory() as db:
        return list(
            (await db.execute(select(Harness).where(Harness.item_id == item_id))).scalars().all()
        )


async def _harnessing(
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    body_id: uuid.UUID,
    item_id: uuid.UUID,
    *,
    held: asyncio.Event | None = None,
) -> str:
    """Harness this body to this vehicle. Given `held`, the side that goes
    first: the row is held until the other side provably waits on it."""
    async with factory() as db, db.begin():
        me = await db.get(Body, body_id)
        thing = await db.get(Item, item_id)
        assert me is not None and thing is not None
        pulled = await transport.harness(db, constants, catalog, me, thing)
        if held is not None:
            held.set()
            await _until_blocked_by(factory, db)
        return pulled.type_key


async def test_a_barrow_lifted_before_the_harness_is_not_harnessed_in_the_hands(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """A body harnesses to a vehicle standing here after the lock, not before it.

    The lifter picks the barrow up and keeps the transaction open, holding its
    row. The carter, having seen the barrow in the yard, harnesses to it and
    walks into the row (the harness's own lock, or before it the key-share
    lock its foreign key takes). The lift commits. Judged by the sight from
    before the wait, the harness landed on a barrow in the lifter's hands --
    and the carter's first leg would pull it out of them. Judged after the
    wait, the barrow is no longer here.
    """
    _, lifter, carter, barrow = await _yard_with(session, constants, catalog, BARROW)
    lifter_id, carter_id, barrow_id = lifter.id, carter.id, barrow.id
    await session.commit()

    held = asyncio.Event()

    async def yoke() -> str:
        await held.wait()
        return await _harnessing(factory, constants, catalog, carter_id, barrow_id)

    taken, yoked = await asyncio.gather(
        _lifting(factory, constants, catalog, lifter_id, barrow_id, held=held),
        yoke(),
        return_exceptions=True,
    )

    assert taken == pytest.approx(1), taken
    assert isinstance(yoked, transport.NotHere), yoked
    assert yoked.key == "transport-not-here", yoked.key
    assert await _harnesses_of(factory, barrow_id) == []
    async with factory() as db:
        thing = await db.get(Item, barrow_id)
        me = await db.get(Body, lifter_id)
        assert thing is not None and me is not None
        assert thing.container_id == (await world.body_container(db, me)).id


async def test_a_barrow_harnessed_before_the_lift_stays_in_the_yard(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """A hand does not carry off a vehicle somebody is harnessed to.

    The carter harnesses to the barrow and keeps the transaction open, holding
    its row. The lifter, having seen the barrow lying free in the yard, picks
    it up and walks into the row. The harness commits. Asked nothing about the
    harness, the lift went on and the barrow went into the lifter's hands with
    the carter still harnessed to it. Asked after the wait, the barrow is
    pulled and stays where it stands.
    """
    node, carter, lifter, barrow = await _yard_with(session, constants, catalog, BARROW)
    node_id, carter_id, lifter_id, barrow_id = node.id, carter.id, lifter.id, barrow.id
    await session.commit()

    held = asyncio.Event()

    async def lift() -> float:
        await held.wait()
        return await _lifting(factory, constants, catalog, lifter_id, barrow_id)

    yoked, lifted = await asyncio.gather(
        _harnessing(factory, constants, catalog, carter_id, barrow_id, held=held),
        lift(),
        return_exceptions=True,
    )

    assert yoked == BARROW, yoked
    assert isinstance(lifted, storage.StorageError), lifted
    assert lifted.key == "storage-harnessed", lifted.key
    assert [line.body_id for line in await _harnesses_of(factory, barrow_id)] == [carter_id]
    async with factory() as db:
        thing = await db.get(Item, barrow_id)
        spot = await db.get(Node, node_id)
        assert thing is not None and spot is not None
        assert thing.container_id == (await world.node_container(db, spot)).id


async def test_two_bodies_harnessing_one_cart_are_answered_in_words(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """The second of two carters reaching for one cart is refused, not crashed.

    The first harnesses to the cart and keeps the transaction open. The second,
    having seen the cart with nobody in the shafts, harnesses too and walks
    into the first. The first commits. Checked by the sight from before the
    wait, the second insert went to the database and died on
    `uq_harness_item` -- one harness stood, as it must, but the player was
    told the server had failed. Checked after the vehicle's lock, the cart is
    already taken, and the refusal says so.
    """
    _, first, second, cart = await _yard_with(session, constants, catalog, CART)
    first_id, second_id, cart_id = first.id, second.id, cart.id
    await session.commit()

    held = asyncio.Event()

    async def yoke() -> str:
        await held.wait()
        return await _harnessing(factory, constants, catalog, second_id, cart_id)

    yoked, refused = await asyncio.gather(
        _harnessing(factory, constants, catalog, first_id, cart_id, held=held),
        yoke(),
        return_exceptions=True,
    )

    assert yoked == CART, yoked
    assert isinstance(refused, transport.AlreadyHarnessed), refused
    assert refused.key == "transport-vehicle-taken", refused.key
    assert [line.body_id for line in await _harnesses_of(factory, cart_id)] == [first_id]


async def test_a_cart_burnt_while_harnessing_is_gone_in_words(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """A carter reaching for a cart the fire took is told it is gone (D-251).

    The fire takes what lies in the yard, the cart among it, and holds the
    rows. The carter, having seen the cart standing there, harnesses to it and
    walks into its row. The fire commits. The reread after the lock finds no
    row: the refusal names the cart, and no harness is left pointing at it.
    """
    node, carter, _, cart = await _yard_with(session, constants, catalog, CART)
    node_id, carter_id, cart_id = node.id, carter.id, cart.id
    await session.commit()

    held = asyncio.Event()

    async def yoke() -> str:
        await held.wait()
        return await _harnessing(factory, constants, catalog, carter_id, cart_id)

    burnt, yoked = await asyncio.gather(
        _burning(factory, node_id, held), yoke(), return_exceptions=True
    )

    assert burnt == pytest.approx(1), burnt
    assert isinstance(yoked, transport.NotHere), yoked
    assert yoked.key == "thing-gone", yoked.key
    assert await _harnesses_of(factory, cart_id) == []
