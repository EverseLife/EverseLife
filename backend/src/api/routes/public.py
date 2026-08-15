"""Public reads: catalogs and world state.

Nothing here changes the world, and never will. Prices, statistics and the
code are public on purpose: everyone knows the prices (D-047), and closing
the catalogs is pointless -- they lie in the vault anyway.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from src.constants import HOLDER, current
from src.constants import registry as R
from src.db.base import session_factory
from src.engine import market
from src.models.world import Node
from src.runtime import MARKET_BOOK_DEPTH

router = APIRouter(prefix="/public", tags=["reads"])


@router.get("/constants")
async def constants() -> dict[str, Any]:
    """The current set of balance numbers and its fingerprint.

    The client computes the quality forecast and batch cost by the same
    numbers as the server -- otherwise the forecast before starting a batch
    (D-092) would diverge from the result.
    """
    snapshot = HOLDER.current()
    return {"digest": snapshot.digest, "values": snapshot.raw()}


@router.get("/recipes")
async def recipes() -> dict[str, Any]:
    from src.api.app import catalog

    book = catalog().recipes
    return {
        "raw": list(book.raw),
        "operations": [operation.model_dump(by_alias=True) for operation in book.operations],
        "recipes": [recipe.model_dump() for recipe in book.recipes],
        "tool_classes": {name: list(tools) for name, tools in book.tool_classes.items()},
        "synonyms": book.synonyms,
        "labor_hours": book.labor_hours,
    }


@router.get("/plants")
async def plants() -> dict[str, Any]:
    from src.api.app import catalog

    return {"plants": [plant.model_dump() for plant in catalog().plants.plants]}


@router.get("/map")
async def world_map() -> dict[str, Any]:
    """The world map: nodes and edges with transit time.

    Cities and highways are public -- otherwise a newcomer will not find where
    to go (D-097). For now the whole map is public: there is no exploration
    yet, and with it wild nodes and veins become visible only to those who explored them.
    """
    from src.constants import current
    from src.engine import travel as roads
    from src.models.world import Edge, Node

    constants = current()
    async with session_factory()() as db:
        nodes = (await db.execute(select(Node))).scalars().all()
        edges = (await db.execute(select(Edge))).scalars().all()
        by_id = {node.id: node.key for node in nodes}
        return {
            "nodes": [
                {
                    "key": node.key,
                    "name": node.name,
                    #: Layers are a display abstraction: the world stays one
                    #: graph, and the parent hierarchy groups nodes by layer (D-045, D-097).
                    "layer": node.layer.value,
                    "parent": by_id.get(node.parent_id),
                    "ring": node.properties.get("кольцо"),
                    "exit": bool(node.properties.get("выход")),
                }
                for node in nodes
            ],
            "edges": [
                {
                    "a": by_id[edge.node_a_id],
                    "b": by_id[edge.node_b_id],
                    "surface": edge.surface.value,
                    "seconds": round(roads.edge_seconds(constants, edge)),
                }
                for edge in edges
            ],
        }


@router.get("/doors")
async def doors() -> dict[str, Any]:
    """Where a newcomer can print: city, residents, settlement grant (D-013, D-182).

    Read **before any identification**: choosing a door is the first thing a
    person does in the game, and they have no identity at that moment yet.
    """
    from src.api.app import catalog
    from src.engine import world

    async with session_factory()() as db:
        return {"doors": await world.doors(db, current(), catalog())}


@router.get("/lines")
async def lines() -> dict[str, Any]:
    """Character lines and how many play each (D-015, D-104, D-187).

    Read at registration, before identification. Nymphs are in the list and
    marked unplayable: a promise, not a deceptive stub (10-world/03).
    """
    from src.engine import account

    async with session_factory()() as db:
        return {"lines": await account.lines(db)}


@router.get("/market/{node_key}")
async def market_positions(node_key: str) -> dict[str, Any]:
    """What trades in the node at all: goods plus quality tier.

    Public and remote: everyone knows the prices (D-047). Buying from here is
    not possible and will not be -- buying requires legs.
    """
    async with session_factory()() as db:
        node = await _node(db, node_key)
        return {
            "node": node.key,
            "positions": [
                {"goods": goods, "tier": tier}
                for goods, tier in await market.positions(db, node)
            ],
        }


@router.get("/market/{node_key}/book")
async def market_book(node_key: str, goods: str, tier: str) -> dict[str, Any]:
    """The book for one position: buy and sell orders with depth."""
    async with session_factory()() as db:
        node = await _node(db, node_key)
        book = await market.book(db, node, goods, tier, depth=MARKET_BOOK_DEPTH)
        payload = asdict(book)
        payload["node"] = node.key
        payload["spread"] = book.spread
        return payload


@router.get("/quality/tiers")
async def quality_tiers() -> dict[str, Any]:
    """Quality tiers -- the book's shop window (D-058).

    In data the scale is continuous, on the market tiers trade: a continuous
    scale would make the order book unreadable.
    """
    tiers = current()[R.QUALITY_TIERS]
    return {"tiers": [{"from": t.frm, "to": t.to, "name": t.name} for t in tiers]}


async def _node(db, key: str) -> Node:
    node = (await db.execute(select(Node).where(Node.key == key))).scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail=f"нет узла {key!r}")
    return node


@router.get("/laws")
async def laws() -> dict[str, Any]:
    """Charter, code-laws and sanctions with defaults.

    A new city works on defaults, filling in nothing (D-130).
    """

    from src.api.app import catalog

    book = catalog().laws
    return {
        "charter": [question.model_dump() for question in book.charter],
        "code_laws": [law.model_dump() for law in book.code_laws],
        "sanctions": [sanction.model_dump() for sanction in book.sanctions],
        "charter_defaults": book.charter_defaults(),
        "code_law_defaults": book.code_law_defaults(),
    }
