"""Недвижимость: выкуп участка, ценная бумага, здание (D-089, D-106, D-116).

Проверяется то, ради чего система введена:

* пустой городской участок покупает тот, кому позволяет код-закон
  `build_permit` (по умолчанию — граждане, D-160), цена — от удалённости до
  биопринтера, выручка — в казну города;
* владение оформляется ценной бумагой; бумага продаётся договором
  купли-продажи, и титул на узел переходит вместе с ней;
* здание строится на своём участке из материалов и по сроку; станок без
  здания не встаёт (см. `test_station`).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import city as town
from src.engine import estate, ledger, world
from src.models.city import Citizen
from src.models.estate import Deed
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Layer, Node, Surface
from src.units import PERCENT, money


async def _город(session: AsyncSession, catalog: Catalog):
    """Городок: ядро с Принтером Предтеч и два участка на первом-втором шаге."""
    from src.engine import travel

    метка = uuid.uuid4().hex[:8]
    планета = await world.create_node(
        session, f"terra.{метка}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    представитель = await world.create_node(
        session, f"terra.town.{метка}", "Городок", area_m2=1,
        layer=Layer.PLANET, parent=планета,
    )
    ядро = await world.create_node(
        session, f"terra.town.{метка}.core", "Ядро", area_m2=100,
        parent=представитель, properties={"кольцо": 0, "предтечи": True},
    )
    #: Биопринтер, от которого меряется удалённость: центр города (D-089).
    двор_ядра = await world.node_container(session, ядро)
    await world.grant_item(session, двор_ядра, world.BIOPRINTER, quality=60, origin="тест")

    ближний = await world.create_node(
        session, f"terra.town.{метка}.lot1", "Ближний участок", area_m2=100,
        parent=представитель, properties={"участок": True},
    )
    дальний = await world.create_node(
        session, f"terra.town.{метка}.lot2", "Дальний участок", area_m2=100,
        parent=представитель, properties={"участок": True},
    )
    await travel.connect(session, ядро, ближний, base_seconds=30, surface=Surface.PAVED)
    await travel.connect(session, ближний, дальний, base_seconds=30, surface=Surface.PAVED)

    город = await town.found(session, catalog, представитель, "Городок")
    for узел in (ядро, ближний, дальний):
        узел.owner_city_id = город.id
    await session.flush()
    return город, ядро, ближний, дальний


async def _покупатель(
    session: AsyncSession,
    где: Node,
    *,
    денег: float = 1_000,
    город=None,
    гражданин: bool = True,
):
    """Покупатель. По умолчанию гражданин: землю продают своим (D-160)."""
    метка = uuid.uuid4().hex[:6]
    identity = await world.create_identity(session, f"Покупатель-{метка}")
    body = await world.print_body(session, identity, где)
    if гражданин and город is not None:
        session.add(Citizen(identity_id=identity.id, city_id=город.id))
        await session.flush()
    if денег:
        счёт = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
        genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
        await ledger.transfer(
            session, PostingReason.GENESIS, debit=genesis.id, credit=счёт.id,
            amount=money(денег), memo={},
        )
    return identity, body


# --- цена и выкуп (D-089) ----------------------------------------------------


async def test_дальний_участок_дешевле_ближнего(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Цена падает с каждым кольцом от биопринтера — центра города."""
    город, _, ближний, дальний = await _город(session, catalog)
    близко = await estate.price_of(session, constants, catalog, город, ближний)
    далеко = await estate.price_of(session, constants, catalog, город, дальний)

    assert близко > далеко > 0
    спад = 1 - constants[R.LAND_PRICE_DECAY_PER_RING] / PERCENT
    assert далеко == pytest.approx(близко * спад, rel=0.01)


