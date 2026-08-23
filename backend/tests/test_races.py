# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Two transactions at once on the same money, the same order, the same body.

Every other test in the suite is one session, one step after another, and
that is exactly why the races the review of 2026-08-23 found were never
seen. Here two sessions run the same operation through `asyncio.gather`;
the invariant must hold whichever of them wins, and one of them must be
refused instead of both succeeding on the same coin.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import current, current_catalog
from src.engine import ledger, market, stock, world
from src.models.ledger import AccountKind, PostingReason
from src.models.market import Order
from src.units import money

ORE = "Железная руда"


async def _wallet(session: AsyncSession, amount: int) -> uuid.UUID:
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    wallet = await ledger.account_for(session, AccountKind.IDENTITY, uuid.uuid4())
    await ledger.transfer(
        session, PostingReason.GENESIS, debit=genesis.id, credit=wallet.id, amount=amount
    )
    return wallet.id


def _slow(monkeypatch: pytest.MonkeyPatch, module: object, name: str, delay: float = 0.2) -> None:
    """Hold the transaction between its check and its write.

    A local database answers in well under a millisecond, and two
    coroutines then rarely overlap in the window the bug needs. The pause
    widens the window to a certainty: without the lock both pass the check
    before either writes; with the lock the second waits at the lock and
    sees the first's commit.
    """
    original = getattr(module, name)

    async def held(*args, **kwargs):
        result = await original(*args, **kwargs)
        await asyncio.sleep(delay)
        return result

    monkeypatch.setattr(module, name, held)


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
    await world.grant_item(session, yard, "Терминал маркетплейса", quality=70, origin="тест")
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


async def test_reads_do_not_write(session: AsyncSession) -> None:
    """`look`-class reads create nothing: no account for a fresh identity, no
    container for an untouched chest, no stall in a node never traded in."""
    from src.engine import storage

    identity = await world.create_identity(session, f"Гость-{uuid.uuid4().hex[:6]}")
    assert await ledger.find_account(session, AccountKind.IDENTITY, identity.id) is None
    node = await world.create_node(
        session, f"terra.quiet.{uuid.uuid4().hex[:6]}", "Тихо", area_m2=100
    )
    assert await market.stall(session, node, identity.id, create=False) is None
    body = await world.print_body(session, identity, node)
    pocket = await world.body_container(session, body)
    chest = await world.grant_item(session, pocket, "Сундук", quality=50, origin="тест")
    assert await storage.content(session, chest) == []
    assert await storage.inside(session, chest, create=False) is None
    assert await storage.is_empty(session, chest)


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
        session, f"terra.lock.{uuid.uuid4().hex[:6]}", "Лес", area_m2=100, properties={"лес": True}
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


async def test_look_writes_nothing(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession]
) -> None:
    """A fresh identity in a fresh node: `look` creates no account, no
    container, no row of any kind (review 2026-08-23)."""
    from sqlalchemy import func

    from src.api.commands import look as api
    from src.models.inventory import Container
    from src.models.ledger import LedgerAccount

    node = await world.create_node(
        session, f"terra.fresh.{uuid.uuid4().hex[:6]}", "Тихо", area_m2=100
    )
    identity = await world.create_identity(session, f"Гость-{uuid.uuid4().hex[:6]}")
    await world.print_body(session, identity, node)
    await session.commit()

    async def count(db: AsyncSession) -> tuple[int, int]:
        containers = await db.scalar(select(func.count()).select_from(Container))
        accounts = await db.scalar(select(func.count()).select_from(LedgerAccount))
        return containers, accounts

    before = await count(session)
    async with factory() as db, db.begin():
        await api._look({"identity_id": identity.id}, db, {"cmd": "look"})
        assert not db.new and not db.dirty, (db.new, db.dirty)
    assert await count(session) == before


