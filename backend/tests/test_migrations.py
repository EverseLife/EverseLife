# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Migrations must match the models.

The world is eternal, no wipes (D-007) -- so a schema divergence from code
is not fixed by recreating the database. The check catches it the day it appears.

The test looks at a database upgraded by migrations (`alembic upgrade head`)
and requires that autogeneration finds not a single difference -- types and
column defaults among them, both of which autogeneration passes over unless
asked (`compare_type`, `compare_server_default`).

**It must first be that database, and the check that it is was missing.** The
comparison is only about the code when the database really is the chain's
product; a database standing short of the head differs from the models by
exactly the migrations not yet applied to it -- which says nothing about the
code and everything about the database. Told that way round, the failure named
some thirty columns and advised `alembic revision --autogenerate`, and taking
the advice would have written a second migration for work already migrated,
breaking the clean-database path that is the whole point. It cost more than
confusion: the run was deselected for days across several sessions
(`--deselect tests/test_migrations.py::...`), so the check was carrying the
drift instead of catching it. Here the standing revision is read before
anything is compared, and a database behind the head skips with a message
saying so -- the local copy is the developer's to upgrade, and CI builds a
fresh one at head on every push, which is where the comparison bites.

The gate is worn by the comparison **alone** (`migrated_at_head`). The rest of
what is asked of a migrated database -- the triggers, the sequence ownership --
has been true since the revision that introduced it, so those questions go
through `migrated` and keep being answered on a copy that is behind.

