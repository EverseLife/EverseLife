# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The household meter: who pays and what happens if they do not (D-135, D-149).

Checked is exactly what the meter exists for:

* the **holder** pays, and for civic -- the treasury, and not in money but in
  energy it could have sold;
* an unowned node produces no bill at all: nobody to pay, and money has
  nowhere to vanish (I2);
* did not pay -- the node is disconnected, and its machines do not work until payment;
* outside a city there is no meter: there is no grid.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import access, craft, energy, estate, ledger, utility, world
from src.engine import city as town
from src.models.city import UtilityMeter
from src.models.estate import Deed
from src.models.event import Event, EventKind
from src.models.ledger import AccountKind, PostingReason
from src.models.world import PLOT, Layer
from src.units import money
from utility_kit import _city, _pool, _resident, _yesterday

#: What a meter says about a house it bills.
_METER_EVENTS = (EventKind.UTILITY_METERED, EventKind.UTILITY_CUT_OFF)


async def test_ownerless_node_has_no_meter(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Nobody to bill -- so there is no meter either."""
    _, _, home = await _city(session, catalog)
    home.owner_city_id = None
    await session.flush()
    assert await utility.meter_of(session, home) is None


async def test_no_meter_outside_city(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """There is no grid there: one works from a battery, and there are no utility relations."""
    identity = await world.create_identity(session, f"Ферма-{uuid.uuid4().hex[:6]}")
    floodplain = await world.create_node(
        session,
        f"terra.wild.{uuid.uuid4().hex[:8]}",
        "Пойма",
        area_m2=400,
        layer=Layer.PLANET,
    )
    floodplain.owner_identity_id = identity.id
    await session.flush()
    assert await utility.meter_of(session, floodplain) is None


async def test_holder_pays_for_household(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The bill is computed from area and written off at the city tariff (D-135)."""
    city, delegate, home = await _city(session, catalog)
    owner, _ = await _resident(session, home, "Хозяин", funds=100)
    home.owner_identity_id = owner.id
    await session.flush()

    pool = await _pool(session, constants, home, 100_000)
    meter = await utility.meter_of(session, home)
    meter.counted_at = _yesterday(constants)
    await session.flush()

    accrued = await utility.bill(session, constants, home)
    assert accrued > 0

    account = await ledger.account_for(session, AccountKind.IDENTITY, owner.id)
    assert await ledger.balance(session, account.id) == money(100) - accrued
    #: Money went to the city treasury, energy from the pool: the meter does
    #: not invent the spend, it writes it off.
    assert await town.treasury_balance(session, city) == accrued
    assert float(pool.stored) < 100_000
    assert not meter.cut_off


async def test_treasury_pays_for_civic_with_energy(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The city does not pay itself in money, but pays in energy (D-149)."""
    city, delegate, home = await _city(session, catalog)
    pool = await _pool(session, constants, home, 100_000)
    before = float(pool.stored)

    meter = await utility.meter_of(session, home)
    meter.counted_at = _yesterday(constants)
    await session.flush()

    assert await utility.bill(session, constants, home) == 0, "казна не платит себе"
    assert float(pool.stored) < before, "энергия всё равно ушла"
    assert await town.treasury_balance(session, city) == 0


async def test_node_disconnected_when_unable_to_pay(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The debt stays on the node, the node is disconnected. The engine may not take it."""
    city, delegate, home = await _city(session, catalog)
    owner, body = await _resident(session, home, "Бедняк")
    home.owner_identity_id = owner.id
    await session.flush()

    await _pool(session, constants, home, 100_000)
    meter = await utility.meter_of(session, home)
    meter.counted_at = _yesterday(constants)
    await session.flush()

    accrued = await utility.bill(session, constants, home)
    assert accrued > 0
    assert meter.cut_off and meter.debt == accrued
    assert await utility.cut_off(session, home)

    #: A disconnected node does not run machines: the meter is as much a
    #: condition of work as the machine itself (D-149).
    yard = await world.node_container(session, home)
    await world.grant_item(session, yard, "workbench", quality=60, origin="сценарий теста")
    await world.learn(session, owner, "shaft_support")
    with pytest.raises(craft.CutOff):
        await craft.plan(session, constants, catalog, body, "shaft_support", 1)


async def test_payment_reconnects_node(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    city, delegate, home = await _city(session, catalog)
    owner, _ = await _resident(session, home, "Должник")
    home.owner_identity_id = owner.id
    await session.flush()

    await _pool(session, constants, home, 100_000)
    meter = await utility.meter_of(session, home)
    meter.counted_at = _yesterday(constants)
    await session.flush()
    debt = await utility.bill(session, constants, home)
    assert meter.cut_off

    #: Still no money -- nothing to pay with, and that is a refusal, not silence.
    with pytest.raises(utility.NotEnoughMoney):
        await utility.pay(session, constants, owner, home)

    account = await ledger.account_for(session, AccountKind.IDENTITY, owner.id)
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    await ledger.transfer(
        session,
        PostingReason.GENESIS,
        debit=genesis.id,
        credit=account.id,
        amount=debt,
    )
    assert await utility.pay(session, constants, owner, home) == debt
    assert not meter.cut_off and meter.debt == 0
    assert await town.treasury_balance(session, city) == debt


async def test_cannot_pay_foreign_bill(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Other people's bills are paid by contract, not by the engine."""
    _, _, home = await _city(session, catalog)
    owner, _ = await _resident(session, home, "Хозяин")
    foreign, _ = await _resident(session, home, "Чужой", funds=100)
    home.owner_identity_id = owner.id
    await session.flush()
    await _pool(session, constants, home, 100_000)
    meter = await utility.meter_of(session, home)
    meter.counted_at = _yesterday(constants)
    await session.flush()
    await utility.bill(session, constants, home)

    with pytest.raises(utility.UtilityError) as refused:
        await utility.pay(session, constants, foreign, home)
    assert refused.value.key == "utility-node-not-yours"


async def test_meter_opens_itself_on_occupied_nodes(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Otherwise the first bill has nowhere to come from: the meter would wait for itself."""
    _, _, home = await _city(session, catalog)
    assert await utility.meter_of(session, home, create=False) is None
    listed = await utility.run_meters(session, constants)
    assert listed >= 1
    assert await utility.meter_of(session, home, create=False) is not None


async def test_a_run_bills_every_house_and_pays_what_the_purse_covers(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """One holder, two houses on one grid, a purse for one bill (D-149).

    The run draws the pool for both, then posts the bills: the first the purse
    covers is paid, the second is a debt and a cut-off -- never a part of it.
    """
    moment = datetime.now(UTC)
    hours = constants[R.ENERGY_METER_PERIOD]
    city, delegate, home = await _city(session, catalog)
    other = await world.create_node(
        session, f"{home.key}.two", "Дом", area_m2=100, parent=delegate, properties={PLOT: True}
    )
    other.owner_city_id = city.id
    owner, _ = await _resident(session, home, "Хозяин")
    home.owner_identity_id = other.owner_identity_id = owner.id
    pool = await _pool(session, constants, home, 100_000)
    meters = [await utility.meter_of(session, node) for node in (home, other)]
    for meter in meters:
        assert meter is not None
        meter.counted_at = moment - timedelta(hours=hours)
    drawn = utility.draw_for(constants, home, hours)
    price = energy.price_at(constants, pool, drawn)
    assert price > 1
    account = await ledger.account_for(session, AccountKind.IDENTITY, owner.id)
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    await ledger.transfer(
        session,
        PostingReason.GENESIS,
        debit=genesis.id,
        credit=account.id,
        amount=price + price // 2,
    )

    assert await utility.run_meters(session, constants, now=moment) == 2

    assert sorted(meter.debt for meter in meters) == [0, price]
    assert sorted(meter.cut_off for meter in meters) == [False, True]
    assert all(meter.counted_at == moment for meter in meters)
    assert await ledger.balance(session, account.id) == price // 2
    assert await town.treasury_balance(session, city) == price
    assert float(pool.stored) == pytest.approx(100_000 - 2 * drawn, abs=0.01)
    told = (
        (
            await session.execute(
                select(Event.kind).where(
                    Event.node_id.in_([home.id, other.id]), Event.kind.in_(_METER_EVENTS)
                )
            )
        )
        .scalars()
        .all()
    )
    assert sorted(told) == sorted(
        [EventKind.UTILITY_CUT_OFF, EventKind.UTILITY_METERED, EventKind.UTILITY_METERED]
    )


async def test_a_free_grid_counts_the_hours_and_bills_nothing(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """At a tariff of nought a holder's house draws the pool and owes nothing:
    the hours are counted, and no bill, no debt and no cut-off are written."""
    moment = datetime.now(UTC)
    hours = constants[R.ENERGY_METER_PERIOD]
    _, _, home = await _city(session, catalog)
    owner, _ = await _resident(session, home, "Хозяин")
    home.owner_identity_id = owner.id
    pool = await _pool(session, constants, home, 100_000)
    pool.tariff = Decimal(0)
    meter = await utility.meter_of(session, home)
    assert meter is not None
    meter.counted_at = moment - timedelta(hours=hours)

    assert await utility.run_meters(session, constants, now=moment) == 1

    assert meter.counted_at == moment and meter.debt == 0 and not meter.cut_off
    assert float(pool.stored) == pytest.approx(
        100_000 - utility.draw_for(constants, home, hours), abs=0.01
    )
    told = (
        (
            await session.execute(
                select(Event).where(Event.node_id == home.id, Event.kind.in_(_METER_EVENTS))
            )
        )
        .scalars()
        .all()
    )
    assert told == []


async def _due_only(
    session: AsyncSession, constants: Constants, moment: datetime, *nodes
) -> list[UtilityMeter]:
    """Every meter of the world opened and counted up to `moment`, and only the
    meters of `nodes` a period due."""
    await utility.ensure_meters(session, constants)
    await session.execute(update(UtilityMeter).values(counted_at=moment))
    meters = []
    for node in nodes:
        meter = await utility.meter_of(session, node, create=False)
        assert meter is not None
        meter.counted_at = moment - timedelta(hours=constants[R.ENERGY_METER_PERIOD])
        meters.append(meter)
    await session.flush()
    return meters


async def test_the_meter_run_posts_every_purse_into_its_own_city(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The run takes every pool first and posts the money after, purse by purse:
    a purse that cannot pay cuts its own node off and the purses after it pay
    on, each into the treasury of the city whose pool gave the energy (D-149)."""
    moment = datetime.now(UTC)
    north, _, north_plot = await _city(session, catalog, "Северная")
    south, _, south_plot = await _city(session, catalog, "Южная")
    for plot in (north_plot, south_plot):
        await _pool(session, constants, plot, 100_000)
    holders = []
    for name in ("Первый", "Второй"):
        identity, _ = await _resident(session, north_plot, name)
        account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
        holders.append((account.id, identity))
    #: The empty purse is posted first, so the one after it is seen to pay on.
    (_, broke), (purse_id, payer) = sorted(holders, key=lambda holder: holder[0])
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    await ledger.transfer(
        session, PostingReason.GENESIS, debit=genesis.id, credit=purse_id, amount=money(100)
    )
    north_plot.owner_identity_id = broke.id
    south_plot.owner_identity_id = payer.id
    unpaid, paid = await _due_only(session, constants, moment, north_plot, south_plot)

    assert await utility.run_meters(session, constants, now=moment) == 2

    #: Two plots of one area, one period, one tariff: one price.
    price = unpaid.debt
    assert price > 0 and unpaid.cut_off
    assert paid.debt == 0 and not paid.cut_off
    assert unpaid.counted_at == paid.counted_at == moment
    assert await ledger.balance(session, purse_id) == money(100) - price
    assert await town.treasury_balance(session, south) == price
    assert await town.treasury_balance(session, north) == 0, "a refused bill pays nobody"


async def test_a_short_pool_goes_to_the_meters_in_their_order(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """What a pool holds goes to its meters in the run's order -- the meters'
    ids, the order they are locked in, not a rule of who deserves the energy --
    and the last one gets what is left: a city without fuel cannot release
    what it does not have."""
    moment = datetime.now(UTC)
    _, delegate, home = await _city(session, catalog)
    annex = await world.create_node(
        session,
        f"{delegate.key}.annex",
        "Флигель",
        area_m2=100,
        parent=delegate,
        properties={PLOT: True},
    )
    annex.owner_city_id = home.owner_city_id
    need = utility.draw_for(constants, home, constants[R.ENERGY_METER_PERIOD])
    pool = await _pool(session, constants, home, need * 1.5)
    meters = await _due_only(session, constants, moment, home, annex)

    await utility.run_meters(session, constants, now=moment)

    first, second = sorted(meters, key=lambda meter: meter.id)
    assert float(first.last_energy) == pytest.approx(need, abs=0.01)
    assert float(second.last_energy) == pytest.approx(need / 2, abs=0.01)
    assert float(pool.stored) == pytest.approx(0, abs=0.01)


async def test_holdings_show_own_nodes(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """An empty list is not a broken panel but "no holdings"."""
    _, _, home = await _city(session, catalog)
    owner, _ = await _resident(session, home, "Хозяин")
    assert await utility.holdings(session, constants, owner.id) == []

    home.owner_identity_id = owner.id
    await session.flush()
    own_items = await utility.holdings(session, constants, owner.id)
    assert len(own_items) == 1
    assert own_items[0]["node"] == home.key
    assert own_items[0]["grid"] is True
    assert own_items[0]["cost_per_period"] > 0


async def test_payer_of_reads_the_three_lines(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Who the bill belongs to, from the outside: holder, city, nobody, no grid."""
    _, _, home = await _city(session, catalog)
    assert await utility.payer_of(session, home) == utility.PAYER_CITY

    owner, _ = await _resident(session, home, "Хозяин")
    home.owner_identity_id = owner.id
    await session.flush()
    assert await utility.payer_of(session, home) == utility.PAYER_OWNER

    home.owner_identity_id = None
    home.owner_city_id = None
    await session.flush()
    assert await utility.payer_of(session, home) == utility.PAYER_NOBODY

    outside = await world.create_node(
        session,
        f"terra.wild.{uuid.uuid4().hex[:8]}",
        "Пойма",
        area_m2=400,
        layer=Layer.PLANET,
    )
    assert await utility.payer_of(session, outside) is None, "за городом счётчика нет"


async def test_cede_moves_the_bill_to_the_city(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A plot handed back stops being a person's bill and becomes the city's energy."""
    city, _, home = await _city(session, catalog)
    owner, body = await _resident(session, home, "Хозяин", funds=100)
    home.owner_identity_id = owner.id
    await session.flush()
    await estate.issue_deed(session, home, owner.id)

    pool = await _pool(session, constants, home, 100_000)
    meter = await utility.meter_of(session, home)
    meter.counted_at = _yesterday(constants)
    await session.flush()
    assert await utility.bill(session, constants, home) > 0, "пока узел свой — платит хозяин"

    await town.cede(session, body, home)

    assert home.owner_identity_id is None
    assert home.owner_city_id == city.id, "земля остаётся городской"
    assert await utility.payer_of(session, home) == utility.PAYER_CITY
    #: The deed is cancelled: civic land is not traded by deed.
    assert (
        await session.execute(select(Deed).where(Deed.node_id == home.id))
    ).scalar_one_or_none() is None

    treasury_before = await town.treasury_balance(session, city)
    account = await ledger.account_for(session, AccountKind.IDENTITY, owner.id)
    money_before = await ledger.balance(session, account.id)
    stored_before = float(pool.stored)

    meter.counted_at = _yesterday(constants)
    await session.flush()
    assert await utility.bill(session, constants, home) == 0, "теперь содержит город"
    assert await ledger.balance(session, account.id) == money_before, "с бывшего хозяина не берут"
    assert await town.treasury_balance(session, city) == treasury_before
    assert float(pool.stored) < stored_before, "город платит энергией, а не монетой"


async def test_cede_refuses_a_node_in_debt(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A debt is not handed over with the ground: it would be a way to write it off."""
    _, _, home = await _city(session, catalog)
    owner, body = await _resident(session, home, "Должник")
    home.owner_identity_id = owner.id
    await session.flush()

    await _pool(session, constants, home, 100_000)
    meter = await utility.meter_of(session, home)
    meter.counted_at = _yesterday(constants)
    await session.flush()
    await utility.bill(session, constants, home)
    assert meter.debt > 0 and meter.cut_off

    #: By the key, not by the sentence: the wording is the locale's (D-251 III).
    with pytest.raises(town.CityError) as refused:
        await town.cede(session, body, home)
    assert refused.value.key == "city-land-debt"
    assert home.owner_identity_id == owner.id


async def test_cede_refuses_somebody_elses_plot(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The city is handed one's own: a plot is not given away over the holder's head."""
    _, _, home = await _city(session, catalog)
    owner, _ = await _resident(session, home, "Хозяин")
    stranger, guest = await _resident(session, home, "Гость")
    home.owner_identity_id = owner.id
    await session.flush()

    with pytest.raises(town.NotYours):
        await town.cede(session, guest, home)
    assert home.owner_identity_id == owner.id


async def test_cede_refuses_a_deed_on_the_market(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A paper up for sale first comes off the auction: the buyer must not pay for nothing."""
    _, _, home = await _city(session, catalog)
    owner, body = await _resident(session, home, "Продавец")
    home.owner_identity_id = owner.id
    await session.flush()
    deed = await estate.issue_deed(session, home, owner.id)
    deed.sale_price = money(10)
    await session.flush()

    #: By the key, not by the sentence: the wording is the locale's (D-251 III).
    with pytest.raises(town.CityError) as refused:
        await town.cede(session, body, home)
    assert refused.value.key == "city-land-deed-on-sale"
    assert home.owner_identity_id == owner.id


async def test_a_reclaimed_location_is_not_left_cut_off(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A location taken back comes back alive, debt and cut-off cleared (D-282).

    `cede` may refuse a debtor -- the holder settles up and gives the plot back
    afterwards. The city taking its own location back cannot refuse: it is
    undoing the engine's own mistake. And a debt left on a node without a
    holder can never be paid: `utility.bill` charges the treasury nothing and
    `utility.pay` takes payment only from the holder, of whom there is none. So
    the meter would stay cut off for ever, and with it the core would come back
    to the city with neither craft nor council possible in it.
    """
    city, _, home = await _city(session, catalog)
    owner, _ = await _resident(session, home, "Захвативший")
    #: Not a plot -- the city's own location, which is why it comes back.
    home.properties = {}
    home.owner_identity_id = owner.id
    await session.flush()

    meter = await utility.meter_of(session, home)
    meter.debt = money(50)
    meter.cut_off = True
    await session.flush()

    assert await town.reclaim(session, home, city) is True

    assert home.owner_identity_id is None
    again = await utility.meter_of(session, home, create=False)
    assert again is not None
    assert again.debt == 0, "долг некому платить: город сам себе счёта не выставляет"
    assert not again.cut_off, "возвращённая локация не остаётся отрезанной навсегда"


async def test_cede_takes_the_door_down(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Civic land has no door: a shut gate left on it would be a lock nobody can open."""
    _, _, home = await _city(session, catalog)
    owner, body = await _resident(session, home, "Хозяин")
    stranger, _ = await _resident(session, home, "Гость")
    home.owner_identity_id = owner.id
    await session.flush()
    await access.set_gate(session, home, owner, closed=True)
    await access.add(session, home, owner, stranger, allowed=False)

    await town.cede(session, body, home)

    assert not home.gated
    assert await access.may_enter(session, home, stranger.id), "город впускает всякого"
