# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The bank tests' shared fixtures: a borrower with a wallet, the money mass,
an account balance, a market deal, a city with turnover. Used by the bank
files (`test_bank*.py`); not collected by pytest.

A borrower here **is a citizen of a city with turnover**, and that is not
convenience but the rule (D-281): only a city lends, only to its own, and only
against its line with the capital -- so an identity belonging nowhere is not a
borrower at all, it is somebody the bank has no answer for.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from src.engine import ledger, world
from src.models.ledger import AccountKind, PostingReason
from src.units import money


async def _enrol(session: AsyncSession, who, *, turnover: float = 4000):
    """Make this person a citizen of a city whose line with the capital is open.

    Enrolled by hand rather than through the door, because the tests around
    this are about the loan and not about the print: what the door does is
    checked where citizenship is (`test_city_founding`).
    """
    from src.models.city import Citizen

    city = await _city_with_turnover(session, turnover=turnover)
    session.add(Citizen(identity_id=who.id, city_id=city.id))
    await session.flush()
    return city


async def _home(session: AsyncSession, who):
    """The city this person belongs to -- the one that lends to them (D-281)."""
    from src.engine import city as town

    entry = await town.citizenship(session, who.id)
    return None if entry is None else await town.by_id(session, entry.city_id)


async def _borrower(session: AsyncSession, *, funds: float = 0, turnover: float = 4000):
    """A person the bank can serve: a citizen of a city whose line is open."""
    identity = await world.create_identity(session, f"Заёмщик-{uuid.uuid4().hex[:6]}")
    await _enrol(session, identity, turnover=turnover)
    if funds:
        account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
        genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
        await ledger.transfer(
            session,
            PostingReason.GENESIS,
            debit=genesis.id,
            credit=account.id,
            amount=money(funds),
            memo={},
        )
    return identity


async def _city_with_turnover(
    session: AsyncSession, catalog=None, turnover: float = 4000, goods: str = "bread"
):
    """The city on whose territory the deals happened: the share is computed by them.

    The catalog is asked of the loaded vault when not handed in: a borrower
    needs a city of its own (D-281), and threading a fixture through every
    call that only wants a lender would say nothing about the test.
    """
    from src.constants import current_catalog
    from src.engine import city as town
    from src.models.market import Order, OrderSide, Trade
    from src.models.world import Layer
    from src.units import amount as _amount

    stamp = uuid.uuid4().hex[:8]
    planet = await world.create_node(
        session, f"terra.{stamp}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    delegate = await world.create_node(
        session,
        f"terra.city.{stamp}",
        f"Город-{stamp}",
        area_m2=1,
        layer=Layer.PLANET,
        parent=planet,
    )
    marketplace = await world.create_node(
        session,
        f"terra.city.{stamp}.market",
        "Рынок",
        area_m2=50,
        parent=delegate,
    )
    city = await town.found(session, catalog or current_catalog(), delegate, f"Город-{stamp}")
    marketplace.owner_city_id = city.id
    seller = await world.create_identity(session, f"Купец-{stamp}")
    order_ = Order(
        node_id=marketplace.id,
        identity_id=seller.id,
        side=OrderSide.SELL,
        type_key=goods,
        tier="common",
        price=money(turnover),
        amount_total=_amount(1),
        amount_left=0,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    session.add(order_)
    await session.flush()
    session.add(
        Trade(
            node_id=marketplace.id,
            sell_order_id=order_.id,
            type_key=goods,
            tier="common",
            price=money(turnover),
            amount=_amount(1),
        )
    )
    await session.flush()
    return city


# --- price sensor and sterilisation (D-087, D-169) ---------------------------


async def _deal(session: AsyncSession, goods: str, price: float, qty: float, seller=None):
    """A concluded deal: the price index is computed from them."""
    from src.models.market import Trade
    from src.units import amount as _amount

    node = await world.create_node(
        session, f"terra.mkt.{uuid.uuid4().hex[:8]}", "Рынок", area_m2=10
    )
    if seller is None:
        seller = await world.create_identity(session, f"П-{uuid.uuid4().hex[:6]}")
    from src.models.market import Order, OrderSide

    order_ = Order(
        node_id=node.id,
        identity_id=seller.id,
        side=OrderSide.SELL,
        type_key=goods,
        tier="common",
        price=money(price),
        amount_total=_amount(qty),
        amount_left=0,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    session.add(order_)
    await session.flush()
    session.add(
        Trade(
            node_id=node.id,
            sell_order_id=order_.id,
            type_key=goods,
            tier="common",
            price=money(price),
            amount=_amount(qty),
        )
    )
    await session.flush()
