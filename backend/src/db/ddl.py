# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Rules the database itself must hold.

Three invariants from 05-domain-model are too important to rest on the
discipline of calling code. Their violation is not balance but a bug, and it
must be discovered at the moment of violation, not a month later in a report:

* **an operation's postings sum to zero** -- money moves, it does not appear (I2);
* **the event journal is immutable** -- otherwise the court's evidence is worthless;
* **the posting journal is immutable** -- for the same reason.

The sum check is deferred until transaction commit (`DEFERRABLE INITIALLY
DEFERRED`): postings are added one at a time, and mid-operation the journal
legitimately does not balance.

The definitions attach to `metadata`, so they land both in the test database
and in migrations -- no second copy of the SQL exists. Each statement on a
separate line: asyncpg does not accept several commands in one query.
"""

from __future__ import annotations

from sqlalchemy import DDL, MetaData, event

BALANCE_FUNCTION = """
CREATE OR REPLACE FUNCTION ledger_entries_balance() RETURNS trigger AS $$
DECLARE
    affected uuid;
    total bigint;
BEGIN
    affected := COALESCE(NEW.transaction_id, OLD.transaction_id);
    SELECT COALESCE(SUM(amount), 0) INTO total
      FROM ledger_entry WHERE transaction_id = affected;
    IF total <> 0 THEN
        -- Сообщение собирается конкатенацией, а не подстановкой: знак процента
        -- в SQL, идущем через SQLAlchemy, означает параметр.
        RAISE EXCEPTION USING MESSAGE =
            'проводки операции ' || affected || ' не сходятся: сумма ' || total ||
            '. Деньги переходят, а не появляются (И2)';
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql
"""

BALANCE_TRIGGER = """
CREATE CONSTRAINT TRIGGER ledger_entry_balanced
AFTER INSERT OR UPDATE OR DELETE ON ledger_entry
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION ledger_entries_balance()
"""

APPEND_ONLY_FUNCTION = """
CREATE OR REPLACE FUNCTION forbid_rewrite() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION USING MESSAGE =
        'таблица ' || TG_TABLE_NAME || ' только для добавления: '
        || 'запись изменять и удалять нельзя';
END;
$$ LANGUAGE plpgsql
"""


def _append_only(table: str) -> tuple[str, ...]:
    #: The function is declared next to each trigger: table creation order is
    #: set by dependencies and cannot be relied on. `OR REPLACE` makes a repeat harmless.
    return (
        APPEND_ONLY_FUNCTION,
        f"""
CREATE TRIGGER {table}_append_only
BEFORE UPDATE OR DELETE ON {table}
FOR EACH ROW EXECUTE FUNCTION forbid_rewrite()
""",
    )


RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    #: A posting can be neither rewritten nor deleted: correcting an error is a
    #: reversing posting, as in real bookkeeping. Otherwise the journal stops
    #: being evidence.
    ("ledger_entry", (BALANCE_FUNCTION, BALANCE_TRIGGER, *_append_only("ledger_entry"))),
    ("event", _append_only("event")),
    ("ledger_transaction", _append_only("ledger_transaction")),
)


def attach(metadata: MetaData) -> None:
    """Attach the rules to table creation."""
    for table_name, statements in RULES:
        table = metadata.tables.get(table_name)
        if table is None:  # pragma: no cover
            raise RuntimeError(f"нет таблицы {table_name}: правило некуда вешать")
        for sql in statements:
            event.listen(table, "after_create", DDL(sql).execute_if(dialect="postgresql"))


def statements() -> tuple[str, ...]:
    """The same SQL for a migration -- no second copy exists."""
    return tuple(sql for _, group in RULES for sql in group)
