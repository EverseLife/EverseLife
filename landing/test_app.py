# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Smoke tests for the landing service: pages, assets, SEO routes.

Run from `landing/` with a throwaway database:
`LANDING_DB=./test-signups.db python -m pytest test_app.py -q`.
The signup intake itself is not covered here -- it needs a database fixture;
these tests pin what a deploy must not silently lose: every page in `PAGES`
is served (GET and HEAD, as uptime monitors probe), the shared assets both
pages link to exist, and the sitemap lists exactly the pages.
"""

import re

from fastapi.testclient import TestClient

from app import PAGES, SITE, app

client = TestClient(app)


def test_pages_serve_get_and_head() -> None:
    for path in PAGES:
        for method in ("GET", "HEAD"):
            answer = client.request(method, path)
            assert answer.status_code == 200, f"{method} {path}"
        assert "text/html" in client.get(path).headers["content-type"]


def test_pages_carry_their_own_canonical() -> None:
    for path in PAGES:
        html = client.get(path).text
        canonical = re.search(r'<link rel="canonical" href="([^"]+)"', html)
        assert canonical, path
        assert canonical.group(1) == f"{SITE}{path}", path


def test_shared_assets_serve_get_and_head() -> None:
    for path, kind in (
        ("/site.css", "text/css"),
        ("/site.js", "text/javascript"),
        ("/space.js", "text/javascript"),
    ):
        for method in ("GET", "HEAD"):
            answer = client.request(method, path)
            assert answer.status_code == 200, f"{method} {path}"
        got = client.get(path)
        assert kind in got.headers["content-type"]
        assert got.headers["cache-control"] == "no-cache"


def test_sitemap_lists_every_page() -> None:
    xml = client.get("/sitemap.xml").text
    locs = re.findall(r"<loc>([^<]+)</loc>", xml)
    assert locs == [f"{SITE}{path}" for path in PAGES]


def test_robots_points_at_the_sitemap() -> None:
    text = client.get("/robots.txt").text
    assert f"Sitemap: {SITE}/sitemap.xml" in text
