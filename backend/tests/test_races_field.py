# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The field automaton's races (D-339): two sessions reaching for one store,
one seed lot, one ripe bed. Every amount the machine changes is changed under
a row lock, and each test here shows the second session waiting its turn.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agro_kit import (
    LUBRICANT,
    SPELT,
    chest,
    field,
    goods_in,
    growing,
    liquid_in,
    plot_of,
    programmed,
    second_now,
    seeds_in,
)
from conftest import _slow
from src.constants import Catalog, Constants, current, current_catalog
from src.constants import registry as R
from src.engine import agro, breed, farm, gear, storage, world
from src.engine.errors import Refusal
from src.models.event import Event, EventKind
from src.models.farm import Plot, PlotState
from src.models.inventory import Item
from src.units import PERCENT, amount_float

WATER = "water"


# --- the race ----------------------------------------------------------------------


async def test_a_harvest_and_a_hand_filling_the_store_never_overfill_it(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    constants: Constants,
    catalog: Catalog,
) -> None:
    """The machine reads the room left in its harvest store and reaps into it,
    while its owner puts things into the same store by hand. Both doors weigh
    the store under its row's lock (D-181, D-313); without it both would read
    the same free kilograms and the store would take a harvest and a load it
    has room for only one of."""
    from src.models.identity import Body

    place = await field(session, constants, water="river", fertility=90)
    await liquid_in(session, place.yard, LUBRICANT, 100)
    t0 = second_now()
    plot = await growing(session, constants, catalog, place.body, t0)
    plot.growth = 100
    await session.flush()
    plant = catalog.plants.by_id(SPELT)
    cultivar = await breed.landrace(session, catalog, SPELT)
    moment = t0 + timedelta(minutes=1)
    seen = farm.peek(
        constants,
        plant,
        farm.signs_of(plant, cultivar),
        place.node,
        await world.epoch(session),
        plot,
        moment,
    )
    crop = farm.crop_of(constants, plot, plant, farm.signs_of(plant, cultivar), seen).scaled(
        constants[R.AGRO_YIELD_SHARE] / PERCENT, constants[R.AGRO_QUALITY_CAP]
    )
    weight = gear.mass_of(catalog, plant.gives, crop.goods) + gear.mass_of(
        catalog, plant.seed, crop.seeds
    )
    store = await chest(session, place.yard)
    limit = storage.capacity(catalog, "chest") or 0.0
    ingot = "iron_ingot"
    per = catalog.recipes.mass_of(ingot)
    #: Room for the harvest or for the load in the hands, never for both.
    await goods_in(session, store, ingot, (limit - weight * 1.5) / per)
    pocket = await world.body_container(session, place.body)
    load = await world.grant_item(
        session, pocket, ingot, amount=weight / per, quality=60, origin="тест"
    )
    row = await programmed(
        session, constants, catalog, place, [{"do": "harvest"}], [plot], t0, harvest=store
    )
    row.counted_at = t0
    await session.commit()
    _slow(monkeypatch, storage, "stored_mass")

    async def tick() -> None:
        async with factory() as db, db.begin():
            await agro.tick_machines(db, current(), now=moment)

    async def put() -> str:
        await asyncio.sleep(0.05)
        async with factory() as db, db.begin():
            own_body = await db.get(Body, place.body.id)
            own_store = await db.get(Item, store.id)
            own_load = await db.get(Item, load.id)
            try:
                await storage.put(db, current(), current_catalog(), own_body, own_store, own_load)
            except storage.Full:
                return "refused"
            return "put"

    _, verdict = await asyncio.gather(tick(), put())

    async with factory() as db:
        own_store = await db.get(Item, store.id)
        assert own_store is not None
        held = await storage.stored_mass(db, current_catalog(), own_store)
        reaped = await db.get(Plot, plot.id)
        assert reaped is not None
    assert held <= limit + 1e-6, "the store never holds more than it takes"
    assert (reaped.state is PlotState.IDLE) != (verdict == "put"), "exactly one of the two got in"


