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
    #: The vent gases travel with the liquids (D-340): without them the client
    #: never offers to empty a vessel of hydrogen.
    assert "hydrogen" in recipes["vent"]

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


def test_the_picture_rasters_are_public_bytes_with_an_etag(client) -> None:
    """The shader's textures (landscape plan wave 5): bytes, named by what
    they hold, and the sketch says what they are."""
    passport = client.get("/public/terrain/terra").json()["raster"]
    n = passport["rows"] * passport["cols"]
    for kind, width in (("height", 2), ("biome", 1), ("form", 1), ("water", 1)):
        answer = client.get(f"/public/terrain/terra/raster/{kind}")
        assert answer.status_code == 200
        assert answer.headers["content-type"] == "application/octet-stream"
        assert answer.headers["etag"] and answer.headers["cache-control"]
        assert len(answer.content) == n * width
    assert client.get("/public/terrain/terra/raster/rivers").status_code == 404
    #: The tile route still answers by row and column beside it.
    assert client.get("/public/terrain/terra/0/0").status_code == 200


def test_the_picture_is_sent_squeezed_once_and_not_again(client) -> None:
    """The picture over HTTP (2026-09-18): squeezed, named by its own bytes,
    and answered with no body to a browser that holds it."""
    for path in ("/public/terrain/terra", "/public/terrain/terra/raster/height"):
        raw = client.get(path, headers={"Accept-Encoding": "identity"})
        assert raw.status_code == 200 and "content-encoding" not in raw.headers
        squeezed = client.get(path, headers={"Accept-Encoding": "gzip, deflate, br, zstd"})
        assert squeezed.headers["content-encoding"] == "gzip"
        assert squeezed.content == raw.content, "сжатое и несжатое — одни байты"
        assert "Accept-Encoding" in squeezed.headers["vary"]
        #: Two bodies, two strong names; either says the browser holds it.
        plain, packed = raw.headers["etag"], squeezed.headers["etag"]
        assert plain != packed and packed == plain[:-1] + '.gz"'
        for tag in (plain, packed, f"W/{packed}", f'"stale", {plain}'):
            again = client.get(path, headers={"If-None-Match": tag})
            assert again.status_code == 304 and again.content == b"", tag
        assert client.get(path, headers={"If-None-Match": '"stale"'}).status_code == 200
        #: Squeezed once here, and not again by the API's gzip middleware.
        assert squeezed.headers["vary"] == "Accept-Encoding"
    #: Not the constants' digest, which every raster shared and which does not
    #: cover the field's own files: each raster has the name of its bytes.
    tags = {
        client.get(f"/public/terrain/terra/raster/{kind}").headers["etag"]
        for kind in ("height", "biome", "form")
    }
    assert len(tags) == 3
    #: And the sketch still reads as the JSON it always was.
    body = client.get("/public/terrain/terra").json()
    assert body["raster"]["grid"] == "healpix" and body["grid"]


def test_the_picture_is_kept_by_its_version_and_the_sketch_asked_every_time(client) -> None:
    """The passport and the rasters are one set (2026-09-18): the sketch is
    asked about at every visit and names the version, the rasters asked for
    by that version are kept a year -- and nothing else is kept at all."""
    sketch = client.get("/public/terrain/terra")
    assert "no-cache" in sketch.headers["cache-control"]
    version = sketch.json()["raster"]["version"]
    assert version
    kept = client.get(f"/public/terrain/terra/raster/height?v={version}")
    assert kept.status_code == 200
    control = kept.headers["cache-control"]
    assert "immutable" in control and "max-age=31536000" in control
    small = sketch.json()["raster"]["preview_nside"]
    copy = client.get(f"/public/terrain/terra/raster/rain?nside={small}&v={version}")
    assert "immutable" in copy.headers["cache-control"]
    #: Without the version, or by an old one: the current bytes, asked about
    #: at every use -- an old picture is never kept under a new name.
    for query in ("", "?v=0000000000000000"):
        loose = client.get(f"/public/terrain/terra/raster/height{query}")
        assert loose.status_code == 200 and loose.content == kept.content
        assert "no-cache" in loose.headers["cache-control"]
    #: Each planet's picture has its own name.
    other = client.get("/public/terrain/pyroxis").json()["raster"]["version"]
    assert other and other != version


def test_the_height_is_sent_coded_when_asked_by_name(client) -> None:
    """The planar coding (2026-09-18, `api.planar`): the heights in about half
    the bytes, for the client that asks by name; the plain ones for any
    other."""
    from src.api import planar

    passport = client.get("/public/terrain/terra").json()["raster"]
    version = passport["version"]
    small = passport["preview_nside"]
    for nside, cols in (
        (passport["nside"], passport["cols"]),
        (small, passport["across"] * (small + 2 * passport["border"])),
    ):
        query = f"?nside={nside}&v={version}"
        plain = client.get(f"/public/terrain/terra/raster/height{query}")
        coded = client.get(f"/public/terrain/terra/raster/height.planar{query}")
        assert coded.status_code == 200
        assert planar.decode(coded.content, cols) == plain.content
        #: Another body, another name; kept by the version like the plain one.
        assert coded.headers["etag"] != plain.headers["etag"]
        assert "immutable" in coded.headers["cache-control"]
    #: A coding no raster but the height has, and a coding nobody has: a
    #: 404, as a server without codings answers the coded name.
    assert client.get("/public/terrain/terra/raster/biome.planar").status_code == 404
    assert client.get("/public/terrain/terra/raster/height.zip").status_code == 404


def test_the_picture_has_a_quick_copy_and_no_other(client) -> None:
    """The preview (2026-09-18): the same rasters a sixteenth the size, at the
    fineness the passport names -- and at no fineness it does not."""
    passport = client.get("/public/terrain/terra").json()["raster"]
    small = passport["preview_nside"]
    assert 0 < small < passport["nside"]
    side = small + 2 * passport["border"]
    n = passport["across"] * side * passport["down"] * side
    for kind, width in (("height", 2), ("biome", 1), ("rain", 1)):
        answer = client.get(f"/public/terrain/terra/raster/{kind}?nside={small}")
        assert answer.status_code == 200 and len(answer.content) == n * width
    full = client.get(f"/public/terrain/terra/raster/height?nside={passport['nside']}")
    assert len(full.content) == passport["rows"] * passport["cols"] * 2
    #: The copy is the shader's: the vector layer's rasters have none.
    for kind in ("water", "province", "flow"):
        assert client.get(f"/public/terrain/terra/raster/{kind}?nside={small}").status_code == 404
        assert client.get(f"/public/terrain/terra/raster/{kind}").status_code == 200
    for wrong in (small + 1, passport["nside"] * 2, 0, -1):
        answer = client.get(f"/public/terrain/terra/raster/height?nside={wrong}")
        assert answer.status_code == 404, wrong
