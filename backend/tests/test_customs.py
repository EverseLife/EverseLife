"""Customs: rate, norm, ban (D-123).

Checked is what the duty was introduced this way for:

* the norm separates household carriage from trade and is counted **per
  window**, not per trip: otherwise it is dodged by splitting the cargo into ten runs;
* no deals -- no reference price, and nothing to take the duty from;
* the ban is absolute: the forbidden does not pass for any money;
* nothing to pay with -- the goods do not pass, but no debt arises;
* a step inside your own city knows no customs.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import city as town
from src.engine import customs, ledger, market, travel, world
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Layer, Surface
from src.units import money

ORE = "Железная руда"


async def _world(session: AsyncSession, catalog: Catalog):
    """A city with a market and an unowned floodplain beyond the gate."""
    stamp = uuid.uuid4().hex[:8]
    planet = await world.create_node(
        session, f"terra.{stamp}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    delegate = await world.create_node(
        session, f"terra.city.{stamp}", "Столица", area_m2=1,
        layer=Layer.PLANET, parent=planet,
    )
    marketplace = await world.create_node(
        session, f"terra.city.{stamp}.market", "Торг", area_m2=200,
        parent=delegate,
    )
    #: The gate says so about itself: a road beyond the walls is tied to the
    #: city's door and to nothing else (D-206), and the border runs right here.
    gate = await world.create_node(
        session, f"terra.city.{stamp}.gate", "Ворота", area_m2=80,
        parent=delegate, properties={travel.EXIT: True},
    )
    field = await world.create_node(
        session, f"terra.field.{stamp}", "Пойма", area_m2=400,
        layer=Layer.PLANET, parent=planet,
    )
    city = await town.found(session, catalog, delegate, "Столица")
    for node in (marketplace, gate):
        node.owner_city_id = city.id
    await session.flush()

    await travel.connect(session, marketplace, gate, base_seconds=10, surface=Surface.PAVED)
    await travel.connect(session, gate, field, base_seconds=60, surface=Surface.ROAD)
    yard = await world.node_container(session, marketplace)
    await world.grant_item(session, yard, "Терминал маркетплейса", quality=70, origin="тест")
    return city, marketplace, gate, field


async def _merchant(session: AsyncSession, node, name: str, *, funds: float = 0, ore=0.0):
    identity, body = await world.spawn(session, f"{name}-{uuid.uuid4().hex[:6]}", node)
    if funds:
        account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
        genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
        await ledger.transfer(
            session, PostingReason.GENESIS,
            debit=genesis.id, credit=account.id, amount=money(funds),
        )
    if ore:
        pocket = await world.body_container(session, body)
        await world.grant_item(
            session, pocket, ORE, amount=ore, quality=60, origin="тест"
        )
    return identity, body


async def _deal(
    session: AsyncSession, constants: Constants, catalog: Catalog, node, price: float
) -> None:
    """One deal in the book: without it the city has no reference price (D-123)."""
    seller, seller_body = await _merchant(session, node, "Продавец", ore=10)
    buyer, buyer_body = await _merchant(session, node, "Покупатель", funds=200)
    tier = market.tier_of(constants, 60)
    await market.load(session, constants, seller_body, ORE, 10)
    await market.sell(
        session, constants, catalog, seller, node,
        type_key=ORE, tier=tier, price=money(price), quantity=10,
    )
    await market.buy(
        session, constants, catalog, buyer_body,
        type_key=ORE, tier=tier, price=money(price), quantity=10,
    )


async def _duty(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    city,
    rate: float,
    norm: float,
    direction: str = customs.EXPORT,
) -> None:
    city.laws = {
        **(city.laws or {}),
        f"{direction}_duty": {ORE: {"rate": rate, "free": norm}},
    }
    await session.flush()


# --- border ------------------------------------------------------------------


async def test_step_inside_city_knows_no_customs(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    city, marketplace, gate, _ = await _world(session, catalog)
    await _deal(session, constants, catalog, marketplace, 3)
    await _duty(session, constants, catalog, city, rate=50, norm=0)

    _, body = await _merchant(session, marketplace, "Свой", funds=100, ore=20)
    charges = await customs.cross(
        session, constants, catalog, body, marketplace, gate
    )
    assert charges == [], "внутри города границы нет"


async def test_export_taxed_by_reference_price(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The duty is a share of the median of the city book's deals (D-123)."""
    city, marketplace, gate, field = await _world(session, catalog)
    await _deal(session, constants, catalog, marketplace, 3)
    await _duty(session, constants, catalog, city, rate=10, norm=0)

    identity, body = await _merchant(session, gate, "Вывозящий", funds=100, ore=20)
    before = await _balance(session, identity.id)
    #: The treasury already holds the tax from the deal that gave the reference
    #: price: we measure the transit's income, not the balance.
    treasury_was = await town.treasury_balance(session, city)
    charges = await customs.cross(session, constants, catalog, body, gate, field)

    assert len(charges) == 1 and charges[0].direction == customs.EXPORT
    #: Twenty units at three TC, ten percent -- six TC.
    assert charges[0].duty == money(6)
    assert await _balance(session, identity.id) == before - money(6)
    assert await town.treasury_balance(session, city) == treasury_was + money(6)