async def test_a_sowing_and_a_hand_taking_the_same_seeds_never_spend_them_twice(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    constants: Constants,
    catalog: Catalog,
) -> None:
    """The machine sows out of the seed chest while its owner takes the very
    lot into the hands. Both doors take the chest's row before its things;
    without the lock the lot would be sown and carried away at once."""
    from src.models.identity import Body

    place = await field(session, constants)
    await liquid_in(session, place.yard, LUBRICANT, 100)
    store = await chest(session, place.yard)
    need = constants[R.FARM_SEED_RATE] * 40
    lot = await seeds_in(session, catalog, store, SPELT, need)
    plot = await plot_of(session, constants, place.body)
    plot.state = PlotState.PLOWED
    t0 = second_now()
    row = await programmed(
        session,
        constants,
        catalog,
        place,
        [{"do": "sow", "culture": SPELT}, {"do": "harvest"}],
        [plot],
        t0,
        seeds=store,
    )
    row.counted_at = t0
    await session.commit()
    _slow(monkeypatch, storage, "inside")
    moment = t0 + timedelta(minutes=1)

    async def tick() -> None:
        async with factory() as db, db.begin():
            await agro.tick_machines(db, current(), now=moment)

    async def take() -> str:
        await asyncio.sleep(0.05)
        async with factory() as db, db.begin():
            own_body = await db.get(Body, place.body.id)
            own_store = await db.get(Item, store.id)
            own_lot = await db.get(Item, lot.id)
            if own_lot is None:
                return "gone"
            try:
                await storage.take(db, current(), current_catalog(), own_body, own_store, own_lot)
            except Refusal:
                return "refused"
            return "taken"

    _, verdict = await asyncio.gather(tick(), take())

    async with factory() as db:
        sown = await db.get(Plot, plot.id)
        pocket = await world.body_container(db, await db.get(Body, place.body.id))
        in_hands = await db.scalar(
            select(func.coalesce(func.sum(Item.amount), 0)).where(
                Item.container_id == pocket.id, Item.type_key == lot.type_key
            )
        )
    assert sown is not None
    got = amount_float(int(in_hands or 0))
    #: Exactly one of the two had the seeds: the bed or the hands.
    assert (sown.state is PlotState.SOWN) != (got > 0), (verdict, got)


async def test_a_machine_harvest_and_a_hand_harvest_reap_one_bed_once(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    constants: Constants,
    catalog: Catalog,
) -> None:
    """The machine and its owner reach for the same ripe bed. The plot's row
    decides: whoever takes it first reaps, and the other finds bare ground --
    a harvest and its seed fund are not handed out twice."""
    from src.models.identity import Body

    place = await field(session, constants, water="river")
    await liquid_in(session, place.yard, LUBRICANT, 100)
    t0 = second_now()
    plot = await growing(session, constants, catalog, place.body, t0)
    plot.growth = 100
    row = await programmed(
        session, constants, catalog, place, [{"do": "harvest"}], [plot], t0, harvest=place.machine
    )
    row.counted_at = t0
    await session.commit()
    _slow(monkeypatch, farm, "settle")
    moment = t0 + timedelta(minutes=1)

    async def tick() -> None:
        async with factory() as db, db.begin():
            await agro.tick_machines(db, current(), now=moment)

    async def reap() -> str:
        await asyncio.sleep(0.05)
        async with factory() as db, db.begin():
            own_body = await db.get(Body, place.body.id)
            own_plot = await db.get(Plot, plot.id, with_for_update=True, populate_existing=True)
            try:
                await farm.harvest(db, current(), current_catalog(), own_body, own_plot, now=moment)
            except farm.WrongState:
                return "bare"
            return "reaped"

    await asyncio.gather(tick(), reap())

    async with factory() as db:
        harvests = await db.scalar(
            select(func.count()).where(Event.kind == EventKind.PLOT_HARVESTED.value)
        )
    assert harvests == 1
