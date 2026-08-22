"""The Net: correspondence and channels (D-222)

Six tables, all empty at birth. A thread and its parties, the letters in it
with the moment each arrives; a channel, its readers, and the posts with the
node the author stood in -- the delay to each reader is measured from there.

Revision ID: 92bd32b349cb
Revises: c2f5a90b7e41
Create Date: 2026-08-22 17:44:11.773736
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "92bd32b349cb"
down_revision: str | None = "c2f5a90b7e41"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "net_thread",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pair_key", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_net_thread")),
        sa.UniqueConstraint("pair_key", name=op.f("uq_net_thread_pair_key")),
    )
    op.create_table(
        "net_channel",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("about", sa.String(), server_default="", nullable=False),
        sa.Column("owner_identity_id", sa.Uuid(), nullable=True),
        sa.Column("city_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["owner_identity_id"],
            ["identity.id"],
            name=op.f("fk_net_channel_owner_identity_id_identity"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_net_channel")),
        sa.UniqueConstraint("city_id", name=op.f("uq_net_channel_city_id")),
    )
    op.create_index("ix_net_channel_owner", "net_channel", ["owner_identity_id"], unique=False)
    op.create_table(
        "net_message",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("identity_id", sa.Uuid(), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["identity_id"], ["identity.id"], name=op.f("fk_net_message_identity_id_identity")
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["net_thread.id"],
            name=op.f("fk_net_message_thread_id_net_thread"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_net_message")),
    )
    op.create_index(
        "ix_net_message_thread_delivered",
        "net_message",
        ["thread_id", "delivered_at"],
        unique=False,
    )
    op.create_table(
        "net_party",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("identity_id", sa.Uuid(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["identity_id"], ["identity.id"], name=op.f("fk_net_party_identity_id_identity")
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["net_thread.id"],
            name=op.f("fk_net_party_thread_id_net_thread"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_net_party")),
        sa.UniqueConstraint("thread_id", "identity_id", name="uq_net_party"),
    )
    op.create_index("ix_net_party_identity", "net_party", ["identity_id"], unique=False)
    op.create_table(
        "net_post",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("channel_id", sa.Uuid(), nullable=False),
        sa.Column("identity_id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["net_channel.id"],
            name=op.f("fk_net_post_channel_id_net_channel"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["identity_id"], ["identity.id"], name=op.f("fk_net_post_identity_id_identity")
        ),
        sa.ForeignKeyConstraint(["node_id"], ["node.id"], name=op.f("fk_net_post_node_id_node")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_net_post")),
    )
    op.create_index("ix_net_post_channel_at", "net_post", ["channel_id", "at"], unique=False)
    op.create_table(
        "net_subscription",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("channel_id", sa.Uuid(), nullable=False),
        sa.Column("identity_id", sa.Uuid(), nullable=False),
        sa.Column("chosen", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["net_channel.id"],
            name=op.f("fk_net_subscription_channel_id_net_channel"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["identity_id"], ["identity.id"], name=op.f("fk_net_subscription_identity_id_identity")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_net_subscription")),
        sa.UniqueConstraint("channel_id", "identity_id", name="uq_net_subscription"),
    )
    op.create_index(
        "ix_net_subscription_identity", "net_subscription", ["identity_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_net_subscription_identity", table_name="net_subscription")
    op.drop_table("net_subscription")
    op.drop_index("ix_net_post_channel_at", table_name="net_post")
    op.drop_table("net_post")
    op.drop_index("ix_net_party_identity", table_name="net_party")
    op.drop_table("net_party")
    op.drop_index("ix_net_message_thread_delivered", table_name="net_message")
    op.drop_table("net_message")
    op.drop_index("ix_net_channel_owner", table_name="net_channel")
    op.drop_table("net_channel")
    op.drop_table("net_thread")
