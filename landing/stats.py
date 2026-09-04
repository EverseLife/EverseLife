# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Who came to the landing, where from, and what they did there.

Our own counter, and it costs the visitor nothing: not a byte of script, not a
cookie, not one request to anybody else. The server already knows a page was
asked for -- this module writes that down. That is the whole difference from
the Google counter this replaces, which cost 855 ms of the load and was the
only cross-border request on an otherwise self-hosted page.

Four ideas hold it together.

**Bookkeeping never touches the request.** `see_page` and `see_event` do pure
arithmetic and a `put_nowait`; every database call -- opening the file, the
salt, the hash, the insert -- happens in one writer thread. Nothing in here
can make a page or a signup fail: the calls are wrapped, and a counter that
returns a 500 to a reader would be worse than no counter at all. A full queue
drops the row rather than the visit, and every kind of loss has its own number
on `/metrics`, because a counter that quietly stops counting is the one
failure nobody notices.

**SQLite is the truth, `/metrics` is a view over it.** Every visit and every
event is a row in the same database the signups live in (`LANDING_DB`, one
volume, one backup). Prometheus keeps a fortnight; the rows keep everything,
so "compare the September post with the August one" stays answerable after
Prometheus has forgotten. The totals the exposition needs are kept up to date
as the rows are written, so a scrape reads a handful of rows instead of
scanning a table that grows for years -- crawlers alone would make that scan
the slowest thing on the server.

**Labels are bounded, rows are not.** Everything in a URL is a stranger's
string, and a stranger who invents a million campaigns would otherwise invent
a million Prometheus series. So the rows keep what was written (clipped and
sanitised), while the exposition names the busiest campaigns and folds the
rest into `other`; the source becomes one of a known list of buckets, and the
medium one of a known list of mediums. Nothing a visitor types reaches a label
unfolded.

