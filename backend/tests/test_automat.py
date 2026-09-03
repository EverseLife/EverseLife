# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The automat: production without the player (D-253).

Checked is what the automat was introduced this way for:

* it works without the player and does not sleep -- and loses to a human in
  everything else: slower (`auto.speed_share`), never above the quality
  ceiling (`auto.quality_cap`);
* three obligations keep it dependent on people: lubricant, energy, inputs.
  Any one violated -- and the machine stands, silently, without an error;
* the programme comes out of the owner's own knowledge: the machine is not
  a free library, and the ladder of stations keeps its meaning through
  `auto.covers` -- every station has its own automat, and some have none.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import automat, energy, ledger, storage, world
from src.models.inventory import Item
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Layer, Node
from src.units import amount_float, money

NAILS = "nails"
IRON = "iron_ingot"
LUBRICANT = "lubricant"


async def _factory_floor(
    session: AsyncSession,
    constants: Constants,
    *,
    machine_kind: str = "auto_station",
    stored_energy: float = 10_000,
    funded: bool = True,
):
    """A city yard with an automat standing in it, a pool, and an owner who may build."""
    stamp = uuid.uuid4().hex[:8]
    capital = await world.create_node(
        session, f"terra.fab.{stamp}", "Столица", area_m2=1, layer=Layer.PLANET
    )
    yard_node = await world.create_node(
        session,
        f"terra.fab.{stamp}.floor",
        "Цех",
        area_m2=200,
        layer=Layer.CITY,
        parent=capital,
    )
    identity = await world.create_identity(session, f"Фабрикант-{stamp}")
    body = await world.print_body(session, identity, yard_node)
    yard = await world.node_container(session, yard_node)
    machine = await world.grant_item(session, yard, machine_kind, quality=70, origin="тест")
    pool = await energy.pool_of(session, constants, yard_node)
    assert pool is not None
    pool.stored = Decimal(str(stored_energy))
    #: The owner can pay the energy bill: an unpaid one stops the machine
    #: (D-135), and that is its own test, not every test's noise.
    if funded:
        account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
        genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
        await ledger.transfer(
            session,
            PostingReason.GENESIS,
            debit=genesis.id,
            credit=account.id,
            amount=money(100_000),
            memo={},
        )
    await session.flush()
    return yard_node, yard, identity, body, machine


