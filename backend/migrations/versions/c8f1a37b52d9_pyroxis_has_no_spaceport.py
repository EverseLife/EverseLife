"""Pyroxis has no spaceport, and cannot have one

Nothing is built on Pyroxis (D-230), so there is nothing to put a yard into --
and D-233 draws the conclusion the old seed did not: the planet takes a landing
**anywhere** on its surface, by the same single edge connector-to-node, and the
spaceport the seed used to lay on the Anvil Plateau goes away.

The node stays: it is a place, people may be standing in it, and it is a landing
site like any other square metre of the planet. What goes is the yard lying in
it -- and only if it is the seed's own: a yard somebody flew in and put down
themselves is their property, and the world does not take property away
(D-007). It cannot be put down on Pyroxis anyway (`estate.construct` refuses
the planet), so in practice there is exactly one yard to remove.

A ship docked at that node keeps its edge and its berth: the beacon rule never
applied here, and the landing does not depend on the yard any more.

Revision ID: c8f1a37b52d9
Revises: b3e9d1c47f60
Create Date: 2026-08-25 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = 'c8f1a37b52d9'
down_revision: str | None = 'b3e9d1c47f60'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The node the old seed put the yard in, and the yard it put there.
PORT = "pyroxis.anvil.port"
PLATEAU = "pyroxis.anvil"
YARD = "Космическая верфь"
#: What it was called, and what it is: a place to set down, not a port.
WAS = "Космодром на плато"
PAD = "Площадка на плато"
#: A walk across the plateau, the same the seed gives its black fields.
STEP = 900


def upgrade() -> None:
    #: A yard is a thing, and a thing may have a container of its own -- the
    #: same orphan the engine clears wherever matter leaves the world. Deleting
    #: the yard alone would leave whatever was inside it alive in a container
    #: with no owner, invisible and unreachable for ever. So the inside goes
    #: first, then the box, then the yard.
    op.execute(
        f"""
        DELETE FROM item
        WHERE container_id IN (
            SELECT inner_box.id FROM container inner_box
            JOIN item yard ON yard.id = inner_box.owner_id
            JOIN container node_box ON node_box.id = yard.container_id
            JOIN node n ON n.id = node_box.owner_id
            WHERE inner_box.kind = 'storage'
              AND yard.type_key = '{YARD}'
              AND node_box.kind = 'node' AND n.key = '{PORT}'
        )
        """
    )
    op.execute(
        f"""
        DELETE FROM container
        WHERE kind = 'storage' AND owner_id IN (
            SELECT yard.id FROM item yard
            JOIN container node_box ON node_box.id = yard.container_id
            JOIN node n ON n.id = node_box.owner_id
            WHERE yard.type_key = '{YARD}'
              AND node_box.kind = 'node' AND n.key = '{PORT}'
        )
        """
    )
    op.execute(
        f"""
        DELETE FROM item
        WHERE type_key = '{YARD}'
          AND container_id IN (
              SELECT c.id FROM container c
              JOIN node n ON n.id = c.owner_id
              WHERE c.kind = 'node' AND n.key = '{PORT}'
          )
        """
    )
    #: And it stops calling itself a spaceport, because it is not one any more:
    #: a landing site like every other square metre of the planet.
    op.execute(
        f"UPDATE node SET name = '{PAD}' WHERE key = '{PORT}' AND name = '{WAS}'"
    )
    #: A way off it. The old seed laid this node under the plateau and gave it
    #: no edge at all -- a ship could dock there and a crew could walk nowhere.
    #: On a planet whose whole point is walking out before the ground moves,
    #: that would be a trap rather than a place.
    op.execute(
        f"""
        INSERT INTO edge (id, node_a_id, node_b_id, base_seconds, surface, condition, created_at)
        SELECT gen_random_uuid(), pad.id, anvil.id, {STEP}, 'trail', 100, now()
        FROM node pad, node anvil
        WHERE pad.key = '{PORT}' AND anvil.key = '{PLATEAU}'
          AND NOT EXISTS (
              SELECT 1 FROM edge e
              WHERE (e.node_a_id = pad.id AND e.node_b_id = anvil.id)
                 OR (e.node_a_id = anvil.id AND e.node_b_id = pad.id)
          )
        """
    )


def downgrade() -> None:
    """Nothing. A world that goes back to before D-233 gets its yard from the
    seed's own catch-up, which laid it there in the first place."""
