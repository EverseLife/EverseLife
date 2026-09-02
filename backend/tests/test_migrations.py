# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Migrations must match the models.

The world is eternal, no wipes (D-007) -- so a schema divergence from code
is not fixed by recreating the database. The check catches it the day it appears.

The test looks at a database upgraded by migrations (`alembic upgrade head`)
and requires that autogeneration finds not a single difference.

The last pair goes further and looks at both schemas at once -- the migrated
one and the one built from the models -- because what a model cannot express
(sequence ownership, triggers, partitions) is exactly what diverges silently.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import func, inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.models import Base

MIGRATED_URL = os.environ.get(
    "EVERSELIFE_MIGRATED_DATABASE_URL",
    "postgresql+asyncpg://everselife:everselife@localhost:5432/everselife",
)


PARTITION = re.compile(r"^event_(\d{6}|default)$")
PARTITION_INDEX = re.compile(r"^event_(\d{6}|default)_")


def _differences(connection) -> list:
    #: The journal's partitions (`event_YYYYMM`, `event_default`) are tables
    #: Postgres makes under the parent; the models know only the parent.
    def include(name, type_, parent_names):
        if name is None:
            return True
        partition = (type_ == "table" and PARTITION.match(name)) or (
            type_ == "index" and PARTITION_INDEX.match(name)
        )
        return not partition

    context = MigrationContext.configure(
        connection, opts={"compare_type": True, "include_name": include}
    )
    return compare_metadata(context, Base.metadata)


async def test_schema_from_migrations_matches_models() -> None:
    engine = create_async_engine(MIGRATED_URL)
    try:
        async with engine.connect() as connection:
            tables = await connection.run_sync(lambda c: inspect(c).get_table_names())
            if "alembic_version" not in tables:
                pytest.skip("база не накатана миграциями: `alembic upgrade head`")
            diff = await connection.run_sync(_differences)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"нет базы {MIGRATED_URL}: {exc}")
    finally:
        await engine.dispose()

    assert not diff, (
        "схема разошлась с моделями: "
        + "; ".join(str(item) for item in diff)
        + ". Нужна миграция: `alembic revision --autogenerate`"
    )