No IP address is stored for a visit. Uniques are counted by a hash of address
and browser salted with the day, so the same person is one visitor today and
an unrelated one tomorrow. The salt lives in the environment when
`LANDING_VISITOR_SALT` is set, and otherwise in the database beside the
hashes -- which means the hash defends the rows against a reader, not against
somebody holding the whole file.
"""

import contextlib
import hashlib
import os
import queue
import re
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs

DB_PATH = Path(os.environ.get("LANDING_DB", "/data/signups.db"))

#: The queue between the request and the disk. Deep enough that a burst -- a
#: post reaching the top of a feed -- is written in full, small enough that a
#: stuck writer cannot eat the process's memory.
QUEUE_MAX = 2048

#: How long an exposition is reused. Prometheus scrapes every 30 s and is the
#: only reader; anything shorter buys nothing.
CACHE_SECONDS = 20.0

#: Nothing from a URL is ever stored longer than this. A campaign name is a
#: label somebody wrote by hand, not a document.
FIELD_MAX = 64

#: How many campaigns the exposition names before folding the rest into
#: `other`. Untagged traffic is reported separately and does not eat a place:
#: it is always the biggest row and would otherwise cost a real campaign its
#: line on the graph.
CAMPAIGN_TOP = 20

#: Windows the dashboard asks for by name, in calendar days counted back from
#: today: `1d` is today, `7d` is today and the six days before it.
WINDOWS = {"1d": 1, "7d": 7, "30d": 30}

#: What a campaign, source or medium may contain once we have finished with
#: it. Everything else is dropped rather than escaped: these values become
#: Prometheus labels and SQL parameters, and the narrow set is easier to
#: defend than the wide one.
SAFE = re.compile(r"[^a-z0-9_.:+-]+")

#: Machines, by the name they give themselves. Not a defence -- a crawler that
#: lies is indistinguishable from a person -- but it keeps the honest ones out
#: of the human numbers, and they are the majority of them. The Google counter
#: never did this, which is why 44 % of its "users" spent three seconds on the
#: page.
BOTS = re.compile(
    r"bot|crawl|spider|slurp|scrape|monitor|preview|headless|curl|wget|"
    r"python-requests|httpx|go-http-client|java/|okhttp|libwww|"
    r"facebookexternalhit|telegrambot|discordbot|whatsapp|skypeuripreview|"
    r"semrush|ahrefs|mj12|dotbot|petal|dataforseo|gptbot|ccbot|claudebot|"
    r"perplexity|bytespider|applebot|duckduck",
    re.IGNORECASE,
)

#: Hosts whose visits are search, not a link somebody placed.
SEARCH_HOSTS = (
    "google.",
    "yandex.",
    "bing.",
    "duckduckgo.",
    "search.marginalia",
    "ecosia.",
    "brave.",
    "mail.ru",
    "rambler.",
)

#: Hosts where a link is somebody talking rather than a page citing. `social`
#: separates "a person shared us" from "a site links us", which is the
#: distinction that decides where the next post goes.
SOCIAL_HOSTS = (
    "t.me",
    "telegram.",
    "vk.com",
    "discord.",
    "reddit.",
    "x.com",
    "twitter.",
    "youtube.",
    "youtu.be",
    "mastodon",
    "bsky.",
)

#: Referrer host -> the name the dashboard shows. A bucket, not a rename: the
#: row keeps the full host, this only decides which line of the graph the
#: visit lands on. Longest match wins, so `news.dtf.ru` does not become
#: `other` because of the subdomain.
SOURCE_BUCKETS = {
    "dtf.ru": "dtf",
    "habr.com": "habr",
    "google.": "google",
    "yandex.": "yandex",
    "bing.": "bing",
    "duckduckgo.": "duckduckgo",
    "t.me": "telegram",
    "telegram.": "telegram",
    "vk.com": "vk",
    "discord.": "discord",
    "reddit.": "reddit",
    "youtube.": "youtube",
    "youtu.be": "youtube",
    "x.com": "x",
    "twitter.": "x",
    "goha.ru": "goha",
    "itch.io": "itch",
}

DIRECT = "(direct)"
NONE = "(none)"

#: The names a bucket can have, so `utm_source=dtf` is recognised as the same
#: thing a referrer from `dtf.ru` is.
_BUCKET_NAMES = frozenset(SOURCE_BUCKETS.values())

#: Longest first, once, instead of sorting the table on every visit.
_BUCKETS_BY_LENGTH = tuple(sorted(SOURCE_BUCKETS.items(), key=lambda pair: -len(pair[0])))

#: The mediums a label may carry. `utm_medium` is written by whoever wrote the
#: link -- ours or a stranger's -- so it is folded into this list before it
#: becomes a label; the row keeps whatever arrived. Without the fold one
#: script with a million mediums is a million series in Prometheus, and since
#: the visit counters cover all of history those series would never age out.
MEDIUMS = frozenset(
    {NONE, "organic", "social", "referral", "cpc", "email", "affiliate", "unknown"}
)

_queue: queue.Queue = queue.Queue(maxsize=QUEUE_MAX)
_writer: threading.Thread | None = None
_writer_lock = threading.Lock()
_salt: str | None = None
_cache: tuple[float, str] | None = None
_cache_lock = threading.Lock()

#: Everything that can go wrong on the way to a row, counted separately and
#: reported next to the numbers it distorts: the queue was full, the write
#: failed, or `see_page` itself raised. A counter that loses rows silently is
#: worse than no counter, so none of these three is allowed to be invisible.
_counts_lock = threading.Lock()
_dropped = 0
_write_failures = 0
_record_errors = 0


def _bump(name: str) -> None:
    global _dropped, _write_failures, _record_errors
    with _counts_lock:
        if name == "dropped":
            _dropped += 1
        elif name == "write":
            _write_failures += 1
        else:
            _record_errors += 1


def connect() -> sqlite3.Connection:
    """A connection with the tables in place.

    WAL because there are now two kinds of user: one writer thread and a
    reader that arrives every thirty seconds. In the default journal mode the
    reader and the writer lock each other out, and a scrape would occasionally
    take a page view down with it.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS visits (
            id INTEGER PRIMARY KEY,
            at TEXT NOT NULL,
            day TEXT NOT NULL,
            path TEXT NOT NULL,
            lang TEXT NOT NULL,
            source TEXT NOT NULL,
            medium TEXT NOT NULL,
            campaign TEXT NOT NULL,
            content TEXT NOT NULL,
            term TEXT NOT NULL,
            referrer TEXT NOT NULL,
            device TEXT NOT NULL,
            bot INTEGER NOT NULL,
            visitor TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS visits_day ON visits (day);
        -- Every windowed query asks for people, and people are `bot = 0`:
        -- with the flag in the middle the index answers the whole question
        -- instead of handing back rows to filter.
        CREATE INDEX IF NOT EXISTS visits_people ON visits (day, bot, visitor);
        DROP INDEX IF EXISTS visits_visitor;

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY,
            at TEXT NOT NULL,
            day TEXT NOT NULL,
            kind TEXT NOT NULL,
            visitor TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS events_day ON events (day, kind);

        -- The counters, kept as the rows are written. A Prometheus counter is
        -- the count over all of history, and computing that by scanning the
        -- whole table put seconds on a scrape once crawlers had grown it.
        -- Here the labels are already folded, so the table stays small.
        CREATE TABLE IF NOT EXISTS visit_totals (
            path TEXT NOT NULL,
            source TEXT NOT NULL,
            medium TEXT NOT NULL,
            device TEXT NOT NULL,
            bot INTEGER NOT NULL,
            count INTEGER NOT NULL,
            PRIMARY KEY (path, source, medium, device, bot)
        );
        CREATE TABLE IF NOT EXISTS event_totals (
            kind TEXT PRIMARY KEY,
            count INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    return conn


def _resolve_salt(conn: sqlite3.Connection) -> str:
    """The secret behind the visitor hash, read once by the writer thread.

    From the environment when it is set, otherwise generated once and kept in
    the database: the hash has to survive a restart, or every deploy would
    invent a new crowd of "unique" visitors out of the same people.
    """
    global _salt
    if _salt is not None:
        return _salt
    given = os.environ.get("LANDING_VISITOR_SALT", "").strip()
    if given:
        _salt = given
        return _salt
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES ('visitor_salt', ?)",
            (os.urandom(16).hex(),),
        )
        row = conn.execute("SELECT value FROM meta WHERE key = 'visitor_salt'").fetchone()
    _salt = row[0]
    return _salt


def visitor(ip: str, agent: str, day: str, secret: str | None = None) -> str:
    """Who this is, for today only.

    Address and browser, salted with the day: enough to tell two people apart
    within a day, useless for following one person across days, and not
    reversible into an address by anyone who has only the rows.
    """
    if secret is None:
        conn = connect()
        try:
            secret = _resolve_salt(conn)
        finally:
            conn.close()
    return hashlib.sha256(f"{secret}|{day}|{ip}|{agent}".encode()).hexdigest()[:16]


def _clean(value: str) -> str:
    return SAFE.sub("-", value.strip().lower())[:FIELD_MAX].strip("-")


def _host(referrer: str) -> str:
    """The host a referrer names, without scheme, port, path or `www.`."""
    if not referrer:
        return ""
    rest = referrer.split("://", 1)[-1]
    host = rest.split("/", 1)[0].split("?", 1)[0].split(":", 1)[0].lower()
    return host.removeprefix("www.")


def _bucket(host: str) -> str:
    """Which line of the graph a source belongs on.

    Takes either a host (`dtf.ru`, from a referrer) or a name somebody already
    wrote on the link (`utm_source=dtf`) -- the second is checked first, so a
    tag we published ourselves does not fall into `other` for want of a
    domain suffix.
    """
    if not host:
        return DIRECT
    if host in _BUCKET_NAMES or host == DIRECT:
        return host
    for known, name in _BUCKETS_BY_LENGTH:
        if known in host:
            return name
    return "other"


def _fold_medium(medium: str) -> str:
    return medium if medium in MEDIUMS else "other"


def _device(agent: str) -> str:
    lowered = agent.lower()
    if "ipad" in lowered or "tablet" in lowered:
        return "tablet"
    if "mobi" in lowered or "android" in lowered or "iphone" in lowered:
        return "mobile"
    return "desktop"


def where_from(referrer: str, query: str, own_host: str = "") -> dict[str, str]:
    """Source, medium and campaign for one visit.

    The UTM tags win when they are there, because they are what we wrote on
    the link ourselves and they say what the referrer cannot: which post, on a
    site that has several. Without them the referrer decides, and a referrer
    from our own domain is not a source at all -- that is a reader walking
    from one page to the next.
    """
    tags = parse_qs(query or "")
    utm = {
        name: _clean(tags.get(f"utm_{name}", [""])[0])
        for name in ("source", "medium", "campaign", "content", "term")
    }
    #: Cleaned once, for both fields that carry it: the host becomes a label
    #: on `/metrics`, and a `Referer` header is a stranger's string like the
    #: rest of the URL.
    host = _host(referrer)
    if host and own_host and host == own_host.lower():
        host = ""
    host = _clean(host)

    if utm["source"]:
        source, medium = utm["source"], utm["medium"] or "unknown"
    elif host:
        source = host
        if any(one in host for one in SEARCH_HOSTS):
            medium = "organic"
        elif any(one in host for one in SOCIAL_HOSTS):
            medium = "social"
        else:
            medium = "referral"
    else:
        source, medium = DIRECT, NONE

    return {
        "source": source,
        "medium": utm["medium"] or medium,
        "campaign": utm["campaign"] or NONE,
        "content": utm["content"] or NONE,
        "term": utm["term"] or NONE,
        "referrer": host,
    }


def _start_writer() -> None:
    """One thread, started on the first row rather than at import.

    At import would mean a thread in `lastmod.py` and `indexnow.py` too --
    both import the app for its page table and neither serves anybody.
    """
    global _writer
    with _writer_lock:
        if _writer is not None and _writer.is_alive():
            return
        _writer = threading.Thread(target=_drain, name="landing-stats", daemon=True)
        _writer.start()


def _backfill(conn: sqlite3.Connection) -> None:
    """Totals from the rows, once, for a database written before they existed."""
    if conn.execute("SELECT 1 FROM visit_totals LIMIT 1").fetchone():
        return
    #: The rows keep the source and the medium as they arrived; the totals
    #: keep them folded, so the fold happens on the way across -- otherwise
    #: the labels it exists to bound would walk in through the back door.
    rows = conn.execute(
        "SELECT path, source, medium, device, bot, count(*) FROM visits"
        " GROUP BY path, source, medium, device, bot"
    ).fetchall()
    with conn:
        for path, source, medium, device, bot, count in rows:
            _add_total(conn, path, _bucket(source), _fold_medium(medium), device, bot, count)
        conn.execute(
            "INSERT OR REPLACE INTO event_totals (kind, count)"
            " SELECT kind, count(*) FROM events GROUP BY kind"
        )


def _add_total(
    conn: sqlite3.Connection, path: str, source: str, medium: str, device: str, bot: int, by: int
) -> None:
    conn.execute(
        "INSERT INTO visit_totals (path, source, medium, device, bot, count)"
        " VALUES (?, ?, ?, ?, ?, ?)"
        " ON CONFLICT (path, source, medium, device, bot)"
        " DO UPDATE SET count = count + excluded.count",
        (path, source, medium, device, bot, by),
    )


def _write(conn: sqlite3.Connection, secret: str, item: tuple) -> None:
    kind, fields = item
    if kind == "visit":
        now, day, path, lang, came, device, bot, ip, agent = fields
        conn.execute(
            "INSERT INTO visits (at, day, path, lang, source, medium, campaign, content,"
            " term, referrer, device, bot, visitor)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                now,
                day,
                path,
                lang,
                came["source"],
                came["medium"],
                came["campaign"],
                came["content"],
                came["term"],
                came["referrer"],
                device,
                bot,
                visitor(ip, agent, day, secret),
            ),
        )
        _add_total(
            conn,
            path,
            _bucket(came["source"]),
            _fold_medium(came["medium"]),
            device,
            bot,
            1,
        )
    else:
        now, day, what, ip, agent = fields
        conn.execute(
            "INSERT INTO events (at, day, kind, visitor) VALUES (?, ?, ?, ?)",
            (now, day, what, visitor(ip, agent, day, secret)),
        )
        conn.execute(
            "INSERT INTO event_totals (kind, count) VALUES (?, 1)"
            " ON CONFLICT (kind) DO UPDATE SET count = count + 1",
            (what,),
        )


