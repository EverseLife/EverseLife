# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A thing the world takes leaves whole (`world.destroy`).

A cart is more than its row: it owns a hold (D-157, D-313) and a body's
harness points at it. Every door that ended a thing deleted the row alone,
and each lost a different half:

* the fire over a field (D-197) and the fall of a house (D-244) died on the
  harness -- `fk_harness_item_id_item` -- and took the whole eruption or the
  whole daily decay down with them, every day again; so did the finish of a
  batch taking apart a barrow somebody had harnessed meanwhile -- which
  D-346 has since made unreachable, and the test at that seam now says so;
* the fire, the rift under a walker (D-233), a death and the taking apart of
  a thing left the hold behind, and the load in it alive for ever in a place
  that no longer exists;
* a broken cart spilt its load but left its empty hold row.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import Catalog, Constants
from src.engine import (
    craft,
    death,
    estate,
    gear,
    jobs,
    luck,
    plates,
    storage,
    transport,
    travel,
    world,
)
from src.models.craft import BatchState, CraftBatch
from src.models.estate import Building
from src.models.identity import Body, BodyState
from src.models.inventory import Container, Item
from src.models.job import JobState
from src.models.travel import Harness, Travel
from src.models.world import Edge, Layer, Node, Surface
from src.units import amount_float

CARGO = "iron_ore"
CART = "cart"
BARROW = "wheelbarrow"
CHEST = "chest"


async def _place(session: AsyncSession) -> Node:
    return await world.create_node(
        session, f"terra.gone.{uuid.uuid4().hex[:8]}", "Yard", area_m2=400, layer=Layer.PLANET
    )


async def _carter(session: AsyncSession, node: Node) -> Body:
    who = await world.create_identity(session, f"Carter-{uuid.uuid4().hex[:8]}")
    return await world.print_body(session, who, node)


async def _harnessed(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    vehicle: str,
    kilograms: float,
) -> tuple[Item, Item]:
    """A vehicle standing beside the body, harnessed and loaded through the doors.

    Returns the vehicle and the sack in its hold.
    """
    node = await session.get(Node, body.node_id)
    assert node is not None
    wagon = await world.grant_item(
        session, await world.node_container(session, node), vehicle, origin="test"
    )
    await transport.harness(session, constants, catalog, body, wagon)
    sack = await world.grant_item(
        session,
        await world.body_container(session, body),
        CARGO,
        amount=kilograms / gear.mass_of(catalog, CARGO, 1),
        origin="test",
    )
    await transport.load(session, constants, catalog, body, sack)
    return wagon, sack


async def _pocketed_barrow(
    session: AsyncSession, constants: Constants, catalog: Catalog, body: Body
) -> tuple[Item, Item]:
    """A barrow with a little load in it, unharnessed and lifted into the hands.

    Legal since D-313: the lift weighs the load, and eight kilograms of barrow
    with five of ore fit in empty hands.
    """
    barrow, sack = await _harnessed(session, constants, catalog, body, BARROW, 5)
    await transport.unharness(session, body)
    await storage.pick(session, constants, catalog, body, barrow)
    assert barrow.container_id == (await world.body_container(session, body)).id
    return barrow, sack


async def _owned_by(session: AsyncSession, owner_id: uuid.UUID) -> list[Container]:
    return list(
        (await session.execute(select(Container).where(Container.owner_id == owner_id)))
        .scalars()
        .all()
    )


async def _pulling(session: AsyncSession, item_id: uuid.UUID) -> Harness | None:
    return (
        await session.execute(select(Harness).where(Harness.item_id == item_id))
    ).scalar_one_or_none()


def _house(constants: Constants, node: Node) -> Building:
    return Building(
        node_id=node.id, area_m2=40, footprint_m2=40, floors=1, kind=estate.kinds(constants)[0]
    )


# --- the fire -----------------------------------------------------------------


