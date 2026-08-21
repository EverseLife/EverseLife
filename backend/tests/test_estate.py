# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Real estate: plot purchase, deed, building (D-089, D-106, D-116).

Checked is what the system was introduced for:

* an empty civic plot is bought by whoever the code-law `build_permit`
  allows (citizens by default, D-160), the price depends on the distance to
  the bioprinter, the proceeds go to the city treasury;
* ownership is documented by a deed; the deed is sold by a sale contract, and
  the title to the node passes with it;
* a building is built on one's own plot from materials and on schedule; a
  machine without a building does not stand (see `test_station`);
* one's own house is taken apart as work, part of the material comes back, and
  the yard empties before the demolition rather than losing what stood in it
  (D-205).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import city as town
from src.engine import estate, goods, ledger, world
from src.models.city import Citizen
from src.models.estate import Deed
from src.models.inventory import Item
from src.models.job import JobState
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Layer, Node, Surface
from src.units import PERCENT, SCALE_MAX, money


async def _city(session: AsyncSession, catalog: Catalog):
    """A town: a core with the Forerunners' Printer and two plots at the first and second step."""
    from src.engine import travel

    stamp = uuid.uuid4().hex[:8]
    planet = await world.create_node(
        session, f"terra.{stamp}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    delegate = await world.create_node(
        session, f"terra.town.{stamp}", "Городок", area_m2=1,
        layer=Layer.PLANET, parent=planet,
    )
    core = await world.create_node(
        session, f"terra.town.{stamp}.core", "Ядро", area_m2=100,
        parent=delegate, properties={"кольцо": 0, "предтечи": True},
    )
    #: The bioprinter distance is measured from: the city centre (D-089).
    core_yard = await world.node_container(session, core)
    await world.grant_item(session, core_yard, world.BIOPRINTER, quality=60, origin="тест")

    near = await world.create_node(
        session, f"terra.town.{stamp}.lot1", "Ближний участок", area_m2=100,
        parent=delegate, properties={"участок": True},
    )
    far = await world.create_node(
        session, f"terra.town.{stamp}.lot2", "Дальний участок", area_m2=100,
        parent=delegate, properties={"участок": True},
    )
    await travel.connect(session, core, near, base_seconds=30, surface=Surface.PAVED)
    await travel.connect(session, near, far, base_seconds=30, surface=Surface.PAVED)

    city = await town.found(session, catalog, delegate, "Городок")
    for node in (core, near, far):
        node.owner_city_id = city.id
    await session.flush()
    return city, core, near, far


async def _buyer(
    session: AsyncSession,
    where: Node,
    *,
    funds: float = 1_000,
    city=None,
    citizen: bool = True,
):
    """The buyer. A citizen by default: land is sold to one's own (D-160)."""
    stamp = uuid.uuid4().hex[:6]
    identity = await world.create_identity(session, f"Покупатель-{stamp}")
    body = await world.print_body(session, identity, where)
    if citizen and city is not None:
        session.add(Citizen(identity_id=identity.id, city_id=city.id))
        await session.flush()
    if funds:
        account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
        genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
        await ledger.transfer(
            session, PostingReason.GENESIS, debit=genesis.id, credit=account.id,
            amount=money(funds), memo={},
        )
    return identity, body


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

    deed = (
        await session.execute(select(Deed).where(Deed.node_id == plot.id))
    ).scalar_one()
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


# --- building (D-106, D-125) -------------------------------------------------


async def test_construction_spends_materials_and_places_building_on_term(
    session: AsyncSession, constants: Constants, catalog: Catalog, own_plot
) -> None:
    stamp = uuid.uuid4().hex[:6]
    plot = await world.create_node(
        session, f"terra.plot.{stamp}", "Участок", area_m2=100, layer=Layer.PLANET
    )
    identity, body = await _buyer(session, plot, funds=0)
    await own_plot(plot, identity)

    pocket = await world.body_container(session, body)
    norms = estate.composition(constants, estate.kinds(constants)[0])
    area = 20.0
    for name, per_metre_ in norms.items():
        await world.grant_item(
            session, pocket, name, amount=float(per_metre_) * area + 1,
            quality=60, origin="тест",
        )

    job = await estate.construct(session, constants, body, plot, area)
    assert await estate.built_area(session, plot) == 0, "здание не мгновенно"

    #: The term is the assembly labour: `build.labor_per_m2` hours per metre.
    minutes = area * constants[R.BUILD_LABOR_PER_M2] * 60
    assert (job.run_at - datetime.now(UTC)).total_seconds() / 60 == pytest.approx(
        minutes, rel=0.05
    )

    await estate.finish_build(session, job)
    assert await estate.built_area(session, plot) == pytest.approx(area)


