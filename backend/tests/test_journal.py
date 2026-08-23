# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The journal partitioned by month (wave 4)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.engine import events, journal
from src.models.event import EventKind


def test_partition_ddl_names_the_month() -> None:
    ddl = journal.partition_ddl(datetime(2026, 12, 15, tzinfo=UTC))
    assert "event_202612 PARTITION OF event" in ddl
    assert "FROM ('2026-12-01') TO ('2027-01-01')" in ddl


async def test_months_ahead_exist_and_rows_land_in_them(session: AsyncSession) -> None:
    now = datetime(2026, 8, 23, tzinfo=UTC)
    assert await journal.ensure_partitions(session, now=now) == journal.MONTHS_AHEAD + 1
    names = set(
        (
            await session.execute(
                text(
                    "SELECT inhrelid::regclass::text FROM pg_inherits "
                    "WHERE inhparent = 'event'::regclass"
                )
            )
        ).scalars()
    )
    assert {"event_202608", "event_202609", "event_202610", "event_default"} <= names

    row = await events.record(session, EventKind.TICK_RAN, kind_of_tick="test")
    await session.flush()
    where = await session.scalar(
        text("SELECT tableoid::regclass::text FROM event WHERE id = :id"), {"id": row.id}
    )
    assert where == f"event_{datetime.now(UTC):%Y%m}" or where == "event_default"
    #: The journal stays append-only on every partition.
    try:
        await session.execute(text("DELETE FROM event WHERE id = :id"), {"id": row.id})
    except Exception as refused:  # noqa: BLE001 -- the trigger's refusal is the assertion
        assert "только для добавления" in str(refused)
    else:
        raise AssertionError("удаление из журнала должно быть запрещено")
