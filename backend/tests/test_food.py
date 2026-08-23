# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Cooking and food (D-119, D-121, D-128, D-105).

Checked is what the system is built this way for:

* pot quality is the D-128 formula verbatim, and an empty role hurts more
  than a bad product;
* the combination decides the kind, not the quality: two pots with different
  composition are different dishes for the diet;
* dry feeds whole, hot less but gives satiety; satiety requires quality and
  slows the spend rather than adding reserve;
* variety is counted by what was eaten;
* cooked spoils faster than raw; rotten is not food; the tick sweeps.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import craft, food, jobs, mining, world
from src.models.craft import CraftBatch
from src.models.inventory import Item
from src.units import amount_float

STEW = "Похлёбка"
POT = "Глиняный горшок"
HEARTH = "Очаг"


async def _kitchen(session: AsyncSession, *, hearth: float = 80, utensil: float = 80):
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.inn.{stamp}", "Трактир", area_m2=100)
    identity = await world.create_identity(session, f"Повар-{stamp}")
    body = await world.print_body(session, identity, node)
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, HEARTH, quality=hearth, origin="тест")
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, POT, quality=utensil, origin="тест")
    await world.learn(session, identity, STEW)
    return node, identity, body


async def _product(session: AsyncSession, body, name: str, quality: float, qty=5):
    pocket = await world.body_container(session, body)
    return await world.grant_item(session, pocket, name, amount=qty, quality=quality, origin="тест")


async def _cooked(session, constants, catalog, body, filling) -> Item:
    """A pot brought to readiness by the test's hands."""
    batch = await craft.cook(session, constants, catalog, body, STEW, filling)
    from src.models.job import Job, JobKind

    job = (
        await session.execute(
            select(Job)
            .where(Job.kind == JobKind.CRAFT_BATCH.value)
            .order_by(Job.created_at.desc())
            .limit(1)
        )
    ).scalar_one()
    job.run_at = datetime.now(UTC)
    await craft.finish(session, job)
    batch_reloaded = await session.get(CraftBatch, batch.id)
    assert batch_reloaded is not None
    pocket = await world.body_container(session, body)
    return (
        (
            await session.execute(
                select(Item).where(Item.container_id == pocket.id, Item.type_key == STEW)
            )
        )
        .scalars()
        .first()
    )


# --- pot ---------------------------------------------------------------------


async def test_pot_quality_by_formula_D128(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Ceiling x weighted base - penalty for empty roles, verbatim."""
    _, _, body = await _kitchen(session, hearth=80, utensil=80)
    await _product(session, body, "Бобы", 60)
    await _product(session, body, "Овощи", 40)

    batch = await craft.cook(
        session,
        constants,
        catalog,
        body,
        STEW,
        {"основа": "Бобы", "наполнитель": "Овощи"},
    )

    weights = constants[R.COOK_ROLE_WEIGHTS]
    basis = (60 * weights["основа"] + 40 * weights["наполнитель"]) / (
        weights["основа"] + weights["наполнитель"]
    )
    empty_count = len(weights) - 2
    fine = 1 - constants[R.COOK_EMPTY_ROLE_PENALTY] * empty_count / 100
    expected = 80 * (basis / 100) * fine
    assert float(batch.quality) == pytest.approx(expected, abs=0.01)
    assert float(batch.roles_filled) == pytest.approx(2 / len(weights))


async def test_empty_role_hurts_more_than_bad_product(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The rule a cook derives in an evening: cheap fat is better than none."""
    node, _, body = await _kitchen(session)
    #: Two pots at once -- so two hearths: one person works at a machine (D-150).
    #: Comparing two stews requires a second stove, as in life.
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, HEARTH, quality=80, origin="тест")
    await _product(session, body, "Бобы", 60, qty=10)
    await _product(session, body, "Масло", 5, qty=10)

    without_fat = await craft.cook(session, constants, catalog, body, STEW, {"основа": "Бобы"})
    with_cheap_fat = await craft.cook(
        session,
        constants,
        catalog,
        body,
        STEW,
        {"основа": "Бобы", "жир": "Масло"},
    )
    assert float(with_cheap_fat.quality) > float(without_fat.quality)


