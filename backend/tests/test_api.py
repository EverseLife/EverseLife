# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

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
    #: The socket's tally (D-226, step 4): the poll is watched, not assumed gone.
    tally = body["session"]
    assert set(tally) >= {
        "connections",
        "listening",
        "events_sent",
        "answers",
        "look_per_connection_hour",
    }


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

    #: The founding threshold is a catalog constant and is read once from
    #: here, not carried by every `look` (D-225). Roles as keys, and every
    #: role filled by at least one machine the vault actually knows.
    #:
    #: Against `FOUNDATION_ROLES` and not against a literal on purpose: the
    #: window says a role by rendering `city-role-<role>`, and the message
    #: for every role is guaranteed by `test_i18n` walking **that** tuple. A
    #: role added only to `foundation_needs()` would reach the player as a
    #: bare key with nothing red anywhere; this is what ties the two lists.
    from src.engine.city.founding import FOUNDATION_ROLES

    roles = client.get("/public/founding").json()["roles"]
    assert [row["role"] for row in roles] == list(FOUNDATION_ROLES)
    assert all(row["any_of"] for row in roles), "роль без машины закрыть нечем"


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