def _drain() -> None:
    conn = connect()
    secret = _resolve_salt(conn)
    _backfill(conn)
    while True:
        first = _queue.get()
        batch = [first]
        #: Whatever else is already waiting goes in the same transaction: a
        #: burst costs one commit instead of one per visitor.
        while len(batch) < 256:
            try:
                batch.append(_queue.get_nowait())
            except queue.Empty:
                break
        try:
            with conn:
                for item in batch:
                    _write(conn, secret, item)
        except Exception:
            #: The batch is lost, and that is said out loud on `/metrics`
            #: rather than swallowed. The connection is replaced because the
            #: usual reason for this is the file going away underneath us --
            #: keeping the old handle would lose every batch from now on.
            for _ in batch:
                _bump("write")
            with contextlib.suppress(sqlite3.Error):
                conn.close()
            try:
                conn = connect()
            except Exception:
                #: Nowhere to write to at all. Wait rather than spin: the disk
                #: may come back, and the queue drops rows meanwhile.
                time.sleep(5.0)
        finally:
            for _ in batch:
                _queue.task_done()


def _enqueue(item: tuple) -> None:
    _start_writer()
    try:
        _queue.put_nowait(item)
    except queue.Full:
        _bump("dropped")


def see_page(
    path: str,
    lang: str,
    ip: str,
    agent: str,
    referrer: str,
    query: str,
    own_host: str = "",
) -> None:
    """Write down one page view. Returns before anything touches the disk.

    Nothing in here may raise: this runs inside the page handler, and a
    counter that turns a reader's page into a 500 has cost more than it could
    ever be worth.
    """
    try:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        _enqueue(
            (
                "visit",
                (
                    now,
                    now[:10],
                    path,
                    lang,
                    where_from(referrer, query, own_host=own_host),
                    _device(agent),
                    1 if (not agent or BOTS.search(agent)) else 0,
                    ip,
                    agent,
                ),
            )
        )
    except Exception:
        _bump("record")


