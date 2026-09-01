# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Where warmth comes from (D-231): the plant that reaches a neighbour, the
heater that does not, the brazier with its own fuel -- and what a frozen
node refuses to work.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants, current_catalog
from src.constants import registry as R
from src.db.base import remember
from src.engine import world
from src.engine.frost._base import BRAZIER, HEAT, HEATER, PLANT, Frozen, climate_of
from src.models.inventory import Container, ContainerKind, Item
from src.models.world import Edge, Node


async def is_warm(session: AsyncSession, constants: Constants, node: Node) -> bool:
    """Whether a body and a machine are warm in this node."""
    weather = await climate_of(session, node)
    if weather is None:
        return True
    #: Life support heats the board: a ship is warm wherever it stands (D-231).
    from src.engine import ship  # noqa: PLC0415 -- lazy: breaks the cycle with ship

    if ship.is_aboard(node):
        return True
    #: There are no shelters on the scorching planet and there will be none
    #: (D-230): nothing is built on Pyroxis, so nothing can heat -- or cool -- a
    #: node there. What saves a body is the suit and the board.
    if weather == HEAT:
        return False
    return await heated(session, constants, node)


async def heated(session: AsyncSession, constants: Constants, node: Node) -> bool:
    """Whether something that heats works in this node.

    A working brazier is one with fuel lying in the node: it burns what is
    brought, and an empty brazier is cold iron. A plant and a heater work while
    the city pool has anything in it -- an empty pool is a dark, cold city.

    Asked by every `look` on a cold planet, so the node and all its neighbours
    are read in **one** query rather than one apiece, and the yards are read
    without being created: a read may not write (review 2026-08-23).
    """
    here = (await _standing(session, [node])).get(node.id, frozenset())
    if here & _class_names(BRAZIER) and here & frozenset(constants[R.ENERGY_FUEL_ENERGY]):
        return True
    if await _stove_works(
        session, constants, node, here, _class_names(PLANT) | _class_names(HEATER)
    ):
        return True
    #: The plant reaches one node further -- its own and every neighbour's.
    #: A neighbour is a neighbour by the graph, and heat travels along an edge
    #: like everything else in this world. Asked only now: a node warmed by its
    #: own stove is the common case, and it must cost one query, not three.
    neighbours = await _neighbours(session, node)
    if not neighbours:
        return False
    standing = await _standing(session, neighbours)
    for other in neighbours:
        if await _stove_works(
            session, constants, other, standing.get(other.id, frozenset()), _class_names(PLANT)
        ):
            return True
    return False


async def _stove_works(
    session: AsyncSession,
    constants: Constants,
    node: Node,
    standing: frozenset[str],
    wanted: frozenset[str],
) -> bool:
    """Whether a stove of the wanted kind burns in this node, and on whose energy.

    Two purses, and they are not interchangeable (D-232): what the Forerunners
    left runs on their reactor while it lasts **or** on the city pool once it is
    gone; what people built runs on the pool and on nothing else. Without the
    split a reactor would be heating everything anybody carried into its city,
    free of charge, for a year.
    """
    from src.engine import energy  # noqa: PLC0415 -- lazy: breaks the cycle with energy

    stoves = standing & wanted
    if not stoves:
        return False
    book = current_catalog().recipes
    if await _grid_alive(session, constants, node):
        return True
    if not any(book.is_relic(name) for name in stoves):
        return False
    return await energy.relic_power(session, constants, node) > 0


async def _standing(
    session: AsyncSession, nodes: Sequence[Node]
) -> dict[uuid.UUID, frozenset[str]]:
    """What stands in each of these nodes, by name, in one query.

    Read straight off the containers instead of through `world.thing_kinds`:
    that one creates the yard where a node has none, and warmth is asked for
    by `look` -- including about **neighbouring** nodes, where nobody stands
    and nothing should be brought into being by somebody glancing at the map.
    """
    if not nodes:  # pragma: no cover -- there is always at least the node itself
        return {}
    ids = tuple(sorted(node.id for node in nodes))

    async def read() -> dict[uuid.UUID, frozenset[str]]:
        rows = await session.execute(
            select(Container.owner_id, Item.type_key)
            .join(Item, Item.container_id == Container.id)
            .where(Container.kind == ContainerKind.NODE, Container.owner_id.in_(ids))
            .distinct()
        )
        found: dict[uuid.UUID, set[str]] = {}
        for owner_id, type_key in rows:
            found.setdefault(owner_id, set()).add(type_key)
        return {owner: frozenset(names) for owner, names in found.items()}

    return await remember(session, ("frost_standing", ids), read)


