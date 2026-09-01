# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The land tax (D-248).

The tax falls with every node from the centre and goes by the footprint,
an empty plot pays for the ground it holds, the day of tax reaches the
treasury -- and the planet's own land, a ship, the city itself and the
land beyond the walls pay nothing. The land and the houses live in
`test_estate.py` and `test_estate_build.py`.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from estate_kit import _buyer, _city
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import city as town
from src.engine import estate, ledger, world
from src.models.estate import Building
from src.models.job import JobState
from src.models.ledger import AccountKind
from src.models.world import Layer
from src.units import PERCENT

# --- the land tax (D-127, D-220) ---------------------------------------------


async def _taxed_house(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    *,
    where,
    city,
    area: float = 20.0,
    floors: int = 1,
    funds: float = 1_000,
):
    """A plot of a city, held by a person, with a finished house on it."""
    identity, body = await _buyer(session, where, funds=funds, city=city)
    where.owner_identity_id = identity.id
    await session.flush()

    pocket = await world.body_container(session, body)
    for name, quantity in estate.estimate(
        constants, footprint=area, floors=floors, kind=estate.kinds(constants)[0]
    ).items():
        await world.grant_item(
            session, pocket, name, amount=quantity + 1, quality=60, origin="тест"
        )
    job = await estate.construct(session, constants, body, where, area, floors=floors)
    await estate.finish_build(session, job)
    job.state = JobState.DONE
    await session.flush()
    return identity, body


