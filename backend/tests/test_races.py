# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Two transactions at once on the same money, the same order, the same body.

Every other test in the suite is one session, one step after another, and
that is exactly why the races the review of 2026-08-23 found were never
seen. Here two sessions run the same operation through `asyncio.gather`;
the invariant must hold whichever of them wins, and one of them must be
refused instead of both succeeding on the same coin.

The method is the family's: `conftest._slow` holds a transaction between its
check and its write, or a handshake starts the second side while the first
provably holds its lock. The family has three files -- this one races money,
orders and the body's reserves; `test_races_ground.py` races the ground
itself; `test_races_mining.py` races what the ground gives up. What is not a
race but shares the origin -- "чтение не пишет" -- lives in `test_reads.py`.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import _slow
from src.constants import current, current_catalog
from src.constants import registry as R
from src.engine import city as town
from src.engine import ledger, market, world
from src.models.energy import EnergyPool
from src.models.identity import Body, Identity
from src.models.ledger import AccountKind, PostingReason
from src.models.market import Order
from src.units import MONEY_SCALE, money

ORE = "iron_ore"


async def _wallet(session: AsyncSession, amount: int) -> uuid.UUID:
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    wallet = await ledger.account_for(session, AccountKind.IDENTITY, uuid.uuid4())
    await ledger.transfer(
        session, PostingReason.GENESIS, debit=genesis.id, credit=wallet.id, amount=amount
    )
    return wallet.id


async def test_two_draws_of_the_same_energy_leave_one_refused(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The city pool is a remainder like a purse (CLAUDE.md).

    Without the row lock two transactions read the same hundred, both write off
    a hundred, and the city ends the minute owing energy to nobody -- which in
    a world where heat runs on that pool means two heated nodes for the price
    of one.
    """
    from src.engine import energy, world
    from src.models.world import Layer

    _slow(monkeypatch, energy, "produce")
    stamp = uuid.uuid4().hex[:8]
    city = await world.create_node(
        session, f"terra.pool.{stamp}", "Город", area_m2=1, layer=Layer.PLANET
    )
    yard = await world.create_node(
        session, f"terra.pool.{stamp}.yard", "Двор", area_m2=200, layer=Layer.CITY, parent=city
    )
    pool = await energy.pool_of(session, constants, yard)
    assert pool is not None
    stored = current()[R.ENERGY_BODY_PRINT]
    pool.stored = Decimal(str(stored))
    #: Zero is a tariff too (D-085): the race is about the energy, and a bill
    #: nobody can pay would refuse both before they ever reach the pool.
    pool.tariff = Decimal(0)
    pool.counted_at = datetime.now(UTC)
    await session.flush()

    bodies = []
    for number in range(2):
        who = await world.create_identity(session, f"Потребитель-{stamp}-{number}")
        bodies.append((await world.print_body(session, who, yard)).id)
    await session.commit()

    async def draw(body_id: uuid.UUID) -> None:
        async with factory() as db, db.begin():
            body = await db.get(Body, body_id)
            assert body is not None
            await energy.draw_for_work(db, constants, body, stored, goods="iron_ore")

    outcomes = await asyncio.gather(*(draw(one) for one in bodies), return_exceptions=True)
    refused = [one for one in outcomes if isinstance(one, energy.NotEnough)]
    assert len(refused) == 1, f"второй потребитель должен уйти ни с чем: {outcomes}"

    async with factory() as db:
        again = (
            (await db.execute(select(EnergyPool).where(EnergyPool.node_id == city.id)))
            .scalars()
            .one()
        )
        assert float(again.stored) == 0


async def test_two_spends_of_the_same_coin_leave_one_refused(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The balance is a sum over the journal; without the account lock two
    transactions both see 100, both spend 100, and the account is -100."""
    _slow(monkeypatch, ledger, "_check_funds")
    wallet = await _wallet(session, money(100))
    sinks = [
        (await ledger.account_for(session, AccountKind.IDENTITY, uuid.uuid4())).id for _ in range(2)
    ]
    await session.commit()

    async def spend(sink: uuid.UUID) -> None:
        async with factory() as db, db.begin():
            await ledger.transfer(
                db, PostingReason.TRADE, debit=wallet, credit=sink, amount=money(100)
            )

    outcomes = await asyncio.gather(*(spend(s) for s in sinks), return_exceptions=True)
    refused = [o for o in outcomes if isinstance(o, ledger.InsufficientFunds)]
    assert len(refused) == 1, outcomes
    assert await ledger.balance(session, wallet) == 0
    assert sum([await ledger.balance(session, s) for s in sinks]) == money(100)


async def _book(session: AsyncSession) -> tuple[Order, list[uuid.UUID]]:
    """A sell order of ten and two funded buyers standing at the terminal."""
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.race.{stamp}", "Рынок", area_m2=200)
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, "market_terminal", quality=70, origin="тест")
    seller = await world.create_identity(session, f"Продавец-{stamp}")
    seller_body = await world.print_body(session, seller, node)
    pocket = await world.body_container(session, seller_body)
    await world.grant_item(session, pocket, ORE, amount=10, quality=64, origin="тест")
    constants, catalog = current(), current_catalog()
    await market.load(session, constants, seller_body, ORE, 10)
    order = (
        await market.sell(
            session,
            constants,
            catalog,
            seller,
            node,
            type_key=ORE,
            tier=market.tier_of(constants, 64),
            price=money(3),
            quantity=10,
        )
    ).order
    buyers = []
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    for i in range(2):
        buyer = await world.create_identity(session, f"Купец-{i}-{stamp}")
        await world.print_body(session, buyer, node)
        account = await ledger.account_for(session, AccountKind.IDENTITY, buyer.id)
        await ledger.transfer(
            session,
            PostingReason.GENESIS,
            debit=genesis.id,
            credit=account.id,
            amount=money(500),
        )
        buyers.append(buyer.id)
    return order, buyers


