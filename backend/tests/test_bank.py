"""Банк: резерв, кредит, ключевая ставка (D-030, D-087, D-167).

Проверяется то, ради чего банк устроен именно так:

* деньги идут **из резерва**, и печатается только недостающее;
* погашение возвращает ТК в резерв, а не в оборот, — резерв стерилизатор;
* инвариант «вся масса = счета + резерв» держится и после выдачи, и после
  погашения;
* ставка считается публичной формулой, не прыгает больше шага и не выходит за
  пол и потолок.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import bank, ledger, world
from src.models.bank import LoanState
from src.models.ledger import AccountKind, LedgerAccount, PostingReason
from src.units import PERCENT, money


async def _заёмщик(session: AsyncSession, *, денег: float = 0):
    identity = await world.create_identity(session, f"Заёмщик-{uuid.uuid4().hex[:6]}")
    if денег:
        счёт = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
        genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
        await ledger.transfer(
            session, PostingReason.GENESIS,
            debit=genesis.id, credit=счёт.id, amount=money(денег), memo={},
        )
    return identity


async def _масса(session: AsyncSession) -> tuple[int, int]:
    """Оборотная масса и резерв: их сумма и есть вся масса ТК (D-087)."""
    оборот = await bank.circulating(session)
    return оборот, await bank.reserve(session)


async def _счёт(session: AsyncSession, кто) -> int:
    счёт = await ledger.account_for(session, AccountKind.IDENTITY, кто.id)
    return await ledger.balance(session, счёт.id)


# --- резерв и эмиссия --------------------------------------------------------


async def test_пустой_резерв_печатает_ровно_столько_сколько_нужно(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    кто = await _заёмщик(session)
    заём = await bank.borrow(session, constants, catalog, кто, 100)

    assert заём.printed == money(100), "резерв был пуст — напечатано всё"
    assert await _счёт(session, кто) == money(100)
    assert await bank.reserve(session) == 0, "выданное ушло из резерва"


async def test_погашение_возвращает_деньги_в_резерв_а_не_в_оборот(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Резерв — стерилизатор: деньги выходят из оборота и ждут заёмщика."""
    кто = await _заёмщик(session)
    заём = await bank.borrow(session, constants, catalog, кто, 100)
    оборот_до, резерв_до = await _масса(session)

    вернул = await bank.repay(session, constants, кто, заём, 40)

    оборот_после, резерв_после = await _масса(session)
    assert вернул == money(40)
    assert резерв_после - резерв_до == money(40)
    assert оборот_до - оборот_после == money(40)
    assert оборот_до + резерв_до == оборот_после + резерв_после, (
        "вся масса ТК не изменилась: погашение не сжигает деньги"
    )