async def test_construction_does_not_start_without_materials(
    session: AsyncSession, constants: Constants, catalog: Catalog, own_plot
) -> None:
    from src.engine import craft

    stamp = uuid.uuid4().hex[:6]
    plot = await world.create_node(
        session, f"terra.plot.{stamp}", "Участок", area_m2=100, layer=Layer.PLANET
    )
    identity, body = await _buyer(session, plot, funds=0)
    await own_plot(plot, identity)
    with pytest.raises(craft.NotEnough):
        await estate.construct(session, constants, body, plot, 20)


def test_storeys_cost_more_than_the_same_area_laid_flat(constants: Constants) -> None:
    """Height is paid for: each next floor costs `floor_growth_by_type` (D-125)."""
    plain = estate.kinds(constants)[0]
    flat = estate.estimate(constants, footprint=40, floors=1, kind=plain)
    tall = estate.estimate(constants, footprint=20, floors=2, kind=plain)

    assert sum(tall.values()) > sum(flat.values()), (
        "двадцать метров в два этажа дороже сорока в один: за высоту платят"
    )
    #: And a two-storey house takes half the ground -- that is what it is for.
    assert estate.build_minutes(constants, footprint=20, floors=2, kind=plain) > 0


def test_type_names_the_materials_not_a_multiplier(constants: Constants) -> None:
    """A type is its own composition, not more of one shared recipe (D-218).

    That is the whole difference from the tier ladder it replaced: an all-metal
    house does not spend fourfold timber, it spends iron and glass, and a city
    built of it demands other trades than a city of log huts.
    """
    ladder = estate.kinds(constants)
    plainest, dearest = ladder[0], ladder[-1]
    hut = estate.estimate(constants, footprint=20, floors=1, kind=plainest)
    palace = estate.estimate(constants, footprint=20, floors=1, kind=dearest)

    assert set(hut) != set(palace), "разные типы строятся из разного сырья"
    assert sum(palace.values()) > sum(hut.values()), "дорогой тип и стоит дороже"


def test_dear_types_decay_slower(constants: Constants) -> None:
    """What expensive materials buy is a rarer repair, not a stronger wall (D-218)."""
    ladder = estate.kinds(constants)
    assert estate.decay_per_day(constants, ladder[0]) > estate.decay_per_day(
        constants, ladder[-1]
    )
    #: And the cheap type pays for that with a steeper floor: height is where a
    #: log house becomes ruinous.
    assert estate.floor_growth(constants, ladder[0]) > estate.floor_growth(
        constants, ladder[-1]
    )


def test_no_type_has_a_ceiling_of_height(constants: Constants) -> None:
    """A twenty-storey log house is allowed -- and priced out of existence (D-218)."""
    plain = estate.kinds(constants)[0]
    tower = estate.estimate(constants, footprint=10, floors=20, kind=plain)
    hut = estate.estimate(constants, footprint=10, floors=1, kind=plain)
    assert sum(tower.values()) > sum(hut.values()) * 1000, (
        "запрета на высоту нет — отказывает смета, и она обязана быть разорительной"
    )


async def test_unknown_type_is_refused(
    session: AsyncSession, constants: Constants, catalog: Catalog, own_plot
) -> None:
    """A misnamed type is a refusal, not a silent fallback to the cheap one."""
    stamp = uuid.uuid4().hex[:6]
    plot = await world.create_node(
        session, f"terra.plot.{stamp}", "Участок", area_m2=100, layer=Layer.PLANET
    )
    identity, body = await _buyer(session, plot, funds=0)
    await own_plot(plot, identity)
    with pytest.raises(estate.UnknownKind):
        await estate.construct(session, constants, body, plot, 20, kind="соломенный")


