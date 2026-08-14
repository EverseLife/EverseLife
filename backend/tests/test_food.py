"""Готовка и еда (D-119, D-121, D-128, D-105).

Проверяется то, ради чего система устроена именно так:

* качество котла — дословно формула D-128, и пустая роль бьёт сильнее плохого
  продукта;
* сочетание решает вид, а не качество: два котла с разным составом — разные
  блюда для рациона;
* сухое кормит целиком, горячее — меньше, но даёт сытость; сытость требует
  качества и замедляет расход, а не добавляет запас;
* разнообразие считается по съеденному;
* готовое портится быстрее сырья; тухлое не еда; тик подметает.
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


async def _кухня(session: AsyncSession, *, очаг: float = 80, утварь: float = 80):
    метка = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.inn.{метка}", "Трактир", area_m2=100)
    identity = await world.create_identity(session, f"Повар-{метка}")
    body = await world.print_body(session, identity, node)
    двор = await world.node_container(session, node)
    await world.grant_item(session, двор, HEARTH, quality=очаг, origin="тест")
    карман = await world.body_container(session, body)
    await world.grant_item(session, карман, POT, quality=утварь, origin="тест")
    await world.learn(session, identity, STEW)
    return node, identity, body


async def _продукт(session: AsyncSession, body, имя: str, качество: float, сколько=5):
    карман = await world.body_container(session, body)
    return await world.grant_item(
        session, карман, имя, amount=сколько, quality=качество, origin="тест"
    )


async def _сваренное(session, constants, catalog, body, filling) -> Item:
    """Котёл, доведённый до готовности руками теста."""
    batch = await craft.cook(session, constants, catalog, body, STEW, filling)
    from src.models.job import Job, JobKind

    job = (
        await session.execute(
            select(Job).where(Job.kind == JobKind.CRAFT_BATCH.value)
            .order_by(Job.created_at.desc()).limit(1)
        )
    ).scalar_one()
    job.run_at = datetime.now(UTC)
    await craft.finish(session, job)
    batch_обновл = await session.get(CraftBatch, batch.id)
    assert batch_обновл is not None
    карман = await world.body_container(session, body)
    return (
        await session.execute(
            select(Item).where(Item.container_id == карман.id, Item.type_key == STEW)
        )
    ).scalars().first()


# --- котёл ------------------------------------------------------------------


async def test_качество_котла_по_формуле_D128(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Потолок × взвешенная основа − штраф за пустые роли, дословно."""
    _, _, body = await _кухня(session, очаг=80, утварь=80)
    await _продукт(session, body, "Бобы", 60)
    await _продукт(session, body, "Овощи", 40)

    batch = await craft.cook(
        session, constants, catalog, body, STEW,
        {"основа": "Бобы", "наполнитель": "Овощи"},
    )

    weights = constants[R.COOK_ROLE_WEIGHTS]
    основа = (60 * weights["основа"] + 40 * weights["наполнитель"]) / (
        weights["основа"] + weights["наполнитель"]
    )
    пустых = len(weights) - 2
    штраф = 1 - constants[R.COOK_EMPTY_ROLE_PENALTY] * пустых / 100
    ожидание = 80 * (основа / 100) * штраф
    assert float(batch.quality) == pytest.approx(ожидание, abs=0.01)
    assert float(batch.roles_filled) == pytest.approx(2 / len(weights))