async def test_no_duty_without_deals(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A city whose market is empty cannot tax what it does not know the price of."""
    city, _, gate, field = await _world(session, catalog)
    await _duty(session, constants, catalog, city, rate=50, norm=0)

    _, body = await _merchant(session, gate, "Вывозящий", funds=100, ore=20)
    charges = await customs.cross(session, constants, catalog, body, gate, field)
    assert charges[0].duty == 0


async def test_norm_separates_household_from_trade(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A newcomer with a sack of turnips pays nothing, a wholesaler pays for everything above the
    norm."""
    city, marketplace, gate, field = await _world(session, catalog)
    await _deal(session, constants, catalog, marketplace, 3)
    #: The norm is in kilograms, ore is a kilogram per unit (D-146).
    await _duty(session, constants, catalog, city, rate=10, norm=30)

    _, small = await _merchant(session, gate, "Житель", funds=100, ore=20)
    charges = await customs.cross(session, constants, catalog, small, gate, field)
    assert charges[0].duty == 0, "меньше нормы — бесплатно"

    _, wholesaler = await _merchant(session, gate, "Оптовик", funds=100, ore=50)
    charges = await customs.cross(session, constants, catalog, wholesaler, gate, field)
    #: Twenty units above the norm at three TC, ten percent -- six TC.
    assert charges[0].duty == money(6)


async def test_norm_counted_per_window_not_per_trip(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A norm reset by splitting the cargo into runs cannot be called a norm."""
    city, marketplace, gate, field = await _world(session, catalog)
    await _deal(session, constants, catalog, marketplace, 3)
    await _duty(session, constants, catalog, city, rate=10, norm=30)

    identity, body = await _merchant(session, gate, "Хитрый", funds=100, ore=20)
    first = await customs.cross(session, constants, catalog, body, gate, field)
    assert first[0].duty == 0

    #: A second trip with the same body. The cargo is new: the old is already
    #: hauled out, but the norm is not -- it is counted per window and remembers the last run.
    from sqlalchemy import select

    from src.models.inventory import Item

    pocket = await world.body_container(session, body)
    past = (
        await session.execute(
            select(Item).where(Item.container_id == pocket.id, Item.type_key == ORE)
        )
    ).scalars().all()
    for thing in past:
        await session.delete(thing)
    await session.flush()
    await world.grant_item(
        session, pocket, ORE, amount=20, quality=60, origin="тест"
    )
    second = await customs.cross(session, constants, catalog, body, gate, field)
    assert second[0].duty > 0, "норма исчерпана прошлой ходкой"
    assert await customs.moved_in_window(
        session, constants, identity.id, city, customs.EXPORT, ORE
    ) == pytest.approx(40)


async def test_ban_is_absolute(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The extreme measure: the forbidden does not pass for any money."""
    city, marketplace, gate, field = await _world(session, catalog)
    city.laws = {**(city.laws or {}), "export_ban": ORE}
    await session.flush()

    _, body = await _merchant(session, gate, "Контрабандист", funds=1000, ore=5)
    with pytest.raises(customs.Banned):
        await customs.cross(session, constants, catalog, body, gate, field)


async def test_goods_do_not_pass_without_means_to_pay(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """No debt arises here: customs does not lend (D-123)."""
    city, marketplace, gate, field = await _world(session, catalog)
    await _deal(session, constants, catalog, marketplace, 3)
    await _duty(session, constants, catalog, city, rate=50, norm=0)

    identity, body = await _merchant(session, gate, "Бедный", ore=50)
    with pytest.raises(customs.CannotPay):
        await customs.cross(session, constants, catalog, body, gate, field)
    assert await _balance(session, identity.id) == 0, "долга не возникает"


async def test_transit_does_not_start_without_duty(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The border is settled before leaving: otherwise the unpaid enters the city."""
    city, marketplace, gate, field = await _world(session, catalog)
    await _deal(session, constants, catalog, marketplace, 3)
    await _duty(session, constants, catalog, city, rate=50, norm=0)

    _, body = await _merchant(session, gate, "Бедный", ore=50)
    with pytest.raises(customs.CannotPay):
        await travel.depart(session, constants, body, field)
    assert await travel.current(session, body) is None, "переход не начался"


async def test_imports_and_exports_land_in_summary(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """"Imported and exported by goods, in weight and trips" is the panel line (D-124)."""
    from datetime import UTC, datetime, timedelta

    from src.engine import panel

    city, marketplace, gate, field = await _world(session, catalog)
    await _deal(session, constants, catalog, marketplace, 3)
    await _duty(session, constants, catalog, city, rate=10, norm=0)

    _, body = await _merchant(session, gate, "Возчик", funds=100, ore=20)
    await customs.cross(session, constants, catalog, body, gate, field)

    summary = await panel.collect(session, constants, city)
    trade = summary["trade"]
    assert trade["exported"][ORE] == pytest.approx(20)
    assert trade["trips_out"] == 1
    assert trade["duty_collected"] > 0

    #: And the same directly from customs -- by one formula (D-139).
    direct = await customs.traffic(
        session, constants, city, since=datetime.now(UTC) - timedelta(hours=1)
    )
    assert direct["exported"] == trade["exported"]


async def _balance(session: AsyncSession, identity_id) -> int:
    account = await ledger.account_for(session, AccountKind.IDENTITY, identity_id)
    return await ledger.balance(session, account.id)


def test_numeric_rate_means_on_everything(catalog: Catalog) -> None:
    """The law is read in two ways, both honest (D-123)."""
    from src.models.city import City

    city = City(node_id=uuid.uuid4(), name="Тест", charter={}, charter_params={},
                 laws={"import_duty": "12"})
    rates = customs.rates(catalog, city, customs.IMPORT)
    assert rates["*"]["rate"] == 12 and rates["*"]["free"] == 0

    city.laws = {"import_duty": {ORE: {"rate": 5, "free": 10}}}
    rates = customs.rates(catalog, city, customs.IMPORT)
    assert rates[ORE] == {"rate": 5, "free": 10}
    assert R.TRADE_DUTY_FREE_WINDOW.key == "trade.duty_free_window"


async def test_autopath_breaks_at_border(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Not let through -- the route ends here rather than dropping the journal job.

    Otherwise a customs refusal would turn into an eternally repeating job, and
    the body would hang mid-leg.
    """

    from sqlalchemy import select

    from src.models.job import Job, JobKind, JobState

    city, marketplace, gate, field = await _world(session, catalog)
    await _deal(session, constants, catalog, marketplace, 3)
    await _duty(session, constants, catalog, city, rate=50, norm=0)

    _, body = await _merchant(session, marketplace, "Бедный", ore=50)
    #: Autopath: the first leg inside the city, the second across the border.
    await travel.depart(session, constants, body, field)
    job = (
        await session.execute(
            select(Job).where(
                Job.kind == JobKind.TRAVEL_LEG.value, Job.state == JobState.PENDING
            )
        )
    ).scalars().first()
    assert job is not None
    await travel.arrive(session, job)

    #: Reached the gate and stopped: not let further, but the job did its work.
    assert body.node_id == gate.id
    assert await travel.current(session, body) is None