async def test_house_smaller_than_the_minimum_is_a_lean_to(
    session: AsyncSession, constants: Constants, catalog: Catalog, own_plot
) -> None:
    """Below `build.area_min` there is no building to speak of (D-218)."""
    stamp = uuid.uuid4().hex[:6]
    plot = await world.create_node(
        session, f"terra.plot.{stamp}", "Участок", area_m2=100, layer=Layer.PLANET
    )
    identity, body = await _buyer(session, plot, funds=0)
    await own_plot(plot, identity)

    below = constants[R.BUILD_AREA_MIN] - 1
    with pytest.raises(estate.TooSmall):
        await estate.construct(session, constants, body, plot, below)


async def test_storeys_give_area_without_eating_the_plot(
    session: AsyncSession, constants: Constants, catalog: Catalog, own_plot
) -> None:
    """A two-storey house takes ten metres of ground and gives twenty of floor."""
    stamp = uuid.uuid4().hex[:6]
    plot = await world.create_node(
        session, f"terra.plot.{stamp}", "Участок", area_m2=100, layer=Layer.PLANET
    )
    identity, body = await _buyer(session, plot, funds=0)
    await own_plot(plot, identity)

    pocket = await world.body_container(session, body)
    needed = estate.estimate(
        constants, footprint=10, floors=2, kind=estate.kinds(constants)[0]
    )
    for name, quantity in needed.items():
        await world.grant_item(
            session, pocket, name, amount=quantity + 1, quality=60, origin="тест"
        )

    job = await estate.construct(session, constants, body, plot, 10, floors=2)
    await estate.finish_build(session, job)

    assert await estate.built_area(session, plot) == pytest.approx(20)
    assert await estate.built_area(session, plot, ground=True) == pytest.approx(10)


async def test_building_no_larger_than_plot(
    session: AsyncSession, constants: Constants, catalog: Catalog, own_plot
) -> None:
    stamp = uuid.uuid4().hex[:6]
    plot = await world.create_node(
        session, f"terra.plot.{stamp}", "Участок", area_m2=50, layer=Layer.PLANET
    )
    identity, body = await _buyer(session, plot, funds=0)
    await own_plot(plot, identity)
    with pytest.raises(estate.NoRoom):
        await estate.construct(session, constants, body, plot, 60)


async def test_started_sites_hold_their_ground(
    session: AsyncSession, constants: Constants, catalog: Catalog, own_plot
) -> None:
    """A queue of orders must not walk past the plot (D-218).

    Counting only finished houses, each order is lawful on its own -- and five
    of them put five hundred metres of house on a hundred-metre plot. Ground
    already spoken for is ground taken.
    """
    stamp = uuid.uuid4().hex[:6]
    plot = await world.create_node(
        session, f"terra.plot.{stamp}", "Участок", area_m2=100, layer=Layer.PLANET
    )
    identity, body = await _buyer(session, plot, funds=0)
    await own_plot(plot, identity)

    pocket = await world.body_container(session, body)
    plain = estate.kinds(constants)[0]
    for name, quantity in estate.estimate(
        constants, footprint=80, floors=1, kind=plain
    ).items():
        await world.grant_item(
            session, pocket, name, amount=quantity * 3, quality=60, origin="тест"
        )

    await estate.construct(session, constants, body, plot, 80)
    assert await estate.planned_footprint(session, plot) == pytest.approx(80)
    #: Nothing stands yet, and still there is no room: the first site holds it.
    assert await estate.built_area(session, plot, ground=True) == 0
    with pytest.raises(estate.NoRoom):
        await estate.construct(session, constants, body, plot, 30)


async def test_no_building_on_foreign_land(
    session: AsyncSession, constants: Constants, catalog: Catalog, own_plot
) -> None:
    stamp = uuid.uuid4().hex[:6]
    plot = await world.create_node(
        session, f"terra.plot.{stamp}", "Участок", area_m2=100, layer=Layer.PLANET
    )
    owner, owner_body = await _buyer(session, plot, funds=0)
    await own_plot(plot, owner)

    _, foreign_body = await _buyer(session, plot, funds=0)
    with pytest.raises(estate.EstateError):
        await estate.construct(session, constants, foreign_body, plot, 10)


# --- demolition (D-205) ------------------------------------------------------


