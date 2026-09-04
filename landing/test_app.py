# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Smoke tests for the landing service: pages, assets, SEO routes.

Run from `landing/` with a throwaway database:
`LANDING_DB=./test-signups.db python -m pytest test_app.py -q`.
The signup intake itself is not covered here -- it needs a database fixture;
these tests pin what a deploy must not silently lose: every page in `PAGES`
is served (GET and HEAD, as uptime monitors probe), the shared assets the
pages link to exist, the sitemap lists exactly the pages, and the two
languages of every page point at each other -- in the sitemap, in the page's
own `<head>` and in the switch in its header. A translation that stops
naming its twin is invisible as a translation: the two versions then read as
rivals for one query.
"""

import importlib
import json
import re
import sqlite3
from collections import Counter
from html import unescape
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as landing
import stats
from app import (
    ALTERNATES,
    ASSET_CACHE,
    DEFAULT_LANG,
    FONT_CACHE,
    PAGE_CACHE,
    PAGES,
    SITE,
    SITE_PAGES,
    X_DEFAULT_LANG,
    app,
)

client = TestClient(app)


def language_of(path: str) -> str:
    """Which language a page's own address belongs to."""
    return next(lang for lang, other in ALTERNATES[path].items() if other == path)


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
        #: Never `immutable` while the names carry no version: a stale
        #: `/site.css` pinned for a year could not be called back.
        assert got.headers["cache-control"] == ASSET_CACHE
        assert "immutable" not in ASSET_CACHE


def test_every_typeface_the_css_asks_for_is_served() -> None:
    """And never the material it was cut from.

    `subset_fonts.py` keeps `fonts/src/` as the source and `fonts/` as what
    the browser gets -- the subsets are a third lighter, and the front page is
    mostly typeface by weight. Serving a source file instead would give the
    saving back with nothing on screen to show that it had.
    """
    css = (Path(__file__).parent / "site.css").read_text(encoding="utf-8")
    asked = set(re.findall(r'url\("(/fonts/[^"]+)"\)', css))
    assert asked, "site.css declares no @font-face"
    for path in sorted(asked):
        answer = client.get(path)
        assert answer.status_code == 200, path
        assert answer.headers["cache-control"] == FONT_CACHE, path
        assert "/src/" not in path, path
    assert client.get("/fonts/src/onest.woff2").status_code == 404


def test_sitemap_lists_every_page() -> None:
    xml = client.get("/sitemap.xml").text
    locs = re.findall(r"<loc>([^<]+)</loc>", xml)
    assert locs == [f"{SITE}{path}" for path in PAGES]


def test_robots_points_at_the_sitemap() -> None:
    text = client.get("/robots.txt").text
    assert f"Sitemap: {SITE}/sitemap.xml" in text


@pytest.fixture
def with_key(monkeypatch: pytest.MonkeyPatch):
    """The module reread with an IndexNow key set.

    The key becomes a route, so it can only be picked up at import: the
    fixture reloads the module, hands over a client of its own, and reloads it
    back afterwards so the rest of the suite keeps the app it started with.
    """

    def reload(value: str):
        monkeypatch.setenv("LANDING_INDEXNOW_KEY", value)
        return importlib.reload(landing)

    yield reload
    monkeypatch.delenv("LANDING_INDEXNOW_KEY", raising=False)
    importlib.reload(landing)


def test_without_a_key_the_landing_has_no_extra_route() -> None:
    #: Nothing but robots.txt answers on a `.txt` path while no key is set.
    paths = [getattr(route, "path", "") for route in app.routes]
    assert [one for one in paths if one.endswith(".txt")] == ["/robots.txt"]


def test_the_indexnow_key_is_served_back_at_its_own_name(with_key) -> None:
    key = "abc123def4567890"
    fresh = with_key(key)
    keyed = TestClient(fresh.app)
    for method in ("GET", "HEAD"):
        assert keyed.request(method, f"/{key}.txt").status_code == 200, method
    answer = keyed.get(f"/{key}.txt")
    #: The file holds the key and nothing else -- that is the whole proof.
    assert answer.text == key
    assert "text/plain" in answer.headers["content-type"]


def test_a_malformed_key_opens_no_route(with_key) -> None:
    #: Too short, and with a character the protocol does not allow: a typo
    #: must not turn into a path on the domain.
    fresh = with_key("oops!")
    paths = [getattr(route, "path", "") for route in fresh.app.routes]
    assert [one for one in paths if one.endswith(".txt")] == ["/robots.txt"]


