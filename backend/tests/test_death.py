"""Смерть и печать тела (D-012, D-028, D-032, D-033, D-040).

Проверяется приёмка вольта, дословно:

* «Смерть теряет тело и вещи, но не знания и счёт» (07-implementation-map, Э1);
* «Первое тело мгновенно; столица печатает всегда, но 12 часов» (Э3);
* часть носимого остаётся на месте гибели, и в повреждённом виде;
* город продаёт не жизнь, а скорость: платная дверь быстрее бесплатной.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import city as town
from src.engine import death, energy, ledger, world
from src.models.identity import Body, BodyState, Knowledge
from src.models.inventory import Item
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Layer
from src.units import MINUTES_PER_HOUR, amount_float, money


async def _мир(session: AsyncSession, catalog: Catalog, *, казна: float = 0):
    """Столица с двумя дверями: вечный Принтер Предтеч и городской принтер."""
    метка = uuid.uuid4().hex[:8]
    планета = await world.create_node(
        session, f"terra.{метка}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    представитель = await world.create_node(
        session, f"terra.city.{метка}", "Столица", area_m2=1,
        layer=Layer.PLANET, parent=планета,
    )
    ядро = await world.create_node(
        session, f"terra.city.{метка}.core", "Ядро", area_m2=120,
        parent=представитель, properties={"кольцо": 0, death.PRECURSOR: True},
    )
    кузница = await world.create_node(
        session, f"terra.city.{метка}.forge", "Кузница", area_m2=200,
        parent=представитель, properties={"кольцо": 2},
    )
    город = await town.found(session, catalog, представитель, "Столица")
    for узел in (ядро, кузница):
        узел.owner_city_id = город.id
    await session.flush()

    for узел, качество in ((ядро, 90), (кузница, 60)):
        двор = await world.node_container(session, узел)
        await world.grant_item(
            session, двор, death.PRINTER, quality=качество, origin="тест"
        )
    #: Решения города принимаются в администрации (D-155): без неё президент
    #: не сможет даже разрешить печать за счёт казны.
    await world.grant_item(
        session, await world.node_container(session, ядро),
        town.HALL, quality=65, origin="тест",
    )
    двор_кузницы = await world.node_container(session, кузница)
    await world.grant_item(
        session, двор_кузницы, death.IRON, amount=50, quality=55, origin="тест"
    )

    if казна:
        счёт = await town.treasury(session, город)
        genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
        await ledger.transfer(
            session, PostingReason.GENESIS,
            debit=genesis.id, credit=счёт.id, amount=money(казна),
        )
    return город, ядро, кузница


async def _житель(session: AsyncSession, узел, имя: str, *, денег: float = 0):
    identity, body = await world.spawn(session, f"{имя}-{uuid.uuid4().hex[:6]}", узел)
    if денег:
        счёт = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
        genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
        await ledger.transfer(
            session, PostingReason.GENESIS,
            debit=genesis.id, credit=счёт.id, amount=money(денег),
        )
    return identity, body


async def _пул(session: AsyncSession, constants: Constants, узел, сколько: float):
    pool = await energy.pool_of(session, constants, узел)
    pool.stored = Decimal(str(сколько))
    await session.flush()
    return pool


# --- гибель -----------------------------------------------------------------


async def test_смерть_забирает_вещи_но_не_знания_и_счёт(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Приёмка Э1 дословно: тело и вещи — да, знания и счёт — нет."""
    _, ядро, _ = await _мир(session, catalog)
    identity, body = await _житель(session, ядро, "Шахтёр", денег=40)
    карман = await world.body_container(session, body)
    await world.grant_item(
        session, карман, "Железная кирка", quality=60, origin="тест"
    )
    await world.grant_item(
        session, карман, "Уголь", amount=100, quality=50, origin="тест"
    )
    await world.learn(session, identity, "Гвозди")

    await death.die(session, constants, body, cause="обрушение свода")

    assert body.state is BodyState.DEAD and body.died_at is not None
    осталось = (
        await session.execute(select(Item).where(Item.container_id == карман.id))
    ).scalars().all()
    assert осталось == [], "карман погибшего пуст: вещи гибнут вместе с телом"

    счёт = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
    assert await ledger.balance(session, счёт.id) == money(40), "счёт телу не принадлежит"
    знание = (
        await session.execute(
            select(Knowledge).where(Knowledge.identity_id == identity.id)
        )
    ).scalars().all()
    assert знание, "знание живёт в личности и не теряется"


