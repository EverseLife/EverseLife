"""The automatic machine: industry versus craft (D-035, D-058, D-129).

The split between the modes rests not on skill but on attention and the energy bill:

* the automaton is twice as fast and needs no tool -- it sets the ceiling itself;
* the result is even: no craft premium, the proportion is its setting;
* the master can still surpass the machine by adapting to the raw material;
* the automaton eats energy from the city pool and pays the tariff, a manual
  workbench consumes nothing -- craft is available even without money for bills.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import craft, energy, ledger, world
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Layer
from src.units import MINUTES_PER_HOUR, money

INGOT = "Слиток железа"
NAILS = "Гвозди"
#: Nails are forged: the test's manual mode lives at the forge (machine rebalance).
BENCH = "Кузница"


async def _workshop_(session: AsyncSession, *, automaton: bool = True, quality: float = 60):
    """A city node with a machine: the city is needed for the energy pool."""
    stamp = uuid.uuid4().hex[:8]
    capital = await world.create_node(
        session, f"terra.town.{stamp}", "Город", area_m2=1, layer=Layer.PLANET
    )
    workshop = await world.create_node(
        session, f"terra.town.{stamp}.shop", "Цех", area_m2=200,
        layer=Layer.CITY, parent=capital,
    )
    yard = await world.node_container(session, workshop)
    await world.grant_item(
        session, yard, craft.AUTO_BENCH if automaton else BENCH,
        quality=quality, origin="тест",
    )
    identity = await world.create_identity(session, f"Промышленник-{stamp}")
    body = await world.print_body(session, identity, workshop)
    await world.learn(session, identity, NAILS)

    pocket = await world.body_container(session, body)
    await world.grant_item(
        session, pocket, INGOT, amount=50, quality=70, origin="тест"
    )
    return workshop, identity, body


async def _pool(session, constants, workshop, *, qty: float = 5000):
    pool = await energy.pool_of(session, constants, workshop)
    pool.stored = Decimal(str(qty))
    pool.counted_at = datetime.now(UTC)
    await session.flush()
    return pool


async def _money(session, identity, qty: float = 500):
    account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    await ledger.transfer(
        session, PostingReason.GENESIS, debit=genesis.id, credit=account.id,
        amount=money(qty), memo={},
    )
    return account


# --- modes -------------------------------------------------------------------


async def test_automaton_twice_as_fast(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """`craft.auto_speed_k` -- that many times faster. Volume against quality."""
    workshop, identity, body = await _workshop_(session)
    await _pool(session, constants, workshop)
    await _money(session, identity)
    #: The same machine but in manual mode: we compare exactly the modes.
    yard = await world.node_container(session, workshop)
    await world.grant_item(session, yard, BENCH, quality=60, origin="тест")

    by_hand = await craft.plan(session, constants, catalog, body, NAILS, 2)
    automated = await craft.plan(session, constants, catalog, body, NAILS, 2, auto=True)

    assert automated.minutes == pytest.approx(
        by_hand.minutes / constants[R.CRAFT_AUTO_SPEED_K]
    )
    assert automated.auto and not by_hand.auto


async def test_automaton_needs_no_tool_but_craft_does(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The machine sets the ceiling: industry has no tool at all."""
    workshop, identity, body = await _workshop_(session)
    await _pool(session, constants, workshop)
    await _money(session, identity)

    plan = await craft.plan(session, constants, catalog, body, NAILS, 1, auto=True)
    assert plan.ceiling == pytest.approx(60), "потолок — качество автомата"