async def test_tax_falls_with_every_node_from_the_centre(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The rate is announced at the bioprinter and decays by the node (D-220).

    The same decay the purchase price follows, and for the same reason: the
    centre must cost more to hold, not only to buy.
    """
    city, _, near, far = await _city(session, catalog)
    await _taxed_house(session, constants, catalog, where=near, city=city)
    await _taxed_house(session, constants, catalog, where=far, city=city)

    close = await estate.land_tax_of(session, constants, catalog, near)
    away = await estate.land_tax_of(session, constants, catalog, far)
    assert close > away > 0
    decline = 1 - constants[R.LAND_DECAY_PER_NODE] / PERCENT
    assert away == pytest.approx(close * decline, rel=0.01)


async def test_tax_goes_by_the_footprint_not_by_the_floors(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A tower takes the same ground as the bungalow beside it.

    This is a tax on land. Charging the sum of the floors would undo the whole
    point of height (D-125): storeys are worth building precisely because they
    cost no ground.
    """
    city, _, near, far = await _city(session, catalog)
    await _taxed_house(session, constants, catalog, where=near, city=city, floors=1)
    await _taxed_house(session, constants, catalog, where=far, city=city, floors=3)

    #: The far plot is three storeys and still pays the decay's share of one.
    flat = await estate.land_tax_of(session, constants, catalog, near)
    tall = await estate.land_tax_of(session, constants, catalog, far)
    decline = 1 - constants[R.LAND_DECAY_PER_NODE] / PERCENT
    assert tall == pytest.approx(flat * decline, rel=0.01)
    assert await estate.built_area(session, far) == pytest.approx(60), "дом всё же в три этажа"


async def test_an_empty_plot_pays_for_the_ground_it_holds(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Hold land and pay for land (D-236).

    Charged on the footprint of the buildings instead, an empty plot in the
    middle of a city cost its holder nothing at all, and buying up the centre
    to sit on it was free. The base is the plot, and an empty one is exactly
    the case the tax exists for.
    """
    city, _, near, _ = await _city(session, catalog)
    identity, _ = await _buyer(session, near, city=city)
    near.owner_identity_id = identity.id
    await session.flush()

    empty = await estate.land_tax_of(session, constants, catalog, near)
    assert empty > 0, "пустой участок в центре снова держат бесплатно"

    #: And building on it changes nothing: storeys cost no ground (D-125), and
    #: now neither do walls -- the bill is the land, whatever stands on it.
    session.add(Building(node_id=near.id, area_m2=40, footprint_m2=40))
    await session.flush()
    built = await estate.land_tax_of(session, constants, catalog, near)
    assert built == empty, "налог поехал за застройкой, а он с земли"


async def test_the_day_of_tax_reaches_the_treasury(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    city, _, near, _ = await _city(session, catalog)
    identity, _ = await _taxed_house(session, constants, catalog, where=near, city=city)

    owed = await estate.land_tax_of(session, constants, catalog, near)
    assert owed > 0
    account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
    before = await ledger.balance(session, account.id)
    in_treasury = await town.treasury_balance(session, city)

    levied = await estate.levy_land_tax(session, constants, catalog)
    assert levied == {"paid": owed, "unpaid": 0, "plots": 1}
    assert await ledger.balance(session, account.id) == before - owed
    assert await town.treasury_balance(session, city) == in_treasury + owed


async def test_the_planet_s_own_land_is_nobody_s_and_pays_nothing(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Land tax is charged on the built-up area, not on the planet (D-089, D-198).

    A scout's find beyond the walls is a node of the planet: the mine, the
    grove, the wild plot. Out there is no authority to tax it and no centre to
    count the distance from. Checked with the plot made civic by hand, because
    the rule must hold by itself and not because nothing out there happens to
    carry a city today.
    """
    city, _, near, _ = await _city(session, catalog)
    identity, _ = await _taxed_house(session, constants, catalog, where=near, city=city)

    wild = await world.create_node(
        session,
        f"terra.wild.{uuid.uuid4().hex[:8]}",
        "Дикий участок",
        area_m2=100,
        layer=Layer.PLANET,
    )
    wild.owner_identity_id = identity.id
    wild.owner_city_id = city.id
    session.add(Building(node_id=wild.id, area_m2=40, footprint_m2=40))
    await session.flush()

    assert await estate.land_tax_of(session, constants, catalog, wild) == 0
    levied = await estate.levy_land_tax(session, constants, catalog)
    assert levied["plots"] == 1, "земля планеты в счёт дня не идёт"


async def test_a_ship_is_not_land_and_pays_no_land_tax(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A hull has an owner and a building of its own and is still not land.

    A ship's room is registered as a building so that area and places are
    counted by one rule (D-202), and it belongs to a person -- which is exactly
    the shape the day's levy looks for. It belongs to no city, though, so there
    is nobody to tax it (D-198): it must neither pay nor be counted among the
    plots, and the levy must not even read it.
    """
    from src.engine.ship import ABOARD

    city, _, near, _ = await _city(session, catalog)
    identity, _ = await _taxed_house(session, constants, catalog, where=near, city=city)

    #: Ten rooms aboard, by the same marks a real one carries.
    cabins = []
    for _ in range(10):
        cabin = await world.create_node(
            session,
            f"ship.node.{uuid.uuid4().hex[:8]}",
            "Отсек",
            area_m2=40,
            properties={ABOARD: True},
        )
        cabin.owner_identity_id = identity.id
        session.add(Building(node_id=cabin.id, area_m2=40, footprint_m2=40))
        cabins.append(cabin)
    await session.flush()

    assert await estate.land_tax_of(session, constants, catalog, cabins[0]) == 0

    #: And still nothing to pay even if the hull were made civic land by some
    #: hand the engine does not have: three gates keep a ship out of a city --
    #: a city is founded only on a node of the planet, a plot is found only
    #: from inside a city, and nothing else hands land over -- but the rule
    #: itself must not rest on all three holding forever.
    cabins[0].owner_city_id = city.id
    await session.flush()
    assert await estate.land_tax_of(session, constants, catalog, cabins[0]) == 0, (
        "борт не земля, чей бы он ни был"
    )
    cabins[0].owner_city_id = None
    await session.flush()

    #: Counted, not just checked: without the mark in the query every cabin
    #: would be read and asked which city it belongs to -- two statements
    #: apiece -- only to be dropped for having none. The levy of one plot takes
    #: some sixteen; the bound leaves room for that and none for ten hulls.
    from sqlalchemy import event as sql_event

    seen = {"n": 0}
    engine_ = session.get_bind()

    def _count(*args: object) -> None:
        seen["n"] += 1

    sql_event.listen(engine_, "before_cursor_execute", _count)
    try:
        levied = await estate.levy_land_tax(session, constants, catalog)
    finally:
        sql_event.remove(engine_, "before_cursor_execute", _count)

    assert seen["n"] < 25, f"борта всё-таки читаются: {seen['n']} запросов"

    assert levied["plots"] == 1, "борт не участок: в счёт дня он не идёт"


async def test_what_cannot_be_paid_is_not_paid(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """An empty pocket is charged what it has and no more (D-166).

    Turning the rest into a debt would be inventing debt collection, and the
    shortfall must stay visible instead of quietly vanishing.
    """
    city, _, near, _ = await _city(session, catalog)
    identity, _ = await _taxed_house(session, constants, catalog, where=near, city=city, funds=0)
    owed = await estate.land_tax_of(session, constants, catalog, near)
    account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
    assert await ledger.balance(session, account.id) == 0

    levied = await estate.levy_land_tax(session, constants, catalog)
    assert levied == {"paid": 0, "unpaid": owed, "plots": 1}
    #: Nobody goes below zero: overdraft is a debt mechanic, and there is none.
    assert await ledger.balance(session, account.id) == 0


async def test_the_city_does_not_tax_itself(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A city taxing its own node moves money into the pocket it came from.

    The building is placed straight into the table: a civic node cannot be
    built on through `construct` at all (D-089), and the point here is the node
    that **is** built up and still has no holder to bill.
    """
    from src.models.estate import Building

    city, core, _, _ = await _city(session, catalog)
    session.add(
        Building(
            node_id=core.id,
            area_m2=20,
            footprint_m2=20,
            floors=1,
            kind=estate.kinds(constants)[0],
        )
    )
    await session.flush()

    assert await estate.built_area(session, core, ground=True) == pytest.approx(20)
    #: The core is the city's and stays the city's: nobody holds a deed to it.
    assert core.owner_identity_id is None
    levied = await estate.levy_land_tax(session, constants, catalog)
    assert levied["plots"] == 0, "город не выставляет счёт сам себе"


async def test_land_beyond_the_walls_is_not_taxed(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """No authority out there to tax it (D-198): the homestead pays nothing."""
    stamp = uuid.uuid4().hex[:6]
    wild = await world.create_node(
        session, f"terra.wild.{stamp}", "Пустошь", area_m2=200, layer=Layer.PLANET
    )
    settler, body = await _buyer(session, wild, funds=0)
    pocket = await world.body_container(session, body)
    for name, quantity in estate.estimate(
        constants, footprint=20.0, floors=1, kind=estate.kinds(constants)[0]
    ).items():
        await world.grant_item(
            session, pocket, name, amount=quantity + 1, quality=60, origin="тест"
        )
    job = await estate.construct(session, constants, body, wild, 20.0)
    await estate.finish_build(session, job)

    assert await estate.land_tax_of(session, constants, catalog, wild) == 0
