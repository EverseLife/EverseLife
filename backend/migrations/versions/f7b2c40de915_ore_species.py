"""ore species: «Руда» becomes «Железная руда»

Породы руды (D-151). Мир вечный, вайпов не бывает (D-007) — значит
переименование сырья обязано доехать до уже существующих миров, а не остаться
правилом для новых. Иначе руда в сундуках перестала бы плавиться вовсе: у
плавки теперь свой вход у каждого металла.

Переименовывается **состояние**, а не журналы: события и проводки неизменяемы
по построению, и в них «Руда» остаётся навсегда — такой она и была в тот день.

Revision ID: f7b2c40de915
Revises: d4c1a90f2b73
Create Date: 2026-08-13
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "f7b2c40de915"
down_revision: str | None = "d4c1a90f2b73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = "Руда"
NEW = "Железная руда"

#: Где лежит имя товара в состоянии мира.
TABLES = (
    ("item", "type_key"),
    ("market_order", "type_key"),
    ("market_reservation", "type_key"),
    ("market_trade", "type_key"),
    ("craft_batch", "output"),
    ("vein", "resource"),
)


def upgrade() -> None:
    for table, column in TABLES:
        op.execute(
            f"UPDATE {table} SET {column} = '{NEW}' WHERE {column} = '{OLD}'"  # noqa: S608
        )
    #: Угольная балка стала шахтой: место то же, название честнее — там шахта,
    #: а не овраг, и к ней ведёт дорога.
    op.execute(
        "UPDATE node SET name = 'Угольная шахта' "
        "WHERE key = 'terra.coal' AND name = 'Угольная балка'"
    )


def downgrade() -> None:
    for table, column in TABLES:
        op.execute(
            f"UPDATE {table} SET {column} = '{OLD}' WHERE {column} = '{NEW}'"  # noqa: S608
        )
    op.execute(
        "UPDATE node SET name = 'Угольная балка' "
        "WHERE key = 'terra.coal' AND name = 'Угольная шахта'"
    )
