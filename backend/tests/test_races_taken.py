# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Two transactions at once over a thing the world takes while a hand reaches for it.

One of the race files (see `test_races.py` for the family's method): here one
side is the world ending things whole (`world.destroy`) -- the fire over a
field (D-197), a roof coming down (D-244) -- and the other is somebody doing
exactly what the moment before it is for: taking their goods out.

* **a sack unloaded from a burning cart** -- first, it stays in the carter's
  hands and only the cart burns; second, the unload is told the sack is gone;
* **a sack loaded into a burning cart** -- the load and the fire queue on the
  cart's row (`transport._pulled_here`). A load first is burnt with the cart,
  hold and all; a fire first leaves no cart to load. Before the queue a hold
  is tied to its cart by id and not by a key, so a load judged by the sight
  from before the fire's commit made a fresh hold for a cart already gone --
  goods in a hold nothing owns;
* **a sack picked up off a falling floor** -- the pick first keeps it in the
  hands; the roof's delete used to queue behind the pick and take the sack out
  of them the moment it landed.

The handshake is `automat_kit._until_blocked_by`: the side that went first
keeps its transaction open, holding the contended row, and commits only once
the other side has provably walked into it.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from automat_kit import _until_blocked_by
from src.constants import Catalog, Constants
from src.engine import estate, gear, plates, storage, transport, world
from src.engine.world.things import ItemGone
from src.models.estate import Building
from src.models.identity import Body
from src.models.inventory import Container, Item
from src.models.travel import Harness
from src.models.world import Layer, Node
from src.units import amount_float

CARGO = "iron_ore"
CART = "cart"


async def _field_with_a_cart(
    session: AsyncSession, constants: Constants, catalog: Catalog, *, loaded: bool
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """A field and a carter harnessed to a cart, a sack of ore beside them.

    `loaded` puts the sack into the hold; otherwise it stays in the carter's
    hands and the cart has no hold at all yet. Committed; returns the field,
    the carter, the cart and the sack.
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
    if loaded:
        await transport.load(session, constants, catalog, carter, sack)
    ids = (field.id, carter.id, cart.id, sack.id)
    await session.commit()
    return ids


async def _carter(db: AsyncSession, body_id: uuid.UUID) -> Body:
    """The body as a command takes it (`_alive`): locked, and reread after the lock."""
    me = await db.get(Body, body_id, with_for_update=True, populate_existing=True)
    assert me is not None
    return me


async def _unload(
    db: AsyncSession, constants: Constants, catalog: Catalog, carter_id, sack_id
) -> float:
    me = await _carter(db, carter_id)
    sack = await db.get(Item, sack_id)
    assert sack is not None
    return await transport.unload(db, constants, catalog, me, sack)


async def _load(
    db: AsyncSession, constants: Constants, catalog: Catalog, carter_id, sack_id
) -> float:
    me = await _carter(db, carter_id)
    sack = await db.get(Item, sack_id)
    assert sack is not None
    return await transport.load(db, constants, catalog, me, sack)


async def _burn(db: AsyncSession, field_id: uuid.UUID) -> float:
    place = await db.get(Node, field_id)
    assert place is not None
    return await plates._burn(db, [place])


async def _holds_of(db: AsyncSession, owner_id: uuid.UUID) -> list[Container]:
    return list(
        (await db.execute(select(Container).where(Container.owner_id == owner_id))).scalars().all()
    )


async def _in_hands(db: AsyncSession, body_id: uuid.UUID, item_id: uuid.UUID) -> Item | None:
    """The item, if it lies in this body's hands."""
    me = await db.get(Body, body_id)
    thing = await db.get(Item, item_id)
    assert me is not None
    if thing is None or thing.container_id != (await world.body_container(db, me)).id:
        return None
    return thing


# --- unloading ----------------------------------------------------------------


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
    field_id, carter_id, cart_id, sack_id = await _field_with_a_cart(
        session, constants, catalog, loaded=True
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
        sack = await _in_hands(db, carter_id, sack_id)
        assert sack is not None, "the fire burnt the sack in the hands"
        assert amount_float(sack.amount) == pytest.approx(unloaded)
        assert await db.get(Item, cart_id) is None
        assert await _holds_of(db, cart_id) == [], "the hold outlived its cart"


async def test_a_sack_the_fire_took_first_is_gone_in_words(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """A carter reaching into a hold the fire already took is told so (D-251).

    The fire burns the cart with its load and keeps the transaction open,
    holding the cart. The carter unloads and walks into the cart. The fire
    commits. The reread after the lock finds no cart: the refusal names it,
    and nothing is left of the cart, its hold or its harness.
    """
    field_id, carter_id, cart_id, sack_id = await _field_with_a_cart(
        session, constants, catalog, loaded=True
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
    assert isinstance(refused, (transport.NotHere, ItemGone)), refused
    assert refused.key == "thing-gone", refused.key
    assert waited, "the unload did not wait for the fire"
    async with factory() as db:
        assert await db.get(Item, sack_id) is None
        assert await db.get(Item, cart_id) is None
        assert await _holds_of(db, cart_id) == [], "the hold outlived its cart"
        line = (
            await db.execute(select(Harness).where(Harness.item_id == cart_id))
        ).scalar_one_or_none()
        assert line is None, "the harness outlived the cart"


# --- loading --------------------------------------------------------------------


async def test_a_sack_loaded_before_the_fire_burns_with_the_cart(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """A load and the fire queue on the cart's row, and the load does not save the sack.

    The carter loads a sack into a cart with no hold yet and keeps the
    transaction open, holding the cart. The fire walks into the cart. The load
    commits, hold and all. The fire, reading the cart after the lock, opens
    the hold the load made and burns the sack with the cart. Without the
    cart's row the fire walked past a hold not yet committed, burnt the bare
    cart, and the load then committed a hold owned by nothing.
    """
    field_id, carter_id, cart_id, sack_id = await _field_with_a_cart(
        session, constants, catalog, loaded=False
    )

    async def fire() -> float:
        async with factory() as db, db.begin():
            return await _burn(db, field_id)

    fires: list[asyncio.Future[float]] = []
    async with factory() as db, db.begin():
        loaded = await _load(db, constants, catalog, carter_id, sack_id)
        fires.append(asyncio.ensure_future(fire()))
        waited = await _until_blocked_by(factory, db, unless=fires[0])
    (burnt,) = await asyncio.gather(*fires, return_exceptions=True)

    assert burnt == pytest.approx(1 + loaded), f"the cart and the sack burn: {burnt}"
    assert waited, "the fire did not wait for the load"
    async with factory() as db:
        assert await db.get(Item, sack_id) is None, "the loaded sack outlived the fire"
        assert await db.get(Item, cart_id) is None
        assert await _holds_of(db, cart_id) == [], "a hold outlived its cart"


async def test_a_sack_loaded_into_a_cart_the_fire_holds_stays_in_the_hands(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """A carter loading a cart the fire already took is told the cart is gone (D-251).

    The fire burns the empty cart and keeps the transaction open, holding it.
    The carter loads a sack into it and walks into the cart's row. The fire
    commits. The reread after the lock finds no cart: the load is refused,
    the sack stays in the hands, and no hold is made for a cart that is gone.
    """
    field_id, carter_id, cart_id, sack_id = await _field_with_a_cart(
        session, constants, catalog, loaded=False
    )

    async def load() -> float:
        async with factory() as db, db.begin():
            return await _load(db, constants, catalog, carter_id, sack_id)

    loads: list[asyncio.Future[float]] = []
    async with factory() as db, db.begin():
        burnt = await _burn(db, field_id)
        loads.append(asyncio.ensure_future(load()))
        waited = await _until_blocked_by(factory, db, unless=loads[0])
    (refused,) = await asyncio.gather(*loads, return_exceptions=True)

    assert burnt == pytest.approx(1), f"only the empty cart burns: {burnt}"
    assert isinstance(refused, transport.NotHere), refused
    assert refused.key == "thing-gone", refused.key
    assert waited, "the load did not wait for the fire"
    async with factory() as db:
        assert await _in_hands(db, carter_id, sack_id) is not None, "the sack left the hands"
        assert await db.get(Item, cart_id) is None
        assert await _holds_of(db, cart_id) == [], "a hold was made for a burnt cart"


# --- a falling roof -------------------------------------------------------------


async def test_a_sack_picked_up_before_the_roof_falls_is_not_buried_in_the_hands(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """The fall rereads what lies on the floor after the sack's lock (D-244).

    The holder picks a sack up off the floor of the house and keeps the
    transaction open, holding it. The roof comes down and walks into the sack.
    The pick commits. Read without the lock, the fall queued its delete behind
    the pick and took the sack out of the hands it had just reached; read
    after it, the sack no longer lies under the roof.
    """
    stamp = uuid.uuid4().hex[:8]
    plot = await world.create_node(
        session, f"terra.roof.{stamp}", "Plot", area_m2=400, layer=Layer.PLANET
    )
    who = await world.create_identity(session, f"Holder-{stamp}")
    holder = await world.print_body(session, who, plot)
    plot.owner_identity_id = who.id
    house = Building(
        node_id=plot.id, area_m2=40, footprint_m2=40, floors=1, kind=estate.kinds(constants)[0]
    )
    session.add(house)
    await session.flush()
    sack = await world.grant_item(
        session, await world.node_container(session, plot), CARGO, amount=5, origin="test"
    )
    assert not sack.outdoors, "the sack lies on the floor, under the roof"
    plot_id, holder_id, house_id, sack_id = plot.id, holder.id, house.id, sack.id
    await session.commit()

    async def fall() -> None:
        async with factory() as db, db.begin():
            place = await db.get(Node, plot_id)
            roof = await db.get(Building, house_id)
            assert place is not None and roof is not None
            await estate.collapse(db, place, roof)

    falls: list[asyncio.Future[None]] = []
    async with factory() as db, db.begin():
        me = await _carter(db, holder_id)
        thing = await db.get(Item, sack_id)
        assert thing is not None
        await storage.pick(db, constants, catalog, me, thing)
        falls.append(asyncio.ensure_future(fall()))
        waited = await _until_blocked_by(factory, db, unless=falls[0])
    (fallen,) = await asyncio.gather(*falls, return_exceptions=True)

    assert fallen is None, fallen
    async with factory() as db:
        assert await _in_hands(db, holder_id, sack_id) is not None, "the roof buried the sack"
        assert await db.get(Building, house_id) is None, "the house fell"
    assert waited, "the fall did not wait for the pick"
