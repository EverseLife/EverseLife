# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Handing a thing over speaks in the room, and what it says (D-251 wave IV).

A transfer between two people is a fact the others standing there can see, so
the server says a line into the room itself -- the only line it ever says
there. Nothing covered it, and it had gone wrong in the quietest possible way:
the goods went into the sentence as their **stable key**, so the whole room
read «передаёт Тэрн: iron_ore».

The words are in the locale now, said once in the world's default language --
an utterance is one stored row read by everybody who was there, the same
reason the chronicle is said once rather than per reader.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.commands.things import _item_hand
from src.constants import Catalog, Constants
from src.constants.renames import display_name
from src.engine import world
from src.models.chat import ChatMessage

ORE = "iron_ore"


async def _two_in_a_room(session: AsyncSession):
    """Two bodies on one node, and ore in the first one's hands."""
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.room.{stamp}", "Двор", area_m2=200)
    giver = await world.create_identity(session, f"Даритель-{stamp}")
    taker = await world.create_identity(session, f"Тэрн-{stamp}")
    giving = await world.print_body(session, giver, node)
    taking = await world.print_body(session, taker, node)
    pocket = await world.body_container(session, giving)
    item = await world.grant_item(session, pocket, ORE, amount=10, quality=55, origin="тест")
    await session.flush()
    return giving, taking, item, taker


async def _said(session: AsyncSession) -> list[str]:
    rows = (await session.execute(select(ChatMessage))).scalars().all()
    return [row.text for row in rows]


@pytest.mark.asyncio
async def test_the_room_reads_the_name_of_the_goods_not_its_key(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The regression this file exists for.

    Not "the line renders" -- it always rendered. What it rendered was a
    stable key in the middle of a Russian sentence, and only reading the
    output catches that.
    """
    giving, taking, item, taker = await _two_in_a_room(session)

    await _item_hand(
        {"identity_id": giving.identity_id},
        session,
        {"item": str(item.id), "to": str(taking.id), "amount": 3},
    )

    lines = await _said(session)
    assert len(lines) == 1, "передача из рук в руки должна прозвучать в комнате один раз"
    line = lines[0]
    assert ORE not in line, f"ключ вместо слова: {line}"
    #: The same lookup the message's own `NAME($goods)` does, so the test
    #: cannot pass by agreeing with a broken table.
    assert display_name(ORE) in line, f"не названа вещь: {line}"
    assert taker.name in line, f"не сказано, кому передали: {line}"
    assert "×3" in line, f"не сказано, сколько: {line}"
    assert "{" not in line, f"невыведенный аргумент: {line}"


@pytest.mark.asyncio
async def test_one_thing_is_handed_over_without_a_count(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A single thing is passed without «×1»: the count is only worth saying
    when there is more than one of it."""
    giving, taking, item, _ = await _two_in_a_room(session)

    await _item_hand(
        {"identity_id": giving.identity_id},
        session,
        {"item": str(item.id), "to": str(taking.id), "amount": 1},
    )

    line = (await _said(session))[0]
    assert "×" not in line, f"счёт сказан там, где считать нечего: {line}"
