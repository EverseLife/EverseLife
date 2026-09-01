# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Liquids at the terminal: the tank and the vessel (D-255).

A liquid trades out of the terminal's tank and only into a vessel; the tank
is exactly as big as its own vessel says, and a buyer without one waits.
The positions and the deals live in `test_market.py`.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from market_kit import _city, _trader
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import market, world
from src.models.inventory import Item
from src.units import amount_float, money

# --- the terminal's tank (D-255) ---------------------------------------------


LUBRICANT = "lubricant"


async def _with_canister(
    session: AsyncSession, node, name: str, *, fill: float = 0, funds: float = 0
):
    """A trader carrying a canister, optionally with lubricant already in it."""
    from src.engine import storage

    identity, body = await _trader(session, node, name, funds=funds)
    pocket = await world.body_container(session, body)
    canister = await world.grant_item(session, pocket, "canister", quality=60, origin="тест")
    if fill > 0:
        inside = await storage.inside(session, canister)
        await world.grant_item(session, inside, LUBRICANT, amount=fill, quality=55, origin="тест")
    return identity, body, canister


async def test_a_liquid_trades_out_of_the_tank_and_into_a_vessel(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The whole cycle of D-255: the seller pours in, the deal moves cells,
    the buyer pours out -- and the liquid lies loose nowhere on the way."""
    from src.engine import storage

    node = await _city(session)
    seller_id, seller, seller_can = await _with_canister(session, node, "Нефтяник", fill=50)
    loaded = await market.load(session, constants, seller, LUBRICANT, 30)
    assert loaded == pytest.approx(30)
    inside = await storage.inside(session, seller_can)
    left = (
        await session.execute(
            select(Item).where(Item.container_id == inside.id, Item.type_key == LUBRICANT)
        )
    ).scalar_one()
    assert amount_float(left.amount) == pytest.approx(20), "из канистры ушло ровно налитое"

    await market.sell(
        session,
        constants,
        catalog,
        seller_id,
        node,
        type_key=LUBRICANT,
        tier=market.tier_of(constants, 55),
        price=money(2),
        quantity=30,
    )

    buyer_id, buyer, buyer_can = await _with_canister(session, node, "Покупатель", funds=1000)
    fill = await market.buy(
        session,
        constants,
        catalog,
        buyer,
        type_key=LUBRICANT,
        tier=market.tier_of(constants, 55),
        price=money(2),
        quantity=30,
    )
    assert fill.traded == pytest.approx(30)

    #: Poured by the vessel's room (D-255): the canister holds twenty
    #: kilograms of it, the remainder waits in the tank for the next trip.
    room_units = 20 / catalog.recipes.mass_of(LUBRICANT)
    taken = await market.take(session, constants, buyer, LUBRICANT, 30)
    assert taken == pytest.approx(room_units, abs=0.01)
    inside = await storage.inside(session, buyer_can)
    got = (
        await session.execute(
            select(Item).where(Item.container_id == inside.id, Item.type_key == LUBRICANT)
        )
    ).scalar_one()
    assert amount_float(got.amount) == pytest.approx(taken), "слито ровно по месту тары"

    #: Nowhere on the way did the liquid lie loose in a pocket (D-230).
    pockets = [
        await world.body_container(session, seller),
        await world.body_container(session, buyer),
    ]
    for pocket in pockets:
        loose = (
            (
                await session.execute(
                    select(Item).where(Item.container_id == pocket.id, Item.type_key == LUBRICANT)
                )
            )
            .scalars()
            .all()
        )
        assert loose == []


async def test_the_tank_is_exactly_as_big_as_its_vessel(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """`market.tank_capacity` bounds the liquid market: a full tank refuses."""

    node = await _city(session)
    unit = catalog.recipes.mass_of(LUBRICANT)
    cap_units = constants[R.MARKET_TANK_CAPACITY] / unit
    _, seller, _ = await _with_canister(session, node, "Нефтяник", fill=cap_units + 100)

    #: The fixture overfills the canister on purpose (`grant_item` does not
    #: judge a vessel's store): the tank's own ceiling is what is under test,
    #: and load takes whatever the vessels actually hold.
    poured = await market.load(session, constants, seller, LUBRICANT, cap_units + 100)
    assert poured <= cap_units + 1e-6

    with pytest.raises(market.TankFull):
        await market.load(session, constants, seller, LUBRICANT, 1)


async def test_a_buyer_without_a_vessel_waits(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """No canister -- the purchase stays in the tank, said plainly (D-255)."""
    node = await _city(session)
    seller_id, seller, _ = await _with_canister(session, node, "Нефтяник", fill=50)
    await market.load(session, constants, seller, LUBRICANT, 30)
    await market.sell(
        session,
        constants,
        catalog,
        seller_id,
        node,
        type_key=LUBRICANT,
        tier=market.tier_of(constants, 55),
        price=money(2),
        quantity=30,
    )
    buyer_id, buyer = await _trader(session, node, "Безтарный", funds=1000)
    fill = await market.buy(
        session,
        constants,
        catalog,
        buyer,
        type_key=LUBRICANT,
        tier=market.tier_of(constants, 55),
        price=money(2),
        quantity=30,
    )
    assert fill.traded == pytest.approx(30)
    with pytest.raises(market.NoRoom):
        await market.take(session, constants, buyer, LUBRICANT, 30)
