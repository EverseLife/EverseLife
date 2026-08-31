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
import random
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import current, current_catalog
from src.constants import registry as R
from src.engine import ledger, market, stock, travel, world
from src.models.energy import EnergyPool
from src.models.identity import Body
from src.models.inventory import Item
from src.models.ledger import AccountKind, PostingReason
from src.models.market import Order
from src.models.world import Node, Surface, Vein
from src.units import money

ORE = "iron_ore"


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


async def test_the_eruption_does_not_burn_what_was_carried_out(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The window before an eruption is the whole licence for the burning
    (D-197, P6), and somebody using it must not be robbed by the fire anyway.

    The carry-out goes **first** and holds its row: the sleep sits between
    `storage.pick` and its commit, so the fire meets a sack already moving.

    With the lock the fire waits at that row, rereads it after the commit and
    finds the sack in a pocket -- not in the node -- so there is nothing here
    to burn. Without it the fire reads the sack where it still was, queues its
    delete behind the same row, and takes it **out of the player's hands** the
    moment the carry-out lands: the one place it was safe.
    """
    from src.engine import plates, storage
    from src.models.world import Layer, Planet

    _slow(monkeypatch, storage, "pick")
    stamp = uuid.uuid4().hex[:8]
    sphere = await world.create_node(
        session,
        "pyroxis",
        "Пироксис",
        planet=Planet.PYROXIS,
        area_m2=1,
        layer=Layer.SPACE,
    )
    field = await world.create_node(
        session,
        f"pyroxis.{stamp}.field",
        "Чёрное поле",
        planet=Planet.PYROXIS,
        area_m2=5000,
        layer=Layer.PLANET,
        parent=sphere,
    )
    who = await world.create_identity(session, f"Вахтовик-{stamp}")
    body = await world.print_body(session, who, field)
    sack = await world.grant_item(
        session,
        await world.node_container(session, field),
        ORE,
        amount=10,
        quality=60,
        origin="тест",
    )
    field_id, body_id, sack_id = field.id, body.id, sack.id
    await session.commit()

    async def erupt() -> None:
        #: Long enough for the carry-out to be inside its transaction and
        #: holding the row; short enough to be well inside its sleep.
        await asyncio.sleep(0.05)
        async with factory() as db, db.begin():
            place = await db.get(Node, field_id)
            assert place is not None
            await plates._burn(db, [place])

    async def carry() -> None:
        async with factory() as db, db.begin():
            mine = await db.get(Body, body_id)
            thing = await db.get(Item, sack_id)
            assert mine is not None and thing is not None
            await storage.pick(db, current(), current_catalog(), mine, thing)

    outcome = await asyncio.gather(erupt(), carry(), return_exceptions=True)
    assert not [one for one in outcome if isinstance(one, BaseException)], outcome

    async with factory() as db:
        left = await db.get(Item, sack_id)
        assert left is not None, "вынесенное сгорело в руках"
        pocket = await world.body_container(db, await db.get(Body, body_id))
        assert left.container_id == pocket.id, "вынесенное сгорело в руках"


async def test_the_eruption_does_not_burn_what_was_taken_out_of_a_chest(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same robbery as above, through the door of a chest.

    A chest burns with what is in it, or its goods would outlive the place they
    lay in. But its inside is a second container, and a lock on the things
    lying on the ground says nothing about it: `storage.take` locks the thing,
    not the chest. Without the lock **inside** the box the delete queues behind
    that take and lands the moment it commits -- out of the player's hands.

    On the wild ground of Pyroxis anybody may open anybody's chest
    (`station.may_build` gives the wild to everyone), so this is not a corner:
    it is the ordinary way a sack leaves a field before an eruption.
    """
    from src.engine import plates, storage
    from src.models.world import Layer, Planet

    _slow(monkeypatch, storage, "take")
    stamp = uuid.uuid4().hex[:8]
    sphere = await world.create_node(
        session, "pyroxis", "Пироксис", planet=Planet.PYROXIS, area_m2=1, layer=Layer.SPACE
    )
    field = await world.create_node(
        session,
        f"pyroxis.{stamp}.field",
        "Чёрное поле",
        planet=Planet.PYROXIS,
        area_m2=5000,
        layer=Layer.PLANET,
        parent=sphere,
    )
    who = await world.create_identity(session, f"Вахтовик-{stamp}")
    body = await world.print_body(session, who, field)
    chest = await world.grant_item(
        session,
        await world.node_container(session, field),
        "chest",
        quality=60,
        origin="тест",
    )
    box = await storage.inside(session, chest)
    sack = await world.grant_item(session, box, ORE, amount=10, quality=60, origin="тест")
    field_id, body_id, chest_id, sack_id = field.id, body.id, chest.id, sack.id
    await session.commit()

    async def erupt() -> None:
        await asyncio.sleep(0.05)
        async with factory() as db, db.begin():
            place = await db.get(Node, field_id)
            assert place is not None
            await plates._burn(db, [place])

    async def carry() -> None:
        async with factory() as db, db.begin():
            mine = await db.get(Body, body_id)
            crate = await db.get(Item, chest_id)
            thing = await db.get(Item, sack_id)
            assert mine is not None and crate is not None and thing is not None
            await storage.take(db, current(), current_catalog(), mine, crate, thing)

    outcome = await asyncio.gather(erupt(), carry(), return_exceptions=True)
    assert not [one for one in outcome if isinstance(one, BaseException)], outcome

    async with factory() as db:
        left = await db.get(Item, sack_id)
        assert left is not None, "вынесенное из сундука сгорело в руках"
        pocket = await world.body_container(db, await db.get(Body, body_id))
        assert left.container_id == pocket.id, "вынесенное из сундука сгорело в руках"
        #: And the chest itself is gone with the field: what stayed in it burned.
        assert await db.get(Item, chest_id) is None


async def test_death_and_leaving_one_face_do_not_both_carry_the_haul(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One face, two ways out of it, and one haul.

    A rift takes the miner while the ground moves the vein: `death.die` closes
    the face and leaves the ore in the node, `plates._close_faces` closes it
    and carries the ore into a pocket. Both are right on their own. Together
    they are two transactions over one haul, and the lock on the session row is
    what makes them a queue instead of a collision.

    What goes red without it is the **order**: one side holds the things and
    waits for the session, the other holds the session and waits for the
    things, and the database kills one of them. So the assertion that actually
    catches it is the one about neither call raising -- the count of the ore is
    the invariant that must hold afterwards, not the thing the lock buys.
    """
    from src.engine import death, mining, plates
    from src.models.mining import MiningSession, Pace, SessionState
    from src.models.world import Layer, Planet

    _slow(monkeypatch, mining, "session_container")
    stamp = uuid.uuid4().hex[:8]
    sphere = await world.create_node(
        session, "pyroxis", "Пироксис", planet=Planet.PYROXIS, area_m2=1, layer=Layer.SPACE
    )
    field = await world.create_node(
        session,
        f"pyroxis.{stamp}.field",
        "Чёрное поле",
        planet=Planet.PYROXIS,
        area_m2=5000,
        layer=Layer.PLANET,
        parent=sphere,
    )
    near = await world.create_node(
        session,
        f"pyroxis.{stamp}.near",
        "Соседнее поле",
        planet=Planet.PYROXIS,
        area_m2=5000,
        layer=Layer.PLANET,
        parent=sphere,
    )
    await travel.connect(session, field, near, base_seconds=900, surface=Surface.TRAIL)
    vein = await world.create_vein(session, field, ORE, richness=70, remaining=1000)
    who = await world.create_identity(session, f"Вахтовик-{stamp}")
    body = await world.print_body(session, who, field)
    face = MiningSession(body_id=body.id, vein_id=vein.id, pace=Pace.STEADY, roof=100)
    session.add(face)
    await session.flush()
    await world.grant_item(
        session,
        await mining.session_container(session, face),
        ORE,
        amount=9,
        quality=60,
        origin="тест",
    )
    #: And the long branch of `die`: a pocket with something in it, and a heap
    #: of the same goods already lying in the node. Then the death lays its
    #: salvage into that heap -- `stack_up` takes the node's things under a
    #: lock -- **before** it closes the face, which is the order the eruption
    #: takes them in too. With an empty pocket the death skips all of that and
    #: the test walks a branch where the order cannot be wrong.
    await world.grant_item(
        session,
        await world.body_container(session, body),
        ORE,
        amount=4,
        quality=60,
        origin="тест",
    )
    await world.grant_item(
        session,
        await world.node_container(session, field),
        ORE,
        amount=2,
        quality=60,
        origin="тест",
    )
    field_id, body_id, vein_id, face_id = field.id, body.id, vein.id, face.id
    await session.commit()

    async def dies() -> None:
        async with factory() as db, db.begin():
            mine = await db.get(Body, body_id)
            assert mine is not None
            await death.die(db, current(), mine, cause="разлом под ногами")

    async def ground_moves() -> None:
        await asyncio.sleep(0.05)
        async with factory() as db, db.begin():
            rock = await db.get(Vein, vein_id)
            assert rock is not None
            await plates._close_faces(db, current(), rock, now=datetime.now(UTC))

    outcome = await asyncio.gather(dies(), ground_moves(), return_exceptions=True)
    assert not [one for one in outcome if isinstance(one, BaseException)], outcome

    async with factory() as db:
        closed = await db.get(MiningSession, face_id)
        assert closed is not None and closed.state is SessionState.LEFT
        #: Nine units were mined, and nine units exist -- wherever they ended up.
        here = await world.contents(
            db, await world.node_container(db, await db.get(Node, field_id))
        )
        pocket = await world.contents(
            db, await world.body_container(db, await db.get(Body, body_id))
        )
        total = sum(
            float(thing.amount) / 1000 for thing in [*here, *pocket] if thing.type_key == ORE
        )
        #: Nine in the face, two lying in the node, and of the four in the
        #: pocket whatever the salvage roll kept -- never more than fifteen and
        #: never less than the eleven that were never on the body.
        assert 11 <= total <= 15, f"добытое размножилось или пропало: {total}"


async def test_two_swings_at_once_are_paid_for_twice(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stamina is on the same list as money and remainders (CLAUDE.md).

    Two sockets of one identity, two swings in the same second. Without the
    lock on the body both read the same reserve, both find it enough, and both
    write their own remainder -- the second write erases the first, and one of
    the swings is free. The ore, meanwhile, is mined twice: the vein is locked,
    so it is honestly spent.
    """
    from src.engine import frost, mining
    from src.models.mining import MiningSession, Pace

    #: The pause goes between the reading of the reserve and its write-off:
    #: `drain_multiplier` is the last thing asked before the price is computed.
    _slow(monkeypatch, frost, "drain_multiplier")
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.face.{stamp}", "Забой", area_m2=500)
    vein = await world.create_vein(session, node, ORE, richness=70, remaining=100_000)
    who = await world.create_identity(session, f"Шахтёр-{stamp}")
    body = await world.print_body(session, who, node)
    body.stamina = Decimal("90")
    face = MiningSession(body_id=body.id, vein_id=vein.id, pace=Pace.STEADY, roof=100)
    session.add(face)
    await session.flush()
    body_id, face_id = body.id, face.id
    was = float(body.stamina)
    await session.commit()

    async def swing() -> None:
        async with factory() as db, db.begin():
            open_face = await db.get(MiningSession, face_id)
            assert open_face is not None
            await mining.swing(db, current(), open_face)

    await asyncio.gather(swing(), swing(), return_exceptions=True)

    async with factory() as db:
        again = await db.get(Body, body_id)
        spent = was - float(again.stamina)
        one = mining.swing_cost(current(), again, Pace.STEADY, datetime.now(UTC), chill=1.0)
        assert spent == pytest.approx(2 * one, rel=0.05), (
            f"два удара списали {spent:.2f} вместо {2 * one:.2f}: один достался бесплатно"
        )


async def test_two_scouts_do_not_open_one_room_twice(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A city of the Forerunners is worked out like a vein (D-232), and the
    count of what is open is its remainder.

    Two scouts come back in the same second, in different workers. Without the
    lock on the city's row both read "nothing opened yet", both write one, and
    one of the two rooms is free -- the city outlives its own stock.
    """
    from src.engine import ruins
    from src.models.world import Layer, Planet

    _slow(monkeypatch, ruins, "_fill")
    stamp = uuid.uuid4().hex[:8]
    sphere = await world.create_node(
        session,
        f"aurora.{stamp}.sphere",
        "Аврора",
        planet=Planet.AURORA,
        area_m2=1,
        layer=Layer.SPACE,
    )
    city = await world.create_node(
        session,
        f"aurora.{stamp}",
        "Город Предтеч",
        planet=Planet.AURORA,
        area_m2=1,
        layer=Layer.PLANET,
        parent=sphere,
        properties={ruins.PRECURSOR: True, ruins.KIND: "столица"},
    )
    hall = await world.create_node(
        session,
        f"aurora.{stamp}.hall",
        "Зал",
        planet=Planet.AURORA,
        area_m2=600,
        layer=Layer.CITY,
        parent=city,
        properties={ruins.PRECURSOR: True, ruins.DEPTH: 1},
    )
    city_id, hall_id = city.id, hall.id
    await session.commit()

    async def open_one(seed: int) -> None:
        async with factory() as db, db.begin():
            where = await db.get(Node, hall_id)
            assert where is not None
            await ruins.open_room(db, constants, random.Random(seed), where, who=None)

    await asyncio.gather(open_one(1), open_one(2))

    async with factory() as db:
        again = await db.get(Node, city_id)
        assert again is not None
        assert ruins.opened(again) == 2, "две вскрытые двери — две, а не одна"


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
    chest = await world.grant_item(session, pocket, "chest", quality=50, origin="тест")
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
        await world.grant_item(session, pocket, "stone_pickaxe", quality=50, origin="тест")
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
    coal = await world.grant_item(session, yard, "coal", amount=10, origin="тест")
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
        await session.execute(select(Item.container_id, Item.amount).where(Item.type_key == "coal"))
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
    coal = await world.grant_item(session, yard, "coal", amount=10, origin="тест")
    await session.commit()

    async with factory() as db, db.begin():
        held = (await db.execute(select(Item).where(Item.id == coal.id))).scalar_one()
        assert held.amount == 10_000
        async with factory() as other, other.begin():
            await other.execute(update(Item).where(Item.id == coal.id).values(amount=7_000))
        locked = await stock.locked_stacks(db, yard.id, ("coal",))
        assert locked[0] is held and held.amount == 7_000, "замок обязан перечитать строку"


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


async def test_two_marks_on_one_node_do_not_erase_each_other(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """`Node.properties` is one JSONB dict rewritten whole (review of D-238).

    A founder stamps the gate while a scout's return bumps the counter. Each
    builds its new dict from what it read at the start; without the reread
    under the row lock (`props._held`) the slower writer's snapshot is stale
    and its rewrite silently erases the faster one's key.
    """
    from src.engine import props
    from src.engine.explore import FOUND_HERE

    node = await world.create_node(
        session, f"terra.marks.{uuid.uuid4().hex[:6]}", "Перекрёсток", area_m2=100
    )
    node_id = node.id
    await session.commit()

    async def flag() -> None:
        async with factory() as db, db.begin():
            own = await db.get(Node, node_id)
            assert own is not None
            #: The stale snapshot is loaded by the `get` above; the pause lets
            #: the counter commit inside the window a plain rewrite loses.
            await asyncio.sleep(0.2)
            await props.stamp(db, own, {travel.EXIT: True})

    async def count() -> None:
        async with factory() as db, db.begin():
            own = await db.get(Node, node_id)
            assert own is not None
            await props.bump(db, own, FOUND_HERE)

    await asyncio.gather(flag(), count())

    async with factory() as db:
        again = await db.get(Node, node_id)
        assert again is not None
        held = again.properties or {}
        assert held.get(travel.EXIT) is True, "печать ворот стёрта счётчиком разведки"
        assert int(held.get(FOUND_HERE, 0)) == 1, "счётчик разведки стёрт печатью ворот"


async def test_two_bumps_of_one_counter_lose_neither(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """A counter in the properties map is a remainder like ore in a vein:
    two increments from the same snapshot would both write the same number."""
    from src.engine import props
    from src.engine.explore import FOUND_HERE

    node = await world.create_node(
        session, f"terra.count.{uuid.uuid4().hex[:6]}", "Развилка", area_m2=100
    )
    node_id = node.id
    await session.commit()

    async def one(delay: float) -> None:
        async with factory() as db, db.begin():
            own = await db.get(Node, node_id)
            assert own is not None
            await asyncio.sleep(delay)
            await props.bump(db, own, FOUND_HERE)

    await asyncio.gather(one(0.0), one(0.1))

    async with factory() as db:
        again = await db.get(Node, node_id)
        assert again is not None
        assert int((again.properties or {}).get(FOUND_HERE, 0)) == 2, (
            "две разведки — два, а не одно"
        )