async def test_a_harnessed_cart_burns_with_its_load_and_lets_the_carter_go(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A harness is no roof over a field (D-197).

    The carter stood in a field under the warning and did not drive out. The
    cart burns with what is in its hold -- the hold is the cart's own and goes
    where the cart goes (D-313) -- and the harness lets go of a thing that is
    gone. The carter stays standing: the fire takes what lies, not who stands.
    Before, the delete of the cart died on the harness pointing at it, and the
    whole eruption rolled back.
    """
    node = await _place(session)
    carter = await _carter(session, node)
    cart, sack = await _harnessed(session, constants, catalog, carter, CART, 50)
    cart_id, sack_id, load = cart.id, sack.id, amount_float(sack.amount)

    burnt = await plates._burn(session, [node])

    assert burnt == pytest.approx(1 + load), "the cart and its load are counted as burnt"
    assert await session.get(Item, cart_id) is None
    assert await session.get(Item, sack_id) is None, "the load outlived its cart"
    assert await _owned_by(session, cart_id) == [], "the hold outlived its cart"
    assert await _pulling(session, cart_id) is None, "the harness points at nothing"
    assert carter.state is BodyState.ALIVE
    assert await transport.harnessed(session, carter) is None


async def test_a_standing_cart_burns_to_the_bottom_of_its_hold(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """All the way down through a hold: a chest rides in a cart (D-313).

    The fire opened a chest's inside and not a cart's hold, so the cart's
    goods stayed alive in a hold with no cart -- and a chest in that hold kept
    its own inside for ever, one floor lower.
    """
    node = await _place(session)
    carter = await _carter(session, node)
    cart, sack = await _harnessed(session, constants, catalog, carter, CART, 50)
    chest = await world.grant_item(
        session, await transport.cargo(session, cart), CHEST, quality=60, origin="test"
    )
    coal = await world.grant_item(
        session, await storage.inside(session, chest), "coal", amount=5, origin="test"
    )
    await transport.unharness(session, carter)
    cart_id, chest_id = cart.id, chest.id
    ids = [cart.id, sack.id, chest.id, coal.id]

    await plates._burn(session, [node])

    for gone in ids:
        assert await session.get(Item, gone) is None, "something in the cart outlived the fire"
    assert await _owned_by(session, cart_id) == [], "the hold outlived its cart"
    assert await _owned_by(session, chest_id) == [], "the chest's inside outlived its chest"


async def test_a_cart_left_in_the_field_burns_behind_a_carter_who_set_out_late(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """A convoy has not left until its leg arrives (D-194), so the fire takes it.

    The window is hours wide and a leg is minutes: a carter who sets out in
    the last minute leaves the cart in the burning yard, and the leg lands
    them on foot. Before, the fire died on the harness and burnt nothing; the
    arrival after it must find no convoy and fail no job.
    """
    async with factory() as session, session.begin():
        field, road_end = await _place(session), await _place(session)
        await travel.connect(session, field, road_end, base_seconds=300, surface=Surface.ROAD)
        carter = await _carter(session, field)
        cart, sack = await _harnessed(session, constants, catalog, carter, CART, 50)
        leg = await travel.depart(session, constants, carter, road_end)
        term, ids = leg.arrives_at, (field.id, road_end.id, carter.id, cart.id, sack.id)
    field_id, end_id, carter_id, cart_id, sack_id = ids

    async with factory() as session, session.begin():
        burning = await session.get(Node, field_id)
        assert burning is not None
        assert await plates._burn(session, [burning]) > 1, "the cart and its load burnt"

    arrived = await jobs.run_one(factory, now=term)
    assert arrived is not None and arrived.state is JobState.DONE, "the arrival failed"
    async with factory() as session:
        body = await session.get(Body, carter_id)
        assert body is not None and body.node_id == end_id
        assert await transport.harnessed(session, body) is None, "a harness outlived the cart"
        assert await session.get(Item, cart_id) is None
        assert await session.get(Item, sack_id) is None
        assert await _owned_by(session, cart_id) == [], "the hold outlived its cart"


# --- the rift, the roof, a death ------------------------------------------------


async def test_a_rift_takes_a_loaded_barrow_whole_with_the_pocket(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The pocket goes with a walker a way breaks under (D-233), and a barrow
    in it goes with its load: no hold is left over in nowhere."""
    here, there = await _place(session), await _place(session)
    await travel.connect(session, here, there, base_seconds=600, surface=Surface.ROAD)
    walker = await _carter(session, here)
    barrow, sack = await _pocketed_barrow(session, constants, catalog, walker)
    way = await session.scalar(
        select(Edge).where(
            or_(
                (Edge.node_a_id == here.id) & (Edge.node_b_id == there.id),
                (Edge.node_a_id == there.id) & (Edge.node_b_id == here.id),
            )
        )
    )
    assert way is not None
    session.add(
        Travel(
            body_id=walker.id,
            from_node_id=here.id,
            to_node_id=there.id,
            edge_id=way.id,
            arrives_at=datetime.now(UTC) + timedelta(minutes=5),
        )
    )
    await session.flush()
    barrow_id, sack_id = barrow.id, sack.id

    assert await plates._kill_on(session, constants, [way], now=datetime.now(UTC)) == 1

    assert await session.get(Item, barrow_id) is None
    assert await session.get(Item, sack_id) is None, "the load outlived its barrow"
    assert await _owned_by(session, barrow_id) == [], "the hold outlived its barrow"


async def test_a_falling_roof_buries_a_harnessed_cart_whole(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """What stood under the roof goes down with it (D-244), a cart too.

    The fall died on the harness, and the fall runs inside the daily decay of
    every house: one carter under one rotten roof stopped the decay of the
    whole world, and came back to the same wall the next day.
    """
    node = await _place(session)
    carter = await _carter(session, node)
    node.owner_identity_id = carter.identity_id
    house = _house(constants, node)
    session.add(house)
    await session.flush()
    cart, sack = await _harnessed(session, constants, catalog, carter, CART, 50)
    assert not cart.outdoors, "the cart stands on the floor, under the roof"
    cart_id, sack_id = cart.id, sack.id

    await estate.collapse(session, node, house)

    assert await session.get(Item, cart_id) is None
    assert await session.get(Item, sack_id) is None, "the load outlived its cart"
    assert await _owned_by(session, cart_id) == [], "the hold outlived its cart"
    assert await _pulling(session, cart_id) is None
    assert await transport.harnessed(session, carter) is None


async def test_a_barrow_lost_in_a_death_goes_with_its_load(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What a death does not leave lying is gone whole (D-012).

    Nothing survives here by construction -- no share, no lucky roll -- so the
    barrow takes the path of what is lost, and its hold goes with it.
    """

    async def unlucky(*_args, **_kwargs) -> bool:
        return False

    monkeypatch.setattr(luck, "hit", unlucky)
    node = await _place(session)
    walker = await _carter(session, node)
    barrow, sack = await _pocketed_barrow(session, constants, catalog, walker)
    barrow_id, sack_id = barrow.id, sack.id

    await death.die(
        session, constants.with_overrides({"death.salvage_ratio": 0}), walker, cause="test"
    )

    assert await session.get(Item, barrow_id) is None
    assert await session.get(Item, sack_id) is None, "the load outlived its barrow"
    assert await _owned_by(session, barrow_id) == [], "the hold outlived its barrow"


# --- a batch, a breakdown -------------------------------------------------------


async def test_a_barrow_put_down_from_a_waiting_batch_is_not_taken_apart(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """A thing under the knife does not leave the hands while the work goes (D-346).

    This seam used to read the other way: nothing pinned the target, so the
    master put the barrow down mid-batch, a carter harnessed it, and the
    finish took it out of the world from under them -- the delete died on the
    harness and the job failed at every retry. D-346 shuts both halves. While
    the batch **goes**, the drop is refused in words. While it **waits** its
    turn it pins nothing, so the barrow may be put down and harnessed -- and
    then the end finds nothing on the bench: the barrow stays whole under its
    harness and the batch closes with no materials. So the finish no longer
    meets a harness at all; that `destroy` lets one go is the fire's and the
    falling roof's business, above.
    """
    async with factory() as session, session.begin():
        node = await _place(session)
        master = await _carter(session, node)
        carter = await _carter(session, node)
        await world.grant_item(
            session, await world.node_container(session, node), "workbench", origin="test"
        )
        pocket = await world.body_container(session, master)
        going_on = await world.grant_item(session, pocket, BARROW, origin="test")
        queued_on = await world.grant_item(session, pocket, BARROW, origin="test")
        going = await craft.recycle(session, constants, catalog, master, going_on)
        #: One body works one batch (D-209): the second waits its turn.
        queued = await craft.recycle(session, constants, catalog, master, queued_on)
        assert queued.state is BatchState.WAITING

        with pytest.raises(world.TakenApart):
            await storage.drop(session, constants, catalog, master, going_on)

        await storage.drop(session, constants, catalog, master, queued_on)
        await transport.harness(session, constants, catalog, carter, queued_on)
        term, ids = going.ready_at, (carter.id, queued_on.id, queued.id)
    carter_id, barrow_id, queued_id = ids

    finished = await jobs.run_one(factory, now=term)
    assert finished is not None and finished.state is JobState.DONE, "the first finish failed"
    async with factory() as session:
        waiting = await session.get(CraftBatch, queued_id)
        assert waiting is not None and waiting.ready_at is not None, "the queue moved on"
        later = waiting.ready_at

    second = await jobs.run_one(factory, now=later)
    assert second is not None and second.state is JobState.DONE, "the finish failed"

    async with factory() as session:
        assert await session.get(Item, barrow_id) is not None, "the barrow stayed whole"
        body = await session.get(Body, carter_id)
        assert body is not None
        assert await transport.harnessed(session, body) is not None, "the harness holds"
        batch = await session.get(CraftBatch, queued_id)
        assert batch is not None and batch.state is BatchState.DONE, "the batch closed empty"


async def test_a_broken_cart_leaves_no_hold_behind(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """A breakdown spills the load where the convoy stopped (D-157), and the
    emptied hold goes with the cart: a row owned by nothing is an orphan even
    empty."""
    async with factory() as session, session.begin():
        here, there = await _place(session), await _place(session)
        await travel.connect(session, here, there, base_seconds=300, surface=Surface.ROAD)
        carter = await _carter(session, here)
        cart, sack = await _harnessed(session, constants, catalog, carter, CART, 20)
        #: On its last legs: the next leg finishes it.
        cart.condition = Decimal("0.5")
        leg = await travel.depart(session, constants, carter, there)
        term, ids = leg.arrives_at, (there.id, cart.id, sack.id)
    there_id, cart_id, sack_id = ids

    assert await jobs.run_one(factory, now=term) is not None

    async with factory() as session:
        spot = await session.get(Node, there_id)
        load = await session.get(Item, sack_id)
        assert spot is not None and load is not None
        assert await session.get(Item, cart_id) is None, "the cart broke"
        assert load.container_id == (await world.node_container(session, spot)).id
        assert await _owned_by(session, cart_id) == [], "the empty hold outlived its cart"