async def _house(
    session: AsyncSession,
    constants: Constants,
    own_plot,
    *,
    area: float = 20.0,
    floors: int = 1,
    plot_area: float = 100,
):
    """A plot of one's own with a finished house on it."""
    stamp = uuid.uuid4().hex[:6]
    plot = await world.create_node(
        session, f"terra.plot.{stamp}", "Участок", area_m2=plot_area, layer=Layer.PLANET
    )
    identity, body = await _buyer(session, plot, funds=0)
    await own_plot(plot, identity)

    pocket = await world.body_container(session, body)
    for name, quantity in estate.estimate(
        constants, footprint=area, floors=floors, kind=estate.kinds(constants)[0]
    ).items():
        await world.grant_item(
            session, pocket, name, amount=quantity + 1, quality=60, origin="тест"
        )
    job = await estate.construct(session, constants, body, plot, area, floors=floors)
    await estate.finish_build(session, job)
    #: The worker closes a finished job, and here there is no worker: a job left
    #: pending would read as a construction still going on, and demolition waits
    #: for those.
    job.state = JobState.DONE
    await session.flush()
    return plot, identity, body


async def test_demolition_takes_time_and_returns_a_share_of_materials(
    session: AsyncSession, constants: Constants, catalog: Catalog, own_plot
) -> None:
    """The house goes when the work is done, and part of the material comes back."""
    plot, identity, body = await _house(session, constants, own_plot)
    houses = await estate.buildings_of(session, plot)
    back = estate.salvage(constants, houses)
    spent = estate.estimate(
        constants, footprint=20.0, floors=1, kind=estate.kinds(constants)[0]
    )

    share = constants[R.BUILD_DEMOLISH_SALVAGE]
    for name, quantity in back.items():
        #: The share of the bill, cut down to whole pieces where the material is
        #: counted (D-212): a house does not give back two thirds of a board.
        assert quantity == goods.whole(name, spent[name] * share), (
            "возвращается доля сметы, а не смета"
        )
        assert quantity < spent[name], "возврат меньше вложенного"

    job = await estate.demolish(session, constants, body, plot)
    assert await estate.built_area(session, plot) > 0, "снос не мгновенен"
    minutes = estate.demolish_minutes(constants, houses)
    assert (job.run_at - datetime.now(UTC)).total_seconds() / 60 == pytest.approx(
        minutes, rel=0.05
    )
    assert minutes < estate.build_minutes(
        constants, footprint=20.0, floors=1, kind=estate.kinds(constants)[0]
    ), "разбор быстрее сборки"

    #: The owner is standing here, so the salvage goes into their hands.
    await estate.finish_demolish(session, job)
    assert await estate.built_area(session, plot) == 0, "участок пуст"

    pocket = await world.body_container(session, body)
    from src.models.inventory import Item
    from src.units import amount_float

    at_hand = {
        thing.type_key: amount_float(thing.amount)
        for thing in (
            await session.execute(select(Item).where(Item.container_id == pocket.id))
        ).scalars().all()
    }
    for name, quantity in back.items():
        assert at_hand.get(name, 0) == pytest.approx(quantity, rel=0.01)


async def test_demolition_waits_for_the_yard_to_empty(
    session: AsyncSession, constants: Constants, catalog: Catalog, own_plot
) -> None:
    """Machines and cargo leave before the work, not after it (D-205).

    Losing possessions to a button is what this order exists to prevent: after
    the demolition a machine has nowhere to stand and the cargo has no room.
    """
    from src.engine import station, storage

    plot, identity, body = await _house(session, constants, own_plot, area=40)
    yard = await world.node_container(session, plot)
    bench = await world.grant_item(
        session, yard, "Верстак", quality=60, origin="тест"
    )

    reasons = await estate.demolish_blockers(session, constants, plot)
    assert reasons and "оборудование" in reasons[0]
    with pytest.raises(estate.NoRoom):
        await estate.demolish(session, constants, body, plot)

    #: Taken into the hands -- and the way is clear.
    await station.take(session, catalog, body, bench)
    assert await estate.demolish_blockers(session, constants, plot) == []
    assert await estate.demolish(session, constants, body, plot) is not None

    #: Cargo that fits under a roof but not in the bare yard blocks it the same
    #: way. Two storeys on a small plot are exactly that gap: forty metres of
    #: floor over twenty metres of ground (D-125).
    from src.engine import gear

    tight, _, owner = await _house(
        session, constants, own_plot, area=20, floors=2, plot_area=20
    )
    per_m2 = constants[R.BUILD_FLOOR_PER_M2]
    roofed = await estate.built_area(session, tight)
    #: Halfway between what the yard holds and what the house holds.
    kilos = (float(tight.area_m2) + roofed) / 2 * per_m2
    quantity = kilos / gear.mass_of(catalog, "Труба", 1)

    pocket = await world.body_container(session, owner)
    goods = await world.grant_item(
        session, pocket, "Труба", amount=quantity, quality=55, origin="тест"
    )
    await storage.drop(session, constants, catalog, owner, goods, quantity)
    assert any(
        "на полу" in reason
        for reason in await estate.demolish_blockers(session, constants, tight)
    )