async def _grid_alive(session: AsyncSession, constants: Constants, node: Node) -> bool:
    """Whether the **city pool** has anything for this node. A read: no pool is created.

    Only the pool: the Forerunners' reactor is another purse and pays only for
    their own things (`_stove_works`). Remembered by the city rather than by the
    node: every node of a city shares one pool, and `heated` asks about a whole
    ring of neighbours at once.
    """

    async def read() -> bool:
        from src.engine import energy  # noqa: PLC0415 -- lazy: breaks the cycle with energy

        pool = await energy.pool_of(session, constants, node, create=False)
        return pool is not None and float(pool.stored) > 0

    return await remember(session, ("frost_grid", node.parent_id, node.layer), read)


async def _neighbours(session: AsyncSession, node: Node) -> list[Node]:
    """The nodes one edge away. Read straight off the edge table: warmth is
    asked for by every look, and the road engine has nothing to add here."""

    async def read() -> list[Node]:
        edges = (
            (
                await session.execute(
                    select(Edge).where(or_(Edge.node_a_id == node.id, Edge.node_b_id == node.id))
                )
            )
            .scalars()
            .all()
        )
        ids = {edge.node_b_id if edge.node_a_id == node.id else edge.node_a_id for edge in edges}
        if not ids:
            return []
        return list((await session.execute(select(Node).where(Node.id.in_(ids)))).scalars().all())

    return await remember(session, ("frost_neighbours", node.id), read)


def _class_names(thing_class: str) -> frozenset[str]:
    """Every thing of the class, by name (D-215)."""
    return frozenset(world.station_names(thing_class))


# --- machines in the cold -----------------------------------------------------


def burns_own_fuel(type_key: str) -> bool:
    """Whether this machine keeps its own fire going.

    Not a flag but the fuel behaviour itself (D-231): the fuel station burns
    what is hauled to it, the brazier burns what is put in it, and both are the
    reason a frozen city can be lit at all.

    Answers to a class as readily as to a thing: the craft engine asks by class
    («Верстак»), the node scene by name («Угольная станция»), and the rule is
    one and the same rule.
    """
    from src.engine import energy  # noqa: PLC0415 -- lazy: breaks the cycle with energy

    burning = (energy.FUEL_PLANT, BRAZIER)
    if type_key in burning:
        return True
    return any(type_key in _class_names(one) for one in burning)


async def works_here(
    session: AsyncSession, constants: Constants, node: Node, type_key: str
) -> bool:
    """Whether this machine works in this node. In the frost only what burns does."""
    if burns_own_fuel(type_key):
        return True
    return await is_warm(session, constants, node)


async def require_working(
    session: AsyncSession, constants: Constants, node: Node, type_key: str
) -> None:
    """Refuse work at a machine standing in a frozen node."""
    if await works_here(session, constants, node, type_key):
        return
    raise Frozen(
        key="frost-node-frozen",
        node=node.name,
        station=type_key,
        plant=PLANT,
        heater=HEATER,
        brazier=BRAZIER,
    )


def heat_draw(constants: Constants, standing: dict[str, float]) -> tuple[float, float]:
    """What the stoves of a node take an hour, as a pair: **theirs, ours**.

    `standing` is name -> how many stand there. Counted by `energy.produce` in
    the same pass that fills the pool: generation and heat are one balance, and
    two passes over the same city would sooner or later disagree.

    The pair matters because the two are paid from different purses (D-232):
    the Forerunners' plant runs on the Forerunners' reactor, and everything
    people built runs on the city pool. Were it one number, a reactor would be
    heating twenty heaters somebody carried in, and "a city on the permafrost
    pays for its own existence" would be off exactly where it must bite.
    """
    plants = _class_names(PLANT)
    heaters = _class_names(HEATER)
    book = current_catalog().recipes
    theirs = ours = 0.0
    for name, count in standing.items():
        if name in plants:
            draw = constants[R.FROST_PLANT_DRAW] * count
        elif name in heaters:
            draw = constants[R.FROST_HEATER_DRAW] * count
        else:
            continue
        if book.is_relic(name):
            theirs += draw
        else:
            ours += draw
    return theirs, ours
