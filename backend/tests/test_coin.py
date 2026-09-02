# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Coin: minting and melting (D-016, D-086).

Checked is what the coin was introduced separately from the account for at all:

* a coin is an item, not an entry: it has a mark, a fineness and a place in the pocket;
* one fineness for the whole world -- `coin.default_fineness` (900 per mille),
  the issuer has no choice: the composition is set by recipe amounts -- 0.9
  refined and 0.1 iron;
* the batch reaches the purse through the job journal -- coins do not vanish;
* melting returns the refined metal minus loss, the alloy is lost;
* minting happens only at the mint press and only with one's own metal;
* the coin does not go through craft's common door: it has its own.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import coin, craft, jobs, world
from src.models.craft import CraftBatch
from src.models.inventory import Item
from src.models.job import Job
from src.units import PERCENT, amount_float

GOLD = "gold_coin"
GOLD_METAL = "refined_gold"
IRON = "iron_ingot"


#: Ten ingots, not a hundred: a body over the carry limit drops what it mints
#: (D-265), and a hundred kilograms of iron is over it three times.
async def _yard(session: AsyncSession, *, metal_: float = 100, iron: float = 10):
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.mint.{stamp}", "Двор", area_m2=100)
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, "coin_station", quality=60, origin="тест")
    identity = await world.create_identity(session, f"Чеканщик-{stamp}")
    body = await world.print_body(session, identity, node)
    pocket = await world.body_container(session, body)
    if metal_:
        await world.grant_item(
            session, pocket, GOLD_METAL, amount=metal_, quality=60, origin="тест"
        )
    if iron:
        await world.grant_item(session, pocket, IRON, amount=iron, quality=55, origin="тест")
    await world.learn(session, identity, GOLD)
    return node, identity, body


async def _bring_to(session: AsyncSession, batch: CraftBatch) -> None:
    """Finish **this** batch early by the test's hands -- as the worker would."""
    job = (
        await session.execute(select(Job).where(Job.dedup_key == f"craft.batch:{batch.id}"))
    ).scalar_one()
    job.run_at = datetime.now(UTC)
    await craft.finish(session, job)


async def _coins(session: AsyncSession, body) -> list[Item]:
    pocket = await world.body_container(session, body)
    rows = await session.execute(
        select(Item).where(Item.container_id == pocket.id, Item.type_key == GOLD)
    )
    return list(rows.scalars().all())


# --- minting -----------------------------------------------------------------


