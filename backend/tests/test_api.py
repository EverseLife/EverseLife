"""Поверхность API.

Главная проверка здесь — не что ручки отвечают, а что **действий в API нет**.
Античит держится не на защите клиента, а на отсутствии удобного REST для
«сделать удар» (60-meta/01-anti-cheat, 01-tech-notes, паттерн 6).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app()) as test_client:
        yield test_client


def test_сервер_поднимается_и_знает_свои_числа(client) -> None:
    body = client.get("/health").json()
    assert body["ok"] is True
    assert body["constants"], "отпечаток набора констант обязан быть известен"


def test_константы_отдаются_клиенту_целиком(client) -> None:
    body = client.get("/public/constants").json()
    #: Клиент считает прогноз качества теми же числами, что и сервер, — иначе
    #: прогноз до партии разойдётся с результатом (D-092).
    assert body["values"]["mine.roof_start"]
    assert body["digest"]


def test_справочники_доступны(client) -> None:
    recipes = client.get("/public/recipes").json()
    assert recipes["recipes"] and recipes["raw"] and recipes["operations"]

    laws = client.get("/public/laws").json()
    #: Новый город работает на умолчаниях, ничего не заполняя (D-130).
    assert laws["charter_defaults"] and laws["code_law_defaults"]

    plants = client.get("/public/plants").json()
    assert len(plants["plants"]) == 8


def test_api_действий_не_существует(client) -> None:
    """Ни один маршрут не меняет мир. Это ограничение архитектуры, а не настройка."""
    routes = client.app.routes
    mutating = {
        (route.path, method)
        for route in routes
        if getattr(route, "methods", None)
        for method in route.methods
        if method in {"POST", "PUT", "PATCH", "DELETE"}
    }
    assert not mutating, (
        f"в API появились изменяющие маршруты: {sorted(mutating)}. "
        "Присутственное действие идёт только через сессию клиента (D-042, D-110)"
    )