def see_event(kind: str, ip: str, agent: str) -> None:
    """Write down something that is not a page view: a signup, a click out.

    The visitor hash is the same one the page views carry, so "which campaign
    produced this signup" is a join on the day and the hash -- no cookie, no
    identifier travelling with the visitor, nothing for the client to send.

    A machine is not recorded at all, rather than recorded and labelled: a
    visit by a crawler is still a fact about the page, but a crawler does not
    accept an invitation or leave an address, so its "event" would be noise in
    the one table that is supposed to count people.
    """
    try:
        if not agent or BOTS.search(agent):
            return
        now = datetime.now(UTC).isoformat(timespec="seconds")
        _enqueue(("event", (now, now[:10], kind, ip, agent)))
    except Exception:
        _bump("record")


def flush(timeout: float = 5.0) -> None:
    """Wait for the queue to drain. For tests, and for nothing else."""
    _start_writer()
    deadline = time.monotonic() + timeout
    while not _queue.empty() and time.monotonic() < deadline:
        time.sleep(0.01)
    #: `Queue.join()` has no timeout of its own, and a writer that has died
    #: would hang the caller for good; the unfinished count is the same thing
    #: it waits on.
    while _queue.unfinished_tasks and time.monotonic() < deadline:
        time.sleep(0.01)


