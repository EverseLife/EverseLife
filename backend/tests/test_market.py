"""Стакан заявок (D-003, D-047, D-127).

Проверяется то, ради чего рынок написан именно так:

* движок товар не оценивает — цену называют люди (D-002);
* материя требует присутствия, распоряжение — нет: загрузка и покупка ногами,
  ордера откуда угодно;
* деньги переходят, а не появляются: сумма проводок сделки равна нулю (И2);
* налог с продажи платит продавец, покупатель платит ровно цену стакана (D-127);
* ордер живёт по сроку и снимается заданием, а не проверкой при чтении.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import jobs, ledger, market, world
from src.models.inventory import Item
from src.models.ledger import AccountKind, PostingReason
from src.models.market import Order, OrderSide, OrderState, Trade
from src.units import amount_float, money

ORE = "Железная руда"
TERMINAL = market.TERMINAL


async def _город(session: AsyncSession, *, city=None):
    """Узел с терминалом. Маркетплейс в городе один (D-100)."""
    метка = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.market.{метка}", "Торг", area_m2=100)
    node.owner_city_id = None if city is None else city.id
    двор = await world.node_container(session, node)
    await world.grant_item(session, двор, TERMINAL, quality=70, origin="сценарий теста")
    return node


async def _власть(session: AsyncSession, catalog: Catalog):
    """Город-институт: ставку налога назначает он, а не вольт (D-154)."""
    from src.engine import city as town
    from src.models.world import Layer

    метка = uuid.uuid4().hex[:8]
    представитель = await world.create_node(
        session, f"terra.city.{метка}", "Город", area_m2=1, layer=Layer.PLANET
    )
    return await town.found(session, catalog, представитель, "Город")


async def _торговец(session: AsyncSession, node, имя: str, *, денег: float = 0):
    identity = await world.create_identity(session, f"{имя}-{uuid.uuid4().hex[:6]}")
    body = await world.print_body(session, identity, node)
    if денег:
        счёт = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
        genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
        await ledger.transfer(
            session,
            PostingReason.GENESIS,
            debit=genesis.id,
            credit=счёт.id,
            amount=money(денег),
        )
    return identity, body


async def _с_товаром(session: AsyncSession, constants: Constants, node, имя: str,
                     *, сколько: float = 10, качество: float = 65):
    identity, body = await _торговец(session, node, имя)
    карман = await world.body_container(session, body)
    await world.grant_item(
        session, карман, ORE, amount=сколько, quality=качество, origin="сценарий теста"
    )
    await market.load(session, constants, body, ORE, сколько)
    return identity, body


async def _баланс(session: AsyncSession, identity_id: uuid.UUID) -> int:
    счёт = await ledger.account_for(session, AccountKind.IDENTITY, identity_id)
    return await ledger.balance(session, счёт.id)


# --- ступени ----------------------------------------------------------------


def test_товар_торгуется_ступенями(constants: Constants) -> None:
    """Непрерывная шкала сделала бы книгу нечитаемой и убила ликвидность (D-058)."""
    ступени = {market.tier_of(constants, q) for q in (0, 25, 50, 75, 100)}
    assert len(ступени) == len(constants[R.QUALITY_TIERS])
    assert market.tier_of(constants, 63) == market.tier_of(constants, 64), (
        "соседние числа обязаны попадать в одну позицию стакана"
    )
    #: Границы полос в данных целые, качество дробное: 39.5 не проваливается
    #: между «…39» и «40…», а падает в нижнюю полосу.
    assert market.tier_of(constants, 39.5) == market.tier_of(constants, 39)


def test_у_безкачественного_товара_одна_позиция(constants: Constants) -> None:
    """У энергии и денег качества нет вовсе — не ноль, а нет."""
    assert market.tier_of(constants, None) == constants[R.QUALITY_TIERS][0].name


# --- присутствие ------------------------------------------------------------


async def test_без_терминала_торговли_нет(
    session: AsyncSession, constants: Constants
) -> None:
    """Маркетплейс — постройка, а не право. Нет её — нет и рынка."""
    node = await world.create_node(session, f"terra.field.{uuid.uuid4().hex[:6]}", "Поле",
                                   area_m2=100)
    _, body = await _торговец(session, node, "Селянин")
    with pytest.raises(market.NoTerminal):
        await market.load(session, constants, body, ORE, 1)


async def test_загруженное_лежит_в_терминале_а_не_в_кармане(
    session: AsyncSession, constants: Constants
) -> None:
    """Продавец один раз привозит товар, дальше управляет им удалённо (D-047)."""
    node = await _город(session)
    identity, body = await _с_товаром(session, constants, node, "Возчик", сколько=10)
    await session.commit()

    карман = await world.body_container(session, body)
    в_кармане = await session.scalar(
        select(func.coalesce(func.sum(Item.amount), 0)).where(
            Item.container_id == карман.id, Item.type_key == ORE
        )
    )
    ячейка = await market.stall(session, node, identity.id)
    в_терминале = await session.scalar(
        select(func.coalesce(func.sum(Item.amount), 0)).where(
            Item.container_id == ячейка.id, Item.type_key == ORE
        )
    )
    assert в_кармане == 0
    assert amount_float(int(в_терминале)) == pytest.approx(10)


async def test_отданное_под_ордер_не_забрать(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Иначе один и тот же мешок продаётся дважды."""
    node = await _город(session)
    identity, body = await _с_товаром(session, constants, node, "Хитрец", сколько=10,
                                      качество=65)
    ступень = market.tier_of(constants, 65)
    await market.sell(session, constants, catalog, identity, node,
                      type_key=ORE, tier=ступень, price=money(5), quantity=8)

    забрал = await market.take(session, constants, body, ORE, 10)
    assert забрал == pytest.approx(2), "свободны только те две, что не под ордером"