async def test_demolition_is_not_ordered_twice(
    session: AsyncSession, constants: Constants, catalog: Catalog, own_plot
) -> None:
    """The house is one, and the salvage comes back once (I2: matter does not multiply).

    Each order carries its own salvage in the payload, so two orders on one house
    would pay for it twice -- and the second one is refused by name.
    """
    plot, identity, body = await _house(session, constants, own_plot)
    first = await estate.demolish(session, constants, body, plot)

    assert await estate.demolishing(session, plot)
    with pytest.raises(estate.NoRoom):
        await estate.demolish(session, constants, body, plot)

    #: And a job that fires over an already emptied plot gives nothing at all.
    await estate.finish_demolish(session, first)
    pocket = await world.body_container(session, body)
    from src.models.inventory import Item
    from src.units import amount_float

    async def at_hand() -> dict[str, float]:
        return {
            thing.type_key: amount_float(thing.amount)
            for thing in (
                await session.execute(
                    select(Item).where(Item.container_id == pocket.id)
                )
            ).scalars().all()
        }

    once = await at_hand()
    await estate.finish_demolish(session, first)
    assert await at_hand() == once, "повторное задание материалов не удваивает"


async def test_foreign_civic_plot_is_not_demolished(
    session: AsyncSession, constants: Constants, catalog: Catalog, own_plot
) -> None:
    """Somebody else's house on civic land is taken apart by a court order (D-095)."""
    plot, identity, body = await _house(session, constants, own_plot)

    _, stranger = await _buyer(session, plot, funds=0)
    with pytest.raises(estate.NotOwner):
        await estate.demolish(session, constants, stranger, plot)