async def test_pot_cooks_in_portions_and_kind_decides_composition(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The combination decides the kind, not the quality (D-128): the composition shows in the
    name."""
    _, _, body = await _kitchen(session)
    await _product(session, body, "Бобы", 60)

    stack = await _cooked(session, constants, catalog, body, {"основа": "Бобы"})
    assert stack is not None
    assert amount_float(stack.amount) == constants[R.COOK_POT_PORTIONS]
    assert stack.flavor == f"{STEW} · Бобы"
    assert stack.spoils_at is not None


async def test_pickaxe_does_not_go_into_pot(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """What counts as a product is decided by data (`edible`), not by the thing having quality."""
    _, _, body = await _kitchen(session)
    await _product(session, body, "Каменная кирка", 50, qty=1)
    await _product(session, body, "Шахтная крепь", 50, qty=2)
    await _product(session, body, "Бобы", 60)

    for inedible in ("Каменная кирка", "Шахтная крепь"):
        with pytest.raises(craft.NotIngredient):
            await craft.cook(
                session,
                constants,
                catalog,
                body,
                STEW,
                {"основа": "Бобы", "наполнитель": inedible},
            )


async def test_utensil_required_and_sets_ceiling(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A utensil is a tool, not a container (D-119)."""
    stamp = uuid.uuid4().hex[:6]
    node = await world.create_node(session, f"terra.bare.{stamp}", "Голо", area_m2=50)
    identity = await world.create_identity(session, f"Босой-{stamp}")
    body = await world.print_body(session, identity, node)
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, HEARTH, quality=80, origin="тест")
    await world.learn(session, identity, STEW)
    await _product(session, body, "Бобы", 60)

    with pytest.raises(craft.NoTool):
        await craft.cook(session, constants, catalog, body, STEW, {"основа": "Бобы"})


# --- food --------------------------------------------------------------------


