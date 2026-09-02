# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Land: the price, the deed and the name on the gate (D-089, D-156).

A far plot is cheaper than a near one and every city counts from its own
printer; a purchase pays the treasury and issues a deed that passes with a
sale; the owner names, marks and describes what is theirs and nobody
else's. Building on the land lives in `test_estate_build.py`, the land tax
in `test_estate_tax.py`.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from estate_kit import _buyer, _city
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import city as town
from src.engine import estate, ledger, world
from src.models.estate import Deed
from src.models.inventory import Item
from src.models.ledger import AccountKind
from src.models.world import Layer, Node, Planet, Surface
from src.units import PERCENT, money

# --- price and purchase (D-089) ----------------------------------------------


async def test_far_plot_cheaper_than_near(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The price falls with each node from the bioprinter -- the city centre."""
    city, _, near, far = await _city(session, catalog)
    close = await estate.price_of(session, constants, catalog, city, near)
    far_away = await estate.price_of(session, constants, catalog, city, far)

    assert close > far_away > 0
    decline = 1 - constants[R.LAND_DECAY_PER_NODE] / PERCENT
    assert far_away == pytest.approx(close * decline, rel=0.01)


async def test_each_city_counts_from_its_own_printer(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Distance is counted from the city's own bioprinter, not from the capital's.

    Two towns, each with its printer in its core. A plot one step from its own
    core is one step away -- in both, and for the same money. Counting from a
    single world centre would have made the second town's land the cheapest in
    the world for no reason a player could see: it is simply far from somebody
    else's printer.
    """
    one, one_core, one_near, _ = await _city(session, catalog)
    other, other_core, other_near, _ = await _city(session, catalog)

    assert await estate.nodes_from_center(session, one_near, one) == 1
    assert await estate.nodes_from_center(session, other_near, other) == 1
    assert one_core.id != other_core.id

    assert await estate.price_of(session, constants, catalog, one, one_near) == (
        await estate.price_of(session, constants, catalog, other, other_near)
    )


async def test_measured_distance_is_written_down(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """One walk measures the whole city, not the plot that was asked about."""
    city, core, near, far = await _city(session, catalog)
    assert near.center_steps is None

    assert await estate.nodes_from_center(session, near, city) == 1

    assert near.center_node_id == core.id
    #: The far plot was never asked for, and is measured all the same: the walk
    #: passed it, and the day's tax will want it within the minute.
    assert far.center_steps == 2


async def test_a_new_road_is_measured_again(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A way through changes the distance, and the written number goes with it."""
    from src.engine import travel

    city, core, _, far = await _city(session, catalog)
    assert await estate.nodes_from_center(session, far, city) == 2

    await travel.connect(session, core, far, base_seconds=30, surface=Surface.PAVED)

    assert far.center_steps is None, "новое ребро обязано сбросить измеренное"
    assert await estate.nodes_from_center(session, far, city) == 1


async def test_a_trail_to_a_new_place_keeps_the_measurements(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A scout's find hangs on the map by one edge and changes nobody's distance.

    This is what exploration does all day (D-206), and it must not cost the
    world its measurements: a road through a dead end would have to come back
    along the same edge, so it lies on nobody's shortest way.
    """
    from src.engine import travel

    city, core, near, far = await _city(session, catalog)
    assert await estate.nodes_from_center(session, far, city) == 2

    #: A plot found inside the walls belongs to the city it was found in
    #: (D-206) -- otherwise the trail would be crossing a border, and those are
    #: laid only at the gate.
    fresh = await world.create_node(
        session, f"terra.town.{uuid.uuid4().hex[:8]}.find", "Находка", area_m2=100
    )
    fresh.owner_city_id = city.id
    await session.flush()
    await travel.connect(session, far, fresh, base_seconds=30, surface=Surface.PAVED)

    assert far.center_steps == 2, "тропа в новое место не должна сбрасывать измеренное"
    assert near.center_steps == 1
    #: And the find itself is measured at once, from what it was hung on: the
    #: map grows at its edge, so a new place is one step further than the old.
    assert fresh.center_steps == far.center_steps + 1
    assert fresh.center_node_id == core.id


async def test_a_city_that_lost_its_printer_keeps_its_rates(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The machine carried out of the core does not make the land dearer.

    Distance is counted from the bioprinter (D-220), so a city without one has
    nothing to count from. Calling that distance nought would charge every plot
    the centre's own rate -- the dearest in town, for the place that just lost
    its centre. What was measured while the printer stood is kept instead: the
    land did not move.
    """
    from sqlalchemy import select as sql_select

    from src.engine import city as town_

    city, core, near, far = await _city(session, catalog)
    steps = await estate.nodes_from_center(session, far, city)
    assert steps == 2
    priced = await estate.price_of(session, constants, catalog, city, far)

    #: Taken out through the objects, not by a bulk statement: that is how the
    #: engine carries a machine away, and it is what empties the command's memory.
    yard = await world.node_container(session, core)
    printer = (
        (
            await session.execute(
                sql_select(Item).where(
                    Item.container_id == yard.id,
                    Item.type_key.in_(world.station_names(world.BIOPRINTER)),
                )
            )
        )
        .scalars()
        .first()
    )
    await session.delete(printer)
    await session.flush()

    assert await town_.core(session, city) is None, "ядра у города больше нет"
    assert await estate.nodes_from_center(session, far, city) == steps
    assert await estate.price_of(session, constants, catalog, city, far) == priced


async def test_a_find_is_measured_without_a_printer_too(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A plot found while the printer is gone still knows how far out it is.

    Distance comes from the place the scout set out from, not from the centre,
    so the machine does not have to be standing for a new plot to be priced.
    Without this a find in a city that lost its printer would have had nothing
    to keep and would have stood, absurdly, at the centre's own rate.
    """
    from sqlalchemy import select as sql_select

    from src.engine import city as town_
    from src.engine import travel

    city, core, _, far = await _city(session, catalog)
    assert await estate.nodes_from_center(session, far, city) == 2

    yard = await world.node_container(session, core)
    printer = (
        (
            await session.execute(
                sql_select(Item).where(
                    Item.container_id == yard.id,
                    Item.type_key.in_(world.station_names(world.BIOPRINTER)),
                )
            )
        )
        .scalars()
        .first()
    )
    await session.delete(printer)
    await session.flush()
    assert await town_.core(session, city) is None

    fresh = await world.create_node(
        session, f"terra.town.{uuid.uuid4().hex[:8]}.find", "Находка", area_m2=100
    )
    fresh.owner_city_id = city.id
    await session.flush()
    await travel.connect(session, far, fresh, base_seconds=30, surface=Surface.PAVED)

    assert fresh.center_steps == 3, "находка на шаг дальше того, откуда её нашли"
    assert await estate.nodes_from_center(session, fresh, city) == 3


async def test_purchase_pays_treasury_and_issues_deed(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The city sells its land: proceeds to the treasury, title to the buyer."""
    city, _, near, _ = await _city(session, catalog)
    identity, body = await _buyer(session, near, city=city)

    treasury_before_ = await town.treasury_balance(session, city)
    deed = await estate.buy(session, constants, catalog, body, near)

    assert near.owner_identity_id == identity.id
    assert deed.owner_identity_id == identity.id
    assert deed.paid > 0
    assert await town.treasury_balance(session, city) == treasury_before_ + deed.paid


async def test_the_city_does_not_sell_its_own_location(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A city location is not on the price list, empty or not (D-282).

    The other door into the same defect the allotment had: buying asked that
    the node be the city's and free, and the city's own core answers both.
    `is_vacant` is not that rule -- it asks about machines, veins and the gate,
    so a location whose machines were taken down or whose vein ran out was on
    sale like any plot. And a bought one is worse than an allotted one: money
    changed hands for the centre of the city.
    """
    city, core, _, _ = await _city(session, catalog)
    _, body = await _buyer(session, core, city=city)
    #: Bare ground, so that nothing but the missing mark can refuse the sale.
    yard = await world.node_container(session, core)
    await session.execute(delete(Item).where(Item.container_id == yard.id))
    await session.flush()
    assert await estate.is_vacant(session, constants, core), "фикстура не оставила ядро пустым"

    with pytest.raises(estate.NotForSale):
        await estate.buy(session, constants, catalog, body, core)
    assert core.owner_identity_id is None


async def test_no_purchase_without_money(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    city, _, near, _ = await _city(session, catalog)
    _, body = await _buyer(session, near, funds=0, city=city)
    with pytest.raises(estate.NotEnoughMoney):
        await estate.buy(session, constants, catalog, body, near)


async def test_occupied_plot_not_for_sale(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    city, _, near, _ = await _city(session, catalog)
    first, first_body = await _buyer(session, near, city=city)
    await estate.buy(session, constants, catalog, first_body, near)

    _, second_body = await _buyer(session, near, city=city)
    with pytest.raises(estate.NotForSale):
        await estate.buy(session, constants, catalog, second_body, near)


# --- plot name (D-178) -------------------------------------------------------


async def test_owner_names_plot(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Bought -- named. The node key stays the same: deeds reference it."""
    city, _, near, _ = await _city(session, catalog)
    _, body = await _buyer(session, near, city=city)
    await estate.buy(session, constants, catalog, body, near)
    key = near.key

    await estate.rename(session, body, near, "  Кузня у ворот  ")

    assert near.name == "Кузня у ворот", "пробелы по краям обрезаются"
    assert near.key == key, "ключ узла переименованием не трогают"


async def test_owner_marks_plot_and_takes_the_mark_down(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The map mark is nailed and pulled by the same hand as the nameplate (D-238)."""
    city, _, near, _ = await _city(session, catalog)
    _, body = await _buyer(session, near, city=city)
    await estate.buy(session, constants, catalog, body, near)

    await estate.emblem(session, body, near, "workshop")
    assert near.properties[estate.EMBLEM_PROPERTY] == "workshop"

    await estate.emblem(session, body, near, None)
    assert estate.EMBLEM_PROPERTY not in near.properties


async def test_unknown_mark_refused(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The list is the engine's: the world's own signs must not be forgeable."""
    city, _, near, _ = await _city(session, catalog)
    _, body = await _buyer(session, near, city=city)
    await estate.buy(session, constants, catalog, body, near)

    with pytest.raises(estate.BadName):
        await estate.emblem(session, body, near, "руины")
    assert estate.EMBLEM_PROPERTY not in near.properties


async def test_owner_describes_plot_and_wipes_the_words(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The place's words are written and wiped by the same hand as the nameplate (D-238)."""
    city, _, near, _ = await _city(session, catalog)
    _, body = await _buyer(session, near, city=city)
    await estate.buy(session, constants, catalog, body, near)

    await estate.describe(session, body, near, "  Кузня на отшибе, стучим с утра.  ")
    assert near.properties[estate.ABOUT_PROPERTY] == "Кузня на отшибе, стучим с утра."
    assert estate.public_about(near) == "Кузня на отшибе, стучим с утра."

    await estate.describe(session, body, near, "")
    assert estate.ABOUT_PROPERTY not in near.properties
    assert estate.public_about(near) is None


async def test_overlong_description_refused(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A paragraph, not a page: the limit refuses before the base sees it."""
    city, _, near, _ = await _city(session, catalog)
    _, body = await _buyer(session, near, city=city)
    await estate.buy(session, constants, catalog, body, near)

    with pytest.raises(estate.BadName):
        await estate.describe(session, body, near, "х" * 301)
    assert estate.ABOUT_PROPERTY not in near.properties


async def test_cannot_describe_foreign_plot(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Somebody's words are not rewritten -- even standing nearby."""
    city, _, near, _ = await _city(session, catalog)
    _, owner = await _buyer(session, near, city=city)
    await estate.buy(session, constants, catalog, owner, near)

    _, passerby = await _buyer(session, near, city=city)
    with pytest.raises(estate.NotOwner):
        await estate.describe(session, passerby, near, "Моё слово")
    assert estate.ABOUT_PROPERTY not in near.properties


async def test_cannot_mark_foreign_plot(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Somebody's mark is not repainted -- even standing nearby."""
    city, _, near, _ = await _city(session, catalog)
    _, owner = await _buyer(session, near, city=city)
    await estate.buy(session, constants, catalog, owner, near)

    _, passerby = await _buyer(session, near, city=city)
    with pytest.raises(estate.NotOwner):
        await estate.emblem(session, passerby, near, "house")
    assert estate.EMBLEM_PROPERTY not in near.properties


def test_public_map_serves_signs_by_allowlist() -> None:
    """The public map shows the type signs and nothing else (D-238, D-097).

    Default-open would leak every future boolean to the unauthenticated
    internet; the emblem and the ring are not booleans and must not slip in
    either.
    """
    node = Node(
        key="x",
        name="x",
        layer=Layer.CITY,
        planet=Planet.TERRA,
        area_m2=Decimal("1"),
        properties={
            "woods": True,
            "precursors": True,
            "library": True,
            "будущий-флаг": True,
            "ring": 2,
            estate.EMBLEM_PROPERTY: "house",
        },
    )
    assert world.public_signs(node) == ["precursors", "woods"]
    #: The emblem wears the same belt on the way out: a value planted past
    #: the command -- junk, or not a string at all -- stays inside.
    assert estate.public_emblem(node) == "house"
    node.properties = {**node.properties, estate.EMBLEM_PROPERTY: "руины"}
    assert estate.public_emblem(node) is None
    node.properties = {**node.properties, estate.EMBLEM_PROPERTY: 7}
    assert estate.public_emblem(node) is None


async def test_cannot_rename_foreign_plot(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The nameplate on somebody's house is not changed -- even standing nearby."""
    city, _, near, _ = await _city(session, catalog)
    _, owner = await _buyer(session, near, city=city)
    await estate.buy(session, constants, catalog, owner, near)
    before = near.name

    _, passerby = await _buyer(session, near, city=city)
    with pytest.raises(estate.NotOwner):
        await estate.rename(session, passerby, near, "Моё теперь")
    assert near.name == before


async def test_authority_names_city_land_not_private(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The `land` right is about civic plots, not somebody's yard (D-089)."""
    city, core, near, far = await _city(session, catalog)
    ruler, ruler_body = await _buyer(session, far, city=city)
    await town.install_founder(session, city, ruler)

    await estate.rename(session, ruler_body, far, "Площадь совета")
    assert far.name == "Площадь совета"

    #: The same ruler on a bought plot is no longer authority but a guest.
    _, owner = await _buyer(session, near, city=city)
    await estate.buy(session, constants, catalog, owner, near)
    ruler_body.node_id = near.id
    await session.flush()
    with pytest.raises(estate.NotOwner):
        await estate.rename(session, ruler_body, near, "Городское теперь")


async def test_name_neither_empty_nor_endless(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    from src.runtime import LAND_NAME_LIMIT

    city, _, near, _ = await _city(session, catalog)
    _, body = await _buyer(session, near, city=city)
    await estate.buy(session, constants, catalog, body, near)

    with pytest.raises(estate.BadName):
        await estate.rename(session, body, near, "   ")
    with pytest.raises(estate.BadName):
        await estate.rename(session, body, near, "я" * (LAND_NAME_LIMIT + 1))


# --- deed and sale contract (D-116) ------------------------------------------


async def test_deed_sold_and_title_passes_with_it(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    city, _, near, _ = await _city(session, catalog)
    seller, seller_body = await _buyer(session, near, city=city)
    deed = await estate.buy(session, constants, catalog, seller_body, near)

    buyer, _ = await _buyer(session, near, funds=500)
    price = money(100)
    await estate.offer_deed(session, seller, deed, price)
    await estate.buy_deed(session, buyer, deed)

    assert deed.owner_identity_id == buyer.id
    assert deed.sale_price is None, "после сделки бумага снята с продажи"
    await session.refresh(near)
    assert near.owner_identity_id == buyer.id, "титул ходит с бумагой"

    account = await ledger.account_for(session, AccountKind.IDENTITY, seller.id)
    assert await ledger.balance(session, account.id) > 0, "деньги дошли продавцу"


async def test_addressed_contract_does_not_sell_to_stranger(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A deed promised to one is not given to another."""
    city, _, near, _ = await _city(session, catalog)
    seller, body = await _buyer(session, near, city=city)
    deed = await estate.buy(session, constants, catalog, body, near)

    own, _ = await _buyer(session, near, funds=500)
    foreign, _ = await _buyer(session, near, funds=500)
    await estate.offer_deed(session, seller, deed, money(50), to=own)

    with pytest.raises(estate.NotForSale):
        await estate.buy_deed(session, foreign, deed)
    await estate.buy_deed(session, own, deed)
    assert deed.owner_identity_id == own.id


async def test_land_handed_over_by_a_city_gives_a_deed(
    session: AsyncSession, constants: Constants, catalog: Catalog, own_plot
) -> None:
    """The title is one for all roads to land -- and all of them run through a city (D-198)."""
    stamp = uuid.uuid4().hex[:6]
    plot = await world.create_node(
        session, f"terra.plot.{stamp}", "Участок", area_m2=200, layer=Layer.PLANET
    )
    identity, body = await _buyer(session, plot, funds=0)
    await own_plot(plot, identity)

    deed = (await session.execute(select(Deed).where(Deed.node_id == plot.id))).scalar_one()
    assert deed.owner_identity_id == identity.id
    assert deed.paid == 0


async def test_no_deed_outside_a_city(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Nobody's land is not privatized at all: there is nobody to issue the paper (D-198)."""
    stamp = uuid.uuid4().hex[:6]
    wild = await world.create_node(
        session, f"terra.wild.{stamp}", "Дикий", area_m2=200, layer=Layer.PLANET
    )
    identity, _ = await _buyer(session, wild, funds=0)
    with pytest.raises(world.LandError):
        await world.grant_node(session, wild, identity)
    assert (
        await session.execute(select(Deed).where(Deed.node_id == wild.id))
    ).scalar_one_or_none() is None