The last pair goes further and looks at both schemas at once -- the migrated
one and the one built from the models -- because what a model cannot express
(sequence ownership, triggers, partitions) is exactly what diverges silently.
"""

from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import func, inspect, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine

from src.models import Base
from tools.check_migration_parents import parse as parse_migration

MIGRATED_URL = os.environ.get(
    "EVERSELIFE_MIGRATED_DATABASE_URL",
    "postgresql+asyncpg://everselife:everselife@localhost:5432/everselife",
)

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"


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

    #: Column defaults are compared only when asked, and the silence cost nine
    #: columns: the migration wrote a `server_default` and the model declared
    #: only a Python-side `default=`, so a `create_all` database left the
    #: column bare where the deployed one filled it in. Neither this test nor
    #: `--autogenerate` said a word. Five were an `add_column` that had rows to
    #: backfill on a `NOT NULL` add, but four stood inside a `create_table`
    #: with nothing to backfill -- the divergence is not a habit of one
    #: operation, and it is worth looking for wherever a default is written in
    #: a migration and not in the model.
    #:
    #: Plain `True` and no exceptions, though `event.id` looks like it needs
    #: one: it holds `nextval('event_id_seq')` in the migrated schema and
    #: nothing in the built one, where the `Sequence` is drawn ORM-side before
    #: the insert. Alembic's Postgres dialect drops that default on reflection
    #: when the sequence is *owned* by the column it feeds ("assuming SERIAL
    #: and omitting"), so the comparison never reaches it. Only the migrated
    #: database is reflected here, so it is that one's ownership this rests on:
    #: take it away and the check starts reporting `event.id`. (The built
    #: schema's ownership matters too, but for `TRUNCATE ... RESTART
    #: IDENTITY` -- that is the pair at the end of this file.)
    #:
    #: What the comparison forgives is spelling, and not by normalizing text:
    #: where the two differ it asks the server whether they are equal
    #: (`SELECT <database default> = <model default>`), which is why
    #: `server_default=text("0")` and `server_default="0"` pass as one. The
    #: first reaches Postgres bare, the second quoted and then recorded by
    #: type -- `0` for an integer, `'0'::bigint`, `'0'::numeric`. Alembic warns
    #: in its own source that asking the server is a poor test for a default
    #: that is a SQL function, which is worth knowing for a tenth column.
    #:
    #: The models here spell each default the way its own migration did, so a
    #: raw diff of `information_schema.columns` between the two databases --
    #: the reading that found all nine -- comes out empty as well. That half is
    #: discipline and not a check: either spelling passes this test.
    context = MigrationContext.configure(
        connection,
        opts={
            "compare_type": True,
            "compare_server_default": True,
            "include_name": include,
        },
    )
    return compare_metadata(context, Base.metadata)


def _chain() -> tuple[dict[str, str], dict[str, tuple[str, ...]]]:
    """The migrations lying here: revision -> file, and revision -> parents.

    The same parser the pre-commit check uses (`tools/check_migration_parents`):
    two copies of this regex would rot apart the day alembic changes its
    template, and both would rot silently.
    """
    revisions: dict[str, str] = {}
    parents: dict[str, tuple[str, ...]] = {}
    for path in sorted(VERSIONS.glob("*.py")):
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
    return revisions, parents


def _heads(revisions: dict[str, str], parents: dict[str, tuple[str, ...]]) -> list[str]:
    """The revisions nothing follows. One, when the chain is sound."""
    claimed = {parent for up in parents.values() for parent in up}
    return sorted(set(revisions) - claimed)


def _ancestry(revision: str, parents: dict[str, tuple[str, ...]]) -> set[str]:
    """A revision and everything it stands on.

    A walk, not a slice of the sorted list: `alembic merge` gives a revision
    two parents, and a first-parent walk would count the other branch as never
    applied.
    """
    seen: set[str] = set()
    front = [revision]
    while front:
        at = front.pop()
        if at in seen or at not in parents:
            continue
        seen.add(at)
        front.extend(parents[at])
    return seen


#: How to get a database this file can question, and the second half is the
#: half that matters. `alembic upgrade head` on its own points at the default
#: -- the shared dev database every worktree sees and the dev server runs on --
#: and the house rule is the opposite: "Миграция проверяется на чистой базе, а
#: не на dev-овской". Told only the short version, a reader whose branch holds
#: an unmerged migration writes that branch's schema into the database
#: everybody shares, and nobody downgrades it back. The variable is named here
#: because nothing else in the repository names it except CI.
OWN_DATABASE = (
    "нужна своя чистая база (CLAUDE.md: «Миграция проверяется на чистой базе»): "
    "createdb, потом `EVERSELIFE_DATABASE_URL=...<своя> alembic upgrade head`, "
    "и прогон с `EVERSELIFE_MIGRATED_DATABASE_URL=...<своя>` -- обе переменные в "
    "той же команде, окружение между вызовами не сохраняется"
)


async def _not_migrated(connection: AsyncConnection) -> str | None:
    """No `alembic_version` at all: nothing here was built by the chain."""
    tables = await connection.run_sync(lambda c: inspect(c).get_table_names())
    if "alembic_version" not in tables:
        return f"база {MIGRATED_URL} не накатана миграциями. {OWN_DATABASE}"
    return None


async def _not_at_head(connection: AsyncConnection) -> str | None:
    """Why this database cannot answer *for the models*, or `None` when it can."""
    rows = await connection.execute(text("SELECT version_num FROM alembic_version"))
    return _wrong_revision({row[0] for row in rows})


def _wrong_revision(standing: set[str]) -> str | None:
    """The judgement itself, apart from the database it was read from.

    Split out so every branch below can be checked without arranging a
    Postgres in that state: `alembic downgrade base` and a two-headed database
    are exactly the states nobody sets up on purpose, and the first two
    versions of these messages were wrong about both.

    Anything returned here is a fact about the database in front of us, never
    about the models: the point of the gate is that the two are not confused.
    """
    revisions, parents = _chain()
    heads = _heads(revisions, parents)
    if len(heads) != 1:
        #: Nothing could have upgraded to a head that is not one. Which two
        #: files disagree is `test_migrations_have_exactly_one_head`'s to say.
        return f"у миграций в этом дереве {len(heads)} головы, накатывать было нечего"
    head = heads[0]
    if standing == {head}:
        return None

    #: `alembic downgrade base` empties the table rather than dropping it, so
    #: the schema is gone while the marker of a migrated database remains.
    if not standing:
        return f"база {MIGRATED_URL} опущена до нуля: `alembic_version` пуста. {OWN_DATABASE}"

    #: A revision this tree never wrote: a neighbour's branch, or one that was
    #: merged away. Nothing here can say what such a database is missing, and
    #: it is certainly not evidence about these models.
    strangers = sorted(standing - set(revisions))
    if strangers:
        return (
            f"база {MIGRATED_URL} стоит на ревизии, которой в этом дереве нет "
            f"({', '.join(strangers)}): это чужая ветка, а не расхождение схемы с моделями"
        )

    #: At the head *and* somewhere else: a database with two heads of its own.
    #: Nothing is missing from it, so "отставшая" would be a lie -- but the
    #: extra branch is in its schema and in nobody's models.
    if head in standing:
        return (
            f"база {MIGRATED_URL} стоит не только на голове {head}, но и на "
            f"{', '.join(sorted(standing - {head}))}: у неё несколько голов, "
            "и сравнивать такую схему с моделями нечего"
        )

    applied = {seen for at in standing for seen in _ancestry(at, parents)}
    behind = _ancestry(head, parents) - applied
    return (
        f"база {MIGRATED_URL} стоит на {', '.join(sorted(standing))}, "
        f"а голова цепочки -- {head} ({revisions[head]}). "
        f"Не накатано миграций: {len(behind)}"
        + (f" ({', '.join(revisions[at] for at in sorted(behind))})" if behind else "")
        + f". Это отставшая база, а не расхождение схемы с моделями: {OWN_DATABASE}"
    )


@pytest_asyncio.fixture(loop_scope="session")
async def migrated() -> AsyncIterator[AsyncConnection]:
    """A connection to a database built by migrations -- at whatever revision.

    Deliberately **without** the head gate: what is asked through this fixture
    are facts a migrated schema has held since the revision that introduced
    them (the triggers of `db.ddl.RULES`, the journal's sequence ownership).
    Gating those on the head would silence them on every developer's copy, and
    they are the pair that once let a divergence into main.

    The guard is deliberately **not** wrapped around the `yield`: an
    `AssertionError` from the test body travels back through it, and an
    `except Exception` there would turn a real divergence into a skip -- the
    one outcome this file exists to prevent.
    """
    engine = create_async_engine(MIGRATED_URL)
    try:
        connection = await engine.connect()
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f"нет базы {MIGRATED_URL}: {exc}")

    try:
        reason = await _not_migrated(connection)
        if reason is not None:
            pytest.skip(reason)
        yield connection
    finally:
        await connection.close()
        await engine.dispose()


@pytest_asyncio.fixture(loop_scope="session")
async def migrated_at_head(migrated: AsyncConnection) -> AsyncConnection:
    """The same connection, once the database is known to be the chain's end.

    Only the comparison with the models needs this much: a database short of
    the head differs from them by exactly its pending migrations, and that is
    a fact about the database.
    """
    reason = await _not_at_head(migrated)
    if reason is not None:
        pytest.skip(reason)
    return migrated


async def test_schema_from_migrations_matches_models(migrated_at_head: AsyncConnection) -> None:
    diff = await migrated_at_head.run_sync(_differences)

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


async def test_database_rules_in_place(migrated: AsyncConnection) -> None:
    """The balance and immutability triggers must be in the upgraded database."""
    rows = await migrated.execute(text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal"))
    names = {row[0] for row in rows}

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


async def test_the_journal_counter_belongs_to_its_column_when_migrated(
    migrated: AsyncConnection,
) -> None:
    """The migrated schema: the sequence is owned by `event.id`."""
    owner = (await migrated.execute(OWNER_OF_JOURNAL_SEQUENCE)).all()
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


def test_a_database_is_told_what_is_wrong_with_it_and_not_something_else() -> None:
    """Each state of `alembic_version` gets the sentence that is true of it.

    Three of these are states nobody arranges on purpose -- an emptied version
    table, a revision from a neighbour's branch, a database standing on two
    heads -- and the first cut of the gate called all three "отставшая база",
    which for a database already holding the head is simply false. A wrong
    sentence here is worse than no sentence: this file exists because a
    misdiagnosis sent several sessions to `--deselect`.
    """
    revisions, parents = _chain()
    heads = _heads(revisions, parents)
    assert len(heads) == 1, heads
    head = heads[0]

    assert _wrong_revision({head}) is None, "на голове -- спрашивать не о чем"

    #: `alembic downgrade base` empties the table without dropping it.
    emptied = _wrong_revision(set())
    assert emptied is not None and "пуста" in emptied, emptied

    #: A revision this tree never wrote is not a count of missing migrations.
    stranger = _wrong_revision({"c0ffee000000"})
    assert stranger is not None and "чужая ветка" in stranger, stranger
    assert "Не накатано" not in stranger, stranger

    #: The head *and* another revision of this tree: nothing is missing from
    #: such a database, so "отставшая" would be a lie about it.
    parent = parents[head][0]
    two_headed = _wrong_revision({head, parent})
    assert two_headed is not None and "несколько голов" in two_headed, two_headed
    assert "отставшая" not in two_headed, two_headed

    #: And a database genuinely behind is counted, not merely named.
    behind = _wrong_revision({parent})
    assert behind is not None and "Не накатано миграций: 1" in behind, behind
    assert revisions[head] in behind, behind
    #: With the way out, and the way out is a database of one's own.
    assert "EVERSELIFE_MIGRATED_DATABASE_URL" in behind, behind


def test_a_merge_is_counted_as_applied_through_both_its_parents() -> None:
    """What the gate must not get wrong: a merge migration has two parents.

    `_ancestry` decides whether a database is behind, and a database behind is
    told to upgrade rather than accused of drift. Walking first parents only --
    the obvious way to write it, and the way a sorted list of revisions
    invites -- would count the second branch of every `alembic merge` as never
    applied, and a database at the head would be sent away to upgrade to the
    head it already stands on. Then the drift check never runs at all, which
    is the failure this whole file is about, arrived at from the other side.

    No database and no files: the shapes are written out here, so the walk is
    checked on a merge whether or not the tree happens to hold one today.
    """
    #: root -> a -> \        root -> b -> /  merged, and `top` above it.
    parents = {
        "root": (),
        "a": ("root",),
        "b": ("root",),
        "merged": ("a", "b"),
        "top": ("merged",),
    }

    assert _ancestry("top", parents) == {"top", "merged", "a", "b", "root"}
    assert _ancestry("a", parents) == {"a", "root"}
    #: `top` is the one head -- not the merge, which `top` follows.
    assert _heads({name: f"{name}.py" for name in parents}, parents) == ["top"]
    #: A database standing on the merge is behind by `top` alone -- not by the
    #: branch a first-parent walk would have missed.
    assert _ancestry("top", parents) - _ancestry("merged", parents) == {"top"}


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
    revisions, parents = _chain()

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

    heads = _heads(revisions, parents)
    assert len(heads) == 1, (
        f"голов должно быть одна, а их {len(heads)}: "
        + ", ".join(f"{head} ({revisions[head]})" for head in heads)
        + (f"; у одного родителя несколько детей: {named}" if named else "")
    )
