# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Where a road's surface is taken from (D-315).

A road used to be laid out of the pocket alone, and forty units of surface are
a wagonload: the crew drove up with a full cart and was told its hands were
empty. Laying gathers like every other work now --

* out of the pocket and out of the convoy the crew is harnessed to, and never
  out of a cart under somebody else's harness;
* off the ground and out of the chests of a place the crew may dispose of, and
  not off a place that is somebody else's;
* counted under the lock: a shared pile is one road, and one edge is one work.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import _hold_the_first
from reads_kit import _writes_forbidden
from road_kit import _edge, _finish
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import road, storage, transport, travel, world
from src.models.identity import Body, Identity
from src.models.inventory import Item
from src.models.world import Edge, Surface


async def _crew(session: AsyncSession, node, name: str) -> Body:
    identity = await world.create_identity(session, f"{name}-{uuid.uuid4().hex[:8]}")
    return await world.print_body(session, identity, node)


async def _cart(session: AsyncSession, constants: Constants, catalog: Catalog, node, body):
    """A cart in the node's yard with this body in its harness."""
    yard = await world.node_container(session, node)
    cart = await world.grant_item(session, yard, "cart", amount=1, origin="test scenario")
    await transport.harness(session, constants, catalog, body, cart)
    return cart


async def test_road_laid_out_of_the_crews_own_convoy(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Forty units are a wagonload: the surface is taken from the hold of the
    cart the crew is harnessed to, without unloading it into the hands first.
    The window counts the same place, or the button would stay grey over a
    full wagon."""
    norm = constants[R.ROAD_SURFACE_PER_EDGE]
    here, _, body, edge = await _edge(session)
    cart = await _cart(session, constants, catalog, here, body)
    hold = await transport.cargo(session, cart)
    await world.grant_item(session, hold, "road_paving", amount=norm, origin="test scenario")

    shown = {path["edge"]: path for path in await road.view(session, constants, body)}
    assert shown[str(edge.id)]["at_hand"] == pytest.approx(norm)

    await _finish(session, await road.lay(session, constants, catalog, body, edge))

    assert edge.surface is Surface.ROAD
    assert edge.paving == "road_paving"
    assert not await transport.cargo_items(session, cart), "the hold paid for the road"


async def test_the_pocket_and_the_hold_pay_for_one_road_together(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    norm = constants[R.ROAD_SURFACE_PER_EDGE]
    here, _, body, edge = await _edge(session, surface_amount=norm / 2)
    cart = await _cart(session, constants, catalog, here, body)
    hold = await transport.cargo(session, cart)
    await world.grant_item(session, hold, "road_paving", amount=norm / 2, origin="test scenario")

    await road.lay(session, constants, catalog, body, edge)

    assert await road._surface_at_hand(session, body) == pytest.approx(0)


async def test_somebody_elses_convoy_is_not_the_crews(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Only one's own harness is a title (D-315): a loaded cart standing here
    under another body's harness lays nobody else's road."""
    norm = constants[R.ROAD_SURFACE_PER_EDGE]
    here, _, body, edge = await _edge(session)
    carter = await world.print_body(
        session, await world.create_identity(session, f"Carter-{uuid.uuid4().hex[:8]}"), here
    )
    cart = await _cart(session, constants, catalog, here, carter)
    hold = await transport.cargo(session, cart)
    await world.grant_item(session, hold, "road_paving", amount=norm, origin="test scenario")

    with pytest.raises(road.NoSurfaceGoods):
        await road.lay(session, constants, catalog, body, edge)
    assert len(await transport.cargo_items(session, cart)) == 1, "the carter's cargo is whole"


async def test_two_crews_over_one_pile_lay_one_road(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ground of a place is shared by everybody entitled to it (D-315), so
    the pile is counted under the lock: one norm lying there is one road, and
    the second crew is refused instead of starting on what the first took."""
    norm = constants[R.ROAD_SURFACE_PER_EDGE]
    here, _, first, one_way = await _edge(session)
    far = await world.create_node(session, f"terra.rdc.{uuid.uuid4().hex[:8]}", "Far", area_m2=100)
    other_way = await travel.connect(session, here, far, base_seconds=600, surface=Surface.TRAIL)
    second = await world.print_body(
        session, await world.create_identity(session, f"Second-{uuid.uuid4().hex[:8]}"), here
    )
    yard = await world.node_container(session, here)
    pile = await world.grant_item(session, yard, "road_paving", amount=norm, origin="test scenario")
    await session.commit()

    _hold_the_first(monkeypatch, factory, road, "_surface_stacks")

    async def lay(body_id: uuid.UUID, edge_id: uuid.UUID) -> bool:
        async with factory() as own:
            try:
                await road.lay(
                    own,
                    constants,
                    catalog,
                    await own.get(Body, body_id),
                    await own.get(Edge, edge_id),
                )
            except road.NoSurfaceGoods:
                await own.rollback()
                return False
            await own.commit()
            return True

    laid = await asyncio.gather(lay(first.id, one_way.id), lay(second.id, other_way.id))

    assert sorted(laid) == [False, True], "one norm is one road"
    async with factory() as check:
        assert await check.get(Item, pile.id) is None, "the pile went into the one road"


async def test_road_laid_out_of_a_chest_on_ones_own_place(
    session: AsyncSession, constants: Constants, catalog: Catalog, own_plot
) -> None:
    """A chest put up on the crew's own plot is a store the work reaches into."""
    norm = constants[R.ROAD_SURFACE_PER_EDGE]
    here, _, body, edge = await _edge(session)
    await own_plot(here, await session.get(Identity, body.identity_id))
    yard = await world.node_container(session, here)
    chest = await world.grant_item(
        session, yard, "chest", quality=60, origin="test scenario", installed=True
    )
    inside = await storage.inside(session, chest)
    await world.grant_item(session, inside, "road_paving", amount=norm, origin="test scenario")

    await _finish(session, await road.lay(session, constants, catalog, body, edge))

    assert edge.surface is Surface.ROAD
    assert not await world.contents(session, inside), "the chest paid for the road"


async def test_somebody_elses_place_gives_the_crew_nothing(
    session: AsyncSession, constants: Constants, catalog: Catalog, own_plot
) -> None:
    """The place is reached only where the body may dispose of it: a pile on
    the host's plot is neither counted for a guest nor spent by one."""
    norm = constants[R.ROAD_SURFACE_PER_EDGE]
    here, _, guest, edge = await _edge(session)
    host = await world.create_identity(session, f"Host-{uuid.uuid4().hex[:8]}")
    await own_plot(here, host)
    yard = await world.node_container(session, here)
    pile = await world.grant_item(session, yard, "road_paving", amount=norm, origin="test scenario")

    assert await road._surface_at_hand(session, guest) == pytest.approx(0)
    with pytest.raises(road.NoSurfaceGoods):
        await road.lay(session, constants, catalog, guest, edge)
    await session.refresh(pile)
    assert float(pile.amount) > 0, "the host's pile is whole"


async def test_the_view_counts_the_reach_and_writes_nothing(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """`road.here` is a read: counting a hold and a pile makes neither."""
    norm = constants[R.ROAD_SURFACE_PER_EDGE]
    here, _, body, edge = await _edge(session)
    cart = await _cart(session, constants, catalog, here, body)
    hold = await transport.cargo(session, cart)
    await world.grant_item(session, hold, "road_paving", amount=norm, origin="test scenario")
    yard = await world.node_container(session, here)
    await world.grant_item(session, yard, "road_paving", amount=norm, origin="test scenario")
    await session.flush()

    async with _writes_forbidden(session):
        shown = {path["edge"]: path for path in await road.view(session, constants, body)}

    assert shown[str(edge.id)]["at_hand"] == pytest.approx(norm * 2)


async def test_two_crews_on_one_edge_are_one_work(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The edge's row is taken before "is anybody working here" is asked: two
    crews with a norm each do not both pay for the same tier."""
    norm = constants[R.ROAD_SURFACE_PER_EDGE]
    here, _, first, edge = await _edge(session, surface_amount=norm)
    second = await _crew(session, here, "Second")
    pocket = await world.body_container(session, second)
    await world.grant_item(session, pocket, "road_paving", amount=norm, origin="test scenario")
    await session.commit()

    _hold_the_first(monkeypatch, factory, road, "pending")

    async def lay(body_id: uuid.UUID) -> bool:
        async with factory() as own:
            try:
                await road.lay(
                    own,
                    constants,
                    catalog,
                    await own.get(Body, body_id),
                    await own.get(Edge, edge.id),
                )
            except road.AlreadyWorking:
                await own.rollback()
                return False
            await own.commit()
            return True

    laid = await asyncio.gather(lay(first.id), lay(second.id))

    assert sorted(laid) == [False, True], "one edge is one work"
    async with factory() as check:
        left = [
            await road._surface_at_hand(check, await check.get(Body, one))
            for one in (first.id, second.id)
        ]
    assert sorted(left) == pytest.approx([0, norm]), "the refused crew keeps its surface"
