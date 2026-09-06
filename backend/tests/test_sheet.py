# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The map as a thing (D-319 item 6, OQ-145): memory forgets, a map keeps.

Drawn from the drawer's memory at the moment of drawing; carried, it shows
its places in the tone of memory with the day of that moment; read, it puts
nothing back into memory; gone, its places go with it; on the counter it
is never laid out.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src import globe
from src.constants import Constants, current
from src.constants import registry as R
from src.engine import climate, mapshot, memory, places, sheet, travel, world
from src.engine.market import counter
from src.models.city import City
from src.models.event import Event, EventKind
from src.models.identity import Body
from src.models.inventory import Item
from src.models.sheet import MapSheet
from src.models.world import Layer, Node, Planet, Surface
from src.units import METRES_PER_KM

HOME = (41.0, 24.0)


def _pin(point: globe.Geo) -> dict:
    return {places.PLACE: {places.PLACE_LAT: point[0], places.PLACE_LON: point[1]}}


async def _world(session: AsyncSession, constants: Constants) -> tuple[Node, Node, Node]:
    stamp = uuid.uuid4().hex[:6]
    terra = await world.create_node(
        session, f"terra.{stamp}", "Terra", area_m2=1, planet=Planet.TERRA, layer=Layer.SPACE
    )
    radius = globe.radius_m(constants, Planet.TERRA)
    far_off = globe.offset(radius, HOME, 0.0, 40 * METRES_PER_KM)
    home = await world.create_node(
        session, f"terra.home.{stamp}", "Home", area_m2=100, parent=terra, properties=_pin(HOME)
    )
    far = await world.create_node(
        session, f"terra.far.{stamp}", "Far", area_m2=100, parent=terra, properties=_pin(far_off)
    )
    await travel.connect(session, home, far, base_seconds=600, surface=Surface.WILD)
    return terra, home, far


async def _drawer(session: AsyncSession, home: Node) -> Body:
    identity = await world.create_identity(session, f"Drawer-{uuid.uuid4().hex[:6]}")
    return await world.print_body(session, identity, home)


async def _blank(session: AsyncSession, body: Body) -> Item:
    pocket = await world.body_container(session, body)
    return await world.grant_item(session, pocket, sheet.SHEET, amount=1, origin="test")


async def _forget(session: AsyncSession, body: Body) -> None:
    await session.execute(
        delete(memory.Knowledge).where(memory.Knowledge.identity_id == body.identity_id)
    )


async def test_a_sheet_is_drawn_from_memory_and_shows_its_places_from_the_pocket(
    session: AsyncSession, constants: Constants
) -> None:
    _, home, far = await _world(session, constants)
    body = await _drawer(session, home)
    now = datetime.now(UTC)
    await memory.remember(session, constants, body.identity_id, [far.key], at=now)
    blank = await _blank(session, body)
    before = float(body.stamina)
    drawn = await sheet.draw(session, constants, body, blank, now=now)
    assert far.key in drawn.places
    assert float(body.stamina) == pytest.approx(before - constants[R.MAP_DRAW_STAMINA])
    with pytest.raises(sheet.SheetError):
        await sheet.draw(session, constants, body, blank, now=now)
    told = await session.scalar(
        __import__("sqlalchemy").select(Event).where(Event.kind == EventKind.MAP_DRAWN.value)
    )
    assert told is not None and told.payload["places"] == 1

    #: Forgotten by the identity, still on the sheet: the map shows the
    #: place dark, marked with the day it was drawn -- in the planet's own
    #: calendar, counted from one as the clock counts.
    await _forget(session, body)
    answer = await mapshot.personal(session, constants, body, now)
    rows = {row["key"]: row for row in answer["nodes"]}
    epoch = await world.epoch(session)
    day = climate.day_index(constants, Planet.TERRA, epoch, now) + 1
    assert rows[far.key]["faded"] is True and rows[far.key]["drawn"] == day
    assert "drawn" not in rows[home.key], "виденное сейчас — не по карте"
    #: Reading puts nothing back: memory is as empty as it was.
    assert not await memory.known(session, body.identity_id)