def test_a_page_loads_nothing_from_another_host() -> None:
    """Every byte a page fetches comes from this domain.

    The Google counter used to cost 855 ms of the load measured from a fast
    line, and far more from the networks most of the audience is on, where
    Google's infrastructure is throttled -- a page that is otherwise 171 KB
    of our own bytes. Nothing was worth a cross-border request, so the rule
    is absolute rather than a budget, and a third host creeping back into a
    `src` is what this test is here to name.

    Links are another matter and not checked: an anchor to Discord costs
    nothing until somebody clicks it.
    """
    for path in PAGES:
        answer = client.get(path)
        html = answer.text
        assert answer.headers["cache-control"] == PAGE_CACHE, path
        for banned in ("googletagmanager", "google-analytics", "gtag", "dataLayer"):
            assert banned not in html, f"{path}: {banned}"
        loaded = re.findall(r'<(?:script|img)[^>]+src="([^"]+)"', html)
        loaded += re.findall(r'<link[^>]+rel="(?:stylesheet|preload)"[^>]+href="([^"]+)"', html)
        for one in loaded:
            assert one.startswith("/"), f"{path} fetches from elsewhere: {one}"


def test_sitemap_dates_pages_by_content_not_by_deploy() -> None:
    """`lastmod` comes from the stamps, not from mtime.

    mtime is the deploy's own timestamp -- a checkout rewrites every file --
    so a sitemap built on it calls every page fresh after any deploy, and a
    search engine that notices stops trusting the field at all.
    """
    stamps = json.loads((Path(__file__).parent / "lastmod.json").read_text(encoding="utf-8"))
    xml = client.get("/sitemap.xml").text
    for path in PAGES:
        entry = f"<loc>{SITE}{path}</loc>\n    <lastmod>{stamps[path]['date']}</lastmod>"
        assert entry in xml, path


def test_every_page_carries_a_stamp() -> None:
    #: A page added to PAGES without restamping would silently fall back to
    #: mtime; pre-commit runs `lastmod.py --check` for the same reason.
    stamps = json.loads((Path(__file__).parent / "lastmod.json").read_text(encoding="utf-8"))
    assert set(stamps) == set(PAGES)


def test_every_page_exists_in_every_language() -> None:
    #: A row with a language missing would serve a 404 the sitemap advertises.
    for row in SITE_PAGES:
        assert set(row) == set(landing.LANGS), row


def test_a_page_declares_the_language_it_is_written_in() -> None:
    """`<html lang>` is what the script reads to pick its own words.

    A page mislabelled here answers a signup in the wrong language and prints
    the clock and the deadlines in it too, all while looking correct.
    """
    for path in ALTERNATES:
        html = client.get(path).text
        said = re.search(r"<html lang=\"([a-z-]+)\">", html)
        assert said, path
        assert said.group(1) == language_of(path), path


def test_a_page_says_its_language_in_the_headers() -> None:
    """`Content-Language` on the response, matching `<html lang>`.

    Google reads the text and the `hreflang` and does not need this; Bing and
    Yandex do read the header, and a page whose header and markup disagree
    about its own language is worse than one that says nothing.
    """
    for path in ALTERNATES:
        answer = client.get(path)
        assert answer.headers["content-language"] == language_of(path), path


def test_pages_name_their_translations_in_the_head() -> None:
    """Each page carries the whole set of alternates, its own included.

    The annotation only counts when it is reciprocal: a page that names its
    twin while the twin stays silent is read as a separate page, not as a
    translation. x-default is the version for a reader whose language the
    site does not have at all, and it is the same on both pages of a pair --
    an x-default that disagrees between two versions is the one mistake in
    this markup a search engine cannot resolve for us.
    """
    for path, languages in ALTERNATES.items():
        html = client.get(path).text
        for lang, other in languages.items():
            link = f'<link rel="alternate" hreflang="{lang}" href="{SITE}{other}">'
            assert link in html, f"{path}: {link}"
        catch_all = languages[X_DEFAULT_LANG]
        fallback = f'<link rel="alternate" hreflang="x-default" href="{SITE}{catch_all}">'
        assert fallback in html, path