async def test_two_floors_cannot_both_take_the_same_fine_stack(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only four of the ten are good enough for a floor of 75 (D-239).

    Both buyers count the deliverable part of one sell order and both would
    take four; without the lock on the stacks the seller hands out eight
    stacks that never existed. The one who arrives second must find the fine
    ore gone and take nothing at all -- their order simply waits.
    """
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.floor.{stamp}", "Рынок", area_m2=200)
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, "market_terminal", quality=70, origin="тест")
    constants, catalog = current(), current_catalog()

    seller = await world.create_identity(session, f"Рудник-{stamp}")
    seller_body = await world.print_body(session, seller, node)
    pocket = await world.body_container(session, seller_body)
    for quality, amount in ((62, 6), (78, 4)):
        await world.grant_item(session, pocket, ORE, amount=amount, quality=quality, origin="тест")
    await market.load(session, constants, seller_body, ORE, 10)
    order = (
        await market.sell(
            session,
            constants,
            catalog,
            seller,
            node,
            type_key=ORE,
            tier=market.tier_of(constants, 62),
            price=money(3),
            quantity=10,
        )
    ).order

    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    bodies = []
    for i in range(2):
        buyer = await world.create_identity(session, f"Придира-{i}-{stamp}")
        body = await world.print_body(session, buyer, node)
        account = await ledger.account_for(session, AccountKind.IDENTITY, buyer.id)
        await ledger.transfer(
            session,
            PostingReason.GENESIS,
            debit=genesis.id,
            credit=account.id,
            amount=money(500),
        )
        bodies.append(body.id)
    #: The settlement sits between counting the deliverable part and moving it.
    _slow(monkeypatch, ledger, "transfer")
    await session.commit()

    async def demand(body_id: uuid.UUID) -> float:
        async with factory() as db, db.begin():
            body = await db.get(Body, body_id)
            fill = await market.buy(
                db,
                current(),
                current_catalog(),
                body,
                type_key=ORE,
                tier=market.tier_of(constants, 62),
                price=money(3),
                quantity=4,
                min_quality=75,
            )
            return fill.traded

    traded = await asyncio.gather(*(demand(b) for b in bodies), return_exceptions=True)
    assert not [o for o in traded if isinstance(o, Exception)], traded
    assert sum(traded) == pytest.approx(4), (
        f"хорошей руды было четыре единицы, роздано {sum(traded)}"
    )
    left = await session.scalar(select(Order.amount_left).where(Order.id == order.id))
    assert left == 6000, "непроданным остался только тот товар, что порога не проходит"


async def test_two_reservations_of_the_last_ten_leave_one_without_goods(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`amount_left` is read, checked and decremented; without the row lock
    both buyers pass the check and the order goes to -10."""
    order, buyers = await _book(session)
    #: The deposit transfer sits between the remainder check and the decrement.
    _slow(monkeypatch, ledger, "transfer")
    await session.commit()

    async def reserve(buyer_id: uuid.UUID) -> None:
        async with factory() as db, db.begin():
            from src.models.identity import Identity

            buyer = await db.get(Identity, buyer_id)
            own = await db.get(Order, order.id)
            await market.reserve(db, current(), buyer, own, 10)

    outcomes = await asyncio.gather(*(reserve(b) for b in buyers), return_exceptions=True)
    refused = [o for o in outcomes if isinstance(o, market.NoGoods)]
    assert len(refused) == 1, outcomes
    left = await session.scalar(select(Order.amount_left).where(Order.id == order.id))
    assert left == 0
    from src.models.market import Reservation

    held = (
        (await session.execute(select(Reservation).where(Reservation.order_id == order.id)))
        .scalars()
        .all()
    )
    assert len(held) == 1, "одна бронь на десять, не две"


async def test_alive_locks_the_body_for_the_whole_command(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_alive` is the prologue of every in-person command and it locks the
    body: the second command of the same identity waits for the first and
    sees its stamina, not the snapshot. Two foraging starts at once: the
    second must be refused as busy, not started twice."""
    from src.api.commands import common as api
    from src.engine import forage, occupation

    node = await world.create_node(
        session,
        f"terra.lock.{uuid.uuid4().hex[:6]}",
        "Лес",
        area_m2=100,
        properties={"woods": True},
    )
    identity = await world.create_identity(session, f"Тело-{uuid.uuid4().hex[:6]}")
    await world.print_body(session, identity, node)
    await session.commit()
    #: Between the "is it free" check and the occupation write.
    _slow(monkeypatch, occupation, "require_free")

    async def begin() -> None:
        async with factory() as db, db.begin():
            body = await api._alive({"identity_id": identity.id}, db)
            await forage.start(db, current(), body)

    outcomes = await asyncio.gather(begin(), begin(), return_exceptions=True)
    busy = [o for o in outcomes if isinstance(o, (occupation.Busy, forage.ForageError))]
    assert len(busy) == 1, outcomes


async def test_two_copies_at_once_cannot_outspend_one_reserve(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recipe is free in money and paid for in stamina (D-148), and stamina
    is on the same list as money and remainders (CLAUDE.md).

    Two sockets of one identity at one shelf, two different recipes, and a
    reserve that covers exactly one copy. Without the lock on the body both
    read that reserve, both find it enough and both write their own remainder
    -- the second write erases the first, both recipes are learned and the
    body has carried off more knowledge than it had strength for. With the
    lock the second waits at the row, rereads it after the first commits and
    is refused.

    The pause goes into the window the lock has to close: `library.has` is
    asked before the body's row is taken, so both sessions are past the shelf
    and at the lock together.
    """
    from src.engine import craft
    from src.engine import library as shelf
    from src.models.identity import Knowledge

    _slow(monkeypatch, shelf, "has")
    spend = current()[R.CRAFT_COPY_STAMINA]
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(
        session,
        f"terra.shelf.{stamp}",
        "Библиотека",
        area_m2=100,
        properties={"library": True},
    )
    who = await world.create_identity(session, f"Переписчик-{stamp}")
    body = await world.print_body(session, who, node)
    #: Enough for one copy and not for two: the reserve is what the race is over.
    body.stamina = Decimal(str(spend * 1.5))
    catalog = current_catalog()
    first, second = (recipe.type_key for recipe in catalog.recipes.recipes[:2])
    await shelf.stock(session, node, (first, second))
    await session.flush()
    body_id, who_id = body.id, who.id
    was = float(body.stamina)
    await session.commit()

    async def copy(recipe: str) -> None:
        async with factory() as db, db.begin():
            hand = await db.get(Body, body_id)
            assert hand is not None
            await craft.copy_recipe(db, current_catalog(), hand, recipe)

    outcomes = await asyncio.gather(copy(first), copy(second), return_exceptions=True)

    refused = [one for one in outcomes if isinstance(one, craft.NoStrength)]
    other = [one for one in outcomes if isinstance(one, BaseException) and one not in refused]
    assert not other, f"сорвалось не отказом: {other}"
    assert len(refused) == 1, "второй переписке не хватило выносливости, а её пропустили"

    async with factory() as db:
        again = await db.get(Body, body_id)
        assert again is not None
        assert float(again.stamina) == pytest.approx(was - spend), (
            f"списано {was - float(again.stamina):.2f} вместо {spend:.2f}: "
            "одна переписка досталась бесплатно"
        )
        learned = (
            (
                await db.execute(
                    select(Knowledge.key).where(
                        Knowledge.identity_id == who_id,
                        Knowledge.key.in_((first, second)),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(learned) == 1, f"выучено рецептов: {len(learned)} на один оплаченный"


async def test_two_copies_of_one_recipe_are_paid_for_once(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What is already known is not rewritten: the same body does not pay twice
    (D-148) -- and "the same body" includes its two sockets at one shelf.

    The knowledge check has to sit **under** the same lock as the payment, not
    before it. Outside the lock both sessions find the recipe unknown, both pay
    and only the first learns anything: the second `learn` sees the committed
    row and returns nothing, having charged for it. Here the reserve covers
    both copies, so nothing refuses them -- only the ledger of the body shows
    that one of them was for free of knowledge and not free of strength.

    `carrier.read` is not raced separately: both paths take the same
    `_lock_body` before the same two reads, and what is pinned here is that
    helper's placement.
    """
    from src.engine import craft
    from src.engine import library as shelf
    from src.models.identity import Knowledge

    _slow(monkeypatch, shelf, "has")
    spend = current()[R.CRAFT_COPY_STAMINA]
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(
        session,
        f"terra.shelf.{stamp}",
        "Библиотека",
        area_m2=100,
        properties={"library": True},
    )
    who = await world.create_identity(session, f"Переписчик-{stamp}")
    body = await world.print_body(session, who, node)
    #: Enough for both copies: the refusal must not be what saves the reserve.
    body.stamina = Decimal(str(spend * 5))
    recipe = current_catalog().recipes.recipes[0].type_key
    await shelf.stock(session, node, (recipe,))
    await session.flush()
    body_id, who_id = body.id, who.id
    was = float(body.stamina)
    await session.commit()

    async def copy() -> None:
        async with factory() as db, db.begin():
            hand = await db.get(Body, body_id)
            assert hand is not None
            await craft.copy_recipe(db, current_catalog(), hand, recipe)

    outcomes = await asyncio.gather(copy(), copy(), return_exceptions=True)
    assert not [one for one in outcomes if isinstance(one, BaseException)], outcomes

    async with factory() as db:
        again = await db.get(Body, body_id)
        assert again is not None
        assert float(again.stamina) == pytest.approx(was - spend), (
            f"списано {was - float(again.stamina):.2f} вместо {spend:.2f}: "
            "за один рецепт заплачено дважды"
        )
        learned = (
            (
                await db.execute(
                    select(Knowledge.key).where(
                        Knowledge.identity_id == who_id, Knowledge.key == recipe
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(learned) == 1, f"рецепт записан {len(learned)} раза"


async def test_two_sellers_do_not_overfill_the_tank(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The terminal's tank is finite (D-255), and the room is read before it
    is filled: without the terminal lock two sellers pouring at once both see
    the same room and the tank ends up holding more than its vessel."""
    from src.constants import registry as R
    from src.engine import storage
    from src.engine.market import counter

    stamp = uuid.uuid4().hex[:6]
    node = await world.create_node(session, f"terra.tank.{stamp}", "Торг", area_m2=100)
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, "market_terminal", quality=70, origin="тест")

    catalog = current_catalog()
    unit = catalog.recipes.mass_of("lubricant")
    cap_units = constants[R.MARKET_TANK_CAPACITY] / unit
    each = int(cap_units * 0.7)

    bodies = []
    for i in range(2):
        identity = await world.create_identity(session, f"Нефтяник-{i}-{stamp}")
        body = await world.print_body(session, identity, node)
        pocket = await world.body_container(session, body)
        canister = await world.grant_item(session, pocket, "canister", quality=60, origin="тест")
        inside = await storage.inside(session, canister)
        await world.grant_item(session, inside, "lubricant", amount=each, quality=55, origin="тест")
        bodies.append(body.id)
    await session.commit()
    _slow(monkeypatch, counter, "_tank_mass")

    async def pour(body_id) -> float:
        async with factory() as db, db.begin():
            own = await db.get(Body, body_id)
            try:
                return await counter.load(db, constants, own, "lubricant", each)
            except counter.TankFull:
                return 0.0

    poured = await asyncio.gather(*(pour(b) for b in bodies))
    total_kg = sum(poured) * unit
    assert total_kg <= constants[R.MARKET_TANK_CAPACITY] + 1e-6, (
        "бак держит не больше своей ёмкости: второй налив увидел остаток места"
    )
    assert sum(poured) > 0, "первый налив прошёл"


async def test_two_loans_of_the_same_room_leave_one_refused(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The free room under the credit limit is a remainder like a purse.

    A loan adds a row instead of changing one, so there is nothing to lock but
    the borrower: without that row two commands read the same room and both
    take it in full, and the world prints money nobody's limit allowed.
    """
    from src.engine import bank
    from src.engine.bank import loan as loan_module
    from src.models.bank import Loan

    who = await world.create_identity(session, f"Заёмщик-{uuid.uuid4().hex[:6]}")
    #: The reserve is opened beforehand: both sessions would otherwise race to
    #: create it, and a unique violation would hide the race being tested.
    await bank.reserve_account(session)
    await session.commit()
    limit_, _ = await bank.credit_limit(session, constants, who.id)
    assert limit_ > 0

    _slow(monkeypatch, loan_module, "debt_of")

    async def take() -> None:
        async with factory() as db, db.begin():
            borrower = await db.get(Identity, who.id)
            assert borrower is not None
            await bank.borrow(db, constants, current_catalog(), borrower, limit_ / MONEY_SCALE)

    outcomes = await asyncio.gather(*(take() for _ in range(2)), return_exceptions=True)
    refused = [one for one in outcomes if isinstance(one, bank.TooMuch)]
    assert len(refused) == 1, f"второй заём должен упереться в лимит: {outcomes}"

    async with factory() as db:
        taken = (await db.execute(select(Loan).where(Loan.identity_id == who.id))).scalars().all()
        assert sum(one.outstanding for one in taken) == limit_, "долг вышел за лимит"


async def test_two_payments_of_the_same_debt_are_not_counted_twice(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anyone may pay for the debtor (D-063), so two payers meet on one loan.

    Without the loan's row both read the same outstanding and both settle it
    in full: the ledger moves twice the money, and since D-280 the doubled
    `interest_paid` buys a credit limit that was never earned.
    """
    from src.engine import bank
    from src.engine.bank import loan as loan_module
    from src.models.bank import Loan

    who = await world.create_identity(session, f"Должник-{uuid.uuid4().hex[:6]}")
    account = await ledger.account_for(session, AccountKind.IDENTITY, who.id)
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    await ledger.transfer(
        session, PostingReason.GENESIS, debit=genesis.id, credit=account.id, amount=money(500)
    )
    taken_at = datetime.now(UTC) - timedelta(days=constants[R.BANK_YEAR_DAYS])
    debt = await bank.borrow(session, current(), current_catalog(), who, 100, now=taken_at)
    await session.commit()

    _slow(monkeypatch, loan_module, "accrue")

    async def pay() -> int:
        async with factory() as db, db.begin():
            payer = await db.get(Identity, who.id)
            owed = await db.get(Loan, debt.id)
            assert payer is not None and owed is not None
            return await bank.repay(db, constants, payer, owed)

    outcomes = await asyncio.gather(*(pay() for _ in range(2)), return_exceptions=True)
    refused = [one for one in outcomes if isinstance(one, bank.NothingToRepay)]
    assert len(refused) == 1, f"платить дважды по одному займу нечем: {outcomes}"

    async with factory() as db:
        settled = await db.get(Loan, debt.id)
        assert settled is not None
        assert settled.outstanding == 0
        assert settled.interest_paid == settled.interest_accrued, (
            "уплаченный процент не может быть больше начисленного: он покупает лимит"
        )


async def test_two_citizens_do_not_lie_twice_on_the_same_line(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The city's line with the capital is one remainder for all its citizens.

    `bank.debt_to_turnover_cap` bounds how much of its turnover a city may owe
    (D-175). Without the city's row two citizens read the same free room and
    both lie down on it -- the cap is through, and the capital prints against
    turnover that was counted once.
    """
    from src.engine import bank
    from src.engine.bank import line as line_module
    from src.models.bank import Loan
    from src.models.city import Citizen
    from src.models.market import Order, OrderSide, Trade
    from src.models.world import Layer
    from src.units import amount as _amount

    stamp = uuid.uuid4().hex[:8]
    catalog = current_catalog()
    planet = await world.create_node(
        session, f"terra.{stamp}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    delegate = await world.create_node(
        session, f"terra.city.{stamp}", "Город", area_m2=1, layer=Layer.PLANET, parent=planet
    )
    marketplace = await world.create_node(
        session, f"terra.city.{stamp}.market", "Рынок", area_m2=50, parent=delegate
    )
    city = await town.found(session, catalog, delegate, f"Город-{stamp}")
    marketplace.owner_city_id = city.id

    #: One deal makes the city's whole turnover, so the line is exactly known --
    #: and small enough that the line binds before anyone's personal limit does
    #: (`bank.unsecured_limit`), otherwise both borrowers are refused for their
    #: own reasons and the race never happens.
    merchant = await world.create_identity(session, f"Купец-{stamp}")
    order = Order(
        node_id=marketplace.id,
        identity_id=merchant.id,
        side=OrderSide.SELL,
        type_key="bread",
        tier="common",
        price=money(50),
        amount_total=_amount(1),
        amount_left=0,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    session.add(order)
    await session.flush()
    session.add(
        Trade(
            node_id=marketplace.id,
            sell_order_id=order.id,
            type_key="bread",
            tier="common",
            price=money(50),
            amount=_amount(1),
        )
    )
    borrowers = []
    for number in range(2):
        who = await world.create_identity(session, f"Гражданин-{number}-{stamp}")
        session.add(Citizen(identity_id=who.id, city_id=city.id))
        borrowers.append(who.id)
    await bank.reserve_account(session)
    await session.commit()

    permitted, occupied, free = await bank.city_line(session, constants, city)
    assert occupied == 0 and free == permitted > 0
    limit_, _ = await bank.credit_limit(session, constants, borrowers[0])
    assert free < limit_, "линия города должна упереться раньше личного лимита"

    _slow(monkeypatch, line_module, "city_outstanding")

    async def take(identity_id: uuid.UUID) -> None:
        async with factory() as db, db.begin():
            borrower = await db.get(Identity, identity_id)
            assert borrower is not None
            await bank.borrow(db, constants, catalog, borrower, free / MONEY_SCALE)

    outcomes = await asyncio.gather(*(take(one) for one in borrowers), return_exceptions=True)
    #: Both are served: the line shrinks smoothly, and whoever finds it spent
    #: borrows straight from the capital at the worse rate (D-175). Without the
    #: city's row the two collide in the ledger instead, and one of them dies
    #: of a deadlock -- the cap holds by accident, and the player sees a crash.
    assert not [one for one in outcomes if isinstance(one, Exception)], outcomes

    async with factory() as db:
        on_line = (
            (
                await db.execute(
                    select(Loan).where(Loan.city_id == city.id, Loan.identity_id.in_(borrowers))
                )
            )
            .scalars()
            .all()
        )
        assert on_line, "первый заём лёг на линию города"
        assert sum(one.outstanding for one in on_line) <= permitted, (
            "на линию города легло больше, чем город может занять"
        )
