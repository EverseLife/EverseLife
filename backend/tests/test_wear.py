# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Wear, repair and recycling (D-129, D-058, 15-quality).

Checked is what the system was written for:

* a thing is finite (pillar P2): a tool runs out in as many sessions as the
  acceptance promises, and disappears rather than working forever at zero;
* quality determines the **speed** of wear, condition -- **how good the thing
  is now**: a broken anvil does worse, not just breaks suddenly;
* the service-life formula is taken from the vault and evaluated, not rewritten in code;
* repair restores condition but lowers the ceiling -- otherwise the thing would become eternal;
* recycling returns less than invested, and the difference is a sink.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.constants.formula import NotComputable, evaluate
from src.engine import craft, jobs, mining, wear, world
from src.models.craft import BatchKind
from src.models.identity import Body
from src.models.inventory import Item
from src.units import amount_float

PICK = "Железная кирка"
#: Steel goes into a hammer three pieces at a time: the one tool whose recycling
#: share is more than a whole piece (D-212).
HAMMER = "Молот"
STEEL = "Сталь"
BENCH = "Верстак"
INGOT = "Слиток железа"
HANDLE = "Рукоять"
BASKET = "Корзина"


async def _master(session: AsyncSession, *, machine: str | None = BENCH):
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.wear.{stamp}", "Двор", area_m2=100)
    identity = await world.create_identity(session, f"Хозяин-{stamp}")
    body = await world.print_body(session, identity, node)
    if machine is not None:
        yard = await world.node_container(session, node)
        await world.grant_item(session, yard, machine, quality=70, origin="сценарий теста")
    return node, identity, body


async def _thing(session: AsyncSession, body, type_key: str, *, quality: float, state: float = 100):
    pocket = await world.body_container(session, body)
    item = await world.grant_item(
        session, pocket, type_key, quality=quality, origin="сценарий теста"
    )
    item.condition = state
    await session.flush()
    return item


# --- the formula from the vault ----------------------------------------------


def test_formula_evaluated_not_copied(constants: Constants) -> None:
    """The formula's numbers stay in the vault -- otherwise an edit requires a release (D-065)."""
    formula = constants[R.QUALITY_DURABILITY_FACTOR]
    computed = formula.value(base_life=1, quality=80)
    assert computed == pytest.approx(evaluate(formula.text, base_life=1, quality=80))
    assert computed > formula.value(base_life=1, quality=40)


def test_algorithm_honestly_rejected() -> None:
    """A formula with summation over levels is code, and the engine writes it itself."""
    with pytest.raises(NotComputable):
        evaluate("sum(x^n for n in 1..floors)", x=1, floors=2)
    with pytest.raises(NotComputable):
        evaluate("__import__('os').system('ls')")


def test_good_thing_lasts_longer(constants: Constants) -> None:
    bad = wear.life_factor(constants, 20)
    good = wear.life_factor(constants, 90)
    assert good > bad > 0


# --- condition ---------------------------------------------------------------


async def test_wear_inverse_to_quality(session: AsyncSession, constants: Constants) -> None:
    """A good pickaxe wears slower exactly as many times as it is better."""
    _, _, body = await _master(session)
    bad = await _thing(session, body, PICK, quality=20)
    good = await _thing(session, body, PICK, quality=90)

    await wear.spend(session, constants, bad, constants[R.WEAR_TOOL_PER_SESSION], cause="проверка")
    await wear.spend(session, constants, good, constants[R.WEAR_TOOL_PER_SESSION], cause="проверка")
    await session.commit()

    assert float(good.condition) > float(bad.condition)


async def test_tool_wears_out_in_promised_sessions(
    session: AsyncSession, constants: Constants
) -> None:
    """The acceptance benchmark: `100 / wear.tool_per_session` sessions (07-implementation-map)."""
    _, _, body = await _master(session)
    scale = constants[R.QUALITY_SCALE]
    ordinary = scale.mid
    pickaxe = await _thing(session, body, PICK, quality=ordinary)
    per_session = constants[R.WEAR_TOOL_PER_SESSION] / wear.life_factor(constants, ordinary)
    need = int(scale.max / per_session) + 1

    ran_out = False
    for _ in range(need):
        ran_out = await wear.spend(
            session, constants, pickaxe, constants[R.WEAR_TOOL_PER_SESSION], cause="сессия"
        )
        if ran_out:
            break
    await session.commit()

    assert ran_out, "вещь обязана кончиться, а не работать вечно"
    assert await session.get(Item, pickaxe.id) is None