def test_the_sitemap_names_the_translations_too() -> None:
    xml = client.get("/sitemap.xml").text
    assert 'xmlns:xhtml="http://www.w3.org/1999/xhtml"' in xml
    for path, languages in ALTERNATES.items():
        for lang, other in languages.items():
            assert f'<xhtml:link rel="alternate" hreflang="{lang}" href="{SITE}{other}"/>' in xml
        fallback = f'href="{SITE}{languages[X_DEFAULT_LANG]}"/>'
        assert f'hreflang="x-default" {fallback}' in xml, path


def test_the_header_switch_leads_to_the_same_page() -> None:
    """The switch goes to this page's twin, not to the other front page.

    Landing on the front page after asking for the same page in English is
    the classic way a language switch loses the reader, and nothing but a
    test notices it: the link works, it is simply pointing at the wrong page.
    """
    for path, languages in ALTERNATES.items():
        html = client.get(path).text
        switch = re.search(r'<nav class="lang".*?</nav>', html, re.DOTALL)
        assert switch, path
        for lang, other in languages.items():
            if other == path:
                continue
            assert f'href="{other}" hreflang="{lang}"' in switch.group(0), f"{path} -> {other}"


def test_the_ru_mirror_redirects_to_the_page_at_the_root() -> None:
    """`/ru/...` answers, but only by pointing at the address that is real.

    Russian lives at the root; `/ru/` is the address a person guesses once
    `/en/` exists. It must never serve the page itself -- a second address
    for the same words is the duplicate that hreflang exists to prevent.
    """
    for row in SITE_PAGES:
        root = row[DEFAULT_LANG]
        mirror = f"/{DEFAULT_LANG}{root}".rstrip("/")
        answer = client.get(mirror, follow_redirects=False)
        assert answer.status_code == 301, mirror
        assert answer.headers["location"] == root, mirror


def test_translations_keep_the_same_markup() -> None:
    """A page and its translation are the same page in two languages.

    They are two hand-written files, because here the words are the layout --
    the line breaks in the `h1`, the `<b>` inside a sentence, the length of a
    label under a planet. What that costs is drift: a section added to one and
    forgotten in the other, a class renamed on one side, a `rv` lost so half a
    page never fades in. Nothing else notices, and nobody reads both files at
    once. So the shape is pinned here: the same tags in the same order, and
    the same classes, in every language of a page. The words are free.
    """
    for row in SITE_PAGES:
        pages = {lang: client.get(path).text for lang, path in row.items()}
        original = row[DEFAULT_LANG]
        tags = {lang: re.findall(r"<([a-zA-Z][\w-]*)", html) for lang, html in pages.items()}
        classes = {
            lang: Counter(re.findall(r'class="([^"]*)"', html)) for lang, html in pages.items()
        }
        for lang, path in row.items():
            if lang == DEFAULT_LANG:
                continue
            #: The switch is the one place the two differ on purpose: the
            #: current language is a `span` and the other one a link, so the
            #: pair swaps places between the two files.
            assert Counter(tags[lang]) == Counter(tags[DEFAULT_LANG]), f"{path} vs {original}"
            assert classes[lang] == classes[DEFAULT_LANG], f"{path} vs {original}"


def test_the_faq_markup_repeats_the_faq_on_the_page() -> None:
    """Structured data has to say what the page says, in both languages.

    A rich result quoting an answer the page no longer gives is the kind of
    mismatch a search engine drops the whole markup over, and nobody sees it
    while reading either file: the questions sit two hundred lines from their
    copies in the `<head>`. The README asks for the two to be edited
    together; this is what makes that true.
    """

    def flat(fragment: str) -> str:
        without_tags = re.sub(r"<[^>]+>", "", fragment)
        return unescape(re.sub(r"\s+", " ", without_tags)).strip().rstrip(".")

    for path in ALTERNATES:
        html = client.get(path).text
        if 'class="faq' not in html:
            continue
        shown = {
            flat(question): flat(answer)
            for question, answer in re.findall(
                r"<summary>(.*?)</summary>\s*<p class=\"a\">(.*?)</p>",
                html[html.index('class="faq') :],
                re.DOTALL,
            )
        }
        assert shown, path
        graph = json.loads(
            re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL).group(
                1
            )
        )
        faq = [node for node in graph["@graph"] if node["@type"] == "FAQPage"]
        assert faq, path
        for node in faq[0]["mainEntity"]:
            question = node["name"].rstrip(".")
            assert question in shown, f"{path}: {question} is not on the page"
            assert shown[question] == node["acceptedAnswer"]["text"].rstrip("."), (
                f"{path}: {question}"
            )


