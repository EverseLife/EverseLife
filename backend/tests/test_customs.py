"""Таможня: ставка, норма, запрет (D-123).

Проверяется то, ради чего пошлина введена именно такой:

* норма отделяет бытовой провоз от промысла и считается **за окно**, а не за
  одну ходку: иначе её обходят, разбив груз на десять заходов;
* нет сделок — нет справочной цены, и пошлину брать не с чего;
* запрет абсолютен: запрещённое не проходит ни за какие деньги;
* нечем платить — товар не проходит, но долга не возникает;
* шаг внутри своего города таможни не знает.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import city as town
from src.engine import customs, ledger, market, travel, world
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Layer, Surface
from src.units import money

ORE = "Железная руда"


async def _мир(session: AsyncSession, catalog: Catalog):
    """Город с рынком и ничейная пойма за воротами."""
    метка = uuid.uuid4().hex[:8]
    планета = await world.create_node(
        session, f"terra.{метка}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    представитель = await world.create_node(
        session, f"terra.city.{метка}", "Столица", area_m2=1,
        layer=Layer.PLANET, parent=планета,
    )
    рынок = await world.create_node(
        session, f"terra.city.{метка}.market", "Торг", area_m2=200,
        parent=представитель,
    )
    ворота = await world.create_node(
        session, f"terra.city.{метка}.gate", "Ворота", area_m2=80,
        parent=представитель,
    )
    поле = await world.create_node(
        session, f"terra.field.{метка}", "Пойма", area_m2=400,
        layer=Layer.PLANET, parent=планета,
    )
    город = await town.found(session, catalog, представитель, "Столица")
    for узел in (рынок, ворота):
        узел.owner_city_id = город.id
    await session.flush()

    await travel.connect(session, рынок, ворота, base_seconds=10, surface=Surface.PAVED)
    await travel.connect(session, ворота, поле, base_seconds=60, surface=Surface.ROAD)
    двор = await world.node_container(session, рынок)
    await world.grant_item(session, двор, market.TERMINAL, quality=70, origin="тест")
    return город, рынок, ворота, поле


async def _купец(session: AsyncSession, узел, имя: str, *, денег: float = 0, руды=0.0):
    identity, body = await world.spawn(session, f"{имя}-{uuid.uuid4().hex[:6]}", узел)
    if денег:
        счёт = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
        genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
        await ledger.transfer(
            session, PostingReason.GENESIS,
            debit=genesis.id, credit=счёт.id, amount=money(денег),
        )
    if руды:
        карман = await world.body_container(session, body)
        await world.grant_item(
            session, карман, ORE, amount=руды, quality=60, origin="тест"
        )
    return identity, body


async def _сделка(
    session: AsyncSession, constants: Constants, catalog: Catalog, узел, цена: float
) -> None:
    """Одна сделка в стакане: без неё у города нет справочной цены (D-123)."""
    продавец, тело_продавца = await _купец(session, узел, "Продавец", руды=10)
    покупатель, тело_покупателя = await _купец(session, узел, "Покупатель", денег=200)
    ступень = market.tier_of(constants, 60)
    await market.load(session, constants, тело_продавца, ORE, 10)
    await market.sell(
        session, constants, catalog, продавец, узел,
        type_key=ORE, tier=ступень, price=money(цена), quantity=10,
    )
    await market.buy(
        session, constants, catalog, тело_покупателя,
        type_key=ORE, tier=ступень, price=money(цена), quantity=10,
    )


async def _пошлина(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    город,
    ставка: float,
    норма: float,
    направление: str = customs.EXPORT,
) -> None:
    город.laws = {
        **(город.laws or {}),
        f"{направление}_duty": {ORE: {"rate": ставка, "free": норма}},
    }
    await session.flush()


# --- граница ----------------------------------------------------------------


async def test_шаг_внутри_города_таможни_не_знает(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    город, рынок, ворота, _ = await _мир(session, catalog)
    await _сделка(session, constants, catalog, рынок, 3)
    await _пошлина(session, constants, catalog, город, ставка=50, норма=0)

    _, тело = await _купец(session, рынок, "Свой", денег=100, руды=20)
    начисления = await customs.cross(
        session, constants, catalog, тело, рынок, ворота
    )
    assert начисления == [], "внутри города границы нет"


async def test_вывоз_облагается_по_справочной_цене(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Пошлина — доля от медианы сделок городского стакана (D-123)."""
    город, рынок, ворота, поле = await _мир(session, catalog)
    await _сделка(session, constants, catalog, рынок, 3)
    await _пошлина(session, constants, catalog, город, ставка=10, норма=0)

    identity, тело = await _купец(session, ворота, "Вывозящий", денег=100, руды=20)
    было = await _баланс(session, identity.id)
    #: В казне уже лежит налог с той сделки, что дала справочную цену: меряем
    #: приход от перехода, а не остаток.
    казна_была = await town.treasury_balance(session, город)
    начисления = await customs.cross(session, constants, catalog, тело, ворота, поле)

    assert len(начисления) == 1 and начисления[0].direction == customs.EXPORT
    #: Двадцать единиц по три ТК, десять процентов — шесть ТК.
    assert начисления[0].duty == money(6)
    assert await _баланс(session, identity.id) == было - money(6)
    assert await town.treasury_balance(session, город) == казна_была + money(6)