def _label(value: str) -> str:
    return value.replace("\\", "").replace('"', "").replace("\n", "")


def _line(name: str, labels: dict[str, str], value: float) -> str:
    #: Plain integers: `%g` turns 1234567 into `1.23457e+06`, and a counter
    #: that stops moving in its sixth digit is a counter that lies.
    shown = f"{value:.0f}"
    if not labels:
        return f"{name} {shown}"
    inside = ",".join(f'{key}="{_label(str(one))}"' for key, one in labels.items())
    return f"{name}{{{inside}}} {shown}"


def _since(days: int) -> str:
    """The first day of a window of `days` calendar days ending today."""
    return (datetime.now(UTC).date() - timedelta(days=days - 1)).isoformat()


def _people(conn: sqlite3.Connection, since: str, extra: str = "", args: tuple = ()) -> int:
    (count,) = conn.execute(
        "SELECT count(DISTINCT visitor) FROM visits WHERE bot = 0 AND day >= ?" + extra,
        (since, *args),
    ).fetchone()
    return count


def exposition() -> str:
    """Everything Prometheus scrapes.

    The counters come from the totals kept alongside the rows, so they are
    honestly monotonic, survive a restart, and cost the same whether the table
    holds a thousand rows or ten million. The windowed numbers are the only
    ones that read `visits`, and they read at most a month of it.
    """
    conn = connect()
    try:
        out: list[str] = []

        out.append("# HELP landing_visits_total Page views, by page and where they came from.")
        out.append("# TYPE landing_visits_total counter")
        for path, source, medium, bot, count in conn.execute(
            "SELECT path, source, medium, bot, sum(count) FROM visit_totals"
            " GROUP BY path, source, medium, bot ORDER BY path, source, medium, bot"
        ):
            out.append(
                _line(
                    "landing_visits_total",
                    {
                        "path": path,
                        "source": source,
                        "medium": medium,
                        "bot": "yes" if bot else "no",
                    },
                    count,
                )
            )

        out.append("# HELP landing_visits_by_device_total Page views by the kind of screen.")
        out.append("# TYPE landing_visits_by_device_total counter")
        for device, count in conn.execute(
            "SELECT device, sum(count) FROM visit_totals WHERE bot = 0"
            " GROUP BY device ORDER BY device"
        ):
            out.append(_line("landing_visits_by_device_total", {"device": device}, count))

        out.append("# HELP landing_unique_visitors People seen in a window, bots excluded.")
        out.append("# TYPE landing_unique_visitors gauge")
        for window, days in WINDOWS.items():
            out.append(
                _line("landing_unique_visitors", {"window": window}, _people(conn, _since(days)))
            )

        out.append("# HELP landing_campaign_visits Visits over 30 days by utm_campaign.")
        out.append("# TYPE landing_campaign_visits gauge")
        out.extend(_top(conn, "campaign", "landing_campaign_visits"))

        out.append("# HELP landing_content_visits Visits over 30 days by utm_content.")
        out.append("# TYPE landing_content_visits gauge")
        out.extend(_top(conn, "content", "landing_content_visits"))

        #: The source graph draws buckets -- `dtf`, `other` -- so that a
        #: stranger cannot invent a line. The hosts themselves are here, by
        #: the same top-and-fold rule: `other` on the graph is answered by
        #: name in this table.
        out.append("# HELP landing_referrer_visits Visits over 30 days by the referring host.")
        out.append("# TYPE landing_referrer_visits gauge")
        out.extend(_top(conn, "referrer", "landing_referrer_visits", blank=DIRECT))

        out.append("# HELP landing_events_total Things that are not a page view.")
        out.append("# TYPE landing_events_total counter")
        for kind, count in conn.execute(
            "SELECT kind, count FROM event_totals ORDER BY kind"
        ):
            out.append(_line("landing_events_total", {"kind": kind}, count))

        #: The funnel in one metric, so a single panel can show all of it: how
        #: many people came, how many reached the page that explains the
        #: alpha, how many went for the invite, how many left an address.
        out.append("# HELP landing_funnel People at each step of the funnel, by window.")
        out.append("# TYPE landing_funnel gauge")
        for window, days in WINDOWS.items():
            since = _since(days)
            steps = {
                "visit": _people(conn, since),
                "alpha": _people(conn, since, " AND path IN ('/alpha', '/en/alpha')"),
            }
            for kind in ("discord_click", "signup"):
                (count,) = conn.execute(
                    "SELECT count(DISTINCT visitor) FROM events WHERE day >= ? AND kind = ?",
                    (since, kind),
                ).fetchone()
                steps[kind] = count
            for step, count in steps.items():
                out.append(_line("landing_funnel", {"step": step, "window": window}, count))

        out.append("# HELP landing_signups_total Addresses waiting for an invitation.")
        out.append("# TYPE landing_signups_total gauge")
        try:
            (total,) = conn.execute("SELECT count(*) FROM signups").fetchone()
        except sqlite3.Error:
            total = 0
        out.append(_line("landing_signups_total", {}, total))

        #: Three ways a visit fails to become a row, each with its own number.
        #: Any of them above zero means everything above is an undercount, and
        #: that is worth more than the numbers themselves.
        out.append("# HELP landing_rows_lost_total Visits that never became a row.")
        out.append("# TYPE landing_rows_lost_total counter")
        with _counts_lock:
            losses = {"queue_full": _dropped, "write": _write_failures, "record": _record_errors}
        for why, count in losses.items():
            out.append(_line("landing_rows_lost_total", {"why": why}, count))

        return "\n".join(out) + "\n"
    finally:
        conn.close()


