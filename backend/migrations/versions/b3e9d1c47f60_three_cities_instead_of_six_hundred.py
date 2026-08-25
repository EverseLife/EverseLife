"""Aurora: three cities instead of six hundred and sixty-six piers

The old seed laid `AURORA_CITIES` identical abandoned cities with one spaceport
each -- a hedge against a race for a single berth (D-230). D-232 replaces them
with three cities that differ: Merid, Caldar, Veyr, each with a hall, a plant
and a reactor of the Forerunners. The seed lays those on the next start; this
migration takes away what they replace.

**Only what nobody has touched is removed.** A city where a body stands, a ship
is docked, a house was built, a plot was marked, a batch was started, a word was
said in the Net -- anything at all that points at one of its nodes -- is left
alone, monument and all. Better a stray ruin on the map than a foreign key torn
out from under somebody's work: the world is eternal and there are no wipes
(D-007).

What goes with a node: its yard and the relic spaceport lying in it, the edges
that end on it, its energy pool, its meter, its door lists and the chat buffer
of the place. All of those exist only for the node itself.

Revision ID: b3e9d1c47f60
Revises: a1c4f7e30b28
Create Date: 2026-08-25 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = 'b3e9d1c47f60'
down_revision: str | None = 'a1c4f7e30b28'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The keys the old seed used: `aurora.ruins.001`, `aurora.ruins.001.port`.
RUINS = "aurora.ruins.%"
#: What the old seed granted the abandoned ports. Anything else lying in a node
#: is somebody's, and its city stays.
RELIC_YARD = "Космическая верфь"

#: Everything that points at a node and means "a player has been here". A city
#: with any of these keeps every one of its nodes.
TOUCHED_BY = (
    ("body", "node_id"),
    ("building", "node_id"),
    ("city", "node_id"),
    ("craft_batch", "node_id"),
    ("deed", "node_id"),
    ("forage", "node_id"),
    ("library_entry", "node_id"),
    ("market_order", "node_id"),
    ("market_reservation", "node_id"),
    ("market_trade", "node_id"),
    ("net_post", "node_id"),
    ("nursery", "node_id"),
    ("plot", "node_id"),
    ("rig", "node_id"),
    ("sanction", "node_id"),
    ("ship", "node_id"),
    ("ship", "connector_node_id"),
    ("ship", "docked_node_id"),
    ("travel", "from_node_id"),
    ("travel", "to_node_id"),
    ("vein", "node_id"),
)


def _victims() -> str:
    """SQL for the nodes that go: every ruin of an untouched city.

    A city is judged by its whole subtree: a touched pier keeps its city, and a
    kept city keeps its pier -- half a city on the map would be worse than a
    whole one.
    """
    marks = "\n           UNION ALL\n".join(
        f"           SELECT r.id, r.parent_id FROM ruins r"
        f" WHERE EXISTS (SELECT 1 FROM {table} t WHERE t.{column} = r.id)"
        for table, column in TOUCHED_BY
    )
    return f"""
        WITH ruins AS (
            SELECT id, parent_id FROM node WHERE key LIKE '{RUINS}'
        ),
        touched AS (
{marks}
           UNION ALL
           SELECT r.id, r.parent_id FROM ruins r WHERE EXISTS (
               SELECT 1 FROM container c
               JOIN item i ON i.container_id = c.id
               WHERE c.kind = 'node' AND c.owner_id = r.id AND i.type_key <> '{RELIC_YARD}'
           )
           UNION ALL
           -- An edge is the surest mark of all: the old seed laid none here at
           -- all, so any edge means somebody walked, docked or explored. And a
           -- find made from a ruin hangs on the planet, not on the city, so it
           -- would not be seen by any of the checks above -- while deleting the
           -- port would leave whoever stands on that find with no way out.
           SELECT r.id, r.parent_id FROM ruins r WHERE EXISTS (
               SELECT 1 FROM edge e WHERE e.node_a_id = r.id OR e.node_b_id = r.id
           )
        ),
        kept AS (
            SELECT id AS city FROM ruins WHERE id IN (SELECT id FROM touched)
            UNION
            SELECT parent_id AS city FROM touched WHERE parent_id IS NOT NULL
        )
        SELECT id FROM ruins
        WHERE id NOT IN (SELECT city FROM kept WHERE city IS NOT NULL)
          AND (parent_id IS NULL OR parent_id NOT IN (SELECT city FROM kept WHERE city IS NOT NULL))
    """


def upgrade() -> None:
    victims = _victims()
    op.execute(f"CREATE TEMP TABLE ruins_to_drop ON COMMIT DROP AS {victims}")

    #: What lies in the yard, then the yard itself: an item points at a
    #: container, and a container at the node.
    op.execute(
        "DELETE FROM item WHERE container_id IN ("
        " SELECT c.id FROM container c WHERE c.kind = 'node'"
        " AND c.owner_id IN (SELECT id FROM ruins_to_drop))"
    )
    op.execute(
        "DELETE FROM container WHERE kind = 'node'"
        " AND owner_id IN (SELECT id FROM ruins_to_drop)"
    )
    #: A market cell is keyed by node without a foreign key; it would otherwise
    #: be left pointing nowhere.
    op.execute(
        "DELETE FROM item WHERE container_id IN ("
        " SELECT c.id FROM container c WHERE c.node_id IN (SELECT id FROM ruins_to_drop))"
    )
    op.execute("DELETE FROM container WHERE node_id IN (SELECT id FROM ruins_to_drop)")

    for table, column in (
        ("edge", "node_a_id"),
        ("edge", "node_b_id"),
        ("energy_pool", "node_id"),
        ("node_pass", "node_id"),
        ("utility_meter", "node_id"),
        ("chat_message", "node_id"),
        ("chat_group", "node_id"),
    ):
        op.execute(f"DELETE FROM {table} WHERE {column} IN (SELECT id FROM ruins_to_drop)")

    #: Children before parents: a pier hangs on its city.
    op.execute(
        "DELETE FROM node WHERE id IN (SELECT id FROM ruins_to_drop) AND parent_id IS NOT NULL"
    )
    op.execute("DELETE FROM node WHERE id IN (SELECT id FROM ruins_to_drop)")


def downgrade() -> None:
    """Nothing. The six hundred cities are not restored, and could not be: what
    made them was deleted with the code that laid them (D-232). The three that
    replace them are laid by the seed at the next start."""
