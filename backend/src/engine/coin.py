# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Coin: minting and melting (D-016, D-086).

Money in the world has two forms, and they are built in fundamentally
different ways (30-economy/01-currency):

* **Terracoin** -- an electronic entry on an account. Weightless, instant,
  exists only as somebody's debt. Lives in double-entry bookkeeping
  (`engine/ledger.py`);
* **Coin** -- an item. Lies in a pocket, perishes with the body, circulates
  where there is no terminal.

This module is about the second one. The coin is deliberately not an account:
it travels the same paths as a pickaxe or a sack of grain, because it is the
same kind of matter.

## One fineness, decided by the vault

Coin fineness is `coin.default_fineness` (900 per mille), and **the issuer does
not choose it**: the coin's composition is fixed by the recipe amounts -- 0.9 of
refined metal and 0.1 of iron ingot as alloy per coin. The debasement mechanic
is gone: there is no varying fineness in the world, and a coin always contains
what it promises.

**The mark is `maker_identity_id`**, the very field that signs every item a
craftsman makes (D-058): the coin remembers its minter the same way a pickaxe
remembers its smith.

## The alloy is spent by whole ingots

A tenth of an iron ingot goes into a coin, and a tenth of an ingot does not
exist: a counted thing gives itself to a work whole (D-212). So the batch total
rounds up -- seven coins eat the ingot as entirely as ten do -- and the small
batch is dearer than the big one, which is the declared price of countedness
and is shown in the forecast before the work starts (D-092). The refined metal
is weighed, not counted, and its fraction is honest. The arithmetic is
`goods`', the same one every other batch is spent by.

**Melting returns the refined metal** -- by the `craft.recycle_return` share,
like every recycling. The alloy is lost: picking a tenth of iron out of the
alloy costs more than the iron itself.

## What is not here and why

* **The engine knows no TC-to-coin exchange rate** and must not: the ratio is
  set by the player market (D-016), and the order book for coins is the same
  one used for ore.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, ConstantError, Constants
from src.constants import registry as R
from src.constants.catalog import ItemKind
from src.engine import craft, events, goods, travel, wear, world
from src.engine.errors import Refusal
from src.engine.world import body_container
from src.models.craft import BatchKind, CraftBatch
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Container, Item
from src.units import PERCENT, amount, amount_float


class CoinError(Refusal):
    pass


class NotCoin(CoinError):
    """Not a coin. A coin is an item of kind `money` from `build/recipes.json`."""


def is_coin(catalog: Catalog, type_key: str) -> bool:
    """Whether this is a coin. Data decides: recipe kind `money` (D-090)."""
    try:
        return catalog.recipes.recipe(type_key).kind is ItemKind.MONEY
    except ConstantError:
        #: Raw material with no recipe is certainly not money either.
        return False


def fineness_of(constants: Constants) -> float:
    """Coin fineness, per mille. One for the whole world: no debasement mechanic."""
    return constants[R.COIN_DEFAULT_FINENESS]


def per_coin(catalog: Catalog, coin: str) -> dict[str, float]:
    """Coin composition: input name -> amount per coin.

    Taken from the recipe amounts (0.9 refined + 0.1 iron), not from constants:
    the composition *is* the recipe, there must be no second table (D-065).
    """
    recipe = catalog.recipes.recipe(coin)
    if recipe.kind is not ItemKind.MONEY:
        raise NotCoin(key="coin-not-a-coin", goods=recipe.type_key)
    if not recipe.amounts:
        raise NotCoin(key="coin-no-composition", goods=recipe.type_key)
    return {catalog.recipes.resolve(name): value for name, value in recipe.amounts.items()}


def metal_of(catalog: Catalog, coin: str) -> str:
    """The coin's refined metal -- the first recipe input. It is what melting
    returns; the alloy (iron) is lost."""
    recipe = catalog.recipes.recipe(coin)
    if recipe.kind is not ItemKind.MONEY:
        raise NotCoin(key="coin-not-a-coin", goods=recipe.type_key)
    if not recipe.inputs:
        raise NotCoin(key="coin-no-input", goods=recipe.type_key)
    return catalog.recipes.resolve(recipe.inputs[0])