def _top(conn: sqlite3.Connection, column: str, name: str, blank: str = NONE) -> list[str]:
    """The busiest values of one tag over 30 days, with the tail folded away.

    Untagged traffic -- `(none)` for a tag, an empty host for a referrer -- is
    reported as `blank`, outside the cap: it is always the largest row, and
    letting it take a place would cost a real campaign its line. A value that
    is literally `other` goes into the fold whatever its rank: named, it
    would be a second row under the fold's own label, and Prometheus keeps
    one of two equal series and drops the other. The order breaks ties by
    name so a scrape does not reshuffle equal rows.
    """
    rows = conn.execute(
        f"SELECT {column}, count(*) FROM visits WHERE bot = 0 AND day >= ?"  # noqa: S608
        f" GROUP BY {column} ORDER BY count(*) DESC, {column} ASC",
        (_since(30),),
    ).fetchall()
    lines, rest, shown = [], 0, 0
    for value, count in rows:
        if value in (NONE, ""):
            lines.append(_line(name, {column: blank}, count))
        elif shown < CAMPAIGN_TOP and value != "other":
            lines.append(_line(name, {column: value}, count))
            shown += 1
        else:
            rest += count
    if rest:
        lines.append(_line(name, {column: "other"}, rest))
    return lines


def metrics() -> str:
    """The exposition, at most one query every `CACHE_SECONDS`."""
    global _cache
    with _cache_lock:
        now = time.monotonic()
        if _cache and now - _cache[0] < CACHE_SECONDS:
            return _cache[1]
        text = exposition()
        _cache = (now, text)
        return text
