"""The journal partitioned by month (wave 4)

`event` becomes `PARTITION BY RANGE (at)` with a primary key of `(id, at)`;
rows move into monthly partitions (`event_YYYYMM`) from the first month on
record to two months ahead, plus `event_default` for anything else. The id
sequence, the append-only and announce triggers and the three indexes
survive. Nothing references `event.id` by foreign key (`ledger_transaction
.event_id`, `job.cause_event_id` are plain bigints), so nothing else moves.

Takes as long as copying the journal: run it in a maintenance window.

Revision ID: d4b8e6c15a72
Revises: c91f4a7e2d58
Create Date: 2026-08-23 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from alembic import op
from sqlalchemy import text

from src.db import ddl
from src.engine.journal import _month_start, partition_ddl

revision: str = "d4b8e6c15a72"
down_revision: str | None = "c91f4a7e2d58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _event_rules() -> tuple[str, ...]:
    """The journal's triggers from `ddl.RULES`, minus the default partition
    (created explicitly above, in order)."""
    rules = dict(ddl.RULES)["event"]
    return tuple(s for s in rules if s is not ddl.DEFAULT_PARTITION)


def _months(first: datetime, last: datetime) -> list[datetime]:
    months = []
    cursor = _month_start(first)
    while cursor <= last:
        months.append(cursor)
        cursor = _month_start(cursor, 1)
    return months


def upgrade() -> None:
    bind = op.get_bind()
    op.execute("ALTER TABLE event RENAME TO event_old")
    op.execute("ALTER INDEX IF EXISTS ix_event_kind_at RENAME TO ix_event_old_kind_at")
    op.execute("ALTER INDEX IF EXISTS ix_event_actor_at RENAME TO ix_event_old_actor_at")
    op.execute("ALTER INDEX IF EXISTS ix_event_node_at RENAME TO ix_event_old_node_at")
    op.execute("DROP TRIGGER IF EXISTS event_announced ON event_old")
    op.execute("DROP TRIGGER IF EXISTS event_append_only ON event_old")
    #: The same sequence goes on: ids stay monotonic across the move.
    op.execute(
        """
CREATE TABLE event (
    id bigint NOT NULL DEFAULT nextval('event_id_seq'),
    at timestamptz NOT NULL DEFAULT now(),
    kind varchar NOT NULL,
    actor_identity_id uuid,
    node_id uuid,
    payload jsonb NOT NULL,
    constants_digest varchar,
    CONSTRAINT event_pkey PRIMARY KEY (id, at)
) PARTITION BY RANGE (at)
"""
    )
    op.execute("ALTER SEQUENCE event_id_seq OWNED BY event.id")
    first = bind.execute(text("SELECT min(at) FROM event_old")).scalar()
    now = datetime.now(UTC)
    start = first if first is not None else now
    for month in _months(start, _month_start(now, 2)):
        op.execute(partition_ddl(month))
    op.execute(ddl.DEFAULT_PARTITION)
    op.execute(
        """
INSERT INTO event (id, at, kind, actor_identity_id, node_id, payload, constants_digest)
SELECT id, at, kind, actor_identity_id, node_id, payload, constants_digest FROM event_old
"""
    )
    op.execute("DROP TABLE event_old")
    op.create_index("ix_event_kind_at", "event", ["kind", "at"])
    op.create_index("ix_event_actor_at", "event", ["actor_identity_id", "at"])
    op.create_index("ix_event_node_at", "event", ["node_id", "at"])
    for statement in _event_rules():
        op.execute(statement)


def downgrade() -> None:
    op.execute("ALTER TABLE event RENAME TO event_part")
    op.execute(
        """
CREATE TABLE event (
    id bigint NOT NULL DEFAULT nextval('event_id_seq'),
    at timestamptz NOT NULL DEFAULT now(),
    kind varchar NOT NULL,
    actor_identity_id uuid,
    node_id uuid,
    payload jsonb NOT NULL,
    constants_digest varchar,
    CONSTRAINT event_pkey PRIMARY KEY (id)
)
"""
    )
    op.execute("ALTER SEQUENCE event_id_seq OWNED BY event.id")
    op.execute(
        """
INSERT INTO event (id, at, kind, actor_identity_id, node_id, payload, constants_digest)
SELECT id, at, kind, actor_identity_id, node_id, payload, constants_digest FROM event_part
"""
    )
    op.execute("DROP TABLE event_part")
    op.create_index("ix_event_kind_at", "event", ["kind", "at"])
    op.create_index("ix_event_actor_at", "event", ["actor_identity_id", "at"])
    op.create_index("ix_event_node_at", "event", ["node_id", "at"])
    for statement in _event_rules():
        op.execute(statement)