async def test_beyond_the_walls_whoever_came_builds_and_takes_apart(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Land outside a city is nobody's, and work on it is open to everyone (D-198, D-205).

    A homestead far from any city is the whole point of that freedom: one builds
    without buying a plot and without taxes -- and the same freedom takes the
    house down. There is no title beyond the walls to make one of them the owner.
    """
    stamp = uuid.uuid4().hex[:6]
    wild = await world.create_node(
        session, f"terra.wild.{stamp}", "Пустошь", area_m2=200, layer=Layer.PLANET
    )
    assert wild.owner_identity_id is None and wild.owner_city_id is None

    settler, settler_body = await _buyer(session, wild, funds=0)
    pocket = await world.body_container(session, settler_body)
    for name, quantity in estate.estimate(
        constants, footprint=20.0, floors=1, kind=estate.kinds(constants)[0]
    ).items():
        await world.grant_item(
            session, pocket, name, amount=quantity + 1, quality=60, origin="тест"
        )
    raising = await estate.construct(session, constants, settler_body, wild, 20.0)
    await estate.finish_build(session, raising)
    raising.state = JobState.DONE
    await session.flush()
    assert await estate.built_area(session, wild) == pytest.approx(20)

    #: Whoever came may take it down -- the settler themselves, or a passer-by.
    _, passerby = await _buyer(session, wild, funds=0)
    job = await estate.demolish(session, constants, passerby, wild)
    await estate.finish_demolish(session, job)
    assert await estate.built_area(session, wild) == 0


async def test_nothing_to_demolish_on_an_empty_plot(
    session: AsyncSession, constants: Constants, catalog: Catalog, own_plot
) -> None:
    stamp = uuid.uuid4().hex[:6]
    plot = await world.create_node(
        session, f"terra.plot.{stamp}", "Участок", area_m2=100, layer=Layer.PLANET
    )
    identity, body = await _buyer(session, plot, funds=0)
    await own_plot(plot, identity)
    with pytest.raises(estate.NoBuilding):
        await estate.demolish(session, constants, body, plot)


# --- decay, repair and collapse (D-218) --------------------------------------


async def test_house_wears_out_by_its_type(
    session: AsyncSession, constants: Constants, catalog: Catalog, own_plot
) -> None:
    """A day of the world costs the house `build.decay_by_type` of condition."""
    plot, identity, body = await _house(session, constants, own_plot)
    house = (await estate.buildings_of(session, plot))[0]
    assert float(house.condition) == pytest.approx(SCALE_MAX)

    worn, fallen = await estate.decay(session, constants)
    assert worn >= 1 and fallen == 0
    await session.refresh(house)
    assert float(house.condition) == pytest.approx(
        SCALE_MAX - estate.decay_per_day(constants, house.kind)
    )


async def test_repair_costs_what_the_house_is_built_of(
    session: AsyncSession, constants: Constants, catalog: Catalog, own_plot
) -> None:
    """Mended with the same materials, in the share of condition missing (D-145)."""
    plot, identity, body = await _house(session, constants, own_plot)
    house = (await estate.buildings_of(session, plot))[0]

    whole = await estate.buildings_of(session, plot)
    assert estate.repair_bill(constants, whole) == {}, "целый дом не чинят"
    with pytest.raises(estate.Ruined):
        await estate.repair(session, constants, body, plot)

    house.condition = Decimal("50")
    await session.flush()
    houses = await estate.buildings_of(session, plot)
    needed = estate.repair_bill(constants, houses)
    built_of = estate.composition(constants, house.kind)
    assert set(needed) <= set(built_of), "чинят тем же, чем построено"
    assert needed, "изношенный дом требует материалов"

    #: Cheaper than raising it anew: the walls are standing.
    fresh = estate.bill(
        constants,
        footprint=float(house.footprint_m2),
        floors=house.floors,
        kind=house.kind,
    )
    assert sum(needed.values()) < sum(fresh.values())

    pocket = await world.body_container(session, body)
    for name, quantity in needed.items():
        await world.grant_item(
            session, pocket, name, amount=quantity + 1, quality=60, origin="тест"
        )
    job = await estate.repair(session, constants, body, plot)
    assert float(house.condition) == pytest.approx(50), "состояние — в конце работ"
    await estate.finish_repair(session, job)
    await session.refresh(house)
    assert float(house.condition) == pytest.approx(SCALE_MAX)


async def test_house_at_nothing_collapses_with_what_it_sheltered(
    session: AsyncSession, constants: Constants, catalog: Catalog, own_plot
) -> None:
    """Full strength until zero, then gone -- and the yard with it (D-218)."""
    plot, identity, body = await _house(session, constants, own_plot)
    house = (await estate.buildings_of(session, plot))[0]

    yard = await world.node_container(session, plot)
    await world.grant_item(
        session, yard, "Дерево", amount=5, quality=60, origin="тест"
    )

    #: One step short of nothing the house is still whole: no places lost, no
    #: area lost. That is what makes repair a decision rather than a levy.
    house.condition = Decimal(str(estate.decay_per_day(constants, house.kind)))
    await session.flush()
    standing = await estate.built_area(session, plot)
    assert standing > 0

    worn, fallen = await estate.decay(session, constants)
    assert fallen == 1
    assert await estate.built_area(session, plot) == 0
    left = (
        await session.execute(select(Item).where(Item.container_id == yard.id))
    ).scalars().all()
    assert left == [], "двор уходит вместе с крышей, которой над ним больше нет"


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


async def test_the_yard_is_not_taxed(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Only what is built on pays (D-127): an empty plot owes nothing."""
    city, _, near, _ = await _city(session, catalog)
    identity, _ = await _buyer(session, near, city=city)
    near.owner_identity_id = identity.id
    await session.flush()
    assert await estate.land_tax_of(session, constants, catalog, near) == 0


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


async def test_what_cannot_be_paid_is_not_paid(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """An empty pocket is charged what it has and no more (D-166).

    Turning the rest into a debt would be inventing debt collection, and the
    shortfall must stay visible instead of quietly vanishing.
    """
    city, _, near, _ = await _city(session, catalog)
    identity, _ = await _taxed_house(
        session, constants, catalog, where=near, city=city, funds=0
    )
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
    session.add(Building(
        node_id=core.id, area_m2=20, footprint_m2=20, floors=1,
        kind=estate.kinds(constants)[0],
    ))
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