async def test_выкуп_отдаёт_деньги_в_казну_и_выдаёт_бумагу(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Город продаёт свою землю: выручка — казне, покупателю — титул."""
    город, _, ближний, _ = await _город(session, catalog)
    identity, body = await _покупатель(session, ближний, город=город)

    было_в_казне = await town.treasury_balance(session, город)
    deed = await estate.buy(session, constants, catalog, body, ближний)

    assert ближний.owner_identity_id == identity.id
    assert deed.owner_identity_id == identity.id
    assert deed.paid > 0
    assert await town.treasury_balance(session, город) == было_в_казне + deed.paid


async def test_без_денег_не_покупают(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    город, _, ближний, _ = await _город(session, catalog)
    _, body = await _покупатель(session, ближний, денег=0, город=город)
    with pytest.raises(estate.NotEnoughMoney):
        await estate.buy(session, constants, catalog, body, ближний)


async def test_занятый_участок_не_продаётся(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    город, _, ближний, _ = await _город(session, catalog)
    первый, тело_первого = await _покупатель(session, ближний, город=город)
    await estate.buy(session, constants, catalog, тело_первого, ближний)

    _, тело_второго = await _покупатель(session, ближний, город=город)
    with pytest.raises(estate.NotForSale):
        await estate.buy(session, constants, catalog, тело_второго, ближний)


# --- имя участка (D-178) -----------------------------------------------------


async def test_хозяин_даёт_участку_имя(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Купил — назвал. Ключ узла при этом прежний: на него ссылаются бумаги."""
    город, _, ближний, _ = await _город(session, catalog)
    _, тело = await _покупатель(session, ближний, город=город)
    await estate.buy(session, constants, catalog, тело, ближний)
    ключ = ближний.key

    await estate.rename(session, тело, ближний, "  Кузня у ворот  ")

    assert ближний.name == "Кузня у ворот", "пробелы по краям обрезаются"
    assert ближний.key == ключ, "ключ узла переименованием не трогают"


async def test_чужой_участок_переименовать_нельзя(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Табличку на чужом доме не меняют — даже стоя рядом."""
    город, _, ближний, _ = await _город(session, catalog)
    _, хозяин = await _покупатель(session, ближний, город=город)
    await estate.buy(session, constants, catalog, хозяин, ближний)
    было = ближний.name

    _, прохожий = await _покупатель(session, ближний, город=город)
    with pytest.raises(estate.NotOwner):
        await estate.rename(session, прохожий, ближний, "Моё теперь")
    assert ближний.name == было


async def test_власть_называет_городскую_землю_но_не_частную(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Право `land` — про городские участки, а не про чужой двор (D-089)."""
    город, ядро, ближний, дальний = await _город(session, catalog)
    правитель, тело_правителя = await _покупатель(session, дальний, город=город)
    await town.install_founder(session, город, правитель)

    await estate.rename(session, тело_правителя, дальний, "Площадь совета")
    assert дальний.name == "Площадь совета"

    #: Тот же правитель на выкупленном участке — уже не власть, а гость.
    _, хозяин = await _покупатель(session, ближний, город=город)
    await estate.buy(session, constants, catalog, хозяин, ближний)
    тело_правителя.node_id = ближний.id
    await session.flush()
    with pytest.raises(estate.NotOwner):
        await estate.rename(session, тело_правителя, ближний, "Городское теперь")


async def test_имя_не_бывает_пустым_и_бесконечным(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    from src.runtime import LAND_NAME_LIMIT

    город, _, ближний, _ = await _город(session, catalog)
    _, тело = await _покупатель(session, ближний, город=город)
    await estate.buy(session, constants, catalog, тело, ближний)

    with pytest.raises(estate.BadName):
        await estate.rename(session, тело, ближний, "   ")
    with pytest.raises(estate.BadName):
        await estate.rename(session, тело, ближний, "я" * (LAND_NAME_LIMIT + 1))


# --- бумага и договор купли-продажи (D-116) ----------------------------------


async def test_бумага_продаётся_и_титул_переходит_с_ней(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    город, _, ближний, _ = await _город(session, catalog)
    продавец, тело_продавца = await _покупатель(session, ближний, город=город)
    deed = await estate.buy(session, constants, catalog, тело_продавца, ближний)

    покупатель, _ = await _покупатель(session, ближний, денег=500)
    цена = money(100)
    await estate.offer_deed(session, продавец, deed, цена)
    await estate.buy_deed(session, покупатель, deed)

    assert deed.owner_identity_id == покупатель.id
    assert deed.sale_price is None, "после сделки бумага снята с продажи"
    await session.refresh(ближний)
    assert ближний.owner_identity_id == покупатель.id, "титул ходит с бумагой"

    счёт = await ledger.account_for(session, AccountKind.IDENTITY, продавец.id)
    assert await ledger.balance(session, счёт.id) > 0, "деньги дошли продавцу"


async def test_адресный_договор_чужому_не_продаёт(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Бумага, обещанная одному, второму не отдаётся."""
    город, _, ближний, _ = await _город(session, catalog)
    продавец, тело = await _покупатель(session, ближний, город=город)
    deed = await estate.buy(session, constants, catalog, тело, ближний)

    свой, _ = await _покупатель(session, ближний, денег=500)
    чужой, _ = await _покупатель(session, ближний, денег=500)
    await estate.offer_deed(session, продавец, deed, money(50), to=свой)

    with pytest.raises(estate.NotForSale):
        await estate.buy_deed(session, чужой, deed)
    await estate.buy_deed(session, свой, deed)
    assert deed.owner_identity_id == свой.id


async def test_занятая_дикая_земля_тоже_даёт_бумагу(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Титул один для всех дорог к земле: занял — бумага, купил — бумага."""
    метка = uuid.uuid4().hex[:6]
    дикий = await world.create_node(
        session, f"terra.wild.{метка}", "Дикий", area_m2=200, layer=Layer.PLANET
    )
    identity, body = await _покупатель(session, дикий, денег=0)
    await world.claim_node(session, body, дикий)

    deed = (
        await session.execute(select(Deed).where(Deed.node_id == дикий.id))
    ).scalar_one()
    assert deed.owner_identity_id == identity.id
    assert deed.paid == 0


# --- здание (D-106, D-125) ---------------------------------------------------


async def test_стройка_списывает_материалы_и_ставит_здание_по_сроку(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    метка = uuid.uuid4().hex[:6]
    участок = await world.create_node(
        session, f"terra.plot.{метка}", "Участок", area_m2=100, layer=Layer.PLANET
    )
    identity, body = await _покупатель(session, участок, денег=0)
    await world.claim_node(session, body, участок)

    карман = await world.body_container(session, body)
    нормы = constants[R.BUILD_MATERIALS_PER_M2]
    площадь = 20.0
    for имя, на_метр in нормы.items():
        await world.grant_item(
            session, карман, имя, amount=float(на_метр) * площадь + 1,
            quality=60, origin="тест",
        )

    job = await estate.construct(session, constants, body, участок, площадь)
    assert await estate.built_area(session, участок) == 0, "здание не мгновенно"

    #: Срок — труд сборки: `build.labor_per_m2` часов на метр.
    минут = площадь * constants[R.BUILD_LABOR_PER_M2] * 60
    assert (job.run_at - datetime.now(UTC)).total_seconds() / 60 == pytest.approx(
        минут, rel=0.05
    )

    await estate.finish_build(session, job)
    assert await estate.built_area(session, участок) == pytest.approx(площадь)


async def test_без_материалов_стройка_не_начинается(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    from src.engine import craft

    метка = uuid.uuid4().hex[:6]
    участок = await world.create_node(
        session, f"terra.plot.{метка}", "Участок", area_m2=100, layer=Layer.PLANET
    )
    _, body = await _покупатель(session, участок, денег=0)
    await world.claim_node(session, body, участок)
    with pytest.raises(craft.NotEnough):
        await estate.construct(session, constants, body, участок, 20)


async def test_здание_не_больше_участка(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    метка = uuid.uuid4().hex[:6]
    участок = await world.create_node(
        session, f"terra.plot.{метка}", "Участок", area_m2=50, layer=Layer.PLANET
    )
    _, body = await _покупатель(session, участок, денег=0)
    await world.claim_node(session, body, участок)
    with pytest.raises(estate.NoRoom):
        await estate.construct(session, constants, body, участок, 60)


async def test_на_чужом_не_строят(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    метка = uuid.uuid4().hex[:6]
    участок = await world.create_node(
        session, f"terra.plot.{метка}", "Участок", area_m2=100, layer=Layer.PLANET
    )
    хозяин, тело_хозяина = await _покупатель(session, участок, денег=0)
    await world.claim_node(session, тело_хозяина, участок)

    _, чужое_тело = await _покупатель(session, участок, денег=0)
    with pytest.raises(estate.EstateError):
        await estate.construct(session, constants, чужое_тело, участок, 10)