def melt_return(constants: Constants, catalog: Catalog, coin: str, count: float) -> float:
    """How much refined metal melting returns.

    The metal share in the coin comes from the recipe, the loss is the common
    one for all recycling, `craft.recycle_return`: the vault sets no separate
    number for coins (D-065).
    """
    composition = per_coin(catalog, coin)
    metal = metal_of(catalog, coin)
    share = constants[R.CRAFT_RECYCLE_RETURN] / PERCENT
    return count * composition.get(metal, 0.0) * share


async def mint(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    coin: str,
    count: float,
    *,
    tiers: dict[str, str] | None = None,
    now: datetime | None = None,
) -> CraftBatch:
    """Mint a batch of coins.

    In-person and long-running, like every work at a machine: the mint press
    stands in the node, metal and alloy are written off at once, coins arrive on
    schedule as a journal job. Fineness is always `coin.default_fineness` --
    there is no choice.
    """
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        raise CoinError(key="coin-dead-mints")
    await travel.require_here(session, body)

    #: Import inside: `craft` knows about coins only through this module, not
    #: the other way round -- otherwise there would be a cycle.

    recipe = catalog.recipes.recipe(coin)
    if recipe.kind is not ItemKind.MONEY:
        raise NotCoin(key="coin-not-minted", goods=recipe.type_key)
    if not await craft._knows(session, body, recipe.type_key):  # noqa: SLF001
        raise craft.NotLearned(key="craft-not-learned", recipe=recipe.type_key)

    if count <= 0 or count != int(count):
        raise CoinError(key="coin-whole-only")
    if count > constants[R.CRAFT_BATCH_MAX]:
        raise craft.TooBig(
            key="craft-batch-too-big", units=count, most=constants[R.CRAFT_BATCH_MAX]
        )

    composition = per_coin(catalog, coin)
    proc = craft.Procedure(
        output=recipe.type_key,
        station=catalog.recipes.resolve(recipe.station) if recipe.station else None,
        tools=(),
        inputs=tuple(composition),
        per_unit=composition,
        step_hours=craft.step_hours(catalog, recipe),
        mix=False,
        needs_recipe=True,
    )
    station = await craft._station_item(session, body, proc)  # noqa: SLF001

    #: What a counted thing gives to the work, it gives whole (D-212): the alloy
    #: is a tenth of an iron ingot, and the ingot goes into the batch entire --
    #: half of one exists nowhere, neither in the hands nor in the recipe. The
    #: refined metal is weighed, and its fraction is honest, so it is left as it
    #: is. The arithmetic is `goods`', not a second one of this module's own.
    needed = {
        name: goods.whole(name, qty * count, up=True, catalog=catalog)
        for name, qty in composition.items()
    }
    pocket = await body_container(session, body)
    #: Which metal goes under the die is the minter's choice by tier (D-058).
    stock = await craft._stock(  # noqa: SLF001
        session,
        pocket,
        tuple(needed),
        tiers=craft._tiers_by(catalog, tiers),  # noqa: SLF001
    )
    picks = craft._pick(stock, needed)  # noqa: SLF001

    scale = constants[R.QUALITY_SCALE]
    #: Metal quality is not passed on to the coin: fineness describes it. The
    #: number lives in the batch only because the field is shared by all work
    #: at a machine.
    metal_quality = craft._material_quality(picks, scale.max)  # noqa: SLF001
    for pick in picks:
        if pick.item.amount > pick.take:
            pick.item.amount -= pick.take
        else:
            await session.delete(pick.item)
    await session.flush()

    minutes = craft.batch_minutes(constants, proc, count, wear.effective(constants, station))
    fineness = fineness_of(constants)
    batch = CraftBatch(
        body_id=body.id,
        node_id=body.node_id,
        output=recipe.type_key,
        units=amount(count),
        station=None if station is None else station.type_key,
        quality=Decimal(str(metal_quality)),
        spread=Decimal(str(scale.min)),
        spent=needed,
        fineness=Decimal(str(fineness)),
        remaining_seconds=craft._seconds(minutes),  # noqa: SLF001
    )
    #: Minting is work at a machine like any other (D-150, D-209): it takes the
    #: press while it runs, waits its turn behind the minter's other work, and
    #: freezes when the minter walks away.
    return await craft._launch(  # noqa: SLF001
        session,
        batch,
        body,
        now=moment,
        event={
            "work": "mint",
            "output": recipe.type_key,
            "units": count,
            "fineness": fineness,
            "spent": needed,
        },
    )