async def test_an_automat_settled_often_wears_as_much_as_one_settled_once(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """`auto.wear_per_day` is paid however often the machine is brought up to date.

    Condition is kept to a hundredth, and at five a day a stretch under about a
    minute and a half cannot be written to it -- less still for a good machine,
    which wears slower. Such wear used to be dropped outright, and
    `program`/`stop` settle the row too, so an owner tapping a button kept a
    machine that never wore out. The sliver waits on the thing itself
    (`Item.wear_remainder`), not on any clock: the row's stamp measures the
    output as well, and holding it back would make the same hours twice.
    """
    from src.engine import wear

    floors = []
    for _ in range(2):
        node, yard, identity, body, machine = await _factory_floor(session, constants)
        await _learn(session, identity, NAILS)
        row = await automat.program(session, constants, catalog, body, machine, NAILS)
        machine.condition = Decimal("100")
        floors.append((row, machine))
    await session.flush()
    (often, machine_a), (once, machine_b) = floors
    started = often.counted_at
    once.counted_at = started
    await session.flush()

    #: Through the row every time: the defect lives in the round trip, where
    #: `Numeric(6, 2)` rounds the write away.
    steps, every = 40, timedelta(seconds=30)
    for tick in range(1, steps + 1):
        when = started + every * tick
        await automat.advance(session, constants, often, catalog=catalog, now=when)
        await session.refresh(machine_a, ["condition"])
    await automat.advance(session, constants, once, catalog=catalog, now=started + every * steps)
    await session.refresh(machine_b, ["condition"])

    #: The output is the trap this fix fell into once on the rig: carrying the
    #: sliver on `counted_at` made the next pass repeat the stretch. The busy
    #: machine must have made exactly what the quiet one made.
    #: Sixty sums against one, so to a millionth rather than to the digit:
    #: the double count this guards against was a whole percent and more.
    assert float(often.backlog) == pytest.approx(float(once.backlog))

    term = wear.life_factor(constants, float(machine_a.quality))
    worn = constants[R.AUTO_WEAR_PER_DAY] / term * (steps * every) / timedelta(hours=24)
    #: The whole point: the busy machine wore exactly as much as the quiet one.
    assert Decimal(machine_a.condition) == Decimal(machine_b.condition)
    #: And neither more than the twenty minutes earned, nor a step behind it.
    assert 100 - float(machine_b.condition) <= worn
    assert 100 - float(machine_b.condition) > worn - 0.01


async def _lube_in(session: AsyncSession, yard, units: float) -> Item:
    """Lubricant standing in the node: a canister with the liquid inside (D-230)."""
    canister = await world.grant_item(session, yard, "canister", quality=60, origin="тест")
    inside = await storage.inside(session, canister)
    return await world.grant_item(
        session, inside, LUBRICANT, amount=units, quality=55, origin="тест"
    )


async def _learn(session: AsyncSession, identity, key: str) -> None:
    from src.models.identity import Identity

    row = await session.get(Identity, identity.id)
    await world.learn(session, row, key)


# --- programming -------------------------------------------------------------


async def test_programme_comes_out_of_own_knowledge(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Choosing is loading -- but the machine is not a free library (D-253)."""
    _, _, identity, body, machine = await _factory_floor(session, constants)

    with pytest.raises(automat.RecipeUnknown) as refused:
        await automat.program(session, constants, catalog, body, machine, NAILS)
    assert refused.value.key == "auto-recipe-unknown"

    await _learn(session, identity, NAILS)
    row = await automat.program(session, constants, catalog, body, machine, NAILS)
    assert row.recipe_key == NAILS


async def test_an_operation_needs_no_recipe(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Smelting is everyone's, at the furnace and in the furnace automat alike."""
    _, _, _, body, machine = await _factory_floor(session, constants, machine_kind="auto_furnace")
    row = await automat.program(session, constants, catalog, body, machine, IRON)
    assert row.recipe_key == IRON


async def test_every_station_has_its_own_automat(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """`auto.covers` splits the family: a furnace recipe does not go into the assembler."""
    _, _, identity, body, machine = await _factory_floor(session, constants)
    with pytest.raises(automat.NotCovered) as refused:
        await automat.program(session, constants, catalog, body, machine, IRON)
    assert refused.value.key == "auto-not-covered"

    #: And the hearth has none at all: food is cooked by people (D-119).
    await _learn(session, identity, "bread")
    with pytest.raises(Exception) as dish:
        await automat.program(session, constants, catalog, body, machine, "bread")
    #: A dish refuses even earlier -- by roles (D-119), the same wall craft has.
    assert getattr(dish.value, "key", "") in ("craft-is-a-dish", "auto-not-covered")


async def test_the_pyroxite_tier_is_barred(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Until its own station exists (OQ-106), no automat takes the pyroxite tier."""
    _, _, identity, body, machine = await _factory_floor(
        session, constants, machine_kind="auto_furnace"
    )
    await _learn(session, identity, "pyroxite_slab")
    with pytest.raises(automat.BarredInput) as refused:
        await automat.program(session, constants, catalog, body, machine, "pyroxite_slab")
    assert refused.value.key == "auto-barred-input"


async def test_no_machine_builds_a_station(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A station is a build: its scale is set by hand (D-223)."""
    _, _, identity, body, machine = await _factory_floor(session, constants)
    await _learn(session, identity, "workbench")
    with pytest.raises(automat.NoStationBuilds) as refused:
        await automat.program(session, constants, catalog, body, machine, "workbench")
    assert refused.value.key == "auto-no-station-builds"


# --- the tick ----------------------------------------------------------------


async def test_the_tick_makes_goods_under_the_ceiling(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The machine works the elapsed hours: inputs off the yard, output onto
    it, quality at the ceiling -- a lot of average, never the good (D-253)."""
    _, yard, identity, body, machine = await _factory_floor(session, constants)
    await world.grant_item(session, yard, IRON, amount=100, quality=80, origin="тест")
    await _lube_in(session, yard, 100)
    await _learn(session, identity, NAILS)
    row = await automat.program(session, constants, catalog, body, machine, NAILS)

    moment = row.counted_at + timedelta(hours=8)
    made = await automat.advance(session, constants, row, catalog=catalog, now=moment)
    assert made > 0, "восемь часов фабрики дали продукцию"

    nails = (
        (
            await session.execute(
                select(Item).where(Item.container_id == yard.id, Item.type_key == NAILS)
            )
        )
        .scalars()
        .all()
    )
    assert nails and amount_float(nails[0].amount) == pytest.approx(made)
    #: The ceiling, not the inputs: the iron was 80, the nails are 45.
    assert float(nails[0].quality) == constants[R.AUTO_QUALITY_CAP]

    #: Slower than a hand: eight hours at 70% pace against the recipe's own time.
    proc_hours = 8 * constants[R.AUTO_SPEED_SHARE] / 100
    from src.engine.craft import procedure

    unit_hours = procedure(catalog, NAILS).step_hours
    assert made == pytest.approx(int(proc_hours / unit_hours), abs=1)


async def test_no_lubricant_no_work(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The blood of the factory (D-253): the vessels are dry -- the machine stands."""
    _, yard, identity, body, machine = await _factory_floor(session, constants)
    await world.grant_item(session, yard, IRON, amount=100, quality=60, origin="тест")
    await _learn(session, identity, NAILS)
    row = await automat.program(session, constants, catalog, body, machine, NAILS)

    made = await automat.advance(
        session, constants, row, catalog=catalog, now=row.counted_at + timedelta(hours=8)
    )
    assert made == 0, "без смазки фабрика стоит"
    iron = (
        await session.execute(
            select(Item).where(Item.container_id == yard.id, Item.type_key == IRON)
        )
    ).scalar_one()
    assert amount_float(iron.amount) == pytest.approx(100), "входы не тронуты"


async def test_lubricant_burns_by_the_hour_worked(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """`auto.lube_per_hour` of it, off the vessels standing in the node."""
    _, yard, identity, body, machine = await _factory_floor(session, constants)
    await world.grant_item(session, yard, IRON, amount=1000, quality=60, origin="тест")
    lube = await _lube_in(session, yard, 100)
    await _learn(session, identity, NAILS)
    row = await automat.program(session, constants, catalog, body, machine, NAILS)

    await automat.advance(
        session, constants, row, catalog=catalog, now=row.counted_at + timedelta(hours=8)
    )
    burnt = 100 - amount_float(lube.amount)
    assert burnt == pytest.approx(8 * constants[R.AUTO_LUBE_PER_HOUR], rel=0.05)


async def test_the_pool_is_billed_to_the_owner(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Whoever burns pays (D-135), presence or not: the tariff bill lands on
    the owner with every tick, and the pool loses what the machine drew."""
    from src.engine import ledger
    from src.models.ledger import AccountKind

    node, yard, identity, body, machine = await _factory_floor(session, constants)
    await world.grant_item(session, yard, IRON, amount=1000, quality=60, origin="тест")
    await _lube_in(session, yard, 100)
    await _learn(session, identity, NAILS)
    row = await automat.program(session, constants, catalog, body, machine, NAILS)

    pool = await energy.pool_of(session, constants, node)
    assert pool is not None
    stored_before = float(pool.stored)
    account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
    balance_before = await ledger.balance(session, account.id)

    await automat.advance(
        session, constants, row, catalog=catalog, now=row.counted_at + timedelta(hours=8)
    )
    drawn = stored_before - float(pool.stored)
    assert drawn == pytest.approx(8 * constants[R.AUTO_ENERGY_PER_HOUR], rel=0.01)
    if float(pool.tariff) > 0:
        assert await ledger.balance(session, account.id) < balance_before, (
            "счёт выставлен владельцу"
        )


async def test_a_liquid_output_waits_for_room(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The reactor pours into the vessels standing here; no room -- the work
    waits in the backlog rather than spilling (D-230, D-253)."""
    _, yard, identity, body, machine = await _factory_floor(
        session, constants, machine_kind="auto_reactor"
    )
    #: Spirit: sugar and water, the water itself out of a vessel.
    await world.grant_item(session, yard, "sugar", amount=500, quality=60, origin="тест")
    water_can = await world.grant_item(session, yard, "canister", quality=60, origin="тест")
    inside = await storage.inside(session, water_can)
    await world.grant_item(session, inside, "water", amount=90, quality=60, origin="тест")
    await _lube_in(session, yard, 100)
    await _learn(session, identity, "alcohol")
    row = await automat.program(session, constants, catalog, body, machine, "alcohol")

    made = await automat.advance(
        session, constants, row, catalog=catalog, now=row.counted_at + timedelta(hours=20)
    )
    if made > 0:
        stacks = (
            (await session.execute(select(Item).where(Item.type_key == "alcohol"))).scalars().all()
        )
        yard_loose = [s for s in stacks if s.container_id == yard.id]
        assert not yard_loose, "жидкость не лежит на дворе -- только в таре (D-230)"


async def test_two_ticks_do_not_double_the_goods(
    session: AsyncSession,
    factory,
    constants: Constants,
    catalog: Catalog,
) -> None:
    """Two worker processes advancing one automat must not both pay out the
    same hours: the row is taken for the transaction, and the second sees the
    stamp the first left (the quality bar: amounts under the row lock)."""
    import asyncio

    from src.models.automat import Automat as AutomatRow

    _, yard, identity, body, machine = await _factory_floor(session, constants)
    await world.grant_item(session, yard, IRON, amount=1000, quality=60, origin="тест")
    await _lube_in(session, yard, 1000)
    await _learn(session, identity, NAILS)
    row = await automat.program(session, constants, catalog, body, machine, NAILS)
    row_id, yard_id = row.id, yard.id
    moment = row.counted_at + timedelta(hours=8)
    await session.commit()

    async def tick() -> float:
        async with factory() as db, db.begin():
            own = await db.get(AutomatRow, row_id)
            return await automat.advance(db, constants, own, now=moment)

    made = await asyncio.gather(tick(), tick())
    total = sum(made)
    async with factory() as db:
        nails = (
            (
                await db.execute(
                    select(Item).where(Item.container_id == yard_id, Item.type_key == NAILS)
                )
            )
            .scalars()
            .all()
        )
        landed = sum(amount_float(s.amount) for s in nails)
    assert landed == pytest.approx(total), "выплачено ровно столько, сколько насчитано"
    assert sorted(made)[0] == pytest.approx(0.0), (
        "второй тик увидел штамп первого и не дублировал часы"
    )


async def test_an_unpaid_bill_stops_the_machine(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Whoever burns pays (D-135), and whoever cannot pay does not burn: the
    machine stands, the pool keeps its energy, and the tick survives."""
    _, yard, identity, body, machine = await _factory_floor(session, constants, funded=False)
    await world.grant_item(session, yard, IRON, amount=100, quality=60, origin="тест")
    await _lube_in(session, yard, 100)
    await _learn(session, identity, NAILS)
    row = await automat.program(session, constants, catalog, body, machine, NAILS)

    made = await automat.advance(
        session, constants, row, catalog=catalog, now=row.counted_at + timedelta(hours=8)
    )
    pool = await energy.pool_of(session, constants, await session.get(Node, row.node_id))
    if float(pool.tariff) > 0:
        assert made == 0, "нищий владелец — стоящая фабрика, не упавший тик"
        assert float(pool.stored) == pytest.approx(10_000), "пул не тронут"


async def test_batteries_feed_the_wilderness(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """No grid -- the machine runs on the cells standing beside it (D-071):
    no pool, no tariff, the energy was bought when the battery was charged."""
    from datetime import UTC, datetime

    stamp = uuid.uuid4().hex[:8]
    wild = await world.create_node(session, f"terra.wild.{stamp}", "Глушь", area_m2=200)
    identity = await world.create_identity(session, f"Отшельник-{stamp}")
    body = await world.print_body(session, identity, wild)
    yard = await world.node_container(session, wild)
    machine = await world.grant_item(session, yard, "auto_station", quality=70, origin="тест")
    cell = await world.grant_item(session, yard, "battery", quality=60, origin="тест")
    cell.charge = Decimal("1000")
    cell.charged_at = datetime.now(UTC)
    await world.grant_item(session, yard, IRON, amount=100, quality=60, origin="тест")
    await _lube_in(session, yard, 100)
    await _learn(session, identity, NAILS)
    row = await automat.program(session, constants, catalog, body, machine, NAILS)

    made = await automat.advance(
        session, constants, row, catalog=catalog, now=row.counted_at + timedelta(hours=8)
    )
    assert made > 0, "глушь работает от аккумулятора"
    assert float(cell.charge) < 1000, "заряд ушёл из ячейки"


async def test_stop_takes_the_programme_off(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The machine stays; the recipe goes -- and the view reads it back."""
    _, yard, identity, body, machine = await _factory_floor(session, constants)
    await _learn(session, identity, NAILS)
    await automat.program(session, constants, catalog, body, machine, NAILS)

    floor = await automat.view(session, catalog, body)
    assert floor["machines"] and floor["machines"][0]["recipe"] == NAILS

    #: The row goes with the programme: a machine without one is a thing
    #: again -- it does not wear by the clock and does not cost the tick.
    assert await automat.stop(session, constants, body, machine) is None
    floor = await automat.view(session, catalog, body)
    assert floor["machines"] == []


async def test_reprogramming_pays_the_old_programme_first(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Hours lived under the old recipe pay out under it, not the new one."""
    _, yard, identity, body, machine = await _factory_floor(session, constants)
    await world.grant_item(session, yard, IRON, amount=1000, quality=60, origin="тест")
    await _lube_in(session, yard, 1000)
    await _learn(session, identity, NAILS)
    await _learn(session, identity, "pipe")
    row = await automat.program(session, constants, catalog, body, machine, NAILS)

    #: Eight hours pass; the owner switches the programme. The nails of those
    #: hours land with the switch, and the backlog does not leak into pipes.
    later = row.counted_at + timedelta(hours=8)
    await world.grant_item(session, yard, "steel", amount=1000, quality=60, origin="тест")
    row = await automat.program(session, constants, catalog, body, machine, "pipe", now=later)
    assert row.recipe_key == "pipe"
    assert float(row.backlog) == 0, "недоделанный гвоздь не станет половиной трубы"

    nails = (
        (
            await session.execute(
                select(Item).where(Item.container_id == yard.id, Item.type_key == NAILS)
            )
        )
        .scalars()
        .all()
    )
    assert nails, "часы старой программы выплачены гвоздями при переключении"


# --- the wires (D-253, wave 5) -----------------------------------------------


async def test_a_wire_joins_two_machines_and_the_view_draws_it(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Idempotent both ways: one wire drawn twice is one wire, and cutting
    what is not there changes nothing. Drawn before any programme: the
    picture of the factory comes first."""
    _, yard, identity, body, assembler = await _factory_floor(session, constants)
    furnace = await world.grant_item(session, yard, "auto_furnace", quality=70, origin="тест")

    await automat.link(session, body, furnace, assembler)
    await automat.link(session, body, furnace, assembler)
    floor = await automat.view(session, catalog, body)
    assert floor["links"] == [{"from": str(furnace.id), "to": str(assembler.id)}]

    with pytest.raises(automat.SelfLink):
        await automat.link(session, body, assembler, assembler)

    assert await automat.unlink(session, body, furnace, assembler) is True
    assert await automat.unlink(session, body, furnace, assembler) is False
    floor = await automat.view(session, catalog, body)
    assert floor["links"] == []


async def test_the_chain_flows_within_one_tick(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The wire's mechanical meaning (D-253 wave 5): the furnace smelts
    before the assembler eats, so ore becomes nails in one pass. Unwired,
    the same floor lags: the consumer advances first (lower id) and starves."""
    from datetime import timedelta as delta

    _, yard, identity, body, assembler = await _factory_floor(session, constants)
    furnace = await world.grant_item(session, yard, "auto_furnace", quality=70, origin="тест")
    await world.grant_item(session, yard, "iron_ore", amount=4000, quality=60, origin="тест")
    await world.grant_item(session, yard, "coal", amount=1000, quality=60, origin="тест")
    await _lube_in(session, yard, 1000)
    await _learn(session, identity, NAILS)
    #: The consumer is programmed FIRST: its row id is lower, and the bare id
    #: order would advance it before the furnace it feeds.
    eater = await automat.program(session, constants, catalog, body, assembler, NAILS)
    smelter = await automat.program(session, constants, catalog, body, furnace, IRON)
    assert eater.id != smelter.id

    await automat.link(session, body, furnace, assembler)
    moment = eater.counted_at + delta(hours=10)
    made = await automat.tick_automats(session, constants, now=moment)
    assert made > 0

    nails = (
        (
            await session.execute(
                select(Item).where(Item.container_id == yard.id, Item.type_key == NAILS)
            )
        )
        .scalars()
        .all()
    )
    assert nails, "руда стала гвоздями за один проход: печь отработала раньше сборщика"


def test_chain_order_puts_feeders_first_whatever_the_ids() -> None:
    """The Kahn helper, both id orders: the wire decides, never the uuid.

    The integration test above cannot pin this -- uuids land in random
    order -- so the helper is pinned directly, adversarially both ways.
    """
    from types import SimpleNamespace
    from uuid import UUID

    low, high = UUID(int=1), UUID(int=2)
    for eater_item, smelter_item in ((low, high), (high, low)):
        eater = SimpleNamespace(item_id=eater_item)
        smelter = SimpleNamespace(item_id=smelter_item)
        rows = sorted([eater, smelter], key=lambda r: r.item_id)
        wire = SimpleNamespace(from_item_id=smelter_item, to_item_id=eater_item)
        ordered = automat._chain_order(rows, [wire])
        assert ordered.index(smelter) < ordered.index(eater), (
            "кормящий раньше кормимого при любом порядке id"
        )

    #: A cycle releases everybody, in the incoming order.
    a, b = SimpleNamespace(item_id=low), SimpleNamespace(item_id=high)
    ring = [
        SimpleNamespace(from_item_id=low, to_item_id=high),
        SimpleNamespace(from_item_id=high, to_item_id=low),
    ]
    assert automat._chain_order([a, b], ring) == [a, b]
