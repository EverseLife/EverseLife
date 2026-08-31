# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""City orders on the works board, and the treasury as a borrower (D-248, wave 3).

Checked is what the wave was built for:

* a city order escrows both pockets at posting -- the city's offer plus its
  labour share from the treasury, the fund's share from the fund -- and an
  empty pocket refuses the posting, not the payout;
* the open order is a licence: a stranger may mend or build on the city's
  plot exactly while it hangs, and exactly what it names;
* the engine pays on its own verification -- houses whole, the ordered house
  standing, fuel in the station -- the city's part in full, the fund's under
  the daily cap; a withdrawn order returns each remainder to its pocket;
* fuel is paid per unit as it lands, and two pours cannot collect one unit
  twice;
* the treasury borrows at the key rate with no margin on the city's own
  line, and insolvency machinery never touches a loan with no identity.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import bank, energy, ledger, works, works_city, world
from src.engine import city as town
from src.engine.estate.building import build_minutes, kinds
from src.engine.estate.upkeep import finish_repair, repair, repair_bill, repair_minutes
from src.models.estate import Building
from src.models.ledger import AccountKind, PostingReason
from src.models.market import Order, OrderSide, Trade
from src.models.works import WorkOrderKind, WorkOrderState
from src.models.world import Layer, Node
from src.units import PERCENT, money


async def _city_with_ruler(session: AsyncSession, catalog: Catalog, *, funds: float = 0):
    """A city, its core with the administration, and a ruler standing in it."""
    stamp = uuid.uuid4().hex[:8]
    planet = await world.create_node(
        session, f"terra.{stamp}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    delegate = await world.create_node(
        session,
        f"terra.city.{stamp}",
        f"Город-{stamp}",
        area_m2=1,
        layer=Layer.PLANET,
        parent=planet,
    )
    core = await world.create_node(
        session, f"terra.city.{stamp}.core", "Ядро", area_m2=100, parent=delegate
    )
    city = await town.found(session, catalog, delegate, f"Город-{stamp}")
    core.owner_city_id = city.id
    await session.flush()
    yard = await world.node_container(session, core)
    await world.grant_item(session, yard, town.HALL, quality=65, origin="тест")

    ruler = await world.create_identity(session, f"Мэр-{stamp}")
    ruler_body = await world.print_body(session, ruler, core)
    await town.install_founder(session, city, ruler)

    if funds:
        treasury = await town.treasury(session, city)
        genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
        await ledger.transfer(
            session,
            PostingReason.GENESIS,
            debit=genesis.id,
            credit=treasury.id,
            amount=money(funds),
        )
    return city, core, ruler, ruler_body


async def _civic_plot(
    session: AsyncSession, constants: Constants, city, core, *, condition: float
) -> Node:
    """A city plot next door with one worn house on it."""
    plot = await world.create_node(
        session,
        f"{core.key}.plot{uuid.uuid4().hex[:4]}",
        "Городской двор",
        area_m2=100,
        parent=core,
    )
    plot.owner_city_id = city.id
    session.add(
        Building(
            node_id=plot.id,
            area_m2=20,
            footprint_m2=20,
            floors=1,
            kind=kinds(constants)[0],
            condition=condition,
        )
    )
    await session.flush()
    return plot


async def _worker_at(session: AsyncSession, node: Node, *, materials: dict | None = None):
    identity = await world.create_identity(session, f"Работник-{uuid.uuid4().hex[:6]}")
    body = await world.print_body(session, identity, node)
    if materials:
        pocket = await world.body_container(session, body)
        for name, qty in materials.items():
            await world.grant_item(session, pocket, name, amount=qty, origin="тест")
    return identity, body


async def _feed_fund(session: AsyncSession, amount: int) -> None:
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    await ledger.transfer(
        session,
        PostingReason.WORKS_PRINT,
        debit=genesis.id,
        credit=(await works.fund_account(session)).id,
        amount=amount,
    )


async def _balance(session: AsyncSession, identity) -> int:
    account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
    return await ledger.balance(session, account.id)


# --- repair orders -----------------------------------------------------------


