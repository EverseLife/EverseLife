# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The journal's housekeeping: one partition per month, created ahead.

`event` is partitioned by month of `at` (wave 4). A row for a month without
a partition lands in `event_default`; that is a safety net, not the plan --
the daily tick keeps this month and the next two in place, so the default
stays empty. Partition names are `event_YYYYMM`.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from src.runtime import JOURNAL_MONTHS_AHEAD as MONTHS_AHEAD
from src.runtime import MONTHS_IN_YEAR

log = logging.getLogger(__name__)


def _month_start(moment: datetime, plus: int = 0) -> datetime:
    year, month = moment.year, moment.month + plus
    year += (month - 1) // MONTHS_IN_YEAR
    month = (month - 1) % MONTHS_IN_YEAR + 1
    return datetime(year, month, 1, tzinfo=UTC)


def partition_ddl(month: datetime) -> str:
    start = _month_start(month)
    end = _month_start(month, 1)
    name = f"event_{start:%Y%m}"
    return (
        f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF event "
        f"FOR VALUES FROM ('{start:%Y-%m-%d}') TO ('{end:%Y-%m-%d}')"
    )


async def ensure_partitions(session: AsyncSession, now: datetime | None = None) -> int:
    """Create the partitions for this month and `MONTHS_AHEAD` more. Returns
    how many were created; `IF NOT EXISTS` makes a repeat harmless.

    A month whose rows already landed in `event_default` (the worker was down
    longer than the months kept ahead) cannot be turned into a partition --
    Postgres refuses while the default holds matching rows. That is logged,
    not raised: the default keeps them, the step does not fail every day. The
    fix, when it happens, is a one-off DETACH/redistribute in a window.
    """
    moment = now or datetime.now(UTC)
    count = 0
    for plus in range(MONTHS_AHEAD + 1):
        stmt = partition_ddl(_month_start(moment, plus))
        try:
            await session.execute(text(stmt))
            count += 1
        except (ProgrammingError, IntegrityError) as trouble:
            log.warning("partition not created (%s): rows already in event_default", trouble)
            await session.rollback()
    return count