async def melt(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    item: Item,
    count: float,
    *,
    now: datetime | None = None,
) -> CraftBatch:
    """Melt coins back into refined metal.

    A separate work rather than the general recycling: coins lie in a stack,
    and only part of it needs taking apart, not the whole.
    """
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        raise CoinError(key="coin-dead-works")
    await travel.require_here(session, body)

    if not is_coin(catalog, item.type_key):
        raise NotCoin(key="coin-not-melted", goods=item.type_key)

    pocket = await body_container(session, body)
    if item.container_id != pocket.id:
        raise CoinError(key="coin-not-in-hands")
    qty = amount(count)
    if qty <= 0 or qty > item.amount:
        raise CoinError(key="coin-not-enough", have=amount_float(item.amount))

    machine = catalog.recipes.recipe(item.type_key).station
    proc = craft.Procedure(
        output=item.type_key,
        station=catalog.recipes.resolve(machine) if machine else None,
        tools=(),
        inputs=(),
        per_unit={},
        step_hours=craft.step_hours(catalog, catalog.recipes.recipe(item.type_key)),
        mix=False,
        needs_recipe=False,
    )
    #: Melting happens where minting does: things are taken apart and repaired
    #: at the machine that makes them.
    station = await craft._station_item(session, body, proc)  # noqa: SLF001

    fineness = fineness_of(constants) if item.fineness is None else float(item.fineness)
    if item.amount > qty:
        item.amount -= qty
    else:
        await session.delete(item)
    await session.flush()

    minutes = craft.batch_minutes(constants, proc, count, wear.effective(constants, station))
    scale = constants[R.QUALITY_SCALE]
    batch = CraftBatch(
        body_id=body.id,
        node_id=body.node_id,
        kind=BatchKind.RECYCLE,
        output=item.type_key,
        units=amount(count),
        station=None if station is None else station.type_key,
        quality=Decimal(str(scale.min)),
        spread=Decimal(str(scale.min)),
        spent={item.type_key: count},
        fineness=Decimal(str(fineness)),
        remaining_seconds=craft._seconds(minutes),  # noqa: SLF001
    )
    return await craft._launch(  # noqa: SLF001
        session,
        batch,
        body,
        now=moment,
        event={
            "work": "melt",
            "output": item.type_key,
            "units": count,
            "fineness": fineness,
        },
    )


async def finish_melt(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    batch: CraftBatch,
    where: Container,
) -> list[float]:
    """Melting is done: refined metal came back, the alloy is lost."""
    metal = metal_of(catalog, batch.output)
    returned = melt_return(constants, catalog, batch.output, amount_float(batch.units))
    if returned <= 0:  # pragma: no cover -- a batch of zero never starts
        return []

    #: The metal quality of a coin is unknown: the coin does not remember it and
    #: the vault assigns no quality to metal inside a coin. Take the middle of
    #: the scale -- the same thing the engine does with any raw material without
    #: a history.

    scale = constants[R.QUALITY_SCALE]
    molten = Item(
        container_id=where.id,
        type_key=metal,
        amount=amount(returned),
        quality=Decimal(str(scale.mid)),
    )
    session.add(molten)
    await world.stack_up(session, molten)
    await events.record(
        session,
        EventKind.ITEM_CONSUMED,
        type_key=batch.output,
        cause="coin_melt",
        units=amount_float(batch.units),
        returned=returned,
    )
    await session.flush()
    return [scale.mid]