def test_a_refusal_exists_in_every_language() -> None:
    #: Pages are checked for this above; refusals were not, and a language
    #: missing here answers in the original while everything else translates.
    for which, said in landing.REFUSALS.items():
        assert set(said) == set(landing.LANGS), which


def test_a_front_page_answers_its_own_address_without_the_slash() -> None:
    """`/en` is what gets typed and linked; `/en/` is where the page lives.

    Starlette would answer that by itself with a 307, which tells a crawler
    the address may move back tomorrow. It will not, and `/ru` next door
    answers 301.
    """
    for path in PAGES:
        if path == "/" or not path.endswith("/"):
            continue
        answer = client.get(path.rstrip("/"), follow_redirects=False)
        assert answer.status_code == 301, path
        assert answer.headers["location"] == path, path


def test_every_page_shows_a_card_in_its_own_language() -> None:
    """The headline is drawn into the card, so each language needs its own.

    An English page pointing at `/og.png` shows a Russian headline in every
    Discord and Twitter preview -- the one place the page is seen before it is
    opened. The card it names has to be served, too: a 404 there is a preview
    with no picture at all.
    """
    for path in ALTERNATES:
        html = client.get(path).text
        named = re.search(r'<meta property="og:image" content="([^"]+)"', html)
        assert named, path
        card = named.group(1).removeprefix(SITE)
        assert card in landing.OG_IMAGES, f"{path}: {card}"
        assert client.get(card).status_code == 200, card
        mine = language_of(path)
        wanted = "/og.png" if mine == DEFAULT_LANG else f"/og-{mine}.png"
        assert card == wanted, path


def test_a_refusal_speaks_the_language_of_the_page() -> None:
    """The page says which language it is in; the browser's locale is not asked.

    A Russian-locale browser reading the English page must be refused in
    English -- otherwise the one moment the form talks back is the one moment
    the site forgets which language it was speaking.
    """
    said = client.post("/api/signup", json={"email": "not-an-email", "lang": "en"})
    assert said.status_code == 422
    assert said.json()["error"] == landing.REFUSALS["not_an_email"]["en"]

    #: An unknown language falls back to the original, never to a key name.
    said = client.post("/api/signup", json={"email": "not-an-email", "lang": "fr"})
    assert said.json()["error"] == landing.REFUSALS["not_an_email"][DEFAULT_LANG]


# --- The counter -------------------------------------------------------------
#
# What these pin is the part that is easy to break quietly: a page that stops
# being counted, a label that grows without bound, an address that starts being
# stored. None of it shows on the page, so only a test can say it broke.


def visits_of(where: str) -> list[sqlite3.Row]:
    """Rows the counter wrote for one page, newest first."""
    stats.flush()
    conn = stats.connect()
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT * FROM visits WHERE path = ? ORDER BY id DESC", (where,)
        ).fetchall()
    finally:
        conn.close()


def test_a_page_view_is_written_down_with_where_it_came_from() -> None:
    client.get("/gameplay", headers={"referer": "https://dtf.ru/indie/5273472-something"})
    row = visits_of("/gameplay")[0]
    assert row["source"] == "dtf.ru"
    assert row["medium"] == "referral"
    assert row["referrer"] == "dtf.ru"
    assert row["bot"] == 0


def test_utm_tags_win_over_the_referrer() -> None:
    """The link we wrote knows which post it is; the referrer only knows the site.

    DTF is one host and many posts, so `utm_content` is the difference between
    "DTF works" and "the second post worked and the first did not".
    """
    client.get(
        "/world?utm_source=dtf&utm_medium=social&utm_campaign=alpha-wave-1&utm_content=npc-post",
        headers={"referer": "https://dtf.ru/indie/5273472-something"},
    )
    row = visits_of("/world")[0]
    assert (row["source"], row["medium"]) == ("dtf", "social")
    assert (row["campaign"], row["content"]) == ("alpha-wave-1", "npc-post")


def test_a_visit_stores_no_address() -> None:
    """Uniques are counted by a salted hash, and the address itself is not kept.

    The signups table holds an address on purpose -- that is somebody asking
    to be written to. A page view is not, and a counter that quietly built a
    log of who read what would be a different product than the one described.
    """
    conn = stats.connect()
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(visits)")}
    finally:
        conn.close()
    assert "ip" not in columns
    row = visits_of("/gameplay")[0]
    assert len(row["visitor"]) == 16
    assert row["visitor"] != stats.visitor("testclient", "x", "1999-01-01")


