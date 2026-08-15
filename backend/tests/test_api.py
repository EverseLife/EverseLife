"""The API surface.

The main check here is not that endpoints respond but that **there are no
actions in the API**. Anti-cheat rests not on protecting the client but on
the absence of a convenient REST for "make a swing" (60-meta/01-anti-cheat,
01-tech-notes, pattern 6).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app()) as test_client:
        yield test_client


def test_server_starts_and_knows_its_numbers(client) -> None:
    body = client.get("/health").json()
    assert body["ok"] is True
    assert body["constants"], "отпечаток набора констант обязан быть известен"


def test_constants_served_to_client_whole(client) -> None:
    body = client.get("/public/constants").json()
    #: The client computes the quality forecast by the same numbers as the
    #: server -- otherwise the forecast before the batch diverges from the result (D-092).
    assert body["values"]["mine.roof_start"]
    assert body["digest"]


def test_catalogs_available(client) -> None:
    recipes = client.get("/public/recipes").json()
    assert recipes["recipes"] and recipes["raw"] and recipes["operations"]

    laws = client.get("/public/laws").json()
    #: A new city works on defaults, filling in nothing (D-130).
    assert laws["charter_defaults"] and laws["code_law_defaults"]

    plants = client.get("/public/plants").json()
    assert len(plants["plants"]) == 8


def test_action_api_does_not_exist(client) -> None:
    """Not one route changes the world. This is an architectural constraint, not a setting."""
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
