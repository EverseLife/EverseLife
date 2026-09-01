# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The city order's vocabulary and floor: the tariff and the labor split,
the licence check, and what counts as work under way. Asks nobody above
itself.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine.errors import Refusal
from src.engine.estate.upkeep import repairing
from src.models.job import Job, JobKind, JobState
from src.models.works import WorkOrder, WorkOrderKind, WorkOrderState
from src.models.world import Node
from src.units import AMOUNT_SCALE, MINUTES_PER_HOUR, PERCENT, money

#: Fuel orders count in units split like every amount; a tail below the last
#: digit of the scale is representation noise, not undelivered fuel.
_EPS = 1 / AMOUNT_SCALE


class WorksCityError(Refusal):
    pass


def labor_tariff(constants: Constants, minutes: float) -> int:
    """The labour part of an order, minor units: hours at the public rate."""
    return money(minutes / MINUTES_PER_HOUR * constants[R.WORKS_HOUR_RATE])


def split_labor(constants: Constants, labor: int) -> tuple[int, int]:
    """(city's share, fund's share) of the labour tariff, by `works.city_cofinance`."""
    city_part = int(labor * constants[R.WORKS_CITY_COFINANCE] / PERCENT)
    return city_part, labor - city_part


async def open_city_order(
    session: AsyncSession, kind: WorkOrderKind, node: Node, *, for_update: bool = False
) -> WorkOrder | None:
    stmt = select(WorkOrder).where(
        WorkOrder.kind == kind,
        WorkOrder.state == WorkOrderState.OPEN,
        WorkOrder.node_id == node.id,
    )
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalars().first()


async def _work_under_way(session: AsyncSession, kind: WorkOrderKind, node: Node) -> bool:
    """Whether a pending repair or construction job holds this order's plot.

    Fuel is not here on purpose: a delivery is paid per pour as it lands, so
    withdrawal loses nobody anything already done.
    """
    if kind is WorkOrderKind.BUILDING_REPAIR:
        return await repairing(session, node)
    if kind is WorkOrderKind.BUILDING_BUILD:
        rows = (
            (
                await session.execute(
                    select(Job).where(
                        Job.kind == JobKind.BUILD_FINISH.value,
                        Job.state == JobState.PENDING,
                    )
                )
            )
            .scalars()
            .all()
        )
        return any(job.payload.get("node") == str(node.id) for job in rows)
    return False


async def licensed(session: AsyncSession, kind: WorkOrderKind, node: Node) -> bool:
    """Whether an open city order licenses this work on this plot (D-248).

    The order is the permission: the city posted it holding the TREASURY
    power, and withdrawing it takes the permission back with the escrow.
    """
    return await open_city_order(session, kind, node) is not None