async def test_two_swings_on_one_vein_do_not_mine_the_same_ore_twice(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The vein is shared; without its lock two miners read the same
    remainder and both subtract from it -- ore out of thin air."""
    from src.engine import mining
    from src.models.mining import MiningSession
    from src.models.world import Vein

    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.vein.{stamp}", "Забой", area_m2=100)
    vein = await world.create_vein(session, node, ORE, richness=60, remaining=100_000)
    sessions = []
    for i in range(2):
        identity = await world.create_identity(session, f"Шахтёр-{i}-{stamp}")
        body = await world.print_body(session, identity, node)
        pocket = await world.body_container(session, body)
        await world.grant_item(session, pocket, "Каменная кирка", quality=50, origin="тест")
        sessions.append((await mining.start(session, current(), body, vein)).id)
    await session.commit()
    start = await session.scalar(select(Vein.remaining).where(Vein.id == vein.id))
    _slow(monkeypatch, mining, "session_container")

    async def swing(session_id: uuid.UUID) -> float:
        async with factory() as db, db.begin():
            own = await db.get(MiningSession, session_id)
            return float((await mining.swing(db, current(), own)).mined)

    mined = await asyncio.gather(*(swing(s) for s in sessions))
    left = await session.scalar(select(Vein.remaining).where(Vein.id == vein.id))
    from src.units import amount as to_units

    assert start - left == sum(to_units(m) for m in mined), (
        "жила отдала ровно столько, сколько добыто"
    )


async def test_burning_coal_and_carrying_it_away_at_once_keep_the_count(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A shared yard: the tick burns coal while a player picks it up. Both
    read the stack, both write it -- without the lock one write is lost and
    coal is either doubled or vanishes (wave 2, item 4a)."""
    from src.engine import rig
    from src.models.inventory import Item

    stamp = uuid.uuid4().hex[:6]
    node = await world.create_node(session, f"terra.yard.{stamp}", "Двор", area_m2=100)
    yard = await world.node_container(session, node)
    coal = await world.grant_item(session, yard, "Уголь", amount=10, origin="тест")
    identity = await world.create_identity(session, f"Носильщик-{stamp}")
    body = await world.print_body(session, identity, node)
    pocket = await world.body_container(session, body)
    await session.commit()
    #: The tick counts the coal, and the carry commits before the tick locks:
    #: the stack the tick already holds in memory is stale by then.
    _slow(monkeypatch, rig, "_coal_available")

    async def burn() -> None:
        async with factory() as db, db.begin():
            #: As the tick does: count the coal first, then burn it. The count
            #: loads the stack into the session before the lock; the lock must
            #: reread it, or the burn writes from the value before the carry.
            assert await rig._coal_available(db, yard.id) >= 4
            await rig._burn(db, yard.id, 4)

    async def carry() -> None:
        async with factory() as db, db.begin():
            own = await db.get(Item, coal.id)
            target = await db.get(type(pocket), pocket.id)
            await world.move_stack(db, own, target, 3)

    await asyncio.gather(burn(), carry())
    rows = (
        await session.execute(
            select(Item.container_id, Item.amount).where(Item.type_key == "Уголь")
        )
    ).all()
    from src.units import amount as to_units

    assert sum(a for _, a in rows) == to_units(10 - 4), "сгорело четыре, унесено три, всего шесть"
    assert dict(rows)[pocket.id] == to_units(3)


async def test_locked_stacks_reread_what_the_session_already_holds(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession]
) -> None:
    """A stack loaded before the lock -- the tick counts the coal before it
    burns it -- is reread by the lock: without `populate_existing` the lock
    is on the fresh row and the write comes from the stale object."""
    from sqlalchemy import update

    from src.models.inventory import Item

    node = await world.create_node(
        session, f"terra.stale.{uuid.uuid4().hex[:6]}", "Двор", area_m2=1
    )
    yard = await world.node_container(session, node)
    coal = await world.grant_item(session, yard, "Уголь", amount=10, origin="тест")
    await session.commit()

    async with factory() as db, db.begin():
        held = (await db.execute(select(Item).where(Item.id == coal.id))).scalar_one()
        assert held.amount == 10_000
        async with factory() as other, other.begin():
            await other.execute(update(Item).where(Item.id == coal.id).values(amount=7_000))
        locked = await stock.locked_stacks(db, yard.id, ("Уголь",))
        assert locked[0] is held and held.amount == 7_000, "замок обязан перечитать строку"