def test_a_law_choice_keeps_its_meaning_across_the_rename() -> None:
    """The words a city had become the key the engine acted on, not the key
    that looks like them.

    `build_permit` and `body_print` were free text read by substring, and the
    two read the **empty** value differently: an unset permit opened the ring,
    an unset printer paid for nobody. The migration carries that difference,
    so no city changes behaviour by being migrated -- which is the only thing
    a rename of stored values owes anybody.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "law_choices",
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "b4e91c07af52_law_choices_are_keys.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    #: Left column: what the cities hold. Right: what the old reader did.
    assert module._permit("") == "everyone", "an unset permit opened the ring"
    assert module._print("") == "nobody", "an unset printer paid for nobody"
    for said in ("никто", "Никто и никогда", "нет", "-"):
        assert module._permit(said) == "nobody", said
    for said in ("гражданам", "Гражданам города", "ГРАЖДАНЕ"):
        assert module._permit(said) == "citizens", said
        assert module._print(said) == "citizens", said
    for said in ("всем", "кому угодно", "да"):
        assert module._permit(said) == "everyone", said
        assert module._print(said) == "everyone", said
    #: A value already a key survives a second run untouched.
    for said in ("nobody", "citizens", "everyone"):
        assert module._permit(said) in {"nobody", "citizens", "everyone"}


async def test_a_law_choice_is_rewritten_in_the_rows_themselves(
    session: AsyncSession, catalog
) -> None:
    """And the walk over the table does it, not only the mapping beside it.

    The clean-database run the house rule asks for proves the migration
    *applies*; it cannot prove it rewrites anything, because a fresh database
    has no cities. This puts two of them there with the words they used to
    hold and reads the keys back out.
    """
    import importlib.util

    from city_kit import _capital

    spec = importlib.util.spec_from_file_location(
        "law_choices_rows",
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "b4e91c07af52_law_choices_are_keys.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    city, _ = await _capital(session, catalog)
    city.laws = {"build_permit": "граждане", "body_print": "всем", "tax_trade": "7"}
    await session.commit()

    #: `op.get_bind()` wants an alembic context; the walk itself only wants a
    #: connection, so it is handed one straight.
    await session.run_sync(
        lambda sync: module._rewrite(
            {"build_permit": module._permit, "body_print": module._print},
            bind=sync.connection(),
        )
    )
    await session.commit()
    await session.refresh(city)

    assert city.laws["build_permit"] == "citizens"
    assert city.laws["body_print"] == "everyone"
    #: A law that is not a choice is not touched at all.
    assert city.laws["tax_trade"] == "7"


async def test_database_rules_in_place() -> None:
    """The balance and immutability triggers must be in the upgraded database."""
    engine = create_async_engine(MIGRATED_URL)
    try:
        async with engine.connect() as connection:
            rows = await connection.execute(
                text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")
            )
            names = {row[0] for row in rows}
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"нет базы {MIGRATED_URL}: {exc}")
    finally:
        await engine.dispose()

    assert {
        "ledger_entry_balanced",
        "ledger_entry_append_only",
        "event_append_only",
        "ledger_transaction_append_only",
    } <= names


#: Which column a sequence belongs to, if any. Ownership is not a detail: it
#: decides whether `TRUNCATE ... RESTART IDENTITY` resets the counter, and the
#: two schemas -- built from the models, and migrated -- must agree about it.
OWNER_OF_JOURNAL_SEQUENCE = text(
    """
    SELECT t.relname, a.attname
    FROM pg_class c
    JOIN pg_depend d ON d.objid = c.oid AND d.deptype = 'a'
    JOIN pg_class t ON t.oid = d.refobjid
    JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = d.refobjsubid
    WHERE c.relkind = 'S'
      AND c.relname = 'event_id_seq'
      AND d.classid = 'pg_class'::regclass
      AND d.refclassid = 'pg_class'::regclass
    """
)


async def test_the_journal_counter_belongs_to_its_column_when_migrated() -> None:
    """The migrated schema: the sequence is owned by `event.id`."""
    engine = create_async_engine(MIGRATED_URL)
    try:
        async with engine.connect() as connection:
            owner = (await connection.execute(OWNER_OF_JOURNAL_SEQUENCE)).all()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"нет базы {MIGRATED_URL}: {exc}")
    finally:
        await engine.dispose()

    assert owner == [("event", "id")], owner


async def test_the_journal_counter_belongs_to_its_column_when_built(
    session: AsyncSession,
) -> None:
    """The schema built from the models says the same -- so a test database
    and a deployed one behave alike when the journal is emptied."""
    owner = (await session.execute(OWNER_OF_JOURNAL_SEQUENCE)).all()
    assert owner == [("event", "id")], owner


async def test_an_emptied_journal_counts_from_one(session: AsyncSession) -> None:
    """What the ownership is for: `TRUNCATE ... RESTART IDENTITY` is heard by
    an owned sequence and ignored by a free-standing one. Without it the ids
    kept climbing in a test database while starting from one in a fresh
    deployment -- the same code, two behaviours.

    Emptied here rather than trusting `reset()`: that one truncates only the
    tables holding rows, and a test that rolled its events back leaves the
    journal empty with the counter already moved (a sequence knows no
    rollback). The promise being checked is the sequence's, not the order the
    suite happens to run in.
    """
    from src.models.event import Event

    session.add(Event(kind="test.counted", payload={}))
    await session.flush()
    await session.execute(text('TRUNCATE "event" RESTART IDENTITY CASCADE'))

    session.add(Event(kind="test.counted", payload={}))
    await session.flush()
    again = (await session.execute(select(func.min(Event.id)))).scalar()
    assert again == 1, again


def test_migrations_have_exactly_one_head() -> None:
    """One head, always -- checked by reading the files, not by upgrading.

    Two revisions naming the same `down_revision` are two heads, and
    `alembic upgrade head` then refuses to choose: the deploy stops and every
    database test dies on a world that cannot be built. It reached main on
    2026-09-02 because each branch is faultless alone -- the pair is the
    defect, and nothing looked at pairs.

    No database and no alembic here on purpose: the fault is in the files, so
    the files are what is read. That keeps the test in every run, including the
    ones that skip when Postgres is down -- and this is exactly the check one
    wants to survive a broken environment.

    Two heads *between branches* this cannot see: a sibling branch is not in
    this tree. That is `tools/check_migration_parents.py`, run by the
    pre-commit hook, where the branches actually are.
    """
    #: The same parser the pre-commit check uses (`tools/check_migration_parents`):
    #: two copies of this regex would rot apart the day alembic changes its
    #: template, and both would rot silently.
    from tools.check_migration_parents import parse as parse_migration

    versions = Path(__file__).resolve().parents[1] / "migrations" / "versions"
    revisions: dict[str, str] = {}
    parents: dict[str, tuple[str, ...]] = {}
    for path in sorted(versions.glob("*.py")):
        parsed = parse_migration(path.read_text(encoding="utf-8"))
        assert parsed is not None, f"{path.name}: не найден `revision`"
        revision, up = parsed
        #: Two files under one id is a fault alembic itself trips over, and
        #: a dict would swallow it: the second silently replaces the first.
        assert revision not in revisions, (
            f"ревизия {revision} объявлена дважды: {revisions[revision]} и {path.name}"
        )
        revisions[revision] = path.name
        parents[revision] = up

    #: A parent nobody wrote is a broken chain that still counts one head, so
    #: the head test alone would pass over it.
    dangling = {
        revisions[revision]: parent
        for revision, up in parents.items()
        for parent in up
        if parent not in revisions
    }
    assert not dangling, f"родитель не существует: {dangling}"

    #: A parent named twice is the collision itself -- reported before the head
    #: count, because it says *which* two files disagree rather than that the
    #: chain has two ends. A merge migration legitimately joins two parents,
    #: and it is a head-count question, not a child-count one -- so a parent
    #: whose children are rejoined below is not reported here.
    claimed: dict[str, list[str]] = {}
    for revision, up in parents.items():
        for parent in up:
            claimed.setdefault(parent, []).append(revisions[revision])
    named = {parent: sorted(names) for parent, names in claimed.items() if len(names) > 1}

    heads = sorted(set(revisions) - {parent for up in parents.values() for parent in up})
    assert len(heads) == 1, (
        f"голов должно быть одна, а их {len(heads)}: "
        + ", ".join(f"{head} ({revisions[head]})" for head in heads)
        + (f"; у одного родителя несколько детей: {named}" if named else "")
    )