async def test_гибель_в_пути_обрывает_переход(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Мёртвое тело никуда не приходит, и переход обязан это помнить.

    Состояние отдельное от «пришёл»: иначе разбор эпизода покажет приход туда,
    куда никто не приходил, а задание журнала попробует довезти труп.
    """
    from src.engine import travel
    from src.models.travel import Travel, TravelState
    from src.models.world import Node

    _, ядро, _ = await _мир(session, catalog)
    _, body = await _житель(session, ядро, "Ходок")
    там = await world.create_node(
        session, f"terra.dead.{uuid.uuid4().hex[:8]}", "Там", area_m2=100
    )
    await travel.connect(session, await session.get(Node, body.node_id), там,
                         base_seconds=600)
    переход = await travel.depart(session, constants, body, там)

    await death.die(session, constants, body, cause="обрушение свода")

    переход = await session.get(Travel, переход.id)
    assert переход.state is TravelState.CANCELLED, "переход оборван, а не дошёл"
    assert body.node_id != там.id, "мёртвое тело никуда не приходит"
    assert await travel.current(session, body) is None


async def test_часть_носимого_остаётся_на_месте_и_битой(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Ограбить живого выгоднее, чем убить: мёртвый оставляет треть, и ту битую."""
    _, ядро, _ = await _мир(session, catalog)
    _, body = await _житель(session, ядро, "Шахтёр")
    карман = await world.body_container(session, body)
    await world.grant_item(
        session, карман, "Уголь", amount=100, quality=50, origin="тест"
    )

    уцелело = await death.die(session, constants, body, cause="обвал")
    доля = constants[R.DEATH_SALVAGE_RATIO] / 100

    двор = await world.node_container(session, ядро)
    на_месте = (
        await session.execute(
            select(Item).where(Item.container_id == двор.id, Item.type_key == "Уголь")
        )
    ).scalars().all()
    assert len(на_месте) == 1
    assert amount_float(на_месте[0].amount) == pytest.approx(100 * доля)
    assert float(на_месте[0].condition) == pytest.approx(100 * доля)
    assert уцелело == pytest.approx(100 * доля)


# --- печать -----------------------------------------------------------------


async def test_предтечи_печатают_бесплатно_но_двенадцать_часов(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Верхняя граница цены воскрешения: дольше этого никто не заплатит (D-028)."""
    _, ядро, _ = await _мир(session, catalog)
    identity, body = await _житель(session, ядро, "Погибший")
    await death.die(session, constants, body, cause="обвал")

    задание = await death.order(session, constants, catalog, identity, ядро)
    часов = (задание.run_at - body.died_at).total_seconds() / 3600
    assert часов == pytest.approx(constants[R.DEATH_PRINT_TIME_CAPITAL], rel=0.01)

    счёт = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
    assert await ledger.balance(session, счёт.id) == 0, "у Предтеч печать бесплатна"


async def test_городской_принтер_берёт_энергию_железо_и_деньги(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Город продаёт скорость: минуты вместо часов, но за ресурсы (D-033)."""
    город, ядро, кузница = await _мир(session, catalog)
    identity, body = await _житель(session, ядро, "Богатый", денег=100)
    await death.die(session, constants, body, cause="обвал")

    pool = await _пул(session, constants, кузница, 100_000)
    было_энергии = float(pool.stored)
    двор = await world.node_container(session, кузница)
    было_железа = await death._iron_here(session, кузница)

    задание = await death.order(session, constants, catalog, identity, кузница)
    минут = (задание.run_at - body.died_at).total_seconds() / 60
    assert минут == pytest.approx(constants[R.DEATH_PRINT_TIME_CITY], rel=0.01)
    assert минут < constants[R.DEATH_PRINT_TIME_CAPITAL] * MINUTES_PER_HOUR

    assert float(pool.stored) == pytest.approx(
        было_энергии - constants[R.ENERGY_BODY_PRINT]
    )
    assert await death._iron_here(session, кузница) == pytest.approx(
        было_железа - constants[R.DEATH_IRON_COST]
    )
    счёт = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
    заплачено = money(100) - await ledger.balance(session, счёт.id)
    assert заплачено > 0
    assert await town.treasury_balance(session, город) == заплачено
    assert двор is not None


async def test_без_денег_городской_принтер_отказывает(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Но дверь столицы при этом остаётся открытой — из игры не выпадают."""
    _, ядро, кузница = await _мир(session, catalog)
    identity, body = await _житель(session, ядро, "Бедняк")
    await death.die(session, constants, body, cause="обвал")
    await _пул(session, constants, кузница, 100_000)

    with pytest.raises(death.CannotPay):
        await death.order(session, constants, catalog, identity, кузница)

    #: А бесплатная дверь работает всегда.
    задание = await death.order(session, constants, catalog, identity, ядро)
    assert задание is not None


async def test_город_может_печатать_за_свой_счёт(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Код-закон `body_print` — тот самый аргумент вступить в город (D-032)."""
    город, ядро, кузница = await _мир(session, catalog, казна=500)
    президент, тело_президента = await _житель(session, ядро, "Президент")
    await town.install_founder(session, город, президент)
    await town.set_law(
        session, constants, catalog, президент, город, "body_print", "всем",
        body=тело_президента,
    )

    identity, body = await _житель(session, ядро, "Бедняк")
    await death.die(session, constants, body, cause="обвал")
    await _пул(session, constants, кузница, 100_000)

    задание = await death.order(session, constants, catalog, identity, кузница)
    assert задание is not None
    счёт = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
    assert await ledger.balance(session, счёт.id) == 0, "платит казна, не игрок"


async def test_печать_приводит_личность_обратно(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Тело новое, личность та же: имя и обязательства переживают смерть."""
    _, ядро, _ = await _мир(session, catalog)
    identity, body = await _житель(session, ядро, "Возвращённый")
    await death.die(session, constants, body, cause="обвал")

    задание = await death.order(session, constants, catalog, identity, ядро)
    await death.printed(session, задание)

    новое = await death.alive_body(session, identity.id)
    assert новое is not None and новое.id != body.id
    assert новое.node_id == ядро.id
    assert float(новое.stamina) == constants[R.BODY_STAMINA_MAX]

    #: Повтор задания после сбоя вторым телом не станет (D-011).
    await death.printed(session, задание)
    тела = (
        await session.execute(
            select(Body).where(
                Body.identity_id == identity.id, Body.state == BodyState.ALIVE
            )
        )
    ).scalars().all()
    assert len(тела) == 1


async def test_живому_печать_не_положена(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, ядро, _ = await _мир(session, catalog)
    identity, _ = await _житель(session, ядро, "Живой")
    with pytest.raises(death.Alive):
        await death.order(session, constants, catalog, identity, ядро)


async def test_вторая_печать_подряд_не_ставится(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, ядро, _ = await _мир(session, catalog)
    identity, body = await _житель(session, ядро, "Погибший")
    await death.die(session, constants, body, cause="обвал")

    await death.order(session, constants, catalog, identity, ядро)
    with pytest.raises(death.AlreadyPrinting):
        await death.order(session, constants, catalog, identity, ядро)


async def test_принтеры_видны_из_облака(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Личность в Сети: список дверей доступен и мёртвому (D-033)."""
    _, ядро, кузница = await _мир(session, catalog)
    _, body = await _житель(session, ядро, "Погибший")
    await death.die(session, constants, body, cause="обвал")

    двери = await death.printers(session, constants)
    ключи = {дверь["node"]: дверь for дверь in двери}
    assert ядро.key in ключи and кузница.key in ключи
    assert ключи[ядро.key]["precursor"] is True
    assert ключи[ядро.key]["cost"] == 0
    assert ключи[кузница.key]["iron"] == constants[R.DEATH_IRON_COST]
    #: Быстрая дверь первой: сравнивать двери игрок должен по сроку.
    assert двери[0]["node"] == кузница.key


# --- вход новичка (D-013, D-182) --------------------------------------------


async def test_двери_новичка_показывают_город_а_не_цену(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Первое тело бесплатно везде (D-040), значит выбирают не цену, а людей."""
    город, ядро, кузница = await _мир(session, catalog)
    город.laws = {"newcomer_grant": "50"}
    await _житель(session, ядро, "Старожил")
    await session.flush()

    двери = await world.doors(session, constants, catalog)
    ключи = {дверь["node"]: дверь for дверь in двери}
    assert set(ключи) == {ядро.key, кузница.key}
    assert ключи[кузница.key]["city"] == "Столица"
    assert ключи[кузница.key]["grant"] == money(50)
    #: Ни цены, ни срока: новичку они не назначаются, и врать о них нельзя.
    assert "cost" not in ключи[ядро.key] and "minutes" not in ключи[ядро.key]
    #: Принтер Предтеч последним: запасная дверь без жителей и без казны.
    assert двери[-1]["node"] == ядро.key and двери[-1]["precursor"] is True


async def test_каторга_новичку_дверью_не_является(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Тюремный принтер печатает только удерживаемых (D-174) и в мир не ведёт."""
    from src.engine import justice

    _, ядро, _ = await _мир(session, catalog)
    тюрьма = await world.create_node(
        session, f"terra.jail.{uuid.uuid4().hex[:6]}", "Каторга", area_m2=100
    )
    двор = await world.node_container(session, тюрьма)
    await world.grant_item(session, двор, death.PRINTER, quality=40, origin="тест")
    await world.grant_item(session, двор, justice.KATORGA, quality=40, origin="тест")
    await session.flush()

    ключи = {дверь["node"] for дверь in await world.doors(session, constants, catalog)}
    assert тюрьма.key not in ключи
    assert await world.door(session, тюрьма.key) is None
    #: Обычная дверь по ключу открывается — иначе выбирать было бы нечего.
    assert (await world.door(session, ядро.key)) is not None


async def test_дверью_зовётся_только_узел_с_принтером(
    session: AsyncSession, catalog: Catalog
) -> None:
    """Чужой ключ и узел без принтера отказывают одинаково: печатать негде."""
    _, _, кузница = await _мир(session, catalog)
    поле = await world.create_node(
        session, f"terra.field.{uuid.uuid4().hex[:6]}", "Пойма", area_m2=400
    )
    await session.flush()

    assert await world.door(session, поле.key) is None
    assert await world.door(session, "нет-такого-узла") is None
    assert (await world.door(session, кузница.key)) is not None
