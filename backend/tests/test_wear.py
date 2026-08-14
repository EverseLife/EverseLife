"""Износ, ремонт и переработка (D-129, D-058, 15-quality).

Проверяется то, ради чего система написана:

* вещь конечна (столп П2): инструмент кончается за столько сессий, сколько
  обещано приёмкой, и исчезает, а не работает вечно с нулём;
* качество определяет **скорость** износа, состояние — **насколько вещь хороша
  сейчас**: разбитая наковальня делает хуже, а не только внезапно ломается;
* формула срока службы берётся из вольта и вычисляется, а не переписана кодом;
* ремонт возвращает состояние, но опускает потолок — иначе вещь стала бы вечной;
* переработка возвращает меньше вложенного, и разница — сток.
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
BENCH = "Верстак"
INGOT = "Слиток железа"
HANDLE = "Рукоять"
BASKET = "Корзина"


async def _мастер(session: AsyncSession, *, станок: str | None = BENCH):
    метка = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.wear.{метка}", "Двор", area_m2=100)
    identity = await world.create_identity(session, f"Хозяин-{метка}")
    body = await world.print_body(session, identity, node)
    if станок is not None:
        двор = await world.node_container(session, node)
        await world.grant_item(session, двор, станок, quality=70, origin="сценарий теста")
    return node, identity, body


async def _вещь(session: AsyncSession, body, type_key: str, *, качество: float,
                состояние: float = 100):
    карман = await world.body_container(session, body)
    item = await world.grant_item(
        session, карман, type_key, quality=качество, origin="сценарий теста"
    )
    item.condition = состояние
    await session.flush()
    return item


# --- формула из вольта ------------------------------------------------------


def test_формула_вычисляется_а_не_переписана(constants: Constants) -> None:
    """Числа формулы остаются в вольте — иначе правка требует выката (D-065)."""
    формула = constants[R.QUALITY_DURABILITY_FACTOR]
    посчитано = формула.value(base_life=1, quality=80)
    assert посчитано == pytest.approx(evaluate(формула.text, base_life=1, quality=80))
    assert посчитано > формула.value(base_life=1, quality=40)


def test_алгоритм_честно_отвергается() -> None:
    """Формула со суммированием по этажам — это код, и движок пишет его сам."""
    with pytest.raises(NotComputable):
        evaluate("sum(x^n for n in 1..floors)", x=1, floors=2)
    with pytest.raises(NotComputable):
        evaluate("__import__('os').system('ls')")


def test_хорошая_вещь_служит_дольше(constants: Constants) -> None:
    плохая = wear.life_factor(constants, 20)
    хорошая = wear.life_factor(constants, 90)
    assert хорошая > плохая > 0


# --- состояние --------------------------------------------------------------


async def test_износ_обратен_качеству(
    session: AsyncSession, constants: Constants
) -> None:
    """Хорошая кирка изнашивается медленнее ровно во столько раз, во сколько лучше."""
    _, _, body = await _мастер(session)
    плохая = await _вещь(session, body, PICK, качество=20)
    хорошая = await _вещь(session, body, PICK, качество=90)

    await wear.spend(session, constants, плохая, constants[R.WEAR_TOOL_PER_SESSION],
                     cause="проверка")
    await wear.spend(session, constants, хорошая, constants[R.WEAR_TOOL_PER_SESSION],
                     cause="проверка")
    await session.commit()

    assert float(хорошая.condition) > float(плохая.condition)


async def test_инструмент_кончается_за_обещанное_число_сессий(
    session: AsyncSession, constants: Constants
) -> None:
    """Ориентир приёмки: `100 / wear.tool_per_session` сессий (07-implementation-map)."""
    _, _, body = await _мастер(session)
    шкала = constants[R.QUALITY_SCALE]
    обычная = шкала.mid
    кирка = await _вещь(session, body, PICK, качество=обычная)
    за_сессию = constants[R.WEAR_TOOL_PER_SESSION] / wear.life_factor(constants, обычная)
    надо = int(шкала.max / за_сессию) + 1

    кончилась = False
    for _ in range(надо):
        кончилась = await wear.spend(
            session, constants, кирка, constants[R.WEAR_TOOL_PER_SESSION], cause="сессия"
        )
        if кончилась:
            break
    await session.commit()

    assert кончилась, "вещь обязана кончиться, а не работать вечно"
    assert await session.get(Item, кирка.id) is None


async def test_изношенное_работает_хуже(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Разбитая наковальня даёт худший результат, а не только внезапно ломается."""
    _, _, body = await _мастер(session)
    целая = await _вещь(session, body, BENCH, качество=80, состояние=100)
    убитая = await _вещь(session, body, BENCH, качество=80, состояние=25)

    assert wear.effective(constants, целая) == pytest.approx(80)
    assert wear.effective(constants, убитая) == pytest.approx(20)
    assert wear.effective(constants, None) == constants[R.QUALITY_SCALE].max


