"""two access lists for a location: white and black (D-204)

The roster used to be one, and the gate turned its meaning over: named people
were kept out of an open yard and let into a shut one. Two lists say the same
thing without the turn, and the old rows keep their meaning exactly -- a roster
of a shut node was a white list, a roster of an open one was a black list.

Revision ID: a2d5f38b71c4
Revises: c1e8d2f45a37
Create Date: 2026-08-17 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'a2d5f38b71c4'
down_revision: str | None = 'c1e8d2f45a37'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "node_pass",
        sa.Column(
            "allowed", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
    )
    #: What the old row meant is decided by the node it belongs to: the roster of
    #: a shut node let people in, the roster of an open one kept them out.
    op.execute(
        """
        UPDATE node_pass SET allowed = false
         WHERE node_id IN (SELECT id FROM node WHERE gated = false)
        """
    )


def downgrade() -> None:
    #: Going back the lists merge into one again, and only the one that matches
    #: the node's state survives: the other has nowhere to be kept.
    op.execute(
        """
        DELETE FROM node_pass
         WHERE node_id IN (SELECT id FROM node WHERE gated = true) AND allowed = false
        """
    )
    op.execute(
        """
        DELETE FROM node_pass
         WHERE node_id IN (SELECT id FROM node WHERE gated = false) AND allowed = true
        """
    )
    op.drop_column("node_pass", "allowed")
