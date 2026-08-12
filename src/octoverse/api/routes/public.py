"""Публичное чтение: справочники и состояние мира.

Здесь нет ничего, что меняет мир, и не будет. Цены, статистика и кодекс
публичны намеренно: цены знают все (D-047), а закрывать справочники бессмысленно
— они и так лежат в вольте.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from octoverse.constants import HOLDER

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
    from octoverse.api.app import catalog

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
    from octoverse.api.app import catalog

    return {"plants": [plant.model_dump() for plant in catalog().plants.plants]}


@router.get("/laws")
async def laws() -> dict[str, Any]:
    """Устав, код-законы и санкции с умолчаниями.

    Новый город работает на умолчаниях, ничего не заполняя (D-130).
    """
    from octoverse.api.app import catalog

    book = catalog().laws
    return {
        "charter": [question.model_dump() for question in book.charter],
        "code_laws": [law.model_dump() for law in book.code_laws],
        "sanctions": [sanction.model_dump() for sanction in book.sanctions],
        "charter_defaults": book.charter_defaults(),
        "code_law_defaults": book.code_law_defaults(),
    }