async def test_worn_works_worse(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A broken anvil gives a worse result, not just breaks suddenly."""
    _, _, body = await _master(session)
    intact = await _thing(session, body, BENCH, quality=80, state=100)
    worn_out = await _thing(session, body, BENCH, quality=80, state=25)

    assert wear.effective(constants, intact) == pytest.approx(80)
    assert wear.effective(constants, worn_out) == pytest.approx(20)
    assert wear.effective(constants, None) == constants[R.QUALITY_SCALE].max


async def test_mining_wears_tool(session: AsyncSession, constants: Constants) -> None:
    """The tool wears per session, not per swing (D-129)."""
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.pit.{stamp}", "Забой", area_m2=100)
    vein = await world.create_vein(session, node, "Железная руда", richness=60, remaining=10_000)
    identity = await world.create_identity(session, f"Шахтёр-{stamp}")
    body = await world.print_body(session, identity, node)
    pickaxe = await _thing(session, body, PICK, quality=50)

    sess = await mining.start(session, constants, body, vein, tool_item_id=pickaxe.id)
    await mining.swing(session, constants, sess)
    await mining.leave(session, constants, sess)
    await session.commit()

    expected_ = constants[R.WEAR_TOOL_PER_SESSION] / wear.life_factor(constants, 50)
    assert float(pickaxe.condition) == pytest.approx(100 - expected_, abs=0.01)


async def test_gear_wears_from_wearing(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Sink S2: gear is eaten by wearing, not by use (D-129)."""
    _, _, body = await _master(session)
    basket = await _thing(session, body, BASKET, quality=50)
    pickaxe = await _thing(session, body, PICK, quality=50)

    ended = await wear.daily_gear_wear(session, constants, catalog)
    await session.commit()

    assert ended == 0
    assert float(basket.condition) < 100, "корзина — снаряжение и изнашивается"
    assert float(pickaxe.condition) == 100, "инструмент изнашивается от работы, не от суток"


async def test_environment_speeds_wear(constants: Constants) -> None:
    """Pyroxis is expensive by itself, without a single special mechanic (D-129)."""
    multipliers = constants[R.WEAR_ENVIRONMENT_K]
    assert multipliers[wear.PLANET_NAMES["pyroxis"]] > multipliers[wear.PLANET_NAMES["terra"]]


# --- repair and recycling ----------------------------------------------------


async def test_repair_restores_condition_and_lowers_ceiling(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Each next repair is cheaper than a new thing and worse than the previous."""
    async with factory() as session, session.begin():
        _, _, body = await _master(session, machine="Кузница")
        pickaxe = await _thing(session, body, PICK, quality=60, state=30)
        await _thing(session, body, INGOT, quality=60)
        await _thing(session, body, HANDLE, quality=60)
        work = await craft.repair(session, constants, catalog, body, pickaxe)
        term, item_id = work.ready_at, pickaxe.id
        assert work.kind is BatchKind.REPAIR

    await jobs.run_one(factory, now=term)

    async with factory() as session:
        pickaxe = await session.get(Item, item_id)
        assert pickaxe is not None
        ceiling = 100 + constants[R.QUALITY_REPAIR_CEILING_LOSS]
        assert float(pickaxe.condition_cap) == pytest.approx(ceiling)
        assert float(pickaxe.condition) == pytest.approx(ceiling)
        assert float(pickaxe.quality) == 60, "качество не меняется никогда (D-058)"


async def test_repair_costs_materials(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A share of a new thing -- `craft.repair_cost_share` (D-129)."""
    _, _, body = await _master(session, machine="Кузница")
    pickaxe = await _thing(session, body, PICK, quality=60, state=30)
    await _thing(session, body, INGOT, quality=60)
    await _thing(session, body, HANDLE, quality=60)

    work = await craft.repair(session, constants, catalog, body, pickaxe)
    await session.commit()

    share = constants[R.CRAFT_REPAIR_COST_SHARE] / 100
    recipe = catalog.recipes.recipe(PICK)
    assert work.spent[INGOT] == pytest.approx(recipe.amounts[INGOT] * share)

    pocket = await world.body_container(session, body)
    left = await session.scalar(
        select(func.coalesce(func.sum(Item.amount), 0)).where(
            Item.container_id == pocket.id, Item.type_key == INGOT
        )
    )
    assert amount_float(int(left)) == pytest.approx(1 - recipe.amounts[INGOT] * share)


async def test_cannot_repair_without_materials(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, _, body = await _master(session, machine="Кузница")
    pickaxe = await _thing(session, body, PICK, quality=60, state=30)
    with pytest.raises(craft.NotEnough):
        await craft.repair(session, constants, catalog, body, pickaxe)


async def test_recycling_returns_less_than_invested(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """The difference is a sink, and it also makes recycling not free (20-systems/03).

    A hammer, not a pickaxe: steel goes into it three pieces at a time, so the
    share that comes back is more than one whole piece -- and a counted thing
    comes back in whole pieces only (D-212).
    """
    async with factory() as session, session.begin():
        _, _, body = await _master(session, machine="Кузница")
        hammer = await _thing(session, body, HAMMER, quality=80)
        work = await craft.recycle(session, constants, catalog, body, hammer)
        term, item_id, body_id = work.ready_at, hammer.id, body.id
        assert work.kind is BatchKind.RECYCLE

    await jobs.run_one(factory, now=term)

    async with factory() as session:
        assert await session.get(Item, item_id) is None, "вещи больше нет"

        reloaded = await session.get(Body, body_id)
        pocket = await world.body_container(session, reloaded)
        steel = (
            (
                await session.execute(
                    select(Item).where(Item.container_id == pocket.id, Item.type_key == STEEL)
                )
            )
            .scalars()
            .all()
        )
        assert steel, "часть материалов вернулась"

        share = constants[R.CRAFT_RECYCLE_RETURN] / 100
        recipe = catalog.recipes.recipe(HAMMER)
        norm = recipe.amounts[STEEL]
        returned = sum(amount_float(s.amount) for s in steel)
        #: The share, cut down to whole pieces: a fifth of an ingot is not an ingot.
        assert returned == int(norm * share)
        assert returned < norm

        transfer = constants[R.QUALITY_RECYCLE_CARRYOVER] / 100
        assert float(steel[0].quality) == pytest.approx(80 * transfer)


async def test_recycling_a_thing_of_single_pieces_returns_nothing(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """The sink at its sharpest (D-212): a share of one piece is no piece at all.

    An iron pickaxe is one ingot and one handle; `craft.recycle_return` of one
    piece does not make a piece, and what cannot come back whole does not come
    back. The thing is still gone -- taking apart is not free either way.
    """
    async with factory() as session, session.begin():
        _, _, body = await _master(session, machine="Кузница")
        pickaxe = await _thing(session, body, PICK, quality=80)
        work = await craft.recycle(session, constants, catalog, body, pickaxe)
        term, item_id, body_id = work.ready_at, pickaxe.id, body.id

    await jobs.run_one(factory, now=term)

    async with factory() as session:
        assert await session.get(Item, item_id) is None, "вещи больше нет"
        reloaded = await session.get(Body, body_id)
        pocket = await world.body_container(session, reloaded)
        back = (
            (await session.execute(select(Item).where(Item.container_id == pocket.id)))
            .scalars()
            .all()
        )
        assert back == [], "доля меньше штуки не возвращается"


async def test_foreign_not_repaired(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The thing must be in the hands: one repairs one's own."""
    _, _, body = await _master(session, machine="Кузница")
    node2, _, foreign = await _master(session, machine="Кузница")
    foreign_ = await _thing(session, foreign, PICK, quality=60, state=30)

    with pytest.raises(craft.CraftError):
        await craft.repair(session, constants, catalog, body, foreign_)


async def test_daily_tick_wears_gear(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """The world lives without players: gear decays while the owner is offline."""
    from src.engine import tick

    async with factory() as session, session.begin():
        _, _, body = await _master(session)
        basket = await _thing(session, body, BASKET, quality=50)
        item_id = basket.id
        await tick.ensure_scheduled(session)

    #: The two clock schedulings, then the steps they fan out (wave 4).
    await jobs.run_due(factory, limit=32)

    async with factory() as session:
        basket = await session.get(Item, item_id)
        assert basket is not None
        assert float(basket.condition) < 100


async def test_repeated_repair_hits_ceiling(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """So the thing stays finite however much it is repaired (pillar P2)."""
    async with factory() as session, session.begin():
        _, _, body = await _master(session, machine="Кузница")
        pickaxe = await _thing(session, body, PICK, quality=60, state=10)
        #: Materials for two repairs at once.
        for _ in range(2):
            await _thing(session, body, INGOT, quality=60)
            await _thing(session, body, HANDLE, quality=60)
        work = await craft.repair(session, constants, catalog, body, pickaxe)
        term, item_id, body_id = work.ready_at, pickaxe.id, body.id

    await jobs.run_one(factory, now=term)

    async with factory() as session, session.begin():
        reloaded = await session.get(Body, body_id)
        pickaxe = await session.get(Item, item_id)
        first_ceiling = float(pickaxe.condition_cap)
        work = await craft.repair(session, constants, catalog, reloaded, pickaxe)
        term = work.ready_at

    await jobs.run_one(factory, now=term)

    async with factory() as session:
        pickaxe = await session.get(Item, item_id)
        assert float(pickaxe.condition_cap) == pytest.approx(
            first_ceiling + constants[R.QUALITY_REPAIR_CEILING_LOSS]
        )
        assert float(pickaxe.condition_cap) < first_ceiling
