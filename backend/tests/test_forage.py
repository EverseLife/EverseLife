# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Foraging: the empty land of a place gives up what lies on it (D-210).

Checked is what the mechanic was introduced for:

* the source is **empty** land -- the plot minus the building footprint, and
  storeys above the ground take nothing from it;
* what turns up is not chosen and comes from the vault's table: pace and mix
  are one number per thing, and every thing named there is a possible find;
* the find is revealed by the deadline and only then decided about: taken
  into the hands with its handful, or passed and gone -- and either way the
  search goes on;
* the land is the searcher's or nobody's; walking away abandons the search.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import forage, gear, world
from src.models.estate import Building
from src.models.farm import Plot
from src.models.forage import Forage
from src.models.identity import Identity
from src.models.inventory import Item
from src.units import amount_float


async def _yard(session: AsyncSession, *, area: float = 400):
    """A wild plot and a body standing on it."""
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(
        session, f"terra.yard.{stamp}", "Пустырь", area_m2=area, properties={"дикий": True}
    )
    identity = await world.create_identity(session, f"Собиратель-{stamp}")
    body = await world.print_body(session, identity, node)
    return node, body


def _later(row: Forage) -> datetime:
    return row.ready_at + timedelta(seconds=1)


async def test_empty_land_is_plot_minus_footprint(session: AsyncSession) -> None:
    """A two-storey house of ten metres takes ten from the yard, not twenty (D-125)."""
    node, _ = await _yard(session, area=400)
    assert await forage.empty_area(session, node) == pytest.approx(400)

    session.add(Building(node_id=node.id, area_m2=200, footprint_m2=100, floors=2))
    await session.flush()
    assert await forage.empty_area(session, node) == pytest.approx(300)


async def test_empty_land_loses_the_marked_strips(session: AsyncSession) -> None:
    """A bed is worked land: the foraging walks what neither wall nor strip took (D-246)."""
    node, body = await _yard(session, area=400)
    session.add(Building(node_id=node.id, area_m2=200, footprint_m2=100, floors=2))
    await session.flush()

    session.add(
        Plot(
            node_id=node.id,
            owner_identity_id=body.identity_id,
            name="Грядка",
            area_m2=Decimal("120"),
            fertility=Decimal("50"),
        )
    )
    await session.flush()
    assert await forage.empty_area(session, node) == pytest.approx(180)