async def test_второй_кредит_берёт_из_резерва_и_не_печатает(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    первый = await _заёмщик(session)
    заём = await bank.borrow(session, constants, catalog, первый, 100)
    await bank.repay(session, constants, первый, заём, 100)

    второй = await _заёмщик(session)
    новый = await bank.borrow(session, constants, catalog, второй, 60)
    assert новый.printed == 0, "в резерве было — печатать незачем"


async def test_заём_закрывается_полным_погашением(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    кто = await _заёмщик(session, денег=50)
    заём = await bank.borrow(session, constants, catalog, кто, 100)
    await bank.repay(session, constants, кто, заём)
    assert заём.state is LoanState.REPAID
    assert заём.outstanding == 0
    assert not await bank.loans_of(session, кто.id)


# --- пределы -----------------------------------------------------------------


async def test_без_залога_дают_не_больше_предела(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    кто = await _заёмщик(session)
    предел = constants[R.BANK_UNSECURED_LIMIT]
    with pytest.raises(bank.TooMuch):
        await bank.borrow(session, constants, catalog, кто, предел + 1)
    заём = await bank.borrow(session, constants, catalog, кто, предел)
    assert заём.principal == money(предел)


async def test_формула_ставки_публична_и_детерминирована(
    constants: Constants,
) -> None:
    """Те же входные данные дают тот же ответ — иначе банк это скрытый NPC."""
    первый = bank.compute_rate(
        constants, previous=constants[R.BANK_BASE_RATE], inflation=5, emission_share=20
    )
    второй = bank.compute_rate(
        constants, previous=constants[R.BANK_BASE_RATE], inflation=5, emission_share=20
    )
    assert первый == второй
    assert "инфляция" in первый[1], "решение объясняется словами"


async def test_молчащий_датчик_рычага_не_шевелит(constants: Constants) -> None:
    ставка, почему = bank.compute_rate(
        constants,
        previous=constants[R.BANK_BASE_RATE],
        inflation=None,
        emission_share=None,
    )
    assert ставка == pytest.approx(constants[R.BANK_BASE_RATE])
    assert "не измерена" in почему


async def test_шаг_ставки_ограничен(constants: Constants) -> None:
    """Денежная политика не дёргается: прогноз — половина её смысла."""
    было = constants[R.BANK_BASE_RATE]
    ставка, _ = bank.compute_rate(
        constants, previous=было, inflation=100, emission_share=100
    )
    assert ставка <= было + constants[R.BANK_RATE_STEP_MAX] + 1e-9


async def test_ставка_не_выходит_за_пол_и_потолок(constants: Constants) -> None:
    низкая, _ = bank.compute_rate(
        constants,
        previous=constants[R.BANK_RATE_FLOOR],
        inflation=-100,
        emission_share=-100,
    )
    assert низкая >= constants[R.BANK_RATE_FLOOR]
    высокая, _ = bank.compute_rate(
        constants, previous=constants[R.BANK_RATE_CAP], inflation=100, emission_share=100
    )
    assert высокая <= constants[R.BANK_RATE_CAP]


async def test_пересмотр_сохраняет_решение_и_действует(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    было = await bank.key_rate(session, constants)
    assert было == pytest.approx(constants[R.BANK_BASE_RATE]), "до решений — базовая"

    решение = await bank.review_rate(session, constants)
    assert решение.why, "почему получилось столько, видно всем"
    assert await bank.key_rate(session, constants) == pytest.approx(float(решение.rate))


async def test_ставка_заёмщика_зафиксирована_при_выдаче(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Заём — договор, а не подписка на решения банка (D-167)."""
    кто = await _заёмщик(session)
    заём = await bank.borrow(session, constants, catalog, кто, 50)
    была = float(заём.rate)

    from src.models.bank import RateDecision

    session.add(
        RateDecision(
            rate=constants[R.BANK_RATE_CAP], why="проверка", decided_at=datetime.now(UTC)
        )
    )
    await session.flush()
    assert float(заём.rate) == была


# --- проценты ----------------------------------------------------------------


async def test_проценты_идут_временем(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    кто = await _заёмщик(session)
    await _сделка(session, "Железная руда", 4000, 1, продавец=кто)
    заём = await bank.borrow(session, constants, catalog, кто, 1000)
    было = заём.outstanding

    через_год = заём.taken_at + timedelta(days=constants[R.BANK_YEAR_DAYS])
    начислено = await bank.accrue(session, constants, заём, now=через_год)

    ожидалось = было * float(заём.rate) / PERCENT
    assert начислено == pytest.approx(ожидалось, rel=0.01)
    assert заём.outstanding == было + начислено


async def test_процента_по_вкладу_нет(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Деньги на счёте не растут: это доход без труда, то есть эмиссия (П1)."""
    кто = await _заёмщик(session, денег=100)
    было = await _счёт(session, кто)
    #: Никакого начисления на остаток в движке нет и быть не должно.
    счета = (
        await session.execute(
            select(LedgerAccount).where(LedgerAccount.kind == AccountKind.IDENTITY)
        )
    ).scalars().all()
    assert счета, "счёт есть"
    assert await _счёт(session, кто) == было


# --- несостоятельность (D-063, D-168) ---------------------------------------


async def test_просрочка_считается_от_последнего_платежа(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Необслуживаемый долг — это неоплачиваемый, а не старый."""
    кто = await _заёмщик(session)
    заём = await bank.borrow(session, constants, catalog, кто, 100)
    льгота = constants[R.DEBT_GRACE_PERIOD]

    в_срок = заём.taken_at + timedelta(days=льгота - 1)
    assert not bank.overdue(constants, заём, в_срок)
    поздно = заём.taken_at + timedelta(days=льгота + 1)
    assert bank.overdue(constants, заём, поздно)

    #: Платёж сдвигает отсчёт: заём снова обслуживается.
    await bank.repay(session, constants, кто, заём, 10, now=поздно)
    assert not bank.overdue(constants, заём, поздно)


async def test_удержание_забирает_долю_остатка(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    кто = await _заёмщик(session)
    заём = await bank.borrow(session, constants, catalog, кто, 100)
    было = await _счёт(session, кто)
    поздно = заём.taken_at + timedelta(days=constants[R.DEBT_GRACE_PERIOD] + 1)

    удержано = await bank.collect(session, constants, now=поздно)

    доля = constants[R.DEBT_WORKOFF_RATE] / PERCENT
    assert удержано == pytest.approx(было * доля, rel=0.02)
    assert await _счёт(session, кто) == было - удержано
    assert await bank.reserve(session) == удержано, "удержанное ушло в резерв"


async def test_обслуживаемый_долг_не_трогают(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    кто = await _заёмщик(session)
    await bank.borrow(session, constants, catalog, кто, 100)
    было = await _счёт(session, кто)
    assert await bank.collect(session, constants) == 0
    assert await _счёт(session, кто) == было


async def test_долг_держит_в_узле_и_отпускает_за_выкуп(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Ограничение накладывает система, а платить может кто угодно (D-063)."""
    из_узла = await world.create_node(
        session, f"terra.debt.{uuid.uuid4().hex[:6]}", "Узел", area_m2=100
    )
    куда = await world.create_node(
        session, f"terra.free.{uuid.uuid4().hex[:6]}", "Прочь", area_m2=100
    )
    from src.engine import travel

    await travel.connect(session, из_узла, куда, base_seconds=60)

    должник = await world.create_identity(session, f"Должник-{uuid.uuid4().hex[:6]}")
    тело = await world.print_body(session, должник, из_узла)
    заём = await bank.borrow(session, constants, catalog, должник, 100)
    #: Деньги потрачены, долг остался, и его не обслуживают.
    счёт = await ledger.account_for(session, AccountKind.IDENTITY, должник.id)
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    await ledger.transfer(
        session, PostingReason.TRADE, debit=счёт.id, credit=genesis.id,
        amount=await ledger.balance(session, счёт.id), memo={"прожито": "всё"},
    )
    поздно = заём.taken_at + timedelta(days=constants[R.DEBT_PRISON_THRESHOLD] + 1)

    держит = await bank.restrained(session, constants, должник.id, now=поздно)
    assert держит is not None
    with pytest.raises(travel.Imprisoned):
        await travel.depart(session, constants, тело, куда, now=поздно)

    #: Выкуп: за должника платит третий, и ограничение спадает само.
    доброхот = await _заёмщик(session, денег=500)
    await bank.repay(session, constants, доброхот, заём, now=поздно)
    assert await bank.restrained(session, constants, должник.id, now=поздно) is None
    assert await travel.depart(session, constants, тело, куда, now=поздно) is not None


async def test_платящий_должник_свободен(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Заём, который честно гасят, свободы не отнимает."""
    кто = await _заёмщик(session, денег=1000)
    заём = await bank.borrow(session, constants, catalog, кто, 100)
    поздно = заём.taken_at + timedelta(days=constants[R.DEBT_PRISON_THRESHOLD] + 1)
    #: Денег на счету больше, чем долга: ограничивать не за что.
    assert await bank.restrained(session, constants, кто.id, now=поздно) is None


# --- датчик цен и стерилизация (D-087, D-169) -------------------------------


async def _сделка(
    session: AsyncSession, товар: str, цена: float, сколько: float, продавец=None
):
    """Состоявшаяся сделка: из них и считается индекс цен."""
    from src.models.market import Trade
    from src.units import amount as _amount

    узел = await world.create_node(
        session, f"terra.mkt.{uuid.uuid4().hex[:8]}", "Рынок", area_m2=10
    )
    if продавец is None:
        продавец = await world.create_identity(session, f"П-{uuid.uuid4().hex[:6]}")
    from src.models.market import Order, OrderSide

    ордер = Order(
        node_id=узел.id,
        identity_id=продавец.id,
        side=OrderSide.SELL,
        type_key=товар,
        tier="обычное",
        price=money(цена),
        amount_total=_amount(сколько),
        amount_left=0,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    session.add(ордер)
    await session.flush()
    session.add(
        Trade(
            node_id=узел.id,
            sell_order_id=ордер.id,
            type_key=товар,
            tier="обычное",
            price=money(цена),
            amount=_amount(сколько),
        )
    )
    await session.flush()


async def test_индекс_считается_медианой_из_сделок(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Одна сделка по нелепой цене не двигает денежную политику."""
    assert await bank.price_index(session, constants) is None, "сделок нет — молчим"

    for цена in (10, 10, 1000):
        await _сделка(session, "Железная руда", цена, 1)
    индекс = await bank.price_index(session, constants)
    assert индекс == pytest.approx(money(10)), "медиана, а не среднее"


async def test_индекс_взвешен_оборотом(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Хлеб важнее редкого сплава ровно настолько, насколько его больше берут."""
    await _сделка(session, "Хлеб", 10, 100)
    await _сделка(session, "Сплав", 1000, 1)

    индекс = await bank.price_index(session, constants)
    #: Оборот хлеба 1000, сплава 1000 — веса равные, индекс посередине.
    assert индекс == pytest.approx(money(505), rel=0.01)


async def test_излишек_резерва_сжигается(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Второй рычаг: деньги, вернувшиеся в резерв и там не нужные, исчезают."""
    кто = await _заёмщик(session, денег=100)
    заём = await bank.borrow(session, constants, catalog, кто, 200)
    await bank.repay(session, constants, кто, заём, 200)

    в_обороте = await bank.circulating(session)
    потолок = int(в_обороте * constants[R.BANK_RESERVE_CAP] / PERCENT)
    было = await bank.reserve(session)
    assert было > потолок, "резерв заведомо выше потолка"

    сожжено = await bank.sterilize(session, constants)
    assert сожжено == было - потолок
    assert await bank.reserve(session) == потолок
    assert await bank.circulating(session) == в_обороте, "оборот не тронут"


async def test_резерв_в_пределах_потолка_не_жгут(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    кто = await _заёмщик(session, денег=10_000)
    заём = await bank.borrow(session, constants, catalog, кто, 100)
    await bank.repay(session, constants, кто, заём, 100)
    assert await bank.sterilize(session, constants) == 0


# --- норма залога как рычаг (D-170) -----------------------------------------


async def _город_с_оборотом(
    session: AsyncSession, catalog, оборот: float, товар: str = "Хлеб"
):
    """Город, на территории которого прошли сделки: по ним считается доля."""
    from src.engine import city as town
    from src.models.market import Order, OrderSide, Trade
    from src.models.world import Layer
    from src.units import amount as _amount

    метка = uuid.uuid4().hex[:8]
    планета = await world.create_node(
        session, f"terra.{метка}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    представитель = await world.create_node(
        session, f"terra.city.{метка}", f"Город-{метка}", area_m2=1,
        layer=Layer.PLANET, parent=планета,
    )
    рынок = await world.create_node(
        session, f"terra.city.{метка}.market", "Рынок", area_m2=50,
        parent=представитель,
    )
    город = await town.found(session, catalog, представитель, f"Город-{метка}")
    рынок.owner_city_id = город.id
    продавец = await world.create_identity(session, f"Купец-{метка}")
    ордер = Order(
        node_id=рынок.id, identity_id=продавец.id, side=OrderSide.SELL,
        type_key=товар, tier="обычное", price=money(оборот),
        amount_total=_amount(1), amount_left=0,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    session.add(ордер)
    await session.flush()
    session.add(
        Trade(
            node_id=рынок.id, sell_order_id=ордер.id, type_key=товар,
            tier="обычное", price=money(оборот), amount=_amount(1),
        )
    )
    await session.flush()
    return город


async def test_платёж_гасит_сначала_проценты(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Без этого «доход системы» неизмерим, а значит и не возвращается (D-171)."""
    кто = await _заёмщик(session, денег=1000)
    #: Лимит выше базы даёт труд: оборот продаж за окно (D-173).
    await _сделка(session, "Железная руда", 4000, 1, продавец=кто)
    заём = await bank.borrow(session, constants, catalog, кто, 1000)
    через_год = заём.taken_at + timedelta(days=constants[R.BANK_YEAR_DAYS])
    начислено = await bank.accrue(session, constants, заём, now=через_год)
    assert начислено > 0

    заплачено = await bank.repay(session, constants, кто, заём, 10, now=через_год)
    assert заём.interest_paid == заплачено, "платёж ушёл в проценты целиком"


async def _город_с_ратушей(session: AsyncSession, catalog: Catalog):
    """Город с администрацией: только такой считается при передаче ставки."""
    from src.engine import city as town
    from src.models.world import Layer

    метка = uuid.uuid4().hex[:8]
    планета = await world.create_node(
        session, f"terra.{метка}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    представитель = await world.create_node(
        session, f"terra.town.{метка}", f"Город-{метка}", area_m2=1,
        layer=Layer.PLANET, parent=планета,
    )
    ядро = await world.create_node(
        session, f"terra.town.{метка}.core", "Ядро", area_m2=100,
        parent=представитель,
    )
    город = await town.found(session, catalog, представитель, f"Город-{метка}")
    ядро.owner_city_id = город.id
    двор = await world.node_container(session, ядро)
    await world.grant_item(session, двор, town.HALL, quality=60, origin="тест")
    правитель = await world.create_identity(session, f"Глава-{метка}")
    await town.install_founder(session, город, правитель)
    await session.flush()
    return город, правитель


async def test_пока_городов_мало_решает_алгоритм(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    город, правитель = await _город_с_ратушей(session, catalog)
    assert not await bank.council_decides(session, constants)
    with pytest.raises(bank.NotCouncilTime):
        await bank.council_set_rate(session, constants, город, правитель, 6)


async def test_совет_получает_ставку_на_пороге(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Порог считается по городам с администрацией: вывеска не орган власти."""
    порог = int(constants[R.BANK_COUNCIL_HANDOVER_CITIES])
    города = [await _город_с_ратушей(session, catalog) for _ in range(порог)]
    assert await bank.cities_with_hall(session) == порог
    assert await bank.council_decides(session, constants)

    город, правитель = города[0]
    решение = await bank.council_set_rate(session, constants, город, правитель, 6)
    assert float(решение.rate) == pytest.approx(6)
    assert "Совета городов" in решение.why
    assert await bank.key_rate(session, constants) == pytest.approx(6)


async def test_коридор_ограничивает_совет(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Совет спорит с алгоритмом, а не заменяет его (D-172)."""
    порог = int(constants[R.BANK_COUNCIL_HANDOVER_CITIES])
    города = [await _город_с_ратушей(session, catalog) for _ in range(порог)]
    город, правитель = города[0]
    далеко = (
        constants[R.BANK_BASE_RATE] + constants[R.BANK_COUNCIL_RATE_DEVIATION] + 1
    )
    with pytest.raises(bank.OutOfCorridor):
        await bank.council_set_rate(session, constants, город, правитель, далеко)


async def test_голос_подаёт_имеющий_право_законов(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    порог = int(constants[R.BANK_COUNCIL_HANDOVER_CITIES])
    города = [await _город_с_ратушей(session, catalog) for _ in range(порог)]
    город, _ = города[0]
    from src.engine import city as town

    посторонний = await world.create_identity(session, f"Никто-{uuid.uuid4().hex[:6]}")
    with pytest.raises(town.NotAllowed):
        await bank.council_set_rate(session, constants, город, посторонний, 6)


async def test_авария_возвращает_ставку_алгоритму(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Политическое решение хорошо до момента, когда цена ошибки — деньги у всех."""
    from src.models.bank import RateDecision

    порог = int(constants[R.BANK_COUNCIL_HANDOVER_CITIES])
    города = [await _город_с_ратушей(session, catalog) for _ in range(порог)]
    сейчас = datetime.now(UTC)
    session.add(
        RateDecision(
            rate=constants[R.BANK_BASE_RATE],
            why="авария",
            decided_at=сейчас,
            locked_until=сейчас + timedelta(days=constants[R.BANK_COUNCIL_LOCKOUT]),
        )
    )
    await session.flush()

    assert not await bank.council_decides(session, constants, now=сейчас)
    город, правитель = города[0]
    with pytest.raises(bank.NotCouncilTime):
        await bank.council_set_rate(session, constants, город, правитель, 6, now=сейчас)

    #: Блокировка кончилась — ставка снова у Совета.
    позже = сейчас + timedelta(days=constants[R.BANK_COUNCIL_LOCKOUT] + 1)
    assert await bank.council_decides(session, constants, now=позже)


# --- кредит по труду (D-173) --------------------------------------------------


async def test_оборот_поднимает_лимит(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Лимит выдаёт труд: время в игре — самое дешёвое, что можно нафармить."""
    кто = await _заёмщик(session)
    базовый, _ = await bank.credit_limit(session, constants, кто.id)
    assert базовый == money(constants[R.BANK_UNSECURED_LIMIT])

    await _сделка(session, "Железная руда", 1000, 1, продавец=кто)
    поднятый, почему = await bank.credit_limit(session, constants, кто.id)
    прибавка = money(1000 * constants[R.CREDIT_TURNOVER_SHARE] / PERCENT)
    assert поднятый == базовый + прибавка
    assert "оборот" in почему, "формула объясняется словами, как ставка"


async def test_кредитная_история_актив(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Возвращённое раньше поднимает лимит — и даёт стаж без просрочек."""
    кто = await _заёмщик(session, денег=100)
    заём = await bank.borrow(session, constants, catalog, кто, 100)
    await bank.repay(session, constants, кто, заём)

    лимит, почему = await bank.credit_limit(session, constants, кто.id)
    база = money(constants[R.BANK_UNSECURED_LIMIT])
    ядро = база + money(100 * constants[R.CREDIT_REPAID_SHARE] / PERCENT)
    assert лимит == int(ядро * (1 + constants[R.CREDIT_NO_OVERDUE_BONUS] / PERCENT))
    assert "стаж" in почему


async def test_репорт_режет_доверие_но_не_хоронит(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Дефектная печать снижает кредит; переработку делает только саппорт."""
    кто = await _заёмщик(session)
    лимит_до, _ = await bank.credit_limit(session, constants, кто.id)

    #: Десяток недоброжелателей — и доверие упирается в пол, а не в ноль.
    for номер in range(12):
        недруг = await world.create_identity(
            session, f"Недруг-{номер}-{uuid.uuid4().hex[:4]}"
        )
        await bank.report_defect(session, недруг, кто)

    вера = await bank.trust(session, constants, кто.id)
    assert вера == pytest.approx(constants[R.CREDIT_TRUST_FLOOR] / PERCENT)
    лимит_после, почему = await bank.credit_limit(session, constants, кто.id)
    assert лимит_после == int(лимит_до * вера)
    assert "доверие" in почему


async def test_репорт_один_на_пару_и_отзывается(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    кто = await _заёмщик(session)
    недруг = await world.create_identity(session, f"Недруг-{uuid.uuid4().hex[:6]}")
    await bank.report_defect(session, недруг, кто)
    await bank.report_defect(session, недруг, кто)
    assert await bank.trust(session, constants, кто.id) == pytest.approx(
        1 - constants[R.CREDIT_REPORT_PENALTY] / PERCENT
    ), "второй репорт той же пары не считается"

    assert await bank.withdraw_report(session, недруг, кто)
    assert await bank.trust(session, constants, кто.id) == pytest.approx(1.0)


# --- заём через город (D-175) -------------------------------------------------


async def _гражданин_с_городом(
    session: AsyncSession, catalog: Catalog, *, оборот: float = 4000
):
    """Город с оборотом и его гражданин: линия открыта, маржа по умолчанию."""
    from src.models.city import Citizen

    город = await _город_с_оборотом(session, catalog, оборот)
    кто = await _заёмщик(session)
    session.add(Citizen(identity_id=кто.id, city_id=город.id))
    await session.flush()
    return город, кто


async def test_гражданин_занимает_у_города_с_маржой(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Ставка — ключевая плюс маржа города; заём ложится на линию города."""
    город, кто = await _гражданин_с_городом(session, catalog)
    заём = await bank.borrow(session, constants, catalog, кто, 100)

    маржа = bank.city_margin(constants, catalog, город)
    assert заём.city_id == город.id
    assert float(заём.margin) == pytest.approx(маржа)
    assert float(заём.rate) == pytest.approx(
        constants[R.BANK_BASE_RATE] + маржа
    )
    _, занято, _ = await bank.city_line(session, constants, город)
    assert занято == заём.outstanding, "заём висит на линии города"


async def test_маржа_города_уходит_в_его_казну(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Город зарабатывает на своих заёмщиках — сеньораж не нужен (D-175)."""
    from src.engine import city as town

    город, кто = await _гражданин_с_городом(session, catalog)
    заём = await bank.borrow(session, constants, catalog, кто, 100)
    через_год = заём.taken_at + timedelta(days=constants[R.BANK_YEAR_DAYS])
    await bank.accrue(session, constants, заём, now=через_год)

    казна_до = await town.treasury_balance(session, город)
    резерв_до = await bank.reserve(session)
    #: Гасим ровно проценты: их и делят между городом и столицей.
    проценты = заём.interest_accrued
    плательщик = await _заёмщик(session, денег=1000)
    await bank.repay(
        session, constants, плательщик, заём, проценты / 10_000, now=через_год
    )

    доля_города = int(проценты * float(заём.margin) / float(заём.rate))
    assert await town.treasury_balance(session, город) - казна_до == доля_города
    assert await bank.reserve(session) - резерв_до == проценты - доля_города


async def test_исчерпанная_линия_даёт_прямой_заём_дороже(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Выход есть всегда, но по верху вилки риска: линия города не резиновая."""
    город, кто = await _гражданин_с_городом(session, catalog, оборот=100)
    #: Линия = cap% от оборота 100: первый же крупный заём её переполняет.
    await _сделка(session, "Железная руда", 4000, 1, продавец=кто)
    заём = await bank.borrow(session, constants, catalog, кто, 900)

    assert заём.city_id is None, "линии не хватило — заём прямой"
    assert float(заём.rate) == pytest.approx(
        constants[R.BANK_BASE_RATE] + constants[R.BANK_RISK_PREMIUM].max
    )


async def test_не_гражданин_занимает_напрямую(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    кто = await _заёмщик(session)
    заём = await bank.borrow(session, constants, catalog, кто, 50)
    assert заём.city_id is None
    assert float(заём.rate) == pytest.approx(
        constants[R.BANK_BASE_RATE] + constants[R.BANK_RISK_PREMIUM].max
    )


# --- тюремный зачёт (D-174) ---------------------------------------------------


async def test_казна_платит_за_руду_в_погашение(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Круг замыкается: руда — городу, деньги казны — в резерв столицы."""
    from src.engine import city as town
    from src.engine import ledger as l
    from src.models.ledger import PostingReason as PR

    город, кто = await _гражданин_с_городом(session, catalog)
    заём = await bank.borrow(session, constants, catalog, кто, 100)
    #: Пополняем казну: тюрьма — вложение платёжеспособного города.
    казна = await town.treasury(session, город)
    genesis = await l.account_for(session, AccountKind.GENESIS, None)
    await l.transfer(
        session, PR.GENESIS, debit=genesis.id, credit=казна.id,
        amount=money(500), memo={},
    )

    было = заём.outstanding
    зачтено = await bank.prison_credit(session, constants, город, кто.id, money(60))
    assert зачтено == money(60)
    assert заём.outstanding == было - money(60)


async def test_пустая_казна_зачёта_не_даёт(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Нет денег — нет каторги: руда останется заключённому (D-174)."""
    город, кто = await _гражданин_с_городом(session, catalog)
    await bank.borrow(session, constants, catalog, кто, 100)
    assert await bank.prison_credit(
        session, constants, город, кто.id, money(60)
    ) == 0


async def test_отработка_в_тюремном_забое_гасит_долг(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Сквозной случай: жила в тюрьме, добыча городу, долг тает (D-174)."""
    from src.engine import city as town
    from src.engine import justice, mining
    from src.engine import ledger as l
    from src.models.city import Citizen
    from src.models.ledger import PostingReason as PR

    #: Оборот города — сделками по руде: из них берётся справочная цена.
    город = await _город_с_оборотом(session, catalog, 4000, товар="Железная руда")
    представитель = await session.get(
        __import__("src.models.world", fromlist=["Node"]).Node, город.node_id
    )
    тюрьма = await world.create_node(
        session, f"terra.jail.{uuid.uuid4().hex[:6]}", "Каторга", area_m2=100,
        parent=представитель, properties={justice.PRISON_NODE: True},
    )
    тюрьма.owner_city_id = город.id
    жила = await world.create_vein(
        session, тюрьма, "Железная руда", richness=60, remaining=10_000
    )
    должник = await world.create_identity(session, f"Должник-{uuid.uuid4().hex[:6]}")
    session.add(Citizen(identity_id=должник.id, city_id=город.id))
    тело = await world.print_body(session, должник, тюрьма)

    заём = await bank.borrow(session, constants, catalog, должник, 100)
    #: Деньги прожиты, долг просрочен — узел держит (D-168).
    счёт = await l.account_for(session, AccountKind.IDENTITY, должник.id)
    genesis = await l.account_for(session, AccountKind.GENESIS, None)
    await l.transfer(
        session, PR.TRADE, debit=счёт.id, credit=genesis.id,
        amount=await l.balance(session, счёт.id), memo={"прожито": "всё"},
    )
    заём.serviced_at = заём.taken_at - timedelta(
        days=constants[R.DEBT_PRISON_THRESHOLD] + 1
    )
    казна = await town.treasury(session, город)
    await l.transfer(
        session, PR.GENESIS, debit=genesis.id, credit=казна.id,
        amount=money(1000), memo={},
    )
    await session.flush()

    сессия = await mining.start(session, constants, тело, жила)
    await mining.swing(session, constants, сессия)
    было = заём.outstanding
    добыто = await mining.leave(session, constants, сессия)

    assert добыто > 0
    assert заём.outstanding < было, "добыча зачлась в долг"
    из_двора = await world.node_container(session, тюрьма)
    руда = (
        await session.execute(
            select(
                __import__("src.models.inventory", fromlist=["Item"]).Item
            ).where(
                __import__("src.models.inventory", fromlist=["Item"]).Item.container_id
                == из_двора.id
            )
        )
    ).scalars().all()
    assert руда, "добытое досталось городу, а не заключённому"

