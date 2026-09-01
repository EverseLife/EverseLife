# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The attention list names its lines instead of writing them (D-251 wave IV).

The list is the first thing somebody sees on coming back (04-notifications),
and until this wave every line of it was a Russian sentence assembled in
`world.py` with an f-string. Two things were wrong with that at once: it could
not be read in another language, and one of the four printed a **stable key**
in the middle of it -- «забрать бронь: iron_ore», because the reservation
carries `type_key` and nothing turned it into a word.

So the line travels as a key and its arguments, and the words are found at the
edge. What is checked here is exactly that seam: the payload names a message
the locale has, the ids in it stay ids on the wire, and the sentence they
become says the thing in words.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src import i18n
from src.api.commands.world import _world_summary
from src.constants import Constants
from src.engine import market, world
from src.models.market import Order, OrderSide, OrderState, Reservation, ReservationState
from src.units import money

ORE = "iron_ore"


async def _reserved(session: AsyncSession, constants: Constants) -> dict:
    """A buyer with a lot held for them, and nothing else to be told about."""
    stamp = uuid.uuid4().hex[:6]
    tier = market.tier_of(constants, 60)
    later = datetime.now(UTC) + timedelta(hours=6)
    node = await world.create_node(session, f"terra.attention.{stamp}", "Двор", area_m2=100)
    buyer = await world.create_identity(
        session, f"Покупатель-{stamp}", email=f"buyer-{stamp}@example.com", password="kirka-i-krep"
    )
    seller = await world.create_identity(
        session, f"Продавец-{stamp}", email=f"seller-{stamp}@example.com", password="kirka-i-krep"
    )
    await world.print_body(session, buyer, node)
    order = Order(
        node_id=node.id,
        identity_id=seller.id,
        side=OrderSide.SELL,
        type_key=ORE,
        tier=tier,
        price=money(10),
        amount_total=5,
        amount_left=5,
        state=OrderState.ACTIVE,
        expires_at=later,
    )
    session.add(order)
    await session.flush()
    session.add(
        Reservation(
            order_id=order.id,
            node_id=node.id,
            buyer_identity_id=buyer.id,
            seller_identity_id=seller.id,
            type_key=ORE,
            tier=tier,
            price=money(10),
            amount=5,
            deposit=money(1),
            state=ReservationState.HELD,
            expires_at=later,
        )
    )
    await session.flush()
    return {"identity_id": buyer.id}


@pytest.mark.asyncio
async def test_a_line_of_the_list_is_a_key_and_its_arguments(
    session: AsyncSession, constants: Constants
) -> None:
    """The wire carries the name of the sentence, not the sentence."""
    state = await _reserved(session, constants)
    answer = await _world_summary(state, session, {})

    lines = [line for line in answer["attention"] if line["kind"] == "reservation"]
    assert len(lines) == 1, "одна бронь -- одна строка"
    line = lines[0]
    assert line["say"] == "attention-reservation"
    #: The id travels as an id: turning it into a word is the reader's end of
    #: the pipe, and an agent (D-224) wants the key rather than the word.
    assert line["args"] == {"goods": ORE}
    assert "what" not in line, "предложение, собранное на сервере, ушло с провода"


@pytest.mark.asyncio
async def test_the_line_reads_as_words_not_as_keys(
    session: AsyncSession, constants: Constants
) -> None:
    """And the sentence it becomes has no stable key left in it.

    The regression this file exists for: the line used to read «забрать бронь:
    iron_ore», so it is not enough that the message renders -- what it renders
    to must not contain the id it was given.
    """
    state = await _reserved(session, constants)
    answer = await _world_summary(state, session, {})
    line = next(line for line in answer["attention"] if line["kind"] == "reservation")

    said = i18n.render(line["say"], line["args"], locale=i18n.DEFAULT_LOCALE)
    assert ORE not in said, f"ключ вместо слова: {said}"
    assert said != line["say"], "сообщения нет в локали: отрисовался сам ключ"


@pytest.mark.asyncio
async def test_no_line_writes_its_own_russian(session: AsyncSession, constants: Constants) -> None:
    """Every line of the list, whatever its kind, is named rather than written.

    A `what` on any of them would be a sentence built in Python again -- the
    very thing this wave took out -- and the next language would not have it.
    """
    state = await _reserved(session, constants)
    answer = await _world_summary(state, session, {})
    assert answer["attention"], "список пуст: проверять нечего"
    for line in answer["attention"]:
        assert "say" in line, f"строка без ключа: {line}"
        assert i18n.current().has(line["say"]), line["say"]


@pytest.mark.asyncio
async def test_arrived_says_where(session: AsyncSession, constants: Constants) -> None:
    """The digest's «пришли» names the destination.

    The regression: `travel.arrived` carries the node as a **column**, not in
    the payload, and the client cannot turn a node id into a word -- nodes are
    not in the renames. So the row read as a bare «пришли», and three of them
    in a column said nothing at all. The summary attaches the display name to
    the wire copy; the journal row itself must stay untouched -- a read that
    writes is the older regression (review 2026-08-23).
    """
    from src.engine import events
    from src.models.event import EventKind

    stamp = uuid.uuid4().hex[:6]
    node = await world.create_node(session, f"terra.arrived.{stamp}", "Дальний двор", area_m2=100)
    walker = await world.create_identity(
        session, f"Ходок-{stamp}", email=f"walker-{stamp}@example.com", password="kirka-i-krep"
    )
    await world.print_body(session, walker, node)
    recorded = await events.record(
        session,
        EventKind.TRAVEL_ARRIVED,
        actor_identity_id=walker.id,
        node_id=node.id,
        travel_id=str(uuid.uuid4()),
    )

    answer = await _world_summary({"identity_id": walker.id}, session, {})
    lines = [row for row in answer["happened"] if row["kind"] == EventKind.TRAVEL_ARRIVED.value]
    assert len(lines) == 1, "одно прибытие -- одна строка"
    assert lines[0]["payload"]["node"] == "Дальний двор"

    #: The wire copy was enriched; the journal row was not.
    await session.refresh(recorded)
    assert "node" not in recorded.payload, "сводка вписала имя в журнал: чтение не пишет"
