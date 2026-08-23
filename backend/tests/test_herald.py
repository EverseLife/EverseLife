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

from src import herald
from src.constants import Catalog
from src.engine import events, jobs, world
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
