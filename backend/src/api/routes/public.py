"""Публичное чтение: справочники и состояние мира.

Здесь нет ничего, что меняет мир, и не будет. Цены, статистика и кодекс
публичны намеренно: цены знают все (D-047), а закрывать справочники бессмысленно
— они и так лежат в вольте.
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

router = APIRouter(prefix="/public", tags=["чтение"])


@router.get("/constants")
async def constants() -> dict[str, Any]:
    """Действующий набор балансных чисел и его отпечаток.

    Клиент считает прогноз качества и стоимость партии по тем же числам, что и
    сервер, — иначе прогноз до запуска партии (D-092) разошёлся бы с результатом.
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
    """Карта мира: узлы и рёбра с временем перехода.

    Города и магистрали публичны — иначе новичок не найдёт, куда идти (D-097).
    Пока публична вся карта: разведки ещё нет, а с ней дикие узлы и жилы станут
    видны только разведавшим.
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
                    #: Слои — абстракция показа: мир остаётся одним графом,
                    #: а иерархия parent группирует узлы по слоям (D-045, D-097).
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
    """Где новичок может напечататься: город, жители, подъёмные (D-013, D-182).

    Читается **до всякого опознания**: выбор двери — первое, что человек делает
    в игре, и личности у него в этот момент ещё нет.
    """
    from src.api.app import catalog
    from src.engine import world

    async with session_factory()() as db:
        return {"doors": await world.doors(db, current(), catalog())}


@router.get("/market/{node_key}")
async def market_positions(node_key: str) -> dict[str, Any]:
    """Что вообще торгуется в узле: товар плюс ступень качества.

    Публично и удалённо: цены знают все (D-047). Купить отсюда нельзя и не
    будет можно — покупка требует ног.
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
    """Стакан по одной позиции: заявки на покупку и продажу с глубиной."""
    async with session_factory()() as db:
        node = await _node(db, node_key)
        book = await market.book(db, node, goods, tier, depth=MARKET_BOOK_DEPTH)
        payload = asdict(book)
        payload["node"] = node.key
        payload["spread"] = book.spread
        return payload


@router.get("/quality/tiers")
async def quality_tiers() -> dict[str, Any]:
    """Ступени качества — витрина стакана (D-058).

    В данных шкала непрерывна, на рынке торгуются ступени: непрерывная шкала
    сделала бы книгу заявок нечитаемой.
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
    """Устав, код-законы и санкции с умолчаниями.

    Новый город работает на умолчаниях, ничего не заполняя (D-130).
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
