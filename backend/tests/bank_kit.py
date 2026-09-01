# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The bank tests' shared fixtures: a borrower with a wallet, the money mass,
an account balance, a market deal, a city with turnover. Used by the bank
files (`test_bank*.py`); not collected by pytest.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from src.engine import ledger, world
from src.models.ledger import AccountKind, PostingReason
from src.units import money


async def _borrower(session: AsyncSession, *, funds: float = 0):
    identity = await world.create_identity(session, f"Заёмщик-{uuid.uuid4().hex[:6]}")
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