async def test_добыча_изнашивает_инструмент(
    session: AsyncSession, constants: Constants
) -> None:
    """Инструмент изнашивается за сессию, а не за удар (D-129)."""
    метка = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.pit.{метка}", "Забой", area_m2=100)
    vein = await world.create_vein(session, node, "Железная руда", richness=60, remaining=10_000)
    identity = await world.create_identity(session, f"Шахтёр-{метка}")
    body = await world.print_body(session, identity, node)
    кирка = await _вещь(session, body, PICK, качество=50)

    сессия = await mining.start(session, constants, body, vein, tool_item_id=кирка.id)
    await mining.swing(session, constants, сессия)
    await mining.leave(session, constants, сессия)
    await session.commit()

    ожидалось = constants[R.WEAR_TOOL_PER_SESSION] / wear.life_factor(constants, 50)
    assert float(кирка.condition) == pytest.approx(100 - ожидалось, abs=0.01)


async def test_снаряжение_изнашивается_от_ношения(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Сток С2: снаряжение съедается ношением, а не применением (D-129)."""
    _, _, body = await _мастер(session)
    корзина = await _вещь(session, body, BASKET, качество=50)
    кирка = await _вещь(session, body, PICK, качество=50)

    кончилось = await wear.daily_gear_wear(session, constants, catalog)
    await session.commit()

    assert кончилось == 0
    assert float(корзина.condition) < 100, "корзина — снаряжение и изнашивается"
    assert float(кирка.condition) == 100, "инструмент изнашивается от работы, не от суток"


async def test_среда_ускоряет_износ(constants: Constants) -> None:
    """Пироксис дорог сам по себе, без единой специальной механики (D-129)."""
    множители = constants[R.WEAR_ENVIRONMENT_K]
    assert множители[wear.PLANET_NAMES["pyroxis"]] > множители[wear.PLANET_NAMES["terra"]]


# --- ремонт и переработка ---------------------------------------------------


async def test_ремонт_возвращает_состояние_и_опускает_потолок(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Каждая следующая починка дешевле новой вещи и хуже предыдущей."""
    async with factory() as session, session.begin():
        _, _, body = await _мастер(session, станок="Кузница")
        кирка = await _вещь(session, body, PICK, качество=60, состояние=30)
        await _вещь(session, body, INGOT, качество=60)
        await _вещь(session, body, HANDLE, качество=60)
        работа = await craft.repair(session, constants, catalog, body, кирка)
        срок, item_id = работа.ready_at, кирка.id
        assert работа.kind is BatchKind.REPAIR

    await jobs.run_one(factory, now=срок)

    async with factory() as session:
        кирка = await session.get(Item, item_id)
        assert кирка is not None
        потолок = 100 + constants[R.QUALITY_REPAIR_CEILING_LOSS]
        assert float(кирка.condition_cap) == pytest.approx(потолок)
        assert float(кирка.condition) == pytest.approx(потолок)
        assert float(кирка.quality) == 60, "качество не меняется никогда (D-058)"


async def test_ремонт_стоит_материалов(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Доля от новой вещи — `craft.repair_cost_share` (D-129)."""
    _, _, body = await _мастер(session, станок="Кузница")
    кирка = await _вещь(session, body, PICK, качество=60, состояние=30)
    await _вещь(session, body, INGOT, качество=60)
    await _вещь(session, body, HANDLE, качество=60)

    работа = await craft.repair(session, constants, catalog, body, кирка)
    await session.commit()

    доля = constants[R.CRAFT_REPAIR_COST_SHARE] / 100
    рецепт = catalog.recipes.recipe(PICK)
    assert работа.spent[INGOT] == pytest.approx(рецепт.amounts[INGOT] * доля)

    карман = await world.body_container(session, body)
    осталось = await session.scalar(
        select(func.coalesce(func.sum(Item.amount), 0)).where(
            Item.container_id == карман.id, Item.type_key == INGOT
        )
    )
    assert amount_float(int(осталось)) == pytest.approx(1 - рецепт.amounts[INGOT] * доля)


async def test_без_материалов_не_починишь(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, _, body = await _мастер(session, станок="Кузница")
    кирка = await _вещь(session, body, PICK, качество=60, состояние=30)
    with pytest.raises(craft.NotEnough):
        await craft.repair(session, constants, catalog, body, кирка)


async def test_переработка_возвращает_меньше_вложенного(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Разница — сток, и она же делает переработку не бесплатной (20-systems/03)."""
    async with factory() as session, session.begin():
        _, _, body = await _мастер(session, станок="Кузница")
        кирка = await _вещь(session, body, PICK, качество=80)
        работа = await craft.recycle(session, constants, catalog, body, кирка)
        срок, item_id, body_id = работа.ready_at, кирка.id, body.id
        assert работа.kind is BatchKind.RECYCLE

    await jobs.run_one(factory, now=срок)

    async with factory() as session:
        assert await session.get(Item, item_id) is None, "вещи больше нет"

        тело = await session.get(Body, body_id)
        карман = await world.body_container(session, тело)
        слитки = (
            await session.execute(
                select(Item).where(
                    Item.container_id == карман.id, Item.type_key == INGOT
                )
            )
        ).scalars().all()
        assert слитки, "часть материалов вернулась"

        доля = constants[R.CRAFT_RECYCLE_RETURN] / 100
        рецепт = catalog.recipes.recipe(PICK)
        вернулось = sum(amount_float(s.amount) for s in слитки)
        assert вернулось == pytest.approx(рецепт.amounts[INGOT] * доля)
        assert вернулось < рецепт.amounts[INGOT]

        перенос = constants[R.QUALITY_RECYCLE_CARRYOVER] / 100
        assert float(слитки[0].quality) == pytest.approx(80 * перенос)


async def test_чужое_не_чинят(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Вещь должна быть в руках: чинят своё."""
    _, _, body = await _мастер(session, станок="Кузница")
    node2, _, чужой = await _мастер(session, станок="Кузница")
    чужая = await _вещь(session, чужой, PICK, качество=60, состояние=30)

    with pytest.raises(craft.CraftError):
        await craft.repair(session, constants, catalog, body, чужая)


async def test_суточный_тик_изнашивает_снаряжение(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Мир живёт без игроков: снаряжение ветшает, пока хозяин офлайн."""
    from src.engine import tick

    async with factory() as session, session.begin():
        _, _, body = await _мастер(session)
        корзина = await _вещь(session, body, BASKET, качество=50)
        item_id = корзина.id
        await tick.ensure_scheduled(session)

    #: Разбираем обе постановки часов: обычный тик и суточный.
    await jobs.run_due(factory, limit=2)

    async with factory() as session:
        корзина = await session.get(Item, item_id)
        assert корзина is not None
        assert float(корзина.condition) < 100


async def test_повторный_ремонт_упирается_в_потолок(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Так вещь остаётся конечной, сколько её ни чини (столп П2)."""
    async with factory() as session, session.begin():
        _, _, body = await _мастер(session, станок="Кузница")
        кирка = await _вещь(session, body, PICK, качество=60, состояние=10)
        #: Материалы на две починки сразу.
        for _ in range(2):
            await _вещь(session, body, INGOT, качество=60)
            await _вещь(session, body, HANDLE, качество=60)
        работа = await craft.repair(session, constants, catalog, body, кирка)
        срок, item_id, body_id = работа.ready_at, кирка.id, body.id

    await jobs.run_one(factory, now=срок)

    async with factory() as session, session.begin():
        тело = await session.get(Body, body_id)
        кирка = await session.get(Item, item_id)
        первый_потолок = float(кирка.condition_cap)
        работа = await craft.repair(session, constants, catalog, тело, кирка)
        срок = работа.ready_at

    await jobs.run_one(factory, now=срок)

    async with factory() as session:
        кирка = await session.get(Item, item_id)
        assert float(кирка.condition_cap) == pytest.approx(
            первый_потолок + constants[R.QUALITY_REPAIR_CEILING_LOSS]
        )
        assert float(кирка.condition_cap) < первый_потолок
