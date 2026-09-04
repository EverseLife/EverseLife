# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""What arrives past the limit falls underfoot (D-265).

The carry limit stood at every door a thing is taken through and at none it
arrives through by itself: a batch paid out into the master's hands, the alpha
printer printed into them, and a body walked off with a station it could never
have lifted (playtest 2026-09-02). Here those doors are tried past the limit,
the fall is checked piece by piece and kilogram by kilogram, the overfull floor
is seen to shout, and two arrivals at once are made to share one pair of hands.

The other half of the family -- a limit that falls under a load nobody moved --
is `test_overload_shed.py`.
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import _slow
from overload_kit import (
    FORGE,
    INGOT,
    NAILS,
    ORE,
    SACK,
    STEEL,
    _ground,
    _held,
    _house,
    _lying,
    _told,
)
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import alpha, craft, gear, jobs, overload, world
from src.models.event import EventKind
from src.models.identity import Body
from src.models.world import Node


async def test_a_print_past_the_limit_falls_in_whole_pieces(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Sixty ingots printed into empty hands: what is over the limit lies on the ground."""
    node, identity, body = await _ground(session)
    await alpha.spawn(session, constants, catalog, body, type_key=STEEL, amount=60)

    limit = await gear.capacity(session, constants, catalog, body)
    assert await gear.load_of(session, constants, catalog, body) <= limit + 1e-6
    kept, fell = await _held(session, body, STEEL), await _lying(session, node, STEEL)
    assert kept + fell == 60, "материя не пропала"
    assert kept == int(kept) and fell == int(fell), "штучное падает целыми штуками"
    assert fell > 0
    #: One more ingot would not have fit: the hands are as full as they may be.
    #: Weighed as the load would be, not as it is plus a kilogram -- readings of
    #: a load do not add up once a pack bends them (`gear.carried_mass`).
    unit = gear.mass_of(catalog, STEEL, 1)
    worn = await gear.equipped(session, body)
    mass = await gear.carried_mass(session, catalog, body) + unit
    assert gear.packed(constants, catalog, worn, mass) > limit

    said = await _told(session, identity.id, EventKind.ITEM_FELL)
    assert len(said) == 1 and said[0].payload["roofed"] is False
    assert said[0].payload["amount"] == pytest.approx(fell)


async def test_a_measured_thing_falls_by_the_excess(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    node, _, body = await _ground(session)
    await alpha.spawn(session, constants, catalog, body, type_key=ORE, amount=300)
    limit = await gear.capacity(session, constants, catalog, body)
    assert await gear.load_of(session, constants, catalog, body) == pytest.approx(limit, abs=1e-3)
    total = await _held(session, body, ORE) + await _lying(session, node, ORE)
    assert total == pytest.approx(300)


async def test_what_falls_is_matter_under_a_roomy_pack(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """What falls is measured in matter, not in what the body feels.

    A pack roomier than the whole limit bends the two apart: inside its
    capacity a kilogram put down lightens the body by `factor` of itself, so
    dropping the felt excess would leave the hands over the limit still. No
    pack in the vault reaches there -- every `capacity * factor` is under
    `inventory.carry_mass`, and `test_gear` keeps a tripwire on it -- so the
    pack here is doctored on purpose: the door has to be right for the pack
    that gets written, not only for the five that are.
    """
    node, _, body = await _ground(session)
    roomy = Constants(
        {**constants.raw(), "inventory.pack": {SACK: {"capacity": 100, "factor": 0.5}}},
        source="тест",
    )
    pocket = await world.body_container(session, body)
    sack = await world.grant_item(session, pocket, SACK, amount=1, quality=60, origin="тест")
    await gear.equip(session, roomy, catalog, body, sack)
    #: Seventy kilograms of steel: the body feels thirty-five of them, five over
    #: the limit -- and five kilograms put down would leave it feeling 32.5.
    steel = await world.grant_item(session, pocket, STEEL, amount=70, quality=60, origin="тест")

    fell = await overload.settle_load(session, roomy, catalog, body, [steel])

    limit = await gear.capacity(session, roomy, catalog, body)
    assert fell > 0
    assert await gear.load_of(session, roomy, catalog, body) <= limit + 1e-6, (
        "упал прочувствованный излишек вместо материи"
    )
    assert await _held(session, body, STEEL) + await _lying(session, node, STEEL) == pytest.approx(
        70
    ), "материя не пропала"


async def test_the_yield_of_a_batch_falls_at_the_bench(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """The master stands at the forge with full hands: the nails fall on the floor.

    The hands are over the limit before the batch -- ingots granted past any
    door, as a print or a yield would be -- and what the rule drops is the
    thing that arrives, never what was carried before it: the nails lie on
    the floor whole, the ingots stay in the hands.
    """
    async with factory() as session, session.begin():
        node, identity, body = await _house(session)
        yard = await world.node_container(session, node)
        await world.grant_item(session, yard, FORGE, quality=60, origin="тест")
        await world.learn(session, identity, NAILS)
        pocket = await world.body_container(session, body)
        await world.grant_item(session, pocket, INGOT, amount=60, quality=60, origin="тест")
        batch = await craft.start(session, constants, catalog, body, NAILS, 20)
        ready, identity_id, node_id = batch.ready_at, identity.id, node.id

    assert await jobs.run_one(factory, now=ready) is not None

    async with factory() as session:
        body = (
            await session.execute(select(Body).where(Body.identity_id == identity_id))
        ).scalar_one()
        node = await session.get(Node, node_id)
        assert node is not None
        assert await _lying(session, node, NAILS) == 20, "гвозди легли на пол целиком"
        assert await _held(session, body, NAILS) == 0
        assert await _held(session, body, INGOT) > 0, "несённое до того осталось в руках"
        said = await _told(session, identity_id, EventKind.ITEM_FELL)
        assert said and said[0].payload["roofed"] is True


async def test_an_overfull_floor_is_journaled_and_shouted(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A ten-metre room holds two hundred kilos of cargo: three hundred fall anyway."""
    node, identity, body = await _house(session, area=10)
    budget = 10 * constants[R.BUILD_FLOOR_PER_M2]
    count = int(budget / gear.mass_of(catalog, STEEL, 1)) + 40
    with caplog.at_level(logging.ERROR, logger="src.engine.overload"):
        await alpha.spawn(session, constants, catalog, body, type_key=STEEL, amount=count)

    assert await _lying(session, node, STEEL) > 0, "упало, хотя места не было"
    over = await _told(session, identity.id, EventKind.STORAGE_OVERFULL)
    assert len(over) == 1 and over[0].payload["roofed"] is True
    assert any("floor overfull" in line.message for line in caplog.records)


async def test_two_prints_at_once_share_one_pair_of_hands(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the row lock both arrivals read empty hands and both keep a load."""
    #: On the path the arrival takes: `settle_load` reads the matter in the
    #: hands, not the load through the pack, and a delay hung on a function
    #: nobody calls widens no window at all.
    _slow(monkeypatch, gear, "carried_mass")
    node, _, body = await _ground(session)
    body_id, node_id = body.id, node.id
    await session.commit()

    async def print_steel() -> None:
        async with factory() as db, db.begin():
            who = await db.get(Body, body_id)
            assert who is not None
            await alpha.spawn(db, constants, catalog, who, type_key=STEEL, amount=30)

    await asyncio.gather(print_steel(), print_steel())

    async with factory() as db:
        who = await db.get(Body, body_id)
        assert who is not None
        limit = await gear.capacity(db, constants, catalog, who)
        assert await gear.load_of(db, constants, catalog, who) <= limit + 1e-6, (
            "две печати нашли одно место"
        )
        field = await db.get(Node, node_id)
        assert field is not None
        assert await _held(db, who, STEEL) + await _lying(db, field, STEEL) == 60
