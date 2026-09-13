# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Two transactions at once over a loaded cart the fire comes for (D-157, D-197).

One of the race files (see `test_races.py` for the family's method): here the
contended rows are the sacks in a cart's hold. The fire burns a cart with its
load (`world.destroy` opens the hold as it opens a chest), and the carter
beside it is unloading -- doing exactly what the window before an eruption is
for. The sack is locked by both: by the unload's move (`world.move_stack`),
and by the fire's walk into the hold, which rereads it after the lock.

* the unload goes first -- the fire must find the sack in the carter's hands
  and burn the cart and the rest, not queue its delete behind the move and
  take the sack out of the hands it has just landed in;
* the fire goes first -- the unload must be told the sack is gone, in words,
  not crash on a row that is no longer there. Before the hold was opened the
  fire did not get this far at all: it died on the carter's harness.

The handshake is `automat_kit._until_blocked_by`: the side that went first
keeps its transaction open, holding the sack's row, and commits only once the
other side has provably walked into it.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from automat_kit import _until_blocked_by
from src.constants import Catalog, Constants
from src.engine import gear, plates, transport, world
from src.engine.world.things import ItemGone
from src.models.identity import Body
from src.models.inventory import Container, Item
from src.models.travel import Harness
from src.models.world import Layer, Node
from src.units import amount_float

CARGO = "iron_ore"
CART = "cart"


async def _field_with_a_loaded_cart(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """A field and a carter harnessed to a cart with one sack in its hold.

    Committed; returns the field, the carter, the cart and the sack.
    """
    stamp = uuid.uuid4().hex[:8]
    field = await world.create_node(
        session, f"terra.burn.{stamp}", "Field", area_m2=400, layer=Layer.PLANET
    )
    who = await world.create_identity(session, f"Carter-{stamp}")
    carter = await world.print_body(session, who, field)
    cart = await world.grant_item(
        session, await world.node_container(session, field), CART, origin="test"
    )
    await transport.harness(session, constants, catalog, carter, cart)
    sack = await world.grant_item(
        session,
        await world.body_container(session, carter),
        CARGO,
        amount=10 / gear.mass_of(catalog, CARGO, 1),
        origin="test",
    )
    await transport.load(session, constants, catalog, carter, sack)
    ids = (field.id, carter.id, cart.id, sack.id)
    await session.commit()
    return ids


async def _unload(
    db: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    carter_id: uuid.UUID,
    sack_id: uuid.UUID,
) -> float:
    """Unload the sack into the carter's hands, the body taken as a command takes it."""
    me = await db.get(Body, carter_id, with_for_update=True, populate_existing=True)
    sack = await db.get(Item, sack_id)
    assert me is not None and sack is not None
    return await transport.unload(db, constants, catalog, me, sack)


async def _burn(db: AsyncSession, field_id: uuid.UUID) -> float:
    place = await db.get(Node, field_id)
    assert place is not None
    return await plates._burn(db, [place])


async def test_a_sack_unloaded_before_the_fire_is_not_burnt_in_the_hands(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """The fire rereads what is in a hold after the sack's lock.

    The carter unloads and keeps the transaction open, holding the sack. The
    fire takes the cart and walks into the sack in its hold. The unload
    commits. Judged by the sight from before the wait, the fire deleted the
    sack in the carter's hands; judged after it, the sack is no longer in the
    hold, and only the cart burns.
    """
    field_id, carter_id, cart_id, sack_id = await _field_with_a_loaded_cart(
        session, constants, catalog
    )

    async def fire() -> float:
        async with factory() as db, db.begin():
            return await _burn(db, field_id)

    fires: list[asyncio.Future[float]] = []
    async with factory() as db, db.begin():
        unloaded = await _unload(db, constants, catalog, carter_id, sack_id)
        fires.append(asyncio.ensure_future(fire()))
        waited = await _until_blocked_by(factory, db, unless=fires[0])
    (burnt,) = await asyncio.gather(*fires, return_exceptions=True)

    assert burnt == pytest.approx(1), f"only the cart burns: {burnt}"
    assert waited, "the fire did not wait for the unload"
    async with factory() as db:
        me = await db.get(Body, carter_id)
        sack = await db.get(Item, sack_id)
        assert me is not None and sack is not None, "the fire burnt the sack in the hands"
        assert sack.container_id == (await world.body_container(db, me)).id
        assert amount_float(sack.amount) == pytest.approx(unloaded)
        assert await db.get(Item, cart_id) is None
        assert await _containers_of(db, cart_id) == [], "the hold outlived its cart"


async def test_a_sack_the_fire_took_first_is_gone_in_words(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """A carter reaching into a hold the fire already took is told it is gone (D-011).

    The fire burns the cart with its load and keeps the transaction open,
    holding the sack. The carter unloads and walks into the sack. The fire
    commits. The unload's reread finds no row: the refusal names the sack, and
    nothing is left of the cart, its hold or its harness.
    """
    field_id, carter_id, cart_id, sack_id = await _field_with_a_loaded_cart(
        session, constants, catalog
    )

    async def unload() -> float:
        async with factory() as db, db.begin():
            return await _unload(db, constants, catalog, carter_id, sack_id)

    unloads: list[asyncio.Future[float]] = []
    async with factory() as db, db.begin():
        burnt = await _burn(db, field_id)
        unloads.append(asyncio.ensure_future(unload()))
        waited = await _until_blocked_by(factory, db, unless=unloads[0])
    (refused,) = await asyncio.gather(*unloads, return_exceptions=True)

    assert burnt > 1, "the cart and its load burnt"
    assert isinstance(refused, ItemGone), refused
    assert refused.key == "thing-gone", refused.key
    assert waited, "the unload did not wait for the fire"
    async with factory() as db:
        assert await db.get(Item, sack_id) is None
        assert await db.get(Item, cart_id) is None
        assert await _containers_of(db, cart_id) == [], "the hold outlived its cart"
        line = (
            await db.execute(select(Harness).where(Harness.item_id == cart_id))
        ).scalar_one_or_none()
        assert line is None, "the harness outlived the cart"


async def _containers_of(db: AsyncSession, owner_id: uuid.UUID) -> list[Container]:
    return list(
        (await db.execute(select(Container).where(Container.owner_id == owner_id))).scalars().all()
    )