async def test_minting_spends_recipe_composition(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """0.9 refined and 0.1 iron per coin -- from recipe amounts, not from imagination."""
    _, _, body = await _yard(session)
    batch = await coin.mint(session, constants, catalog, body, GOLD, 10)

    composition = coin.per_coin(catalog, GOLD)
    assert composition == {GOLD_METAL: pytest.approx(0.9), IRON: pytest.approx(0.1)}
    assert batch.spent[GOLD_METAL] == pytest.approx(10 * composition[GOLD_METAL])
    assert batch.spent[IRON] == pytest.approx(10 * composition[IRON])
    #: Fineness is not chosen: it is one for the whole world.
    assert float(batch.fineness) == constants[R.COIN_DEFAULT_FINENESS]


async def test_coin_arrives_with_mark_and_fineness_but_no_quality(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A coin is described by its metal content, not by the quality scale."""
    _, identity, body = await _yard(session)
    batch = await coin.mint(session, constants, catalog, body, GOLD, 5)
    await _bring_to(session, batch)

    (stack,) = await _coins(session, body)
    assert amount_float(stack.amount) == 5
    assert float(stack.fineness) == constants[R.COIN_DEFAULT_FINENESS]
    assert stack.quality is None, "у монеты нет качества: есть проба"
    assert stack.maker_identity_id == identity.id, "клеймо эмитента"


async def test_coins_not_lost_on_way_through_journal(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Regression for "coins vanish on minting": the full path through the worker.

    Metal is written off at start, coins arrive by job execution -- by the same
    code the real worker would deliver them with, including a job retry: a
    second run creates no second coins and eats no first ones.
    """
    async with factory() as session, session.begin():
        _, _, body = await _yard(session)
        batch = await coin.mint(session, constants, catalog, body, GOLD, 7)
        batch_id, body_id, term = batch.id, body.id, batch.ready_at

    fulfilled = await jobs.run_one(factory, now=term)
    assert fulfilled is not None and fulfilled.last_error is None

    #: A repeat of the same job gives no second coins.
    assert await jobs.run_one(factory, now=term) is None

    async with factory() as session:
        from src.models.identity import Body

        body = await session.get(Body, body_id)
        coins = await _coins(session, body)
        assert sum(amount_float(m.amount) for m in coins) == 7
        batch = await session.get(CraftBatch, batch_id)
        assert batch.state.value == "done"


async def test_machine_busy_with_minting(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Minting is work at a machine: a second batch on the same machine does not go
    (D-150). The minter's own second batch waits its turn instead (D-209)."""
    _, _, body = await _yard(session)
    first = await coin.mint(session, constants, catalog, body, GOLD, 2)
    second = await coin.mint(session, constants, catalog, body, GOLD, 2)
    assert first.state.value == "running"
    assert second.state.value == "waiting"


async def test_no_minting_without_mint_press(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A machine in the node -- the same condition as for any craft."""
    stamp = uuid.uuid4().hex[:6]
    node = await world.create_node(session, f"terra.bare.{stamp}", "Голо", area_m2=50)
    identity = await world.create_identity(session, f"Босой-{stamp}")
    body = await world.print_body(session, identity, node)
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, GOLD_METAL, amount=50, quality=60, origin="тест")
    await world.grant_item(session, pocket, IRON, amount=50, quality=60, origin="тест")
    await world.learn(session, identity, GOLD)

    with pytest.raises(craft.NoStation):
        await coin.mint(session, constants, catalog, body, GOLD, 1)


async def test_no_minting_without_metal(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Matter is not created: no gold -- no coin (I1)."""
    _, _, body = await _yard(session, metal_=1)
    with pytest.raises(craft.NotEnough):
        await coin.mint(session, constants, catalog, body, GOLD, 5)


async def test_no_minting_without_iron(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The alloy is an input like any other: without 0.1 iron per coin the batch does not start."""
    _, _, body = await _yard(session, iron=0)
    with pytest.raises(craft.NotEnough):
        await coin.mint(session, constants, catalog, body, GOLD, 5)


async def test_no_fractional_coin(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, _, body = await _yard(session)
    with pytest.raises(coin.CoinError):
        await coin.mint(session, constants, catalog, body, GOLD, 2.5)


# --- melting -----------------------------------------------------------------


async def test_melting_returns_refined_minus_loss(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The return is the `craft.recycle_return` share of 0.9 per coin; iron is loss."""
    _, _, body = await _yard(session, metal_=100)
    batch = await coin.mint(session, constants, catalog, body, GOLD, 4)
    await _bring_to(session, batch)
    (stack,) = await _coins(session, body)

    before = await _in_pocket(session, body, GOLD_METAL)
    smelting = await coin.melt(session, constants, catalog, body, stack, 4)
    await _bring_to(session, smelting)

    after = await _in_pocket(session, body, GOLD_METAL)
    share = constants[R.CRAFT_RECYCLE_RETURN] / PERCENT
    assert after - before == pytest.approx(4 * 0.9 * share, abs=0.01)


async def test_only_part_of_stack_melted(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Coins lie in a stack, and melting one does not destroy the rest."""
    _, _, body = await _yard(session)
    batch = await coin.mint(session, constants, catalog, body, GOLD, 6)
    await _bring_to(session, batch)
    (stack,) = await _coins(session, body)

    await coin.melt(session, constants, catalog, body, stack, 2)
    left = sum(amount_float(item.amount) for item in await _coins(session, body))
    assert left == 4


async def test_cannot_melt_more_than_have(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, _, body = await _yard(session)
    batch = await coin.mint(session, constants, catalog, body, GOLD, 2)
    await _bring_to(session, batch)
    (stack,) = await _coins(session, body)

    with pytest.raises(coin.CoinError):
        await coin.melt(session, constants, catalog, body, stack, 3)


# --- one door ----------------------------------------------------------------


async def test_coin_not_made_by_ordinary_batch(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Otherwise there would be two ways to mint in the world."""
    _, _, body = await _yard(session)
    with pytest.raises(craft.Unmakeable):
        await craft.plan(session, constants, catalog, body, GOLD, 1)


async def test_coin_not_recycled_by_ordinary_recycling(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """General recycling would return metal by the recipe norm, not by composition."""
    _, _, body = await _yard(session)
    batch = await coin.mint(session, constants, catalog, body, GOLD, 3)
    await _bring_to(session, batch)
    (stack,) = await _coins(session, body)

    with pytest.raises(craft.Unmakeable):
        await craft.recycle(session, constants, catalog, body, stack)


async def _in_pocket(session: AsyncSession, body, type_key: str) -> float:
    pocket = await world.body_container(session, body)
    rows = (
        (
            await session.execute(
                select(Item).where(Item.container_id == pocket.id, Item.type_key == type_key)
            )
        )
        .scalars()
        .all()
    )
    return sum(amount_float(item.amount) for item in rows)