async def test_a_map_does_not_age_what_is_remembered_or_public(
    session: AsyncSession, constants: Constants
) -> None:
    """The mark of the day is for what is known from the map alone."""
    _, home, far = await _world(session, constants)
    body = await _drawer(session, home)
    now = datetime.now(UTC)
    await memory.remember(session, constants, body.identity_id, [far.key], at=now)
    blank = await _blank(session, body)
    await sheet.draw(session, constants, body, blank, now=now)
    #: Remembered as well: memory speaks, the map keeps quiet.
    answer = await mapshot.personal(session, constants, body, now)
    rows = {row["key"]: row for row in answer["nodes"]}
    assert "drawn" not in rows[far.key]
    #: Public as well (a city): known to all, not "from a map".
    await _forget(session, body)
    session.add(City(node_id=far.id, name="Far"))
    await session.flush()
    answer = await mapshot.personal(session, constants, body, now)
    rows = {row["key"]: row for row in answer["nodes"]}
    assert rows[far.key]["faded"] is True and "drawn" not in rows[far.key]


async def test_the_places_go_with_the_sheet_and_the_holder_sees_them(
    session: AsyncSession, constants: Constants
) -> None:
    _, home, far = await _world(session, constants)
    body = await _drawer(session, home)
    other = await _drawer(session, home)
    now = datetime.now(UTC)
    await memory.remember(session, constants, body.identity_id, [far.key], at=now)
    blank = await _blank(session, body)
    await sheet.draw(session, constants, body, blank, now=now)
    await _forget(session, body)
    #: In another's hands the map shows its places to them, not to the drawer.
    blank.container_id = (await world.body_container(session, other)).id
    await session.flush()
    mine = {row["key"] for row in (await mapshot.personal(session, constants, body, now))["nodes"]}
    theirs = {
        row["key"] for row in (await mapshot.personal(session, constants, other, now))["nodes"]
    }
    assert far.key not in mine and far.key in theirs
    #: Destroyed, the drawing goes with the sheet.
    await session.delete(blank)
    await session.flush()
    assert await session.get(MapSheet, blank.id) is None


async def test_nothing_remembered_draws_nothing(
    session: AsyncSession, constants: Constants
) -> None:
    _, home, _ = await _world(session, constants)
    body = await _drawer(session, home)
    blank = await _blank(session, body)
    with pytest.raises(sheet.SheetError):
        await sheet.draw(session, constants, body, blank, now=datetime.now(UTC))


async def test_the_counter_lays_out_blank_sheets_and_never_a_drawn_map(
    session: AsyncSession, constants: Constants
) -> None:
    """A drawn map passes from hand to hand (D-132, D-319 addendum): the
    counter sells the blank sheet beside it and leaves the map alone."""
    _, home, far = await _world(session, constants)
    body = await _drawer(session, home)
    now = datetime.now(UTC)
    await memory.remember(session, constants, body.identity_id, [far.key], at=now)
    drawn = await _blank(session, body)
    await sheet.draw(session, constants, body, drawn, now=now)
    blank = await _blank(session, body)
    pocket = await world.body_container(session, body)
    laid = await counter._stacks(session, pocket, sheet.SHEET, None, constants)
    assert [item.id for item in laid] == [blank.id]


async def test_two_hands_do_not_draw_out_of_one_reserve(
    factory: async_sessionmaker[AsyncSession], constants: Constants, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drawing costs stamina, and stamina is money (CLAUDE.md): two sockets of
    one identity over two sheets with strength for one drawing and a half.
    Without the body's row locked and reread both find the reserve enough."""
    original = memory.known

    async def slow(*args, **kwargs):
        out = await original(*args, **kwargs)
        await asyncio.sleep(0.2)
        return out

    monkeypatch.setattr(memory, "known", slow)
    async with factory() as session, session.begin():
        _, home, far = await _world(session, constants)
        body = await _drawer(session, home)
        now = datetime.now(UTC)
        await memory.remember(session, constants, body.identity_id, [far.key], at=now)
        first = await _blank(session, body)
        second = await _blank(session, body)
        spend = float(constants[R.MAP_DRAW_STAMINA])
        body.stamina = Decimal(str(spend * 1.5))
        await session.flush()
        body_id, first_id, second_id = body.id, first.id, second.id

    async def draw(item_id: uuid.UUID) -> None:
        async with factory() as db, db.begin():
            hand = await db.get(Body, body_id)
            item = await db.get(Item, item_id)
            assert hand is not None and item is not None
            await sheet.draw(db, current(), hand, item, now=datetime.now(UTC))

    outcomes = await asyncio.gather(draw(first_id), draw(second_id), return_exceptions=True)
    refused = [one for one in outcomes if isinstance(one, sheet.SheetError)]
    other = [one for one in outcomes if isinstance(one, BaseException) and one not in refused]
    assert not other, f"сорвалось не отказом: {other}"
    assert len(refused) == 1, "второму листу не хватило выносливости, а его нарисовали"
    async with factory() as db:
        again = await db.get(Body, body_id)
        assert again is not None
        assert float(again.stamina) == pytest.approx(spend * 0.5)