async def test_repair_order_escrows_both_pockets_and_pays_the_worker(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The whole loop: posted with two escrows, licensed, verified, paid."""
    city, core, ruler, ruler_body = await _city_with_ruler(session, catalog, funds=1000)
    plot = await _civic_plot(session, constants, city, core, condition=50)
    houses = [b for b in (await session.execute(select(Building))).scalars()]
    labor = works_city.labor_tariff(constants, repair_minutes(constants, houses))
    city_labor, fund_labor = works_city.split_labor(constants, labor)
    await _feed_fund(session, fund_labor)
    treasury = await town.treasury(session, city)
    treasury_before = await ledger.balance(session, treasury.id)

    order = await works_city.post_repair_order(
        session, constants, city, ruler, ruler_body, plot, offer=10
    )
    escrow = await ledger.account_for(session, AccountKind.ESCROW, order.id)
    assert await ledger.balance(session, escrow.id) == order.tariff
    assert order.tariff == money(10) + labor
    assert await ledger.balance(session, treasury.id) == treasury_before - money(10) - city_labor
    assert await works.fund_balance(session) == 0

    #: A stranger mends the city's plot: the order is the licence.
    bill = repair_bill(constants, houses)
    worker, worker_body = await _worker_at(session, plot, materials=bill)
    job = await repair(session, constants, worker_body, plot)
    await finish_repair(session, job)

    expected_fund = min(fund_labor, money(constants[R.WORKS_PLAYER_DAILY_CAP]))
    assert await _balance(session, worker) == money(10) + city_labor + expected_fund
    fresh = await session.get(type(order), order.id)
    assert fresh is not None and fresh.state is WorkOrderState.DONE
    assert fresh.done_by == worker.id
    assert await ledger.balance(session, escrow.id) == 0


async def test_no_licence_without_an_order(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The city's plot stays the city's: no order -- no stranger's hands on it."""
    from src.engine.estate._base import EstateError

    city, core, _, _ = await _city_with_ruler(session, catalog)
    plot = await _civic_plot(session, constants, city, core, condition=50)
    houses = [b for b in (await session.execute(select(Building))).scalars()]
    worker, worker_body = await _worker_at(session, plot, materials=repair_bill(constants, houses))
    with pytest.raises(EstateError):
        await repair(session, constants, worker_body, plot)


async def test_withdrawn_order_returns_each_remainder_to_its_pocket(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    city, core, ruler, ruler_body = await _city_with_ruler(session, catalog, funds=1000)
    plot = await _civic_plot(session, constants, city, core, condition=50)
    houses = [b for b in (await session.execute(select(Building))).scalars()]
    labor = works_city.labor_tariff(constants, repair_minutes(constants, houses))
    _, fund_labor = works_city.split_labor(constants, labor)
    await _feed_fund(session, fund_labor)
    treasury = await town.treasury(session, city)
    treasury_before = await ledger.balance(session, treasury.id)

    order = await works_city.post_repair_order(
        session, constants, city, ruler, ruler_body, plot, offer=10
    )
    to_treasury, to_fund = await works_city.cancel_city_order(
        session, city, ruler, ruler_body, order.id
    )
    assert to_fund == fund_labor
    assert await ledger.balance(session, treasury.id) == treasury_before
    assert await works.fund_balance(session) == fund_labor

    #: The licence went with the order.
    from src.engine.estate._base import EstateError

    worker, worker_body = await _worker_at(session, plot, materials=repair_bill(constants, houses))
    with pytest.raises(EstateError):
        await repair(session, constants, worker_body, plot)


async def test_empty_fund_refuses_the_posting(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The board never promises what is not set aside -- refusal at posting, not at payout."""
    city, core, ruler, ruler_body = await _city_with_ruler(session, catalog, funds=1000)
    plot = await _civic_plot(session, constants, city, core, condition=50)
    #: Pinned by key, not by class: `WorksCityError` covers nineteen different
    #: refusals, and this test must fail if the posting stops for another one
    #: (D-251 wave III).
    with pytest.raises(works_city.WorksCityError) as refused:
        await works_city.post_repair_order(
            session, constants, city, ruler, ruler_body, plot, offer=10
        )
    assert refused.value.key == "works-city-fund-empty"


# --- construction orders -----------------------------------------------------


async def test_build_order_licenses_exactly_the_ordered_house(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A stranger raises the ordered house on the city's plot -- and only it."""
    from src.engine.estate._base import EstateError
    from src.engine.estate.building import bill, construct
    from src.engine.estate.demolition import finish_build

    city, core, ruler, ruler_body = await _city_with_ruler(session, catalog, funds=5000)
    plot = await world.create_node(session, f"{core.key}.site", "Пустырь", area_m2=100, parent=core)
    plot.owner_city_id = city.id
    await session.flush()

    kind = kinds(constants)[0]
    footprint = constants[R.BUILD_AREA_MIN]
    labor = works_city.labor_tariff(
        constants, build_minutes(constants, footprint=footprint, floors=1, kind=kind)
    )
    city_labor, fund_labor = works_city.split_labor(constants, labor)
    await _feed_fund(session, fund_labor)
    await works_city.post_build_order(
        session,
        constants,
        city,
        ruler,
        ruler_body,
        plot,
        building_kind=kind,
        footprint=footprint,
        floors=1,
        offer=20,
    )

    needed = bill(constants, footprint=footprint, floors=1, kind=kind)
    worker, worker_body = await _worker_at(session, plot, materials=needed)

    #: A different house than ordered is not licensed.
    with pytest.raises(EstateError):
        await construct(session, constants, worker_body, plot, footprint, floors=2, kind=kind)

    job = await construct(session, constants, worker_body, plot, footprint, floors=1, kind=kind)
    await finish_build(session, job)

    expected_fund = min(fund_labor, money(constants[R.WORKS_PLAYER_DAILY_CAP]))
    assert await _balance(session, worker) == money(20) + city_labor + expected_fund
    from src.models.works import WorkOrder

    order = (
        (
            await session.execute(
                select(WorkOrder).where(WorkOrder.kind == WorkOrderKind.BUILDING_BUILD)
            )
        )
        .scalars()
        .one()
    )
    assert order.state is WorkOrderState.DONE and order.done_by == worker.id


# --- fuel orders -------------------------------------------------------------


async def _station_city(session: AsyncSession, constants: Constants, catalog: Catalog):
    city, core, ruler, ruler_body = await _city_with_ruler(session, catalog, funds=1000)
    station = await world.create_node(
        session, f"{core.key}.plant", "Станция", area_m2=50, parent=core
    )
    station.owner_city_id = city.id
    await session.flush()
    yard = await world.node_container(session, station)
    plant_name = sorted(world.station_names(energy.FUEL_PLANT))[0]
    await world.grant_item(session, yard, plant_name, quality=60, origin="тест")
    fuel_key = sorted(constants[R.ENERGY_FUEL_ENERGY])[0]
    return city, core, ruler, ruler_body, station, fuel_key


async def test_fuel_order_pays_per_unit_and_closes_when_filled(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    city, _, ruler, ruler_body, station, fuel_key = await _station_city(session, constants, catalog)
    hours = catalog.recipes.mass_of(fuel_key) * 10 / constants[R.WORKS_HAUL_KG_PER_HOUR]
    labor = money(hours * constants[R.WORKS_HOUR_RATE])
    city_labor, fund_labor = works_city.split_labor(constants, labor)
    await _feed_fund(session, fund_labor)

    order = await works_city.post_fuel_order(
        session,
        constants,
        catalog,
        city,
        ruler,
        ruler_body,
        station,
        type_key=fuel_key,
        amount=10,
        price_per_unit=1,
    )
    assert order.tariff == money(10) + labor

    hauler, hauler_body = await _worker_at(session, station, materials={fuel_key: 10})
    pocket = await world.body_container(session, hauler_body)

    from src.models.inventory import Item

    fuel_stack = (
        (await session.execute(select(Item).where(Item.container_id == pocket.id))).scalars().one()
    )
    await energy.fuel(session, constants, hauler_body, fuel_stack, 4)
    half_paid = await _balance(session, hauler)
    assert half_paid > 0, "подвоз платится по факту каждой заливки"

    fuel_stack = (
        (await session.execute(select(Item).where(Item.container_id == pocket.id))).scalars().one()
    )
    await energy.fuel(session, constants, hauler_body, fuel_stack, 6)
    fresh = await session.get(type(order), order.id)
    assert fresh is not None and fresh.state is WorkOrderState.DONE
    expected_fund = min(fund_labor, money(constants[R.WORKS_PLAYER_DAILY_CAP]))
    assert await _balance(session, hauler) == money(10) + city_labor + expected_fund
    escrow = await ledger.account_for(session, AccountKind.ESCROW, order.id)
    assert await ledger.balance(session, escrow.id) == 0

    total = await ledger.money_supply(session)
    from src.telemetry import metrics

    reserve = await metrics.kind_total(session, AccountKind.BANK_RESERVE)
    fund = await metrics.kind_total(session, AccountKind.WORKS_FUND)
    assert total == await bank.circulating(session) + reserve + fund


async def test_two_pours_cannot_collect_one_unit_twice(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The order row serialises fuel payouts: the pot pays for ten units, not twenty."""
    from src.models.works import WorkOrder

    station, fuel_key = await _city_with_fuel_order(session, constants, catalog)
    worker, _ = await _worker_at(session, station)
    await ledger.account_for(session, AccountKind.IDENTITY, worker.id)
    await session.flush()
    order = (
        (
            await session.execute(
                select(WorkOrder).where(WorkOrder.kind == WorkOrderKind.FUEL_DELIVERY)
            )
        )
        .scalars()
        .one()
    )
    station_id, worker_id, order_tariff = station.id, worker.id, order.tariff
    await session.commit()

    original = works.paid_today

    async def held(*args, **kwargs):
        result = await original(*args, **kwargs)
        await asyncio.sleep(0.2)
        return result

    monkeypatch.setattr(works, "paid_today", held)

    async def pour_all() -> int:
        async with factory() as db, db.begin():
            place = await db.get(Node, station_id)
            assert place is not None
            return await works_city.pay_fuel_delivery(
                db, constants, place, fuel_key, 10.0, worker_id
            )

    first, second = await asyncio.gather(pour_all(), pour_all())
    assert sorted((first, second))[0] == 0, "второй налив не оплачен: заказ уже полон"

    async with factory() as db:
        account = await ledger.account_for(db, AccountKind.IDENTITY, worker_id)
        paid = await ledger.balance(db, account.id)
        assert paid <= order_tariff, "за десять единиц не платят дважды"


async def _city_with_fuel_order(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> tuple[Node, str]:
    city, _, ruler, ruler_body, station, fuel_key = await _station_city(session, constants, catalog)
    hours = catalog.recipes.mass_of(fuel_key) * 10 / constants[R.WORKS_HAUL_KG_PER_HOUR]
    labor = money(hours * constants[R.WORKS_HOUR_RATE])
    _, fund_labor = works_city.split_labor(constants, labor)
    await _feed_fund(session, fund_labor)
    await works_city.post_fuel_order(
        session,
        constants,
        catalog,
        city,
        ruler,
        ruler_body,
        station,
        type_key=fuel_key,
        amount=10,
        price_per_unit=1,
    )
    return station, fuel_key


async def test_poured_fuel_cannot_be_picked_back(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The money pump the review of 2026-08-29 found: pour, collect the pay,
    pick the same fuel back up. The station's pile is its tank, not a shelf."""
    from src.engine import storage

    station, fuel_key = await _city_with_fuel_order(session, constants, catalog)
    hauler, hauler_body = await _worker_at(session, station, materials={fuel_key: 10})
    pocket = await world.body_container(session, hauler_body)

    from src.models.inventory import Item

    fuel_stack = (
        (await session.execute(select(Item).where(Item.container_id == pocket.id))).scalars().one()
    )
    await energy.fuel(session, constants, hauler_body, fuel_stack, 10)
    assert await _balance(session, hauler) > 0, "подвоз оплачен"

    yard = await world.node_container(session, station)
    poured = (
        (
            await session.execute(
                select(Item).where(Item.container_id == yard.id, Item.type_key == fuel_key)
            )
        )
        .scalars()
        .one()
    )
    with pytest.raises(storage.StorageError):
        await storage.pick(session, constants, catalog, hauler_body, poured)


async def test_fuel_thirds_leave_no_tail_in_escrow(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Float drift must not strand minor units on the escrow of a DONE order."""
    city, _, ruler, ruler_body, station, fuel_key = await _station_city(session, constants, catalog)
    hours = catalog.recipes.mass_of(fuel_key) * 9 / constants[R.WORKS_HAUL_KG_PER_HOUR]
    labor = money(hours * constants[R.WORKS_HOUR_RATE])
    city_labor, fund_labor = works_city.split_labor(constants, labor)
    await _feed_fund(session, fund_labor)
    order = await works_city.post_fuel_order(
        session,
        constants,
        catalog,
        city,
        ruler,
        ruler_body,
        station,
        type_key=fuel_key,
        amount=9,
        price_per_unit=1,
    )

    hauler, _ = await _worker_at(session, station)
    for _pour in range(3):
        await works_city.pay_fuel_delivery(session, constants, station, fuel_key, 3.0, hauler.id)

    fresh = await session.get(type(order), order.id)
    assert fresh is not None and fresh.state is WorkOrderState.DONE
    escrow = await ledger.account_for(session, AccountKind.ESCROW, order.id)
    assert await ledger.balance(session, escrow.id) == 0, "хвост не завис на эскроу"
    expected_fund = min(fund_labor, money(constants[R.WORKS_PLAYER_DAILY_CAP]))
    assert await _balance(session, hauler) == money(9) + city_labor + expected_fund


async def test_cancel_refused_while_work_is_under_way(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Post, wait for a stranger's timber, revoke -- must not be a strategy."""
    city, core, ruler, ruler_body = await _city_with_ruler(session, catalog, funds=1000)
    plot = await _civic_plot(session, constants, city, core, condition=50)
    houses = [b for b in (await session.execute(select(Building))).scalars()]
    labor = works_city.labor_tariff(constants, repair_minutes(constants, houses))
    _, fund_labor = works_city.split_labor(constants, labor)
    await _feed_fund(session, fund_labor)
    order = await works_city.post_repair_order(
        session, constants, city, ruler, ruler_body, plot, offer=10
    )

    worker, worker_body = await _worker_at(session, plot, materials=repair_bill(constants, houses))
    job = await repair(session, constants, worker_body, plot)
    with pytest.raises(works_city.WorksCityError) as under_way:
        await works_city.cancel_city_order(session, city, ruler, ruler_body, order.id)
    assert under_way.value.key == "works-city-work-under-way"

    await finish_repair(session, job)
    #: Done is not withdrawable either -- but with the other refusal, and now
    #: the test can tell the two apart rather than trusting the comment.
    with pytest.raises(works_city.WorksCityError) as closed:
        await works_city.cancel_city_order(session, city, ruler, ruler_body, order.id)
    assert closed.value.key == "works-city-order-closed"


async def test_database_holds_one_open_order_per_plot(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The backstop under the read-then-insert check: the partial unique index."""
    from sqlalchemy.exc import IntegrityError

    from src.models.works import WorkOrder

    city, core, _, _ = await _city_with_ruler(session, catalog)
    plot = await _civic_plot(session, constants, city, core, condition=50)
    for _try in range(2):
        session.add(
            WorkOrder(
                kind=WorkOrderKind.BUILDING_REPAIR,
                node_id=plot.id,
                city_id=city.id,
                payload={},
                tariff=1,
            )
        )
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


# --- the treasury as a borrower ----------------------------------------------


async def _turnover_on(session: AsyncSession, city, core, turnover_tc: float) -> None:
    from src.units import amount as _amount

    seller = await world.create_identity(session, f"Купец-{uuid.uuid4().hex[:6]}")
    order_ = Order(
        node_id=core.id,
        identity_id=seller.id,
        side=OrderSide.SELL,
        type_key="bread",
        tier="common",
        price=money(turnover_tc),
        amount_total=_amount(1),
        amount_left=0,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    session.add(order_)
    await session.flush()
    session.add(
        Trade(
            node_id=core.id,
            sell_order_id=order_.id,
            type_key="bread",
            tier="common",
            price=money(turnover_tc),
            amount=_amount(1),
        )
    )
    await session.flush()


async def test_treasury_borrows_at_key_rate_on_its_own_line(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    city, core, ruler, ruler_body = await _city_with_ruler(session, catalog)
    await _turnover_on(session, city, core, 100)

    treasury = await town.treasury(session, city)
    loan = await works_city.borrow_for_works(session, constants, city, ruler, ruler_body, 50)
    assert float(loan.rate) == await bank.key_rate(session, constants), "ключевая, без маржи"
    assert loan.identity_id is None and loan.city_id == city.id
    assert await ledger.balance(session, treasury.id) == money(50)
    #: The loan sits on the same line the citizens' loans occupy.
    _, occupied, _ = await bank.city_line(session, constants, city)
    assert occupied == money(50)

    paid = await works_city.repay_for_works(session, constants, city, ruler, ruler_body, loan, 20)
    assert paid == money(20)
    assert await ledger.balance(session, treasury.id) == money(30)


async def test_line_refuses_beyond_the_cap(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    city, core, ruler, ruler_body = await _city_with_ruler(session, catalog)
    await _turnover_on(session, city, core, 10)
    cap_tc = 10 * constants[R.BANK_DEBT_TO_TURNOVER_CAP] / PERCENT
    with pytest.raises(works_city.WorksCityError) as refused:
        await works_city.borrow_for_works(session, constants, city, ruler, ruler_body, cap_tc + 1)
    assert refused.value.key == "works-city-line-exhausted"


async def test_two_rulers_cannot_break_the_line_together(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The line is the treasury loan's only brake, and it is read-then-insert:
    the city row serialises the borrowers, the loser rereads a line already
    carrying the winner's loan."""
    from src.models.city import City
    from src.models.identity import Body, Identity

    city, core, ruler, ruler_body = await _city_with_ruler(session, catalog)
    #: Turnover 10 TC: the line fits one loan of 20 TC, never two.
    await _turnover_on(session, city, core, 10)
    city_id, ruler_id, body_id = city.id, ruler.id, ruler_body.id
    await session.commit()

    original = bank.city_line

    async def held(*args, **kwargs):
        result = await original(*args, **kwargs)
        await asyncio.sleep(0.2)
        return result

    monkeypatch.setattr(bank, "city_line", held)

    async def take() -> object:
        async with factory() as db, db.begin():
            place = await db.get(City, city_id)
            who = await db.get(Identity, ruler_id)
            flesh = await db.get(Body, body_id)
            assert place is not None and who is not None and flesh is not None
            try:
                return await works_city.borrow_for_works(db, constants, place, who, flesh, 20)
            except works_city.WorksCityError as refusal:
                return refusal

    outcomes = await asyncio.gather(take(), take())
    refused = [out for out in outcomes if isinstance(out, works_city.WorksCityError)]
    assert len(refused) == 1, "линию пробили вдвоём: заняли оба"

    async with factory() as db:
        place = await db.get(City, city_id)
        assert place is not None
        treasury = await town.treasury(db, place)
        assert await ledger.balance(db, treasury.id) == money(20)


async def test_collection_never_touches_a_treasury_loan(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A loan with no identity has nobody to withhold from: the line is the discipline."""
    city, core, ruler, ruler_body = await _city_with_ruler(session, catalog, funds=100)
    await _turnover_on(session, city, core, 100)
    loan = await works_city.borrow_for_works(session, constants, city, ruler, ruler_body, 50)
    loan.serviced_at = datetime.now(UTC) - timedelta(days=constants[R.DEBT_GRACE_PERIOD] + 5)
    await session.flush()

    withheld = await bank.collect(session, constants)
    assert withheld == 0
    assert loan.outstanding >= money(50), "казённый долг не списывается силой"
