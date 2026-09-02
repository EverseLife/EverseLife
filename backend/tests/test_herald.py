# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The herald: what the world announces aloud -- and what it never announces.

Checked above all is the boundary, not delivery. Broken delivery is seen at
once and fixed in an evening; a broken boundary is discovered the day
somebody's wallet goes into the common channel, and it cannot be put back together.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src import herald, i18n
from src.constants import Catalog
from src.engine import events, jobs, world
from src.engine.errors import Says
from src.herald import chronicle, webhook
from src.herald.job import run_once
from src.models.event import EventKind
from src.models.job import Job, JobKind, JobState
from src.models.world import Layer
from src.runtime import DISCORD_CONTENT_LIMIT

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
WEBHOOK = "https://discord.invalid/api/webhooks/тест"

#: What never goes out under any settings: money, things, body, conversations,
#: knowledge and reports. The list is deliberately redundant -- it is the contract.
PRIVATE = {
    EventKind.LEDGER_POSTED,
    EventKind.TRADE_EXECUTED,
    EventKind.ITEM_MOVED,
    EventKind.ITEM_CREATED,
    EventKind.MINING_SWING,
    EventKind.BODY_DIED,
    EventKind.BODY_PRINTED,
    EventKind.LOAN_TAKEN,
    EventKind.DEBT_WITHHELD,
    EventKind.KNOWLEDGE_LEARNED,
    EventKind.REPORT_FILED,
    EventKind.EXPLORE_STARTED,
    EventKind.TRAVEL_ARRIVED,
    EventKind.STORAGE_PUT,
}