# --- сведение заявок --------------------------------------------------------


async def test_сделка_идёт_по_цене_стоявшего_в_стакане(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Он назвал условие первым, пришедший его принял."""
    node = await _город(session)
    ступень = market.tier_of(constants, 65)
    продавец, _ = await _с_товаром(session, constants, node, "Продавец", сколько=10)
    покупатель, тело = await _торговец(session, node, "Покупатель", денег=100)

    await market.sell(session, constants, catalog, продавец, node,
                      type_key=ORE, tier=ступень, price=money(4), quantity=10)
    #: Покупатель готов дать больше — и платит меньше, потому что стоял не он.
    сделка = await market.buy(session, constants, catalog, тело,
                              type_key=ORE, tier=ступень, price=money(6), quantity=4)
    await session.commit()

    assert сделка.traded == pytest.approx(4)
    assert сделка.trades[0].price == money(4)


async def test_деньги_переходят_а_не_появляются(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Инвариант И2 держит вся конструкция денег."""
    node = await _город(session)
    ступень = market.tier_of(constants, 65)
    продавец, _ = await _с_товаром(session, constants, node, "Кузнец", сколько=10)
    покупатель, тело = await _торговец(session, node, "Купец", денег=100)
    масса_до = await ledger.money_supply(session)

    await market.sell(session, constants, catalog, продавец, node,
                      type_key=ORE, tier=ступень, price=money(5), quantity=10)
    await market.buy(session, constants, catalog, тело,
                     type_key=ORE, tier=ступень, price=money(5), quantity=10)
    await session.commit()

    assert await _баланс(session, продавец.id) == money(50)
    assert await _баланс(session, покупатель.id) == money(50)
    assert await ledger.money_supply(session) == масса_до, "денежная масса не выросла"


async def test_купленное_ждёт_в_терминале_и_забирается_ногами(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Материя перемещается только физически (D-047)."""
    node = await _город(session)
    ступень = market.tier_of(constants, 65)
    продавец, _ = await _с_товаром(session, constants, node, "Шахтёр", сколько=10)
    покупатель, тело = await _торговец(session, node, "Скупщик", денег=100)

    await market.sell(session, constants, catalog, продавец, node,
                      type_key=ORE, tier=ступень, price=money(5), quantity=10)
    await market.buy(session, constants, catalog, тело,
                     type_key=ORE, tier=ступень, price=money(5), quantity=6)

    ячейка = await market.stall(session, node, покупатель.id)
    лежит = await session.scalar(
        select(func.coalesce(func.sum(Item.amount), 0)).where(
            Item.container_id == ячейка.id, Item.type_key == ORE
        )
    )
    assert amount_float(int(лежит)) == pytest.approx(6)

    забрал = await market.take(session, constants, тело, ORE, 6)
    await session.commit()
    assert забрал == pytest.approx(6)


async def test_излишек_заморозки_возвращается_сразу(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Заморожено ровно то, что может понадобиться, и ни монетой больше."""
    node = await _город(session)
    ступень = market.tier_of(constants, 65)
    продавец, _ = await _с_товаром(session, constants, node, "Рудокоп", сколько=10)
    покупатель, тело = await _торговец(session, node, "Богач", денег=100)

    await market.sell(session, constants, catalog, продавец, node,
                      type_key=ORE, tier=ступень, price=money(4), quantity=10)
    await market.buy(session, constants, catalog, тело,
                     type_key=ORE, tier=ступень, price=money(6), quantity=10)
    await session.commit()

    #: Заплатил по четыре, хотя готов был по шесть: двадцать вернулись сразу.
    assert await _баланс(session, покупатель.id) == money(60)
    эскроу = await ledger.account_for(session, AccountKind.ESCROW, покупатель.id)
    assert await ledger.balance(session, эскроу.id) == 0


async def test_налог_платит_продавец(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Покупатель видит в стакане цену — и это и есть цена (D-127)."""
    город = await _власть(session, catalog)
    node = await _город(session, city=город)
    ступень = market.tier_of(constants, 65)
    продавец, _ = await _с_товаром(session, constants, node, "Обложенный", сколько=10)
    покупатель, тело = await _торговец(session, node, "Приезжий", денег=100)

    ставка = float(catalog.laws.code_law_defaults()["tax_trade"])
    комиссия = constants[R.MARKET_DEFAULT_FEE]
    assert ставка > 0

    await market.sell(session, constants, catalog, продавец, node,
                      type_key=ORE, tier=ступень, price=money(5), quantity=10)
    await market.buy(session, constants, catalog, тело,
                     type_key=ORE, tier=ступень, price=money(5), quantity=10)
    await session.commit()

    цена = money(50)
    удержано = int(цена * ставка / 100) + int(цена * комиссия / 100)
    assert await _баланс(session, покупатель.id) == money(100) - цена, (
        "покупатель платит ровно цену стакана"
    )
    assert await _баланс(session, продавец.id) == цена - удержано

    #: Казна одна на город и живёт на его узле-представителе: туда же идёт
    #: выручка с тарифа за энергию (D-154).
    from src.engine import city as town

    казна = await town.treasury(session, город)
    assert await ledger.balance(session, казна.id) == удержано


async def test_ничей_узел_ничего_не_удерживает(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Платить некому — значит и удерживать нечего: деньги не исчезают (И2)."""
    node = await _город(session)
    ступень = market.tier_of(constants, 65)
    продавец, _ = await _с_товаром(session, constants, node, "Вольный", сколько=5)
    _, тело = await _торговец(session, node, "Вольный покупатель", денег=100)

    await market.sell(session, constants, catalog, продавец, node,
                      type_key=ORE, tier=ступень, price=money(5), quantity=5)
    await market.buy(session, constants, catalog, тело,
                     type_key=ORE, tier=ступень, price=money(5), quantity=5)
    await session.commit()

    assert await _баланс(session, продавец.id) == money(25)


async def test_со_своей_заявкой_сделки_не_будет(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Иначе оборот города накручивается на пустом месте."""
    node = await _город(session)
    ступень = market.tier_of(constants, 65)
    сам, тело = await _с_товаром(session, constants, node, "Сам себе", сколько=5)
    счёт = await ledger.account_for(session, AccountKind.IDENTITY, сам.id)
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    await ledger.transfer(session, PostingReason.GENESIS, debit=genesis.id,
                          credit=счёт.id, amount=money(100))

    await market.sell(session, constants, catalog, сам, node,
                      type_key=ORE, tier=ступень, price=money(5), quantity=5)
    сделка = await market.buy(session, constants, catalog, тело,
                              type_key=ORE, tier=ступень, price=money(5), quantity=5)
    assert сделка.traded == 0


async def test_без_денег_не_купишь(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Это ситуация в игре, а не ошибка сервера."""
    node = await _город(session)
    ступень = market.tier_of(constants, 65)
    продавец, _ = await _с_товаром(session, constants, node, "Торговец", сколько=5)
    _, тело = await _торговец(session, node, "Нищий")

    await market.sell(session, constants, catalog, продавец, node,
                      type_key=ORE, tier=ступень, price=money(5), quantity=5)
    with pytest.raises(market.NoMoney):
        await market.buy(session, constants, catalog, тело,
                         type_key=ORE, tier=ступень, price=money(5), quantity=5)


async def test_остаток_заявки_висит_в_стакане(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Книга даёт асинхронность: продал, пока спал."""
    node = await _город(session)
    ступень = market.tier_of(constants, 65)
    продавец, _ = await _с_товаром(session, constants, node, "Оптовик", сколько=4)
    _, тело = await _торговец(session, node, "Ждущий", денег=100)

    await market.sell(session, constants, catalog, продавец, node,
                      type_key=ORE, tier=ступень, price=money(5), quantity=4)
    сделка = await market.buy(session, constants, catalog, тело,
                              type_key=ORE, tier=ступень, price=money(5), quantity=10)
    await session.commit()

    assert сделка.traded == pytest.approx(4)
    assert сделка.order.state is OrderState.ACTIVE
    assert amount_float(сделка.order.amount_left) == pytest.approx(6)

    стакан = await market.book(session, node, ORE, ступень, depth=10)
    assert стакан.bids and стакан.bids[0].amount == pytest.approx(6)
    assert not стакан.asks
    assert стакан.last == money(5)


async def test_снятый_ордер_возвращает_заморозку(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    node = await _город(session)
    ступень = market.tier_of(constants, 65)
    покупатель, тело = await _торговец(session, node, "Передумавший", денег=100)

    сделка = await market.buy(session, constants, catalog, тело,
                              type_key=ORE, tier=ступень, price=money(5), quantity=10)
    assert await _баланс(session, покупатель.id) == money(50)

    await market.cancel(session, сделка.order, by=покупатель.id)
    await session.commit()
    assert await _баланс(session, покупатель.id) == money(100)


async def test_чужой_ордер_не_снять(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    node = await _город(session)
    ступень = market.tier_of(constants, 65)
    _, тело = await _торговец(session, node, "Свой", денег=100)
    чужой, _ = await _торговец(session, node, "Чужой")

    сделка = await market.buy(session, constants, catalog, тело,
                              type_key=ORE, tier=ступень, price=money(5), quantity=1)
    with pytest.raises(market.NotYours):
        await market.cancel(session, сделка.order, by=чужой.id)


# --- срок -------------------------------------------------------------------


async def test_ордер_истекает_заданием(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Истечение — событие мира, а не следствие того, что кто-то заглянул."""
    async with factory() as session, session.begin():
        node = await _город(session)
        ступень = market.tier_of(constants, 65)
        покупатель, тело = await _торговец(session, node, "Терпеливый", денег=100)
        сделка = await market.buy(session, constants, catalog, тело,
                                  type_key=ORE, tier=ступень, price=money(5), quantity=10)
        срок, order_id, identity_id = сделка.order.expires_at, сделка.order.id, покупатель.id

    ожидание = timedelta(
        hours=constants[R.MARKET_ORDER_LIFETIME] * constants[R.TIME_DAY_TERRA]
    )
    assert срок - ожидание < datetime.now(UTC) + timedelta(minutes=1)

    #: До срока ордер живёт.
    assert await jobs.run_one(factory, now=срок - timedelta(hours=1)) is None
    задание = await jobs.run_one(factory, now=срок)
    assert задание is not None and задание.kind == "market.order_expiry"

    async with factory() as session:
        order = await session.get(Order, order_id)
        assert order is not None and order.state is OrderState.EXPIRED
        счёт = await ledger.account_for(session, AccountKind.IDENTITY, identity_id)
        assert await ledger.balance(session, счёт.id) == money(100), (
            "заморозка вернулась целиком"
        )


async def test_сделка_остаётся_в_журнале(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Ордер можно снять, сделку — нет: по ней считается оборот города (D-100)."""
    node = await _город(session)
    ступень = market.tier_of(constants, 65)
    продавец, _ = await _с_товаром(session, constants, node, "Летописец", сколько=3)
    _, тело = await _торговец(session, node, "Свидетель", денег=100)

    await market.sell(session, constants, catalog, продавец, node,
                      type_key=ORE, tier=ступень, price=money(7), quantity=3)
    await market.buy(session, constants, catalog, тело,
                     type_key=ORE, tier=ступень, price=money(7), quantity=3)
    await session.commit()

    сделок = await session.scalar(
        select(func.count()).select_from(Trade).where(Trade.node_id == node.id)
    )
    assert сделок == 1
    ордера = (
        await session.execute(select(Order).where(Order.node_id == node.id))
    ).scalars().all()
    assert {o.side for o in ордера} == {OrderSide.BUY, OrderSide.SELL}
    assert all(o.state is OrderState.FILLED for o in ордера)
