"""Правила, которые обязана держать сама база.

Три инварианта из 05-domain-model слишком важны, чтобы держаться на дисциплине
вызывающего кода. Их нарушение — не баланс, а баг, и обнаружиться оно должно
в момент нарушения, а не через месяц в отчёте:

* **проводки операции сходятся в ноль** — деньги переходят, а не появляются (И2);
* **журнал событий неизменяем** — иначе доказательная база суда ничего не стоит;
* **журнал проводок неизменяем** — по той же причине.

Проверка суммы отложена до фиксации транзакции (`DEFERRABLE INITIALLY
DEFERRED`): проводки добавляются по одной, и в середине операции журнал
законно не сходится.

Определения подключаются к `metadata`, поэтому попадают и в тестовую базу, и
в миграции — второй копии SQL не существует. Каждое выражение отдельной
строкой: asyncpg не принимает несколько команд в одном запросе.
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
    #: Функция объявляется рядом с каждым триггером: порядок создания таблиц
    #: задаётся зависимостями, и полагаться на него нельзя. `OR REPLACE`
    #: делает повтор безобидным.
    return (
        APPEND_ONLY_FUNCTION,
        f"""
CREATE TRIGGER {table}_append_only
BEFORE UPDATE OR DELETE ON {table}
FOR EACH ROW EXECUTE FUNCTION forbid_rewrite()
""",
    )


RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    #: Проводку нельзя ни переписать, ни удалить: исправление ошибки — это
    #: обратная проводка, как в настоящей бухгалтерии. Иначе журнал перестаёт
    #: быть доказательством.
    ("ledger_entry", (BALANCE_FUNCTION, BALANCE_TRIGGER, *_append_only("ledger_entry"))),
    ("event", _append_only("event")),
    ("ledger_transaction", _append_only("ledger_transaction")),
)


def attach(metadata: MetaData) -> None:
    """Подключить правила к созданию таблиц."""
    for table_name, statements in RULES:
        table = metadata.tables.get(table_name)
        if table is None:  # pragma: no cover
            raise RuntimeError(f"нет таблицы {table_name}: правило некуда вешать")
        for sql in statements:
            event.listen(table, "after_create", DDL(sql).execute_if(dialect="postgresql"))


def statements() -> tuple[str, ...]:
    """Тот же SQL для миграции — второй копии не существует."""
    return tuple(sql for _, group in RULES for sql in group)
