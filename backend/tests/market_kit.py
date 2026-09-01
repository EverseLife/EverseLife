# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The market tests' shared fixtures: a city over the node, an authority in
it, a trader with funds, goods in a pocket, a balance read. Used by the
market files (`test_market*.py`); not collected by pytest.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.engine import ledger, market, world
from src.models.ledger import AccountKind, PostingReason
from src.units import money

ORE = "iron_ore"

TERMINAL = "market_terminal"


async def _city(session: AsyncSession, *, city=None):
    """A node with a terminal. One marketplace per city (D-100)."""
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.market.{stamp}", "Торг", area_m2=100)
    node.owner_city_id = None if city is None else city.id
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, TERMINAL, quality=70, origin="сценарий теста")
    return node


async def _trader(session: AsyncSession, node, name: str, *, funds: float = 0):
    identity = await world.create_identity(session, f"{name}-{uuid.uuid4().hex[:6]}")
    body = await world.print_body(session, identity, node)
    if funds:
        account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
        genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
        await ledger.transfer(
            session,
            PostingReason.GENESIS,
            debit=genesis.id,
            credit=account.id,
            amount=money(funds),
        )
    return identity, body


async def _with_goods(
    session: AsyncSession,
    constants: Constants,
    node,
    name: str,
    *,
    qty: float = 10,
    quality: float = 65,
):
    identity, body = await _trader(session, node, name)
    pocket = await world.body_container(session, body)
    await world.grant_item(
        session, pocket, ORE, amount=qty, quality=quality, origin="сценарий теста"
    )
    await market.load(session, constants, body, ORE, qty)
    return identity, body
