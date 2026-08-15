"""Аккаунт: почта, пароль, жетоны сессии; личность: линия и самоописание (D-187).


Revision ID: a7c1d5e2b9f0
Revises: 384bc599a596
Create Date: 2026-08-15 18:20:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a7c1d5e2b9f0'
down_revision: str | None = '384bc599a596'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('account', sa.Column('email', sa.String(), nullable=True))
    op.add_column('account', sa.Column('password_hash', sa.String(), nullable=True))
    op.create_unique_constraint(op.f('uq_account_email'), 'account', ['email'])

    op.create_table('login_token',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('account_id', sa.Uuid(), nullable=False),
    sa.Column('token_hash', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['account_id'], ['account.id'], name=op.f('fk_login_token_account_id_account')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_login_token')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_login_token_token_hash'))
    )
    op.create_index('ix_login_token_account', 'login_token', ['account_id'], unique=False)

    op.add_column('identity', sa.Column('surname', sa.String(), server_default='', nullable=False))
    op.add_column('identity', sa.Column('age', sa.Integer(), nullable=True))
    op.add_column('identity', sa.Column('about', sa.String(), server_default='', nullable=False))
    op.add_column('identity', sa.Column(
        'line',
        sa.Enum('human', 'nymph', name='identity_line', native_enum=False, length=32),
        server_default='human',
        nullable=False,
    ))


def downgrade() -> None:
    op.drop_column('identity', 'line')
    op.drop_column('identity', 'about')
    op.drop_column('identity', 'age')
    op.drop_column('identity', 'surname')
    op.drop_index('ix_login_token_account', table_name='login_token')
    op.drop_table('login_token')
    op.drop_constraint(op.f('uq_account_email'), 'account', type_='unique')
    op.drop_column('account', 'password_hash')
    op.drop_column('account', 'email')
