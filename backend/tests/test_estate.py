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

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import city as town
from src.engine import estate, ledger, world
from src.models.city import Citizen
from src.models.estate import Deed
from src.models.job import JobState
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Layer, Node, Surface
from src.units import PERCENT, money


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
    """The price falls with each ring from the bioprinter -- the city centre."""
    city, _, near, far = await _city(session, catalog)
    close = await estate.price_of(session, constants, catalog, city, near)
    far_away = await estate.price_of(session, constants, catalog, city, far)

    assert close > far_away > 0
    decline = 1 - constants[R.LAND_PRICE_DECAY_PER_RING] / PERCENT
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
    norms = constants[R.BUILD_MATERIALS_PER_M2]
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
    """Height is paid for: each next floor costs `floor_cost_growth` (D-125)."""
    flat = estate.estimate(constants, footprint=40, floors=1, strength=1)
    tall = estate.estimate(constants, footprint=20, floors=2, strength=1)

    assert sum(tall.values()) > sum(flat.values()), (
        "двадцать метров в два этажа дороже сорока в один: за высоту платят"
    )
    #: And a two-storey house takes half the ground -- that is what it is for.
    assert estate.build_minutes(constants, footprint=20, floors=2, strength=1) > 0


def test_tier_sets_the_ceiling_of_height(constants: Constants) -> None:
    """Timber holds two floors, steel holds eight (D-145)."""
    assert estate.height_cap(constants, 1) < estate.height_cap(constants, 3)


async def test_house_taller_than_the_tier_is_refused(
    session: AsyncSession, constants: Constants, catalog: Catalog, own_plot
) -> None:
    stamp = uuid.uuid4().hex[:6]
    plot = await world.create_node(
        session, f"terra.plot.{stamp}", "Участок", area_m2=100, layer=Layer.PLANET
    )
    identity, body = await _buyer(session, plot, funds=0)
    await own_plot(plot, identity)

    over = estate.height_cap(constants, 1) + 1
    with pytest.raises(estate.TooTall):
        await estate.construct(session, constants, body, plot, 10, floors=over)


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
    needed = estate.estimate(constants, footprint=10, floors=2, strength=1)
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
        constants, footprint=area, floors=floors, strength=1
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
    spent = estate.estimate(constants, footprint=20.0, floors=1, strength=1)

    share = constants[R.BUILD_DEMOLISH_SALVAGE]
    for name, quantity in back.items():
        assert quantity == pytest.approx(spent[name] * share), (
            "возвращается доля сметы, а не смета"
        )

    job = await estate.demolish(session, constants, body, plot)
    assert await estate.built_area(session, plot) > 0, "снос не мгновенен"
    minutes = estate.demolish_minutes(constants, houses)
    assert (job.run_at - datetime.now(UTC)).total_seconds() / 60 == pytest.approx(
        minutes, rel=0.05
    )
    assert minutes < estate.build_minutes(
        constants, footprint=20.0, floors=1, strength=1
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
    quantity = kilos / gear.mass_of(catalog, "Брус", 1)

    pocket = await world.body_container(session, owner)
    goods = await world.grant_item(
        session, pocket, "Брус", amount=quantity, quality=55, origin="тест"
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
        constants, footprint=20.0, floors=1, strength=1
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
