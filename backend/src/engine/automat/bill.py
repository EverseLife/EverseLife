# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The automat's electricity (D-135, D-253): the hours the pool or the cells
let it work, and the bill to whoever programmed it.

Cut out of `run` (2026-09-13) when the machine aboard on its lines (D-340)
came to need the same bill from `aboard`: one arithmetic for both floors.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.engine import battery, energy, ledger
from src.models.automat import Automat as AutomatRow
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Node
from src.units import ENERGY_PER_TARIFF_UNIT, money


async def draw_energy(
    session: AsyncSession,
    constants: Constants,
    row: AutomatRow,
    node: Node,
    worked: float,
    rate: float,
    *,
    now: datetime,
) -> float:
    """Cap the worked hours by energy and pay for them. Returns the hours.

    From the city pool at the tariff, billed to the owner (D-135: whoever
    burns pays, presence or not) -- or from the node's own batteries where no
    grid reaches (D-071): no pool, no tariff, the energy was bought when the
    battery was charged.
    """
    pool = await energy.pool_of(session, constants, node, lock=True)
    if pool is None:
        taken = await battery.drain_batteries(session, constants, node, worked * rate, now=now)
        return taken / rate
    await energy.produce(session, constants, pool, now=now)
    can_hours = float(pool.stored) / rate
    worked = min(worked, can_hours)
    if worked <= 0:
        return 0.0
    drawn = worked * rate
    price = money(drawn / ENERGY_PER_TARIFF_UNIT * float(pool.tariff))
    if price > 0 and row.owner_identity_id is not None:
        account = await ledger.account_for(session, AccountKind.IDENTITY, row.owner_identity_id)
        treasury = await ledger.account_for(session, AccountKind.CITY_TREASURY, pool.node_id)
        try:
            await ledger.transfer(
                session,
                PostingReason.ENERGY_BILL,
                debit=account.id,
                credit=treasury.id,
                amount=price,
                memo={"energy": drawn, "for": "automat", "tariff": float(pool.tariff)},
            )
        except ledger.InsufficientFunds:
            #: Whoever burns pays (D-135), and whoever cannot pay does not
            #: burn: the machine stands, the pool keeps its energy, and the
            #: tick survives -- an unpaid factory is an obligation broken,
            #: not a worker crash.
            return 0.0
    energy.take_from_pool(pool, drawn)
    await session.flush()
    return worked
