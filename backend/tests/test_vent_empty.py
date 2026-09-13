# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Emptying a vessel of its vent gas by hand (D-340).

A cylinder of hydrogen is not stuck with it for good: it is emptied the way
the machines let their gas go -- into the node's flare on the ground under a
sky with air, out where there is no air. Checked:

* the flare on the ground, overboard from a sealed hull, and each says where
  it went in the journal;
* nowhere under a sky with air -- no flare on the ground, a hull in port --
  and nothing leaves the vessel;
* only a vent gas goes out this way: oxygen is poured into another vessel;
* the same reach as a pour: a vessel standing in somebody else's place is
  not yours to empty;
* two hands emptying one cylinder at once take what is in it once.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import _slow
from lines_kit import AIR, CYLINDER, FLARE, HYDROGEN, _events, _held, _hull, _seal, _vessel
from src.constants import Catalog, Constants
from src.engine import liquid, storage, vent, world
from src.models.event import EventKind
from src.models.identity import Body
from src.models.inventory import Item
from src.models.world import Node


async def _field(session: AsyncSession, *, flare: bool) -> tuple[Node, Body, Item]:
    """Unowned ground under Terra's sky, a body on it holding a cylinder of
    hydrogen -- and a flare stack standing there when asked."""
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.flare.{stamp}", "Факельная", area_m2=200)
    identity = await world.create_identity(session, f"Химик-{stamp}")
    body = await world.print_body(session, identity, node)
    if flare:
        yard = await world.node_container(session, node)
        await world.grant_item(session, yard, FLARE, quality=60, origin="тест")
    pocket = await world.body_container(session, body)
    bottle = await world.grant_item(session, pocket, CYLINDER, quality=60, origin="тест")
    await world.grant_item(
        session, await storage.inside(session, bottle), HYDROGEN, amount=20, origin="тест"
    )
    return node, body, bottle


async def test_a_cylinder_of_hydrogen_burns_in_the_flare_on_the_ground(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, body, bottle = await _field(session, flare=True)
    gas, amount, way = await vent.empty(session, constants, catalog, body, bottle)
    assert (gas, amount, way) == (HYDROGEN, pytest.approx(20), vent.FLARE)
    assert await _held(session, bottle) == 0
    (said,) = await _events(session, EventKind.STORAGE_VENTED)
    assert said.payload["way"] == vent.FLARE
    assert said.payload["amount"] == pytest.approx(20)


async def test_under_a_sky_with_no_flare_the_cylinder_keeps_its_hydrogen(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, body, bottle = await _field(session, flare=False)
    with pytest.raises(vent.NowhereToVent) as refused:
        await vent.empty(session, constants, catalog, body, bottle)
    assert refused.value.key == "liquid-vent-nowhere"
    assert refused.value.params["aboard"] == "false"
    assert await _held(session, bottle) == pytest.approx(20)


async def test_a_tank_aboard_goes_overboard_in_the_void_and_nowhere_in_port(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A hull in port under Terra's sky has no flare: refused. Cast off, the
    same tank is emptied overboard."""
    vessel, body, connector = await _hull(session, constants)
    tank = await _vessel(session, connector, CYLINDER, HYDROGEN, 30)
    with pytest.raises(vent.NowhereToVent) as refused:
        await vent.empty(session, constants, catalog, body, tank)
    assert refused.value.params["aboard"] == "true"

    _seal(vessel)
    await session.flush()
    _, amount, way = await vent.empty(session, constants, catalog, body, tank)
    assert (amount, way) == (pytest.approx(30), vent.VOID)
    assert await _held(session, tank) == 0


async def test_only_a_vent_gas_goes_out_and_only_from_a_vessel_one_may_open(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    node, body, bottle = await _field(session, flare=True)
    oxygen = await world.grant_item(
        session, await world.body_container(session, body), CYLINDER, quality=60, origin="тест"
    )
    with pytest.raises(vent.VentError) as empty:
        await vent.empty(session, constants, catalog, body, oxygen)
    assert empty.value.key == "liquid-source-empty"
    await world.grant_item(
        session, await storage.inside(session, oxygen), AIR, amount=3, origin="тест"
    )
    with pytest.raises(vent.VentError) as kept:
        await vent.empty(session, constants, catalog, body, oxygen)
    assert kept.value.key == "liquid-not-vent"
    assert kept.value.params["have"] == AIR
    assert await _held(session, oxygen) == pytest.approx(3)

    #: A cylinder standing on a plot somebody else holds is theirs to open.
    stranger = await world.create_identity(session, f"Хозяин-{uuid.uuid4().hex[:6]}")
    node.owner_identity_id = stranger.id
    standing = await _vessel(session, node, CYLINDER, HYDROGEN, 5)
    with pytest.raises(storage.NotYours):
        await vent.empty(session, constants, catalog, body, standing)
    assert await _held(session, standing) == pytest.approx(5)


async def test_two_hands_emptying_one_cylinder_take_its_hydrogen_once(
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The vessel's row is locked before its stacks are read: the second hand
    waits, finds the cylinder empty and is refused -- the gas is not written
    off twice, and the journal says one emptying of the whole of it."""
    async with factory() as session, session.begin():
        node, owner, bottle = await _field(session, flare=True)
        #: The cylinder stands on the ground, so two bodies reach it.
        bottle.container_id = (await world.node_container(session, node)).id
        other = await world.print_body(
            session, await world.create_identity(session, f"Сосед-{uuid.uuid4().hex[:6]}"), node
        )
        ids = (owner.id, other.id, bottle.id)

    _slow(monkeypatch, liquid, "lock_vessels")

    async def empty(body_id: uuid.UUID) -> float:
        async with factory() as db, db.begin():
            me = await db.get(Body, body_id)
            _, amount, _ = await vent.empty(db, constants, catalog, me, await db.get(Item, ids[2]))
            return amount

    outcomes = await asyncio.gather(empty(ids[0]), empty(ids[1]), return_exceptions=True)
    taken = [one for one in outcomes if isinstance(one, float)]
    refused = [one for one in outcomes if isinstance(one, vent.VentError)]
    assert taken == [pytest.approx(20)], outcomes
    assert len(refused) == 1 and refused[0].key == "liquid-source-empty"
    async with factory() as session:
        assert await _held(session, await session.get(Item, ids[2])) == 0
        assert len(await _events(session, EventKind.STORAGE_VENTED)) == 1