async def _node(session: AsyncSession, name: str = "Медный склон"):
    stamp = uuid.uuid4().hex[:8]
    planet = await world.create_node(
        session, f"terra.{stamp}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    return await world.create_node(session, f"terra.{stamp}.core", name, area_m2=100, parent=planet)


# --- boundary ----------------------------------------------------------------


def test_private_does_not_go_out() -> None:
    leak = chronicle.PUBLIC & {str(kind) for kind in PRIVATE}
    assert not leak, (
        "эти события личные: деньги, вещи, тело и разговоры остаются в игре. "
        f"Наружу собрались: {sorted(leak)}"
    )


def test_list_is_allowlist() -> None:
    """An event kind with no line written for it is silent by itself."""
    assert {str(kind) for kind in chronicle.LINES} == chronicle.PUBLIC
    assert str(EventKind.TICK_RAN) not in chronicle.PUBLIC


def test_mentions_disabled() -> None:
    """A city can be called `@everyone` -- and that must not ping the server."""
    body = webhook.payload("основан город @everyone")
    assert body["allowed_mentions"] == {"parse": []}


def test_markup_in_foreign_name_neutralised() -> None:
    line = chronicle.plain("**@everyone** `код`")
    assert "**@" not in line
    assert line.count("\\") >= 4


def test_long_feed_cut_by_discord_limit() -> None:
    lines = ["ю" * 900] * 5
    pieces = webhook.chunks(lines)
    assert len(pieces) > 1, "пять таких строк в одно сообщение не влезают"
    assert all(len(piece) <= DISCORD_CONTENT_LIMIT for piece in pieces)
    assert sum(piece.count("ю") for piece in pieces) == 4500, "ни одна строка не потеряна"


# --- chronicle lines ---------------------------------------------------------


async def test_city_founding_names_city_and_place(session: AsyncSession, catalog: Catalog) -> None:
    node = await _node(session)
    who = await world.create_identity(session, f"Ким-{uuid.uuid4().hex[:6]}")
    event = await events.record(
        session,
        EventKind.CITY_FOUNDED,
        actor_identity_id=who.id,
        node_id=node.id,
        city_id=str(uuid.uuid4()),
        name="Рудный",
        founded_by_player=True,
    )

    lines = await chronicle.compose(session, [event])

    assert len(lines) == 1
    assert "Рудный" in lines[0]
    assert "Медный склон" in lines[0]
    assert who.name in lines[0]


async def test_exploration_find_does_not_name_species(
    session: AsyncSession, catalog: Catalog
) -> None:
    """The vein's species is the scout's pay for risk, not news for everyone."""
    node = await _node(session, "Пойма")
    who = await world.create_identity(session, f"Вей-{uuid.uuid4().hex[:6]}")
    event = await events.record(
        session,
        EventKind.EXPLORE_FOUND,
        actor_identity_id=who.id,
        node_id=node.id,
        from_node="terra.city",
        found="terra.wild",
        name="Пойма",
        resource="медь",
        minutes=12,
    )

    line = (await chronicle.compose(session, [event]))[0]

    assert "Пойма" in line
    assert "медь" not in line


async def test_silent_event_gives_no_line(session: AsyncSession, catalog: Catalog) -> None:
    event = await events.record(session, EventKind.TICK_RAN, kind_of_tick="world")
    assert await chronicle.compose(session, [event]) == []


async def test_a_rate_that_did_not_move_is_not_told(
    session: AsyncSession, catalog: Catalog
) -> None:
    """The rate is reviewed on a period of its own (D-167), and most reviews
    change nothing: the same inflation gives the same number.

    Announced anyway, those filled the chronicle -- and Discord -- with a line
    saying "the rate is twelve, and it was twelve", over and over. The journal
    keeps every review; the chronicle is for what is worth telling.
    """
    same = await events.record(
        session, EventKind.RATE_DECIDED, rate=12, was=12, why="сенсоры молчат"
    )
    assert await chronicle.compose(session, [same]) == []

    moved = await events.record(session, EventKind.RATE_DECIDED, rate=14, was=12, why="инфляция")
    told = await chronicle.compose(session, [moved])
    assert told and "14" in told[0] and "было 12" in told[0]

    #: A council took its decision by hand, and that is news whether the number
    #: moved or not: people did it (D-172).
    council = await events.record(
        session, EventKind.RATE_DECIDED, rate=14, was=14, by_council=True, city="Рудный"
    )
    assert await chronicle.compose(session, [council])


async def test_the_rate_is_announced_with_its_reasons(
    session: AsyncSession, catalog: Catalog
) -> None:
    """The rate goes out together with the explanation -- that is D-030.

    The reasons are stored as keys and numbers now (D-251 wave IV), and this
    line is what broke silently when they were: the bank started writing
    `why_said` and the chronicle went on reading `why`, so the announcement
    kept coming out with the explanation cut off. Nothing failed -- the line
    was simply shorter.
    """
    moved = await events.record(
        session,
        EventKind.RATE_DECIDED,
        rate=14,
        was=12,
        why_said=i18n.written([Says("bank-why-rate-base", {"rate": 12.0})]),
    )
    told = await chronicle.compose(session, [moved])
    assert told, "ставка сдвинулась и не объявлена"
    said = i18n.render("bank-why-rate-base", {"rate": 12.0}, locale=i18n.DEFAULT_LOCALE)
    assert said in told[0], f"объяснение потерялось: {told[0]}"


async def test_an_old_decision_still_says_why(session: AsyncSession, catalog: Catalog) -> None:
    """A row written before the keys existed keeps its one Russian line.

    An announcement with no reason in it would be worse than an old one in the
    wrong language, so the text field is read when there are no keys to say.
    """
    old = await events.record(session, EventKind.RATE_DECIDED, rate=14, was=12, why="инфляция")
    told = await chronicle.compose(session, [old])
    assert told and "инфляция" in told[0]


async def test_every_line_the_chronicle_may_write_says_something(
    session: AsyncSession, catalog: Catalog
) -> None:
    """No builder may name a message the locale does not have.

    The words moved out of f-strings into the locale (D-251), and a key with
    no message renders as the key itself -- which in this file means the key
    goes to Discord. Nothing else covers the law, the charter, the vote, the
    council seat or the court: they are checked here as a family, so a line
    added without its message fails rather than posting `chronicle-whatever`.
    """
    told = []
    for kind, payload in (
        (EventKind.CITY_LAW_SET, {"law": "налог", "was": "5", "now": "7"}),
        (EventKind.CITY_CHARTER_SET, {"question": "кто правит", "option": "совет"}),
        (EventKind.VOTE_CLOSED, {"passed": True, "yes": 3, "no": 1, "electorate": 5}),
        #: And a vote nobody was for. A zero is a count like any other, and
        #: `plain()` used to turn it into an empty string -- «(за , против 9…)».
        (EventKind.VOTE_CLOSED, {"passed": False, "yes": 0, "no": 9, "electorate": 9}),
        (EventKind.COUNCIL_SEATED, {"who": "Тэрн"}),
        (EventKind.CASE_JUDGED, {"verdict": "виновен", "sanction": "штраф"}),
        #: And the same court with neither half: the two clauses are optional
        #: and a missing one must leave a sentence, not a hole.
        (EventKind.CASE_JUDGED, {}),
    ):
        event = await events.record(session, kind, **payload)
        told.extend(await chronicle.compose(session, [event]))

    assert len(told) == 7, "какая-то строка не написалась"
    for line in told:
        assert "chronicle-" not in line, f"ключ вместо строки: {line}"
        assert "{" not in line and "}" not in line, f"невыведенный аргумент: {line}"
        assert "None" not in line, f"пустое значение просочилось в строку: {line}"
    assert "за 0" in told[3], f"ноль голосов пропал из счёта: {told[3]}"


async def test_a_changed_law_is_named_rather_than_keyed(
    session: AsyncSession, catalog: Catalog
) -> None:
    """The channel is told which law moved, in words, and from what to what.

    The payload carries the D-251 id and the line used to print it raw --
    «код-закон «tax_trade» — было —, стало 1» -- naming nothing and hiding
    the rule that had been in force all along. The name comes from the vault's
    table (`LAW()`), and both values are the rule on either side of the change.
    """
    event = await events.record(session, EventKind.CITY_LAW_SET, law="tax_trade", was="3", now="1")
    line = (await chronicle.compose(session, [event]))[0]
    assert "tax_trade" not in line, f"the key reached the channel: {line}"
    assert "Налог с продажи" in line, f"the law is not named: {line}"
    assert "было 3, стало 1" in line, f"the change is not said: {line}"


async def test_a_name_with_discord_markup_in_it_stays_text(
    session: AsyncSession, catalog: Catalog
) -> None:
    """A city is named by a player, and its name reaches the channel.

    The rate line quotes its reasons, and one of them names the city whose
    council decided. Those reasons are rendered from the locale now, so the
    escaping had to move with them -- it is the stored **arguments** that go
    through `plain()`, not the finished phrase, because a clause carries the
    punctuation of its own language.
    """
    loud = await events.record(
        session,
        EventKind.RATE_DECIDED,
        rate=14,
        was=12,
        why_said=i18n.written([Says("bank-why-council", {"city": "**@everyone**", "advised": 12})]),
    )
    line = (await chronicle.compose(session, [loud]))[0]
    assert "**@everyone**" not in line, f"разметка Discord ушла в канал: {line}"
    assert "@everyone" in line, "имя города потерялось вместе с разметкой"


# --- feed pass ---------------------------------------------------------------


async def test_first_pass_does_not_resend_history(session: AsyncSession, catalog: Catalog) -> None:
    node = await _node(session)
    event = await events.record(session, EventKind.CITY_FOUNDED, node_id=node.id, name="Рудный")

    sent: list[str] = []

    async def _sends(url: str, text: str) -> None:
        sent.append(text)

    boundary = await run_once(session, after=None, url=WEBHOOK, sender=_sends)

    assert sent == [], "лента начинается сейчас, а не с сотворения мира"
    assert boundary == event.id


async def test_pass_sends_and_moves_cursor(session: AsyncSession, catalog: Catalog) -> None:
    node = await _node(session)
    event = await events.record(session, EventKind.CITY_FOUNDED, node_id=node.id, name="Рудный")

    sent: list[str] = []

    async def _sends(url: str, text: str) -> None:
        sent.append(text)

    boundary = await run_once(session, after=0, url=WEBHOOK, sender=_sends)

    assert len(sent) == 1
    assert "Рудный" in sent[0]
    assert boundary == event.id

    #: A second pass with the new cursor is silent: the same event does not go twice.
    sent.clear()
    assert await run_once(session, after=boundary, url=WEBHOOK, sender=_sends) == boundary
    assert sent == []


async def test_cursor_moves_even_without_webhook(session: AsyncSession, catalog: Catalog) -> None:
    """Otherwise a herald switched on a month later would start with a month's tail."""
    node = await _node(session)
    event = await events.record(session, EventKind.CITY_FOUNDED, node_id=node.id, name="Рудный")

    assert await run_once(session, after=0, url="") == event.id


async def test_herald_queues_next_link(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as session, session.begin():
        await herald.ensure_scheduled(session, NOW)

    done = await jobs.run_one(factory, now=NOW)
    assert done is not None
    assert done.state is JobState.DONE

    async with factory() as session:
        costs = (
            (
                await session.execute(
                    select(Job).where(
                        Job.state == JobState.PENDING, Job.kind == JobKind.HERALD_POST
                    )
                )
            )
            .scalars()
            .all()
        )

    assert len(costs) == 1, "лента обязана продолжаться сама"
    assert costs[0].run_at > NOW
    assert costs[0].payload.get("after") is not None, "курсор едет в задании"