def test_a_monitor_is_not_a_reader() -> None:
    before = len(visits_of("/alpha"))
    client.request("HEAD", "/alpha")
    assert len(visits_of("/alpha")) == before


def test_a_crawler_is_marked_and_never_becomes_an_event() -> None:
    crawler = {"user-agent": "Mozilla/5.0 (compatible; Googlebot/2.1)"}
    client.get("/alpha", headers=crawler)
    assert visits_of("/alpha")[0]["bot"] == 1

    stats.flush()
    conn = stats.connect()
    try:
        before = conn.execute("SELECT count(*) FROM events").fetchone()[0]
    finally:
        conn.close()
    client.get("/go/discord", headers=crawler)
    stats.flush()
    conn = stats.connect()
    try:
        assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == before
    finally:
        conn.close()


def test_the_door_to_discord_counts_and_is_closed_to_crawlers() -> None:
    """The invite is reached through our own address, or the click is invisible.

    `discord.com/invite/...` and not `discord.gg`: the short form is a deep
    link into an app that may not open, which is how a reader who wanted in
    spent half an hour outside.
    """
    answer = client.get("/go/discord", follow_redirects=False)
    assert answer.status_code == 302
    assert answer.headers["location"] == landing.DISCORD_INVITE
    assert "discord.com/invite/" in landing.DISCORD_INVITE

    stats.flush()
    conn = stats.connect()
    try:
        assert conn.execute(
            "SELECT count(*) FROM events WHERE kind = 'discord_click'"
        ).fetchone()[0] >= 1
    finally:
        conn.close()

    assert "Disallow: /go/" in client.get("/robots.txt").text


def test_metrics_speak_prometheus_and_keep_their_labels_countable() -> None:
    """A campaign is a stranger's string; a label set has to stay small.

    Anyone can put `?utm_campaign=` and anything after it on a link to us. The
    row keeps what they wrote -- that is the archive's job -- but the
    exposition names only the busiest campaigns and folds the rest into one,
    so a stranger cannot invent a million time series in Prometheus.
    """
    for number in range(stats.CAMPAIGN_TOP + 5):
        client.get(f"/?utm_source=test&utm_campaign=made-up-{number}")
    stats.flush()

    text = stats.exposition()
    assert "# TYPE landing_visits_total counter" in text
    assert "landing_unique_visitors{window=" in text
    assert "landing_funnel{" in text

    named = re.findall(r'landing_campaign_visits\{campaign="([^"]+)"\}', text)
    #: The cap, plus the two rows that are not campaigns: untagged traffic and
    #: the folded tail. Untagged is always the biggest row, so it is reported
    #: beside the cap rather than inside it -- otherwise it alone would cost a
    #: real campaign its line.
    assert len(named) <= stats.CAMPAIGN_TOP + 2, named
    assert len(named) == len(set(named)), named
    assert "(none)" in named and "other" in named
    real = [one for one in named if one not in ("(none)", "other")]
    assert len(real) == stats.CAMPAIGN_TOP, real

    #: Every line is `name{labels} number` -- a stray quote or newline from a
    #: URL would make the whole exposition unparseable, and Prometheus would
    #: drop the scrape rather than the bad line.
    shape = re.compile(r'[a-z_]+(\{[a-z_]+="[^"\n]*"(,[a-z_]+="[^"\n]*")*\})? -?[\d.e+-]+')
    for line in text.splitlines():
        if line and not line.startswith("#"):
            assert shape.fullmatch(line), line


def test_metrics_are_not_part_of_the_site() -> None:
    """The route exists for Prometheus on the compose network, and Caddy 404s it.

    Nothing here is secret, but it is not the site's, and an address that
    answers on the public domain is an address somebody indexes.
    """
    assert "/metrics" not in [row["ru"] for row in SITE_PAGES]
    assert "/metrics" not in client.get("/sitemap.xml").text


def test_a_tag_we_wrote_lands_on_the_same_line_as_the_referrer() -> None:
    """`utm_source=dtf` and a referrer from `dtf.ru` are one source, not two.

    Otherwise the graph splits the same audience across two lines and the
    tagged links -- the ones we control -- are the half that falls into
    `other`.
    """
    assert stats._bucket("dtf") == "dtf"
    assert stats._bucket("dtf.ru") == "dtf"
    assert stats._bucket("news.dtf.ru") == "dtf"
    assert stats._bucket("some-blog.example") == "other"