async def test_пустая_роль_бьёт_сильнее_плохого_продукта(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Правило, которое повар выводит за вечер: дешёвый жир лучше никакого."""
    node, _, body = await _кухня(session)
    #: Два котла разом — значит и два очага: за станком работает один (D-150).
    #: Сравнение двух похлёбок требует второй печи, как и в жизни.
    двор = await world.node_container(session, node)
    await world.grant_item(session, двор, HEARTH, quality=80, origin="тест")
    await _продукт(session, body, "Бобы", 60, сколько=10)
    await _продукт(session, body, "Масло", 5, сколько=10)

    без_жира = await craft.cook(
        session, constants, catalog, body, STEW, {"основа": "Бобы"}
    )
    с_дешёвым_жиром = await craft.cook(
        session, constants, catalog, body, STEW,
        {"основа": "Бобы", "жир": "Масло"},
    )
    assert float(с_дешёвым_жиром.quality) > float(без_жира.quality)


async def test_котёл_варит_порциями_и_вид_решает_состав(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Сочетание решает вид, а не качество (D-128): состав виден в имени."""
    _, _, body = await _кухня(session)
    await _продукт(session, body, "Бобы", 60)

    стопка = await _сваренное(session, constants, catalog, body, {"основа": "Бобы"})
    assert стопка is not None
    assert amount_float(стопка.amount) == constants[R.COOK_POT_PORTIONS]
    assert стопка.flavor == f"{STEW} · Бобы"
    assert стопка.spoils_at is not None


async def test_кирка_в_котёл_не_идёт(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Что продукт — решают данные (`edible`), а не наличие качества у вещи."""
    _, _, body = await _кухня(session)
    await _продукт(session, body, "Каменная кирка", 50, сколько=1)
    await _продукт(session, body, "Шахтная крепь", 50, сколько=2)
    await _продукт(session, body, "Бобы", 60)

    for несъедобное in ("Каменная кирка", "Шахтная крепь"):
        with pytest.raises(craft.NotIngredient):
            await craft.cook(
                session, constants, catalog, body, STEW,
                {"основа": "Бобы", "наполнитель": несъедобное},
            )


async def test_утварь_обязательна_и_задаёт_потолок(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Утварь — инструмент, а не тара (D-119)."""
    метка = uuid.uuid4().hex[:6]
    node = await world.create_node(session, f"terra.bare.{метка}", "Голо", area_m2=50)
    identity = await world.create_identity(session, f"Босой-{метка}")
    body = await world.print_body(session, identity, node)
    двор = await world.node_container(session, node)
    await world.grant_item(session, двор, HEARTH, quality=80, origin="тест")
    await world.learn(session, identity, STEW)
    await _продукт(session, body, "Бобы", 60)

    with pytest.raises(craft.NoTool):
        await craft.cook(session, constants, catalog, body, STEW, {"основа": "Бобы"})


# --- еда --------------------------------------------------------------------


async def test_сухое_кормит_по_качеству(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Дешёвая еда кормит хуже, но кормит (D-121)."""
    _, _, body = await _кухня(session)
    body.stamina = Decimal("10")
    span = constants[R.FOOD_RESTORE_BY_QUALITY]

    хлеб = await _продукт(session, body, "Хлеб", 0, сколько=1)
    вернулось = await food.eat(session, constants, catalog, body, хлеб)
    assert вернулось == pytest.approx(constants[R.BODY_FOOD_RESTORE] * span.min)

    body.stamina = Decimal("10")
    отличный = await _продукт(session, body, "Хлеб", 100, сколько=1)
    вернулось = await food.eat(session, constants, catalog, body, отличный)
    assert вернулось == pytest.approx(constants[R.BODY_FOOD_RESTORE] * span.max)


async def test_несъедобное_не_едят(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Что съедобно — решают данные, а не догадка движка."""
    _, _, body = await _кухня(session)
    крепь = await _продукт(session, body, "Шахтная крепь", 50, сколько=1)
    with pytest.raises(food.NotFood):
        await food.eat(session, constants, catalog, body, крепь)


async def test_горячее_даёт_сытость_а_дешёвое_горячее_нет(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Сытость требует качества: ниже порога это просто еда (D-121)."""
    _, _, body = await _кухня(session)
    body.stamina = Decimal("10")

    #: Хорошая похлёбка — сытость на hot_duration × доля ролей.
    хорошая = await _продукт(session, body, STEW, constants[R.COOK_HOT_QUALITY_MIN] + 10,
                             сколько=1)
    хорошая.flavor = f"{STEW} · тест"
    хорошая.roles_filled = Decimal("0.5")
    момент = datetime.now(UTC)
    вернулось = await food.eat(session, constants, catalog, body, хорошая, now=момент)

    span = constants[R.FOOD_RESTORE_BY_QUALITY]
    q = constants[R.COOK_HOT_QUALITY_MIN] + 10
    полное = constants[R.BODY_FOOD_RESTORE] * (span.min + (span.max - span.min) * q / 100)
    assert вернулось == pytest.approx(полное * constants[R.COOK_HOT_RESTORE_SHARE] / 100)
    assert body.satiated_until == момент + timedelta(
        hours=constants[R.COOK_HOT_DURATION] * 0.5
    )

    #: Дешёвое горячее сытости не даёт — просто еда.
    body.satiated_until = None
    дешёвая = await _продукт(session, body, STEW, constants[R.COOK_HOT_QUALITY_MIN] - 10,
                             сколько=1)
    await food.eat(session, constants, catalog, body, дешёвая, now=момент)
    assert body.satiated_until is None


async def test_сытый_работает_ровнее(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Горячее не добавляет запаса — оно замедляет расход (D-119)."""
    метка = uuid.uuid4().hex[:6]
    node = await world.create_node(session, f"terra.pit.{метка}", "Забой", area_m2=100)
    жила = await world.create_vein(session, node, "Железная руда", richness=60, remaining=10_000)
    identity = await world.create_identity(session, f"Сытый-{метка}")
    body = await world.print_body(session, identity, node)

    сессия = await mining.start(session, constants, body, жила)
    было = float(body.stamina)
    await mining.swing(session, constants, сессия)
    голодный_расход = было - float(body.stamina)

    body.satiated_until = datetime.now(UTC) + timedelta(hours=1)
    было = float(body.stamina)
    await mining.swing(session, constants, сессия)
    сытый_расход = было - float(body.stamina)

    assert сытый_расход == pytest.approx(
        голодный_расход * (1 - constants[R.COOK_HOT_DRAIN_REDUCTION] / 100)
    )


async def test_разнообразие_считается_по_съеденному(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Три вида дешёвой еды дают ту же надбавку, что три дорогой (D-105)."""
    _, _, body = await _кухня(session)
    body.stamina = Decimal("1")
    span = constants[R.FOOD_RESTORE_BY_QUALITY]
    ровно = constants[R.BODY_FOOD_RESTORE] * (span.min + (span.max - span.min) / 2)

    #: Один и тот же вид: надбавки нет.
    for _ in range(int(constants[R.FOOD_VARIETY_MIN_KINDS])):
        хлеб = await _продукт(session, body, "Хлеб", 50, сколько=1)
        body.stamina = Decimal("1")
        вернулось = await food.eat(session, constants, catalog, body, хлеб)
    assert вернулось == pytest.approx(ровно)

    #: Разные виды: надбавка пришла.
    for имя in ("Солонина", "Сушёные овощи"):
        порция = await _продукт(session, body, имя, 50, сколько=1)
        body.stamina = Decimal("1")
        вернулось = await food.eat(session, constants, catalog, body, порция)
    assert вернулось == pytest.approx(
        ровно * (1 + constants[R.BODY_DIET_VARIETY_BONUS] / 100)
    )


# --- порча ------------------------------------------------------------------


async def test_готовое_портится_быстрее_сырья(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    момент = datetime.now(UTC)
    сырьё = food.harvest_spoils_at(constants, 1.0, now=момент)
    готовое = food.cooked_spoils_at(constants, now=момент)
    assert готовое < сырьё
    отношение = (сырьё - момент) / (готовое - момент)
    assert отношение == pytest.approx(constants[R.COOK_SPOILAGE_MULTIPLIER])


async def test_тухлое_не_еда(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, _, body = await _кухня(session)
    хлеб = await _продукт(session, body, "Хлеб", 50, сколько=1)
    хлеб.spoils_at = datetime.now(UTC) - timedelta(hours=1)
    await session.flush()

    with pytest.raises(food.Spoiled):
        await food.eat(session, constants, catalog, body, хлеб)
    assert await session.get(Item, хлеб.id) is None, "тухлое исчезло на глазах"


async def test_суточный_тик_подметает_протухшее(
    factory, constants: Constants, catalog: Catalog
) -> None:
    """Порча — честный сток материи: работает и без свидетелей."""
    from src.engine import tick

    async with factory() as session, session.begin():
        _, _, body = await _кухня(session)
        хлеб = await _продукт(session, body, "Хлеб", 50, сколько=1)
        хлеб.spoils_at = datetime.now(UTC) - timedelta(hours=1)
        item_id = хлеб.id
        await tick.ensure_scheduled(session)

    await jobs.run_due(factory, limit=2)

    async with factory() as session:
        assert await session.get(Item, item_id) is None


async def test_урожай_получает_срок_по_культуре(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Репа портится быстрее льна — скорость из данных культуры."""
    репа = catalog.plants.by_id("turnip")
    лён = catalog.plants.by_id("flax")
    момент = datetime.now(UTC)
    срок_репы = food.harvest_spoils_at(constants, репа.traits.spoilage_k, now=момент)
    срок_льна = food.harvest_spoils_at(constants, лён.traits.spoilage_k, now=момент)
    assert срок_репы < срок_льна