async def test_dry_food_feeds_by_quality(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Cheap food feeds worse, but feeds (D-121)."""
    _, _, body = await _kitchen(session)
    body.stamina = Decimal("10")
    span = constants[R.FOOD_RESTORE_BY_QUALITY]

    bread = await _product(session, body, "Хлеб", 0, qty=1)
    returned = await food.eat(session, constants, catalog, body, bread)
    assert returned == pytest.approx(constants[R.BODY_FOOD_RESTORE] * span.min)

    body.stamina = Decimal("10")
    excellent_ = await _product(session, body, "Хлеб", 100, qty=1)
    returned = await food.eat(session, constants, catalog, body, excellent_)
    assert returned == pytest.approx(constants[R.BODY_FOOD_RESTORE] * span.max)


async def test_inedible_not_eaten(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """What is edible is decided by data, not the engine's guess."""
    _, _, body = await _kitchen(session)
    support = await _product(session, body, "Шахтная крепь", 50, qty=1)
    with pytest.raises(food.NotFood):
        await food.eat(session, constants, catalog, body, support)


async def test_hot_gives_satiety_but_cheap_hot_does_not(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Satiety requires quality: below the threshold it is just food (D-121)."""
    _, _, body = await _kitchen(session)
    body.stamina = Decimal("10")

    #: A good stew -- satiety for hot_duration x share of roles.
    good = await _product(session, body, STEW, constants[R.COOK_HOT_QUALITY_MIN] + 10, qty=1)
    good.flavor = f"{STEW} · тест"
    good.roles_filled = Decimal("0.5")
    moment = datetime.now(UTC)
    returned = await food.eat(session, constants, catalog, body, good, now=moment)

    span = constants[R.FOOD_RESTORE_BY_QUALITY]
    q = constants[R.COOK_HOT_QUALITY_MIN] + 10
    full_ = constants[R.BODY_FOOD_RESTORE] * (span.min + (span.max - span.min) * q / 100)
    assert returned == pytest.approx(full_ * constants[R.COOK_HOT_RESTORE_SHARE] / 100)
    assert body.satiated_until == moment + timedelta(hours=constants[R.COOK_HOT_DURATION] * 0.5)

    #: Cheap hot food gives no satiety -- just food.
    body.satiated_until = None
    cheap_ = await _product(session, body, STEW, constants[R.COOK_HOT_QUALITY_MIN] - 10, qty=1)
    await food.eat(session, constants, catalog, body, cheap_, now=moment)
    assert body.satiated_until is None


async def test_fed_works_steadier(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Hot adds no reserve -- it slows the spend (D-119)."""
    stamp = uuid.uuid4().hex[:6]
    node = await world.create_node(session, f"terra.pit.{stamp}", "Забой", area_m2=100)
    vein = await world.create_vein(session, node, "Железная руда", richness=60, remaining=10_000)
    identity = await world.create_identity(session, f"Сытый-{stamp}")
    body = await world.print_body(session, identity, node)
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, "Каменная кирка", quality=50, origin="сценарий теста")

    sess = await mining.start(session, constants, body, vein)
    before = float(body.stamina)
    await mining.swing(session, constants, sess)
    hungry_spend = before - float(body.stamina)

    body.satiated_until = datetime.now(UTC) + timedelta(hours=1)
    before = float(body.stamina)
    await mining.swing(session, constants, sess)
    fed_spend = before - float(body.stamina)

    assert fed_spend == pytest.approx(
        hungry_spend * (1 - constants[R.COOK_HOT_DRAIN_REDUCTION] / 100)
    )


async def test_variety_counted_by_eaten(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Three kinds of cheap food give the same bonus as three expensive (D-105)."""
    _, _, body = await _kitchen(session)
    body.stamina = Decimal("1")
    span = constants[R.FOOD_RESTORE_BY_QUALITY]
    steady = constants[R.BODY_FOOD_RESTORE] * (span.min + (span.max - span.min) / 2)

    #: One and the same kind: no bonus.
    for _ in range(int(constants[R.FOOD_VARIETY_MIN_KINDS])):
        bread = await _product(session, body, "Хлеб", 50, qty=1)
        body.stamina = Decimal("1")
        returned = await food.eat(session, constants, catalog, body, bread)
    assert returned == pytest.approx(steady)

    #: Different kinds: the bonus arrived.
    for name in ("Солонина", "Сушёные овощи"):
        portion = await _product(session, body, name, 50, qty=1)
        body.stamina = Decimal("1")
        returned = await food.eat(session, constants, catalog, body, portion)
    assert returned == pytest.approx(steady * (1 + constants[R.BODY_DIET_VARIETY_BONUS] / 100))


# --- spoilage ----------------------------------------------------------------


async def test_cooked_spoils_faster_than_raw(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    moment = datetime.now(UTC)
    raw = food.harvest_spoils_at(constants, 1.0, now=moment)
    cooked = food.cooked_spoils_at(constants, now=moment)
    assert cooked < raw
    ratio = (raw - moment) / (cooked - moment)
    assert ratio == pytest.approx(constants[R.COOK_SPOILAGE_MULTIPLIER])


async def test_rotten_is_not_food(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, _, body = await _kitchen(session)
    bread = await _product(session, body, "Хлеб", 50, qty=1)
    bread.spoils_at = datetime.now(UTC) - timedelta(hours=1)
    await session.flush()

    with pytest.raises(food.Spoiled):
        await food.eat(session, constants, catalog, body, bread)
    assert await session.get(Item, bread.id) is None, "тухлое исчезло на глазах"


async def test_daily_tick_sweeps_rotten(factory, constants: Constants, catalog: Catalog) -> None:
    """Spoilage is an honest matter sink: it works without witnesses too."""
    from src.engine import tick

    async with factory() as session, session.begin():
        _, _, body = await _kitchen(session)
        bread = await _product(session, body, "Хлеб", 50, qty=1)
        bread.spoils_at = datetime.now(UTC) - timedelta(hours=1)
        item_id = bread.id
        await tick.ensure_scheduled(session)

    #: The two clock schedulings, then the steps they fan out (wave 4).
    await jobs.run_due(factory, limit=32)

    async with factory() as session:
        assert await session.get(Item, item_id) is None


async def test_harvest_gets_term_by_crop(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Turnip spoils faster than flax -- the speed comes from the crop's data."""
    turnip = catalog.plants.by_id("turnip")
    flax = catalog.plants.by_id("flax")
    moment = datetime.now(UTC)
    turnip_term = food.harvest_spoils_at(constants, turnip.traits.spoilage_k, now=moment)
    flax_term = food.harvest_spoils_at(constants, flax.traits.spoilage_k, now=moment)
    assert turnip_term < flax_term