def test_a_stranger_cannot_invent_mediums_either() -> None:
    """`utm_medium` is somebody else's string too, and it becomes a label.

    The campaign is capped and the source is bucketed; without the same fold
    here, fifty invented mediums are fifty Prometheus series -- and because
    the visit counters cover all of history, they would never age out.
    """
    for number in range(40):
        client.get(f"/gameplay?utm_source=dtf&utm_medium=invented-{number}")
    stats.flush()
    mediums = set(re.findall(r'landing_visits_total\{[^}]*medium="([^"]+)"', stats.exposition()))
    assert not [one for one in mediums if one.startswith("invented-")], mediums
    assert mediums <= stats.MEDIUMS | {"other"}, mediums

    #: The row itself keeps what arrived -- the archive is not the label set.
    kept = {row["medium"] for row in visits_of("/gameplay")}
    assert any(one.startswith("invented-") for one in kept), kept


def test_a_broken_database_never_reaches_the_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    """The counter may lose a row; it may not lose the page or the signup.

    `see_page` runs inside the page handler. Everything it might touch --
    opening the file, the salt, the hash, the insert -- was moved to the
    writer thread for this reason, and what is left is wrapped: a page that
    answers 500 because bookkeeping failed costs more than the bookkeeping
    could ever be worth.
    """
    def explode(*args, **kwargs):
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(stats, "_enqueue", explode)
    assert client.get("/world").status_code == 200
    assert client.get("/go/discord", follow_redirects=False).status_code == 302
    said = client.post("/api/signup", json={"email": "kept@example.test", "lang": "ru"})
    assert said.status_code == 200 and said.json() == {"ok": True}


def test_referrers_are_named_up_to_the_cap_and_direct_is_its_own_row() -> None:
    """The graph shows buckets; the hosts behind `other` are named here.

    A referrer is a stranger's header, so it is folded like a campaign: the
    busiest by name, the tail as `other`, and a visit with no referrer as
    `(direct)` outside the cap -- it is the biggest row and would otherwise
    cost a real host its line.
    """
    client.get("/")
    for number in range(stats.CAMPAIGN_TOP + 5):
        client.get("/world", headers={"referer": f"https://blog-{number}.example/post?x=1"})
    stats.flush()
    named = re.findall(r'landing_referrer_visits\{referrer="([^"]+)"\}', stats.exposition())
    assert len(named) <= stats.CAMPAIGN_TOP + 2, named
    assert len(named) == len(set(named)), named
    assert stats.DIRECT in named and "other" in named
    #: Twenty-five hosts fill the cap whatever the neighbouring tests left
    #: behind, so the count is what is pinned, not one host's place in it.
    real = [one for one in named if one not in (stats.DIRECT, "other")]
    assert len(real) == stats.CAMPAIGN_TOP, real


def test_a_hostile_referrer_is_cleaned_before_it_is_stored() -> None:
    """The host out of `Referer` becomes a label, so it is cleaned like a tag.

    The row keeps the cleaned form too: the label set is bounded by the cap,
    but a quote or a newline inside a value would break the line it sits on.
    Whether this host is named or folded depends on how full the cap is by
    now; what is pinned is that nothing unclean reaches a label either way.
    """
    client.get("/gameplay", headers={"referer": 'https://Ev il"Host.example:8443/x?y=1'})
    row = visits_of("/gameplay")[0]
    assert row["referrer"] == "ev-il-host.example"
    named = re.findall(r'landing_referrer_visits\{referrer="([^"]*)"\}', stats.exposition())
    assert "ev-il-host.example" in named or "other" in named, named
    assert all(one == stats.DIRECT or not stats.SAFE.search(one) for one in named), named


def test_a_value_named_other_never_stands_beside_the_fold() -> None:
    """`Referer: http://other/` must not become a second `other` row.

    Two series with the same labels are one series to Prometheus, and it
    keeps whichever it saw first -- so a stranger's host named `other` would
    silently replace the folded tail, or be replaced by it.
    """
    for _ in range(3):
        client.get("/alpha", headers={"referer": "http://other/"})
    stats.flush()
    named = re.findall(r'landing_referrer_visits\{referrer="other"\}', stats.exposition())
    assert len(named) == 1, named