async def test_no_window_below_min_area(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A plot built up to the edge has nowhere to forage: no window and a refusal."""
    least = constants[R.FORAGE_MIN_AREA]
    node, body = await _yard(session, area=least * 2)
    session.add(Building(node_id=node.id, area_m2=least * 2 - least / 2))
    await session.flush()

    assert await forage.view(session, constants, catalog, body, node) is None
    with pytest.raises(forage.NoRoom):
        await forage.start(session, constants, body)


async def test_more_land_searches_faster_but_not_under_the_floor(
    constants: Constants,
) -> None:
    """The pace scales with the empty area; the floor keeps a big yard from being a tap."""
    reference = constants[R.FORAGE_REFERENCE_AREA]
    dice = random.Random(1)
    at_reference = forage.search_seconds(constants, reference, dice)
    dice = random.Random(1)
    at_double = forage.search_seconds(constants, reference * 2, dice)
    floor = constants[R.FORAGE_SEARCH_FLOOR]
    assert at_double <= at_reference
    assert at_double >= floor
    #: With the same dice the length halves exactly, unless the floor caught it.
    if at_double > floor:
        assert at_double == pytest.approx(at_reference / 2)
    #: A yard the size of a planet still takes the floor.
    assert forage.search_seconds(constants, reference * 1_000_000, random.Random(2)) == floor


async def test_quality_is_mostly_ordinary(session: AsyncSession, constants: Constants) -> None:
    """Triangular over the span, peak in the middle: the middle third beats either edge third.

    Quality is a magnitude, and magnitudes keep rolling plainly (D-213): a
    "how much" has no drought to remember.
    """
    _, body = await _yard(session)
    grade = constants[R.FORAGE_QUALITY]
    rolls = [
        (await forage._roll(session, constants, body, random.Random(seed)))[2]
        for seed in range(3000)
    ]
    assert all(grade.min <= q <= grade.max for q in rolls)
    third = (grade.max - grade.min) / 3
    low = sum(q < grade.min + third for q in rolls)
    mid = sum(grade.min + third <= q < grade.max - third for q in rolls)
    high = sum(q >= grade.max - third for q in rolls)
    assert mid > low and mid > high
    assert sum(rolls) / len(rolls) == pytest.approx(grade.mid, abs=third / 3)


async def test_every_thing_in_the_table_is_found(
    session: AsyncSession, constants: Constants
) -> None:
    """The mix comes from the vault: no thing named there is unreachable, none is invented.

    And now it is guaranteed rather than likely (D-213): the deck holds every
    thing of the table, so one deck's worth of searches shows all of them.
    """
    _, body = await _yard(session)
    table = forage.finds(constants)
    assert table, "таблица находок пуста"
    seen = {
        (await forage._roll(session, constants, body, random.Random(seed)))[0]
        for seed in range(200)
    }
    assert seen == set(table)
    for name in table:
        assert constants[R.FORAGE_HANDFUL][name] >= 1


async def test_search_reveals_a_find_by_the_deadline(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Before the deadline a search, after it a find; stamina goes by the time searched."""
    node, body = await _yard(session)
    before = float(body.stamina)
    moment = datetime.now(UTC)
    row = await forage.start(session, constants, body, now=moment)

    assert row.ready_at > moment
    assert float(body.stamina) == pytest.approx(before - constants[R.FORAGE_SEARCH_STAMINA])

    seen = await forage.view(session, constants, catalog, body, node, now=moment)
    assert seen is not None
    assert seen["state"] == "searching" and seen["found"] is None
    assert seen["area"] == pytest.approx(400)

    seen = await forage.view(session, constants, catalog, body, node, now=_later(row))
    assert seen["state"] == "found"
    assert seen["found"]["goods"] in forage.finds(constants)
    assert seen["found"]["units"] == constants[R.FORAGE_HANDFUL][seen["found"]["goods"]]

    #: The list of what the land gives is always there, before the first search too.
    assert {entry["goods"] for entry in seen["finds"]} == set(forage.finds(constants))


async def test_find_is_decided_before_it_is_taken(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The find is not decided about before it shows -- but it is already settled."""
    node, body = await _yard(session)
    moment = datetime.now(UTC)
    row = await forage.start(session, constants, body, now=moment)
    with pytest.raises(forage.NothingFound):
        await forage.take(session, constants, catalog, body, now=moment)
    with pytest.raises(forage.NothingFound):
        await forage.pass_(session, constants, body, now=moment)
    with pytest.raises(forage.AlreadySearching):
        await forage.start(session, constants, body, now=moment)
    assert row.found in forage.finds(constants)


async def test_take_puts_the_handful_in_the_hands_and_ends_the_search(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    node, body = await _yard(session)
    row = await forage.start(session, constants, body)
    goods, units, quality = row.found, row.units, float(row.quality)

    await forage.take(session, constants, catalog, body, now=_later(row))
    pocket = await world.body_container(session, body)
    got = (
        (
            await session.execute(
                select(Item).where(Item.container_id == pocket.id, Item.type_key == goods)
            )
        )
        .scalars()
        .all()
    )
    assert len(got) == 1
    assert amount_float(got[0].amount) == units
    assert float(got[0].quality) == pytest.approx(quality)
    grade = constants[R.FORAGE_QUALITY]
    assert grade.min <= quality <= grade.max

    #: The search does not start again by itself (D-211): the find is in the
    #: hands, and walking the plot once more is the player's decision.
    assert await forage.current(session, body) is None


async def test_pass_leaves_the_find_and_searches_on(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    node, body = await _yard(session)
    row = await forage.start(session, constants, body)
    following = await forage.pass_(session, constants, body, now=_later(row))
    pocket = await world.body_container(session, body)
    got = (
        (await session.execute(select(Item).where(Item.container_id == pocket.id))).scalars().all()
    )
    assert got == [], "оставленное не попадает в руки"
    assert following.id != row.id


async def test_taking_costs_no_second_search(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The stamina of one search buys one search (D-211).

    Restarting by itself spent strength nobody asked to spend -- and on an
    exhausted body it turned "pick it up" into a refusal.
    """
    node, body = await _yard(session)
    await forage.start(session, constants, body)
    body.stamina = Decimal("0")
    await session.flush()
    row = await forage.current(session, body)
    await forage.take(session, constants, catalog, body, now=_later(row))
    assert await forage.current(session, body) is None


async def test_stop_drops_the_search(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    node, body = await _yard(session)
    await forage.start(session, constants, body)
    await forage.stop(session, body)
    assert await forage.current(session, body) is None
    with pytest.raises(forage.NothingFound):
        await forage.stop(session, body)


async def test_walking_away_abandons_the_search(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The find stayed on the plot, and the plot is behind."""
    node, body = await _yard(session)
    row = await forage.start(session, constants, body)
    elsewhere, _ = await _yard(session)
    body.node_id = elsewhere.id
    await session.flush()
    assert await forage.current(session, body) is None
    assert await session.get(Forage, row.id) is None


async def test_foreign_land_is_not_foraged(
    session: AsyncSession, constants: Constants, catalog: Catalog, own_plot
) -> None:
    """Somebody else's land: what lies on it is theirs. Civic land not bought is the city's."""
    node, body = await _yard(session)
    stranger = await world.create_identity(session, f"Хозяин-{uuid.uuid4().hex[:6]}")
    await own_plot(node, stranger)
    with pytest.raises(forage.NotYours):
        await forage.start(session, constants, body)
    assert await forage.view(session, constants, catalog, body, node) is None

    #: The holder forages their own plot.
    home, owner = await _yard(session)
    holder = await session.get(Identity, owner.identity_id)
    await own_plot(home, holder)
    assert forage.is_yours(home, owner)
    await forage.start(session, constants, owner)


async def test_no_strength_no_search(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    node, body = await _yard(session)
    body.stamina = Decimal("0")
    await session.flush()
    with pytest.raises(forage.NoStrength):
        await forage.start(session, constants, body)
    assert await forage.current(session, body) is None


async def test_full_hands_keep_the_find_waiting(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Did not fit -- not taken, and the find keeps lying: put something down and try again."""
    node, body = await _yard(session)
    pocket = await world.body_container(session, body)
    limit = await gear.capacity(session, constants, catalog, body)
    #: Stones weigh something; fill the hands with them to the limit.
    per_stone = gear.mass_of(catalog, "Камень", 1)
    await world.grant_item(
        session, pocket, "Камень", amount=int(limit / per_stone) + 1, origin="тест"
    )
    row = await forage.start(session, constants, body)
    with pytest.raises(gear.Overloaded):
        await forage.take(session, constants, catalog, body, now=_later(row))
    still = await forage.current(session, body)
    assert still is not None and still.id == row.id
