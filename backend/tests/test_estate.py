"""Real estate: plot purchase, deed, building (D-089, D-106, D-116).

Checked is what the system was introduced for:

* an empty civic plot is bought by whoever the code-law `build_permit`
  allows (citizens by default, D-160), the price depends on the distance to
  the bioprinter, the proceeds go to the city treasury;
* ownership is documented by a deed; the deed is sold by a sale contract, and
  the title to the node passes with it;
* a building is built on one's own plot from materials and on schedule; a
  machine without a building does not stand (see `test_station`).
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


async def test_occupied_wild_land_also_gives_deed(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The title is one for all roads to land: took -- a deed, bought -- a deed."""
    stamp = uuid.uuid4().hex[:6]
    wild = await world.create_node(
        session, f"terra.wild.{stamp}", "Дикий", area_m2=200, layer=Layer.PLANET
    )
    identity, body = await _buyer(session, wild, funds=0)
    await world.claim_node(session, body, wild)

    deed = (
        await session.execute(select(Deed).where(Deed.node_id == wild.id))
    ).scalar_one()
    assert deed.owner_identity_id == identity.id
    assert deed.paid == 0


# --- building (D-106, D-125) -------------------------------------------------


async def test_construction_spends_materials_and_places_building_on_term(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    stamp = uuid.uuid4().hex[:6]
    plot = await world.create_node(
        session, f"terra.plot.{stamp}", "Участок", area_m2=100, layer=Layer.PLANET
    )
    identity, body = await _buyer(session, plot, funds=0)
    await world.claim_node(session, body, plot)

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
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    from src.engine import craft

    stamp = uuid.uuid4().hex[:6]
    plot = await world.create_node(
        session, f"terra.plot.{stamp}", "Участок", area_m2=100, layer=Layer.PLANET
    )
    _, body = await _buyer(session, plot, funds=0)
    await world.claim_node(session, body, plot)
    with pytest.raises(craft.NotEnough):
        await estate.construct(session, constants, body, plot, 20)


async def test_building_no_larger_than_plot(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    stamp = uuid.uuid4().hex[:6]
    plot = await world.create_node(
        session, f"terra.plot.{stamp}", "Участок", area_m2=50, layer=Layer.PLANET
    )
    _, body = await _buyer(session, plot, funds=0)
    await world.claim_node(session, body, plot)
    with pytest.raises(estate.NoRoom):
        await estate.construct(session, constants, body, plot, 60)


async def test_no_building_on_foreign_land(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    stamp = uuid.uuid4().hex[:6]
    plot = await world.create_node(
        session, f"terra.plot.{stamp}", "Участок", area_m2=100, layer=Layer.PLANET
    )
    owner, owner_body = await _buyer(session, plot, funds=0)
    await world.claim_node(session, owner_body, plot)

    _, foreign_body = await _buyer(session, plot, funds=0)
    with pytest.raises(estate.EstateError):
        await estate.construct(session, constants, foreign_body, plot, 10)