async def test_craft_can_outdo_machine(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A human adapts to the raw material, the machine always works by its setting.

    Compare on a **mix**: the craft premium exists only where there are
    proportions. An assembly has none at all, and there the modes have nothing to
    argue about (D-092).
    """
    glass = "Стекло"
    workshop, identity, body = await _workshop_(session, automaton=True, quality=80)
    yard = await world.node_container(session, workshop)
    await world.grant_item(session, yard, "Плавильная печь", quality=80, origin="тест")
    await world.learn(session, identity, glass)
    await _pool(session, constants, workshop)
    await _money(session, identity)

    pocket = await world.body_container(session, body)
    for raw in ("Кварцевый песок", "Уголь"):
        await world.grant_item(
            session, pocket, raw, amount=100, quality=60, origin="тест"
        )

    by_hand = await craft.plan(session, constants, catalog, body, glass, 1)
    automated = await craft.plan(session, constants, catalog, body, glass, 1, auto=True)
    assert by_hand.quality > automated.quality, "ремесло адаптивно, станок — нет"

    #: And the reverse, which makes the fork honest: a careless human loses to
    #: the machine. The machine is always even -- it is never better or worse than itself.
    proc = craft.procedure(catalog, glass)
    miss = {name: share * 3 for name, share in proc.per_unit.items()}
    careless = await craft.plan(
        session, constants, catalog, body, glass, 1, proportions=miss
    )
    assert careless.quality < automated.quality
    assert automated.accuracy == 1.0, "пропорция автомата — его настройка"


# --- energy ------------------------------------------------------------------


async def test_automaton_eats_energy_and_pays_tariff(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Whoever burns pays: otherwise energy is a subsidy, not an economy."""
    workshop, identity, body = await _workshop_(session)
    pool = await _pool(session, constants, workshop)
    account = await _money(session, identity)
    pool_before = float(pool.stored)
    money_before = await ledger.balance(session, account.id)

    plan = await craft.plan(session, constants, catalog, body, NAILS, 2, auto=True)
    hours = plan.minutes / MINUTES_PER_HOUR
    assert plan.energy == pytest.approx(
        constants[R.ENERGY_AUTO_BENCH_DRAW] * hours
    )

    await craft.start(session, constants, catalog, body, NAILS, 2, auto=True)
    assert float(pool.stored) == pytest.approx(pool_before - plan.energy)

    treasury = await ledger.account_for(
        session, AccountKind.CITY_TREASURY, pool.node_id
    )
    paid_ = money_before - await ledger.balance(session, account.id)
    assert paid_ == plan.energy_cost > 0
    assert await ledger.balance(session, treasury.id) == paid_


async def test_manual_batch_eats_no_energy(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Craft stays available to those with no money for bills (D-135)."""
    workshop, identity, body = await _workshop_(session, automaton=False)
    pool = await _pool(session, constants, workshop)
    before = float(pool.stored)

    plan = await craft.plan(session, constants, catalog, body, NAILS, 2)
    assert plan.energy == 0 and plan.energy_cost == 0
    await craft.start(session, constants, catalog, body, NAILS, 2)
    assert float(pool.stored) == pytest.approx(before)


async def test_empty_pool_stops_industry(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A city without fuel stands -- and everyone sees that, not just one owner."""
    workshop, identity, body = await _workshop_(session)
    await _pool(session, constants, workshop, qty=0)
    await _money(session, identity)

    with pytest.raises(energy.NotEnough):
        await craft.start(session, constants, catalog, body, NAILS, 2, auto=True)


async def test_automaton_does_not_work_outside_city(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Outside city territory there is no grid: industry lives in the city."""
    stamp = uuid.uuid4().hex[:6]
    farmstead = await world.create_node(
        session, f"terra.lone.{stamp}", "Хутор", area_m2=100, layer=Layer.PLANET
    )
    yard = await world.node_container(session, farmstead)
    await world.grant_item(session, yard, craft.AUTO_BENCH, quality=60, origin="тест")
    identity = await world.create_identity(session, f"Одиночка-{stamp}")
    body = await world.print_body(session, identity, farmstead)
    await world.learn(session, identity, NAILS)
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, INGOT, amount=50, quality=70, origin="тест")
    await _money(session, identity)

    with pytest.raises(energy.NoGrid):
        await craft.start(session, constants, catalog, body, NAILS, 1, auto=True)