async def test_без_сделок_пошлины_нет(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Город, у которого рынок пуст, не может обложить то, чему не знает цены."""
    город, _, ворота, поле = await _мир(session, catalog)
    await _пошлина(session, constants, catalog, город, ставка=50, норма=0)

    _, тело = await _купец(session, ворота, "Вывозящий", денег=100, руды=20)
    начисления = await customs.cross(session, constants, catalog, тело, ворота, поле)
    assert начисления[0].duty == 0


async def test_норма_отделяет_быт_от_промысла(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Новичок с мешком репы не платит, оптовик платит за всё сверх нормы."""
    город, рынок, ворота, поле = await _мир(session, catalog)
    await _сделка(session, constants, catalog, рынок, 3)
    #: Норма в килограммах, у руды килограмм на единицу (D-146).
    await _пошлина(session, constants, catalog, город, ставка=10, норма=30)

    _, малый = await _купец(session, ворота, "Житель", денег=100, руды=20)
    начисления = await customs.cross(session, constants, catalog, малый, ворота, поле)
    assert начисления[0].duty == 0, "меньше нормы — бесплатно"

    _, оптовик = await _купец(session, ворота, "Оптовик", денег=100, руды=50)
    начисления = await customs.cross(session, constants, catalog, оптовик, ворота, поле)
    #: Двадцать единиц сверх нормы по три ТК, десять процентов — шесть ТК.
    assert начисления[0].duty == money(6)


async def test_норма_считается_за_окно_а_не_за_ходку(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Норму, которую обнуляет разбиение груза на заходы, нормой не назовёшь."""
    город, рынок, ворота, поле = await _мир(session, catalog)
    await _сделка(session, constants, catalog, рынок, 3)
    await _пошлина(session, constants, catalog, город, ставка=10, норма=30)

    identity, тело = await _купец(session, ворота, "Хитрый", денег=100, руды=20)
    первый = await customs.cross(session, constants, catalog, тело, ворота, поле)
    assert первый[0].duty == 0

    #: Вторая ходка тем же телом. Груз новый: старый уже вывезен, а норма —
    #: нет, она считается за окно и помнит прошлый заход.
    from sqlalchemy import select

    from src.models.inventory import Item

    карман = await world.body_container(session, тело)
    прошлое = (
        await session.execute(
            select(Item).where(Item.container_id == карман.id, Item.type_key == ORE)
        )
    ).scalars().all()
    for вещь in прошлое:
        await session.delete(вещь)
    await session.flush()
    await world.grant_item(
        session, карман, ORE, amount=20, quality=60, origin="тест"
    )
    второй = await customs.cross(session, constants, catalog, тело, ворота, поле)
    assert второй[0].duty > 0, "норма исчерпана прошлой ходкой"
    assert await customs.moved_in_window(
        session, constants, identity.id, город, customs.EXPORT, ORE
    ) == pytest.approx(40)


async def test_запрет_абсолютен(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Крайняя мера: запрещённое не проходит ни за какие деньги."""
    город, рынок, ворота, поле = await _мир(session, catalog)
    город.laws = {**(город.laws or {}), "export_ban": ORE}
    await session.flush()

    _, тело = await _купец(session, ворота, "Контрабандист", денег=1000, руды=5)
    with pytest.raises(customs.Banned):
        await customs.cross(session, constants, catalog, тело, ворота, поле)


async def test_нечем_платить_товар_не_проходит(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Долга при этом не возникает: таможня не кредитует (D-123)."""
    город, рынок, ворота, поле = await _мир(session, catalog)
    await _сделка(session, constants, catalog, рынок, 3)
    await _пошлина(session, constants, catalog, город, ставка=50, норма=0)

    identity, тело = await _купец(session, ворота, "Бедный", руды=50)
    with pytest.raises(customs.CannotPay):
        await customs.cross(session, constants, catalog, тело, ворота, поле)
    assert await _баланс(session, identity.id) == 0, "долга не возникает"


async def test_переход_не_начинается_без_пошлины(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Граница считается до выхода: иначе в город входит неоплаченное."""
    город, рынок, ворота, поле = await _мир(session, catalog)
    await _сделка(session, constants, catalog, рынок, 3)
    await _пошлина(session, constants, catalog, город, ставка=50, норма=0)

    _, тело = await _купец(session, ворота, "Бедный", руды=50)
    with pytest.raises(customs.CannotPay):
        await travel.depart(session, constants, тело, поле)
    assert await travel.current(session, тело) is None, "переход не начался"


async def test_ввоз_и_вывоз_попадают_в_сводку(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """«Ввезено и вывезено по товарам, в весе и в ходках» — строка панели (D-124)."""
    from datetime import UTC, datetime, timedelta

    from src.engine import panel

    город, рынок, ворота, поле = await _мир(session, catalog)
    await _сделка(session, constants, catalog, рынок, 3)
    await _пошлина(session, constants, catalog, город, ставка=10, норма=0)

    _, тело = await _купец(session, ворота, "Возчик", денег=100, руды=20)
    await customs.cross(session, constants, catalog, тело, ворота, поле)

    сводка = await panel.collect(session, constants, город)
    торговля = сводка["trade"]
    assert торговля["exported"][ORE] == pytest.approx(20)
    assert торговля["trips_out"] == 1
    assert торговля["duty_collected"] > 0

    #: И то же самое напрямую из таможни — одной формулой (D-139).
    прямо = await customs.traffic(
        session, constants, город, since=datetime.now(UTC) - timedelta(hours=1)
    )
    assert прямо["exported"] == торговля["exported"]


async def _баланс(session: AsyncSession, identity_id) -> int:
    счёт = await ledger.account_for(session, AccountKind.IDENTITY, identity_id)
    return await ledger.balance(session, счёт.id)


def test_ставка_числом_означает_на_всё(catalog: Catalog) -> None:
    """Закон читается двумя способами, и оба честные (D-123)."""
    from src.models.city import City

    город = City(node_id=uuid.uuid4(), name="Тест", charter={}, charter_params={},
                 laws={"import_duty": "12"})
    ставки = customs.rates(catalog, город, customs.IMPORT)
    assert ставки["*"]["rate"] == 12 and ставки["*"]["free"] == 0

    город.laws = {"import_duty": {ORE: {"rate": 5, "free": 10}}}
    ставки = customs.rates(catalog, город, customs.IMPORT)
    assert ставки[ORE] == {"rate": 5, "free": 10}
    assert R.TRADE_DUTY_FREE_WINDOW.key == "trade.duty_free_window"


async def test_автопуть_обрывается_на_границе(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Не пустили — маршрут кончается здесь, а не роняет задание журнала.

    Иначе отказ таможни превращался бы в вечно повторяющееся задание, а тело
    зависало бы посреди перегона.
    """
    from datetime import UTC, datetime, timedelta

    from src.engine import jobs
    from src.models.job import Job, JobKind, JobState
    from sqlalchemy import select

    город, рынок, ворота, поле = await _мир(session, catalog)
    await _сделка(session, constants, catalog, рынок, 3)
    await _пошлина(session, constants, catalog, город, ставка=50, норма=0)

    _, тело = await _купец(session, рынок, "Бедный", руды=50)
    #: Автопуть: первый отрезок внутри города, второй — через границу.
    await travel.depart(session, constants, тело, поле)
    задание = (
        await session.execute(
            select(Job).where(
                Job.kind == JobKind.TRAVEL_LEG.value, Job.state == JobState.PENDING
            )
        )
    ).scalars().first()
    assert задание is not None
    await travel.arrive(session, задание)

    #: Дошёл до ворот и встал: дальше не пустили, но задание отработало.
    assert тело.node_id == ворота.id
    assert await travel.current(session, тело) is None
