"""Город как институт: должности, законы, казна, подъёмные (D-153, D-154).

Проверяется то, ради чего администрация вообще заведена:

* полномочие, а не должность: движок смотрит в `powers`, а не в название поста;
* отдать можно только то, что есть у себя, — иначе `offices` даёт всё;
* закон города бьёт умолчание вольта, а тариф доезжает до пула;
* подъёмные — **перевод из казны**, один раз на личность, и пустая казна не
  платит ничего.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.engine import city as town
from src.engine import energy, ledger, world
from src.models.city import Power
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Layer
from src.units import money


async def _столица(session: AsyncSession, catalog: Catalog, *, денег: float = 0):
    """Город с узлом-представителем, застройкой и основателем."""
    метка = uuid.uuid4().hex[:8]
    планета = await world.create_node(
        session, f"terra.{метка}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    представитель = await world.create_node(
        session, f"terra.city.{метка}", "Столица", area_m2=1,
        layer=Layer.PLANET, parent=планета,
    )
    ядро = await world.create_node(
        session, f"terra.city.{метка}.core", "Ядро", area_m2=100,
        parent=представитель, properties={"кольцо": 0},
    )
    город = await town.found(session, catalog, представитель, "Столица")
    ядро.owner_city_id = город.id
    await session.flush()
    #: Управление присутственно (D-155): решения принимаются там, где стоит
    #: «Администрация». В стартовом мире это отдельный узел, в тесте — ядро.
    двор = await world.node_container(session, ядро)
    await world.grant_item(session, двор, town.HALL, quality=65, origin="тест")

    if денег:
        казна = await town.treasury(session, город)
        genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
        await ledger.transfer(
            session, PostingReason.GENESIS,
            debit=genesis.id, credit=казна.id, amount=money(денег),
        )
    return город, ядро


async def _житель(session: AsyncSession, узел, имя: str):
    identity = await world.create_identity(session, f"{имя}-{uuid.uuid4().hex[:6]}")
    body = await world.print_body(session, identity, узел)
    return identity, body


# --- должности и полномочия -------------------------------------------------


async def test_город_возникает_работающим(
    session: AsyncSession, catalog: Catalog
) -> None:
    """Устав заполняется умолчаниями вольта: анкеты на сорок вопросов нет (D-130)."""
    город, _ = await _столица(session, catalog)
    assert город.charter, "устав пуст: город возник неработающим"
    assert город.charter["ruler_selection"] == "founder"
    #: Своих решений ещё нет — значит действует умолчание вольта.
    assert town.law(catalog, город, "tax_trade") == (
        catalog.laws.code_law_defaults()["tax_trade"]
    )


async def test_основатель_получает_полную_власть(
    session: AsyncSession, catalog: Catalog
) -> None:
    город, ядро = await _столица(session, catalog)
    президент, _ = await _житель(session, ядро, "Президент")
    await town.install_founder(session, город, президент)

    assert await town.powers_of(session, президент.id, город) == set(town.FOUNDER_POWERS)
    #: Первый раз власть берётся, дальше — только назначением.
    with pytest.raises(town.CityError):
        await town.install_founder(session, город, президент)


async def test_без_полномочия_закон_не_правится(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Власть — это должность, а не намерение."""
    город, ядро = await _столица(session, catalog)
    прохожий, _ = await _житель(session, ядро, "Прохожий")
    with pytest.raises(town.NotAllowed):
        await town.set_law(
            session, constants, catalog, прохожий, город, "tax_trade", "10"
        )


async def test_отдать_можно_только_своё(
    session: AsyncSession, catalog: Catalog
) -> None:
    """Иначе любой, кому дали `offices`, назначит себе всё остальное."""
    город, ядро = await _столица(session, catalog)
    президент, тело_президента = await _житель(session, ядро, "Президент")
    await town.install_founder(session, город, президент)

    кадровик, тело_кадровика = await _житель(session, ядро, "Кадровик")
    await town.appoint(
        session, президент, город, кадровик,
        title="Кадровик", powers=(Power.OFFICES.value,), body=тело_президента,
    )
    третий, _ = await _житель(session, ядро, "Третий")
    with pytest.raises(town.NotAllowed):
        await town.appoint(
            session, кадровик, город, третий,
            title="Казначей", powers=(Power.TREASURY.value,), body=тело_кадровика,
        )
    #: А то, что есть, — передаётся.
    await town.appoint(
        session, кадровик, город, третий, title="Помощник",
        powers=(Power.OFFICES.value,), body=тело_кадровика,
    )
    assert Power.OFFICES.value in await town.powers_of(session, третий.id, город)


# --- законы -----------------------------------------------------------------


async def test_решение_города_бьёт_умолчание(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    город, ядро = await _столица(session, catalog)
    президент, тело = await _житель(session, ядро, "Президент")
    await town.install_founder(session, город, президент)

    await town.set_law(
        session, constants, catalog, президент, город, "tax_trade", "11", body=тело
    )
    assert town.law(catalog, город, "tax_trade") == "11"
    assert town.law_number(constants, catalog, город, "tax_trade") == 11


async def test_тариф_доезжает_до_пула(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Решение власти обязано дойти до счётчика, а не остаться записью (D-085)."""
    город, ядро = await _столица(session, catalog)
    президент, тело = await _житель(session, ядро, "Президент")
    await town.install_founder(session, город, президент)

    pool = await energy.pool_of(session, constants, ядро)
    assert pool is not None
    await town.set_law(
        session, constants, catalog, президент, город, "energy_tariff", "9", body=тело
    )
    await session.refresh(pool)
    assert float(pool.tariff) == 9


async def test_умолчание_ссылкой_разворачивается_в_константу(
    constants: Constants, catalog: Catalog
) -> None:
    """`energy_tariff` задан в вольте ссылкой на константу — и она читается."""
    from src.constants import registry as R

    значение = town.law_number(constants, catalog, None, "energy_tariff")
    assert значение == constants[R.ENERGY_TARIFF_DEFAULT]


# --- казна и подъёмные ------------------------------------------------------


async def test_казну_тратит_только_распорядитель(
    session: AsyncSession, catalog: Catalog
) -> None:
    город, ядро = await _столица(session, catalog, денег=100)
    президент, тело = await _житель(session, ядро, "Президент")
    await town.install_founder(session, город, президент)
    житель, тело_жителя = await _житель(session, ядро, "Житель")

    with pytest.raises(town.NotAllowed):
        await town.spend(session, житель, город, житель, money(10), body=тело_жителя)

    await town.spend(
        session, президент, город, житель, money(10), memo="жалованье", body=тело
    )
    счёт = await ledger.account_for(session, AccountKind.IDENTITY, житель.id)
    assert await ledger.balance(session, счёт.id) == money(10)
    assert await town.treasury_balance(session, город) == money(90)


async def test_пустая_казна_не_платит(
    session: AsyncSession, catalog: Catalog
) -> None:
    """Пустая казна — политическое событие, а не повод печатать деньги."""
    город, ядро = await _столица(session, catalog)
    президент, тело = await _житель(session, ядро, "Президент")
    await town.install_founder(session, город, президент)
    житель, _ = await _житель(session, ядро, "Житель")

    with pytest.raises(town.NotEnoughTreasury):
        await town.spend(session, президент, город, житель, money(10), body=тело)


async def test_новичок_печатается_с_нулём(
    session: AsyncSession, catalog: Catalog
) -> None:
    """Мир денег не выдаёт: любой такой выпуск размывает деньги всех (D-153)."""
    город, ядро = await _столица(session, catalog)
    identity, _ = await world.spawn(session, f"Новичок-{uuid.uuid4().hex[:6]}", ядро)
    счёт = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
    assert await ledger.balance(session, счёт.id) == 0


async def test_подъёмные_платит_город_и_один_раз(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Это перевод, а не эмиссия: в мире не появляется ни монеты."""
    город, ядро = await _столица(session, catalog, денег=500)
    президент, тело = await _житель(session, ядро, "Президент")
    await town.install_founder(session, город, президент)
    await town.set_law(
        session, constants, catalog, президент, город, "newcomer_grant", "50", body=тело
    )
    было = await town.treasury_balance(session, город)

    identity, _ = await world.spawn(session, f"Новичок-{uuid.uuid4().hex[:6]}", ядро)
    счёт = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
    assert await ledger.balance(session, счёт.id) == money(50)
    assert await town.treasury_balance(session, город) == было - money(50)

    #: Второй раз тому же человеку в том же городе — ноль.
    assert await town.welcome(session, constants, catalog, город, identity) == 0


async def test_подъёмные_по_умолчанию_нулевые(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Город, ничего не решивший, не платит ничего: умолчание вольта — ноль."""
    город, ядро = await _столица(session, catalog, денег=500)
    identity, _ = await world.spawn(session, f"Новичок-{uuid.uuid4().hex[:6]}", ядро)
    счёт = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
    assert await ledger.balance(session, счёт.id) == 0


# --- земля города -----------------------------------------------------------


async def test_город_раздаёт_участки(
    session: AsyncSession, catalog: Catalog
) -> None:
    """Городскую землю не занимают — её даёт город (D-089)."""
    город, ядро = await _столица(session, catalog)
    президент, тело_президента = await _житель(session, ядро, "Президент")
    await town.install_founder(session, город, президент)
    житель, тело = await _житель(session, ядро, "Житель")

    участок = await world.create_node(
        session, f"terra.lot.{uuid.uuid4().hex[:8]}", "Участок", area_m2=100,
        parent=await session.get(type(ядро), ядро.parent_id),
        properties={"участок": True},
    )
    участок.owner_city_id = город.id
    await session.flush()

    #: Занять городскую землю руками нельзя — на то она и городская.
    тело.node_id = участок.id
    with pytest.raises(world.LandError):
        await world.claim_node(session, тело, участок)

    await town.allot(
        session, президент, город, участок, житель, body=тело_президента
    )
    assert участок.owner_identity_id == житель.id


# --- точечные права и присутствие (D-155) -----------------------------------


async def test_право_на_один_закон_не_открывает_остальные(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """«Министр экономики» правит пошлины и не трогает налог — в этом вся суть."""
    город, ядро = await _столица(session, catalog)
    президент, тело_президента = await _житель(session, ядро, "Президент")
    await town.install_founder(session, город, президент)

    министр, тело_министра = await _житель(session, ядро, "Министр")
    await town.appoint(
        session, президент, город, министр,
        title="Министр экономики",
        powers=("law:import_duty", "law:export_duty", Power.DASHBOARD.value),
        body=тело_президента,
    )

    await town.set_law(
        session, constants, catalog, министр, город, "import_duty", "7",
        body=тело_министра,
    )
    assert town.law(catalog, город, "import_duty") == "7"

    #: А налог — не его: право точечное, и это проверяет движок.
    with pytest.raises(town.NotAllowed):
        await town.set_law(
            session, constants, catalog, министр, город, "tax_trade", "1",
            body=тело_министра,
        )


async def test_крупное_право_покрывает_точечное(
    session: AsyncSession, catalog: Catalog
) -> None:
    """Держащий `laws` вправе выдать `law:toll`; держащий `law:toll` — нет."""
    город, ядро = await _столица(session, catalog)
    президент, тело = await _житель(session, ядро, "Президент")
    await town.install_founder(session, город, президент)

    assert await town.may(session, президент.id, город, "law:toll")

    узкий, тело_узкого = await _житель(session, ядро, "Узкий")
    await town.appoint(
        session, президент, город, узкий,
        title="Смотритель дорог",
        powers=("law:toll", Power.OFFICES.value),
        body=тело,
    )
    другой, _ = await _житель(session, ядро, "Другой")
    with pytest.raises(town.NotAllowed):
        await town.appoint(
            session, узкий, город, другой,
            title="Казначей", powers=(Power.LAWS.value,), body=тело_узкого,
        )


async def test_власть_осуществляется_в_администрации(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Власть, осуществимая из-за океана, не нуждается ни в столице, ни в дорогах."""
    город, ядро = await _столица(session, catalog)
    президент, тело = await _житель(session, ядро, "Президент")
    await town.install_founder(session, город, президент)

    #: Уходим из ратуши в соседний узел того же города.
    склад = await world.create_node(
        session, f"terra.store.{uuid.uuid4().hex[:8]}", "Склад", area_m2=100,
        parent=await session.get(type(ядро), ядро.parent_id),
    )
    склад.owner_city_id = город.id
    тело.node_id = склад.id
    await session.flush()

    with pytest.raises(town.NotAllowed):
        await town.set_law(
            session, constants, catalog, президент, город, "tax_trade", "9", body=тело
        )

    #: Вернулись — и решение проходит.
    тело.node_id = ядро.id
    await session.flush()
    await town.set_law(
        session, constants, catalog, президент, город, "tax_trade", "9", body=тело
    )
    assert town.law(catalog, город, "tax_trade") == "9"


async def test_отключённая_администрация_не_управляет(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Не заплатил — город слеп и нем: это цена содержания (D-140, D-149)."""
    from src.engine import utility

    город, ядро = await _столица(session, catalog)
    президент, тело = await _житель(session, ядро, "Президент")
    await town.install_founder(session, город, президент)

    meter = await utility.meter_of(session, ядро)
    assert meter is not None
    meter.cut_off = True
    await session.flush()

    with pytest.raises(town.NotAllowed):
        await town.set_law(
            session, constants, catalog, президент, город, "tax_trade", "9", body=тело
        )


# --- основание города игроком (D-023, D-098, D-159) -------------------------


async def _пустошь(session: AsyncSession, имя: str = "Основатель"):
    """Свой узел на планете: найден разведкой и занят присутственно."""
    метка = uuid.uuid4().hex[:8]
    планета = await world.create_node(
        session, f"terra.{метка}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    место = await world.create_node(
        session, f"terra.wild.{метка}", "Место под город", area_m2=400,
        layer=Layer.PLANET, parent=планета, properties={"дикий": True},
    )
    identity, body = await _житель(session, место, имя)
    await world.claim_node(session, body, место)
    return место, identity, body


async def _застроить(session: AsyncSession, узел, *, чего_нет: str | None = None):
    """Поставить в узел четыре обязательные постройки, кроме названной."""
    from src.engine import death, market
    from src.engine import energy as power

    двор = await world.node_container(session, узел)
    для = {
        "биопринтер": death.PRINTER,
        "администрация": town.HALL,
        "рынок": market.TERMINAL,
        "источник энергии": power.WHEEL,
    }
    for роль, станок in для.items():
        if роль == чего_нет:
            continue
        await world.grant_item(session, двор, станок, quality=60, origin="тест")
    await session.flush()


async def test_город_основывается_игроком(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Разведка нашла место, четыре постройки сделали его городом (D-098)."""
    место, identity, body = await _пустошь(session)
    await _застроить(session, место)

    город = await town.establish(session, constants, catalog, body, "Новоград")

    assert город.name == "Новоград"
    assert город.founder_identity_id == identity.id
    assert await town.of_node(session, место) is not None, (
        "узел-представитель — территория собственного города"
    )
    #: Основатель управляет с первой секунды: город без власти не город.
    assert await town.may(session, identity.id, город, Power.LAWS)
    assert await town.may(session, identity.id, город, Power.TREASURY)


async def test_без_построек_города_не_бывает(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Порог входа — постройки, а не монета (D-023)."""
    место, _, body = await _пустошь(session)
    await _застроить(session, место, чего_нет="биопринтер")

    with pytest.raises(town.NotReady) as отказ:
        await town.establish(session, constants, catalog, body, "Недоград")
    assert "биопринтер" in str(отказ.value), "отказ называет, чего не хватает"
    assert await town.missing_for_foundation(session, место) == ("биопринтер",)


async def test_на_чужой_земле_города_не_закладывают(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    место, _, body = await _пустошь(session)
    await _застроить(session, место)
    _, чужак = await _житель(session, место, "Чужак")

    with pytest.raises(town.NotYours):
        await town.establish(session, constants, catalog, чужак, "Чужеград")


async def test_земля_под_городом_уходит_городу(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Локация становится территорией города, а бумага гасится (D-159)."""
    from sqlalchemy import select

    from src.models.estate import Deed

    место, identity, body = await _пустошь(session)
    await _застроить(session, место)
    бумага = (
        await session.execute(select(Deed).where(Deed.node_id == место.id))
    ).scalar_one_or_none()
    assert бумага is not None, "занятие участка выдаёт бумагу (D-116)"

    город = await town.establish(session, constants, catalog, body, "Новоград")

    assert место.owner_city_id == город.id
    assert место.owner_identity_id is None, "хозяин двора уступил месту власти"
    assert (
        await session.execute(select(Deed).where(Deed.node_id == место.id))
    ).scalar_one_or_none() is None, "городская земля бумагой не торгуется"


async def test_второго_города_на_том_же_узле_не_бывает(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    место, _, body = await _пустошь(session)
    await _застроить(session, место)
    await town.establish(session, constants, catalog, body, "Новоград")

    with pytest.raises(town.CityError):
        await town.establish(session, constants, catalog, body, "Второй")


# --- гражданство (D-160) ----------------------------------------------------


async def test_свободный_город_принимает_сразу(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """`citizenship_admission: open` — записался и гражданин."""
    from src.models.city import Citizen

    город, ядро = await _столица(session, catalog)
    _, тело = await _житель(session, ядро, "Новичок")

    итог = await town.join(session, тело, город)
    assert isinstance(итог, Citizen)
    assert await town.is_citizen(session, тело.identity_id, город)


async def test_гражданство_одно_на_человека(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Двойного гражданства нет: сначала выйти из прежнего города."""
    первый, ядро1 = await _столица(session, catalog)
    второй, ядро2 = await _столица(session, catalog)
    identity, тело = await _житель(session, ядро1, "Перебежчик")
    await town.join(session, тело, первый)

    тело.node_id = ядро2.id
    await session.flush()
    with pytest.raises(town.AlreadyCitizen):
        await town.join(session, тело, второй)


async def test_по_заявке_решает_власть(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """`application` — заявка ложится и ждёт права `citizens`."""
    from src.models.city import Citizen

    город, ядро = await _столица(session, catalog)
    президент, _ = await _житель(session, ядро, "Президент")
    await town.install_founder(session, город, президент)
    город.charter = {**город.charter, town.ADMISSION: town.APPLICATION}
    await session.flush()

    проситель, тело = await _житель(session, ядро, "Проситель")
    заявка = await town.join(session, тело, город)
    assert not isinstance(заявка, Citizen), "сразу не принимают"
    assert not await town.is_citizen(session, проситель.id, город)

    #: Без права `citizens` заявку не одобрить: кадры города — это власть.
    посторонний, _ = await _житель(session, ядро, "Посторонний")
    with pytest.raises(town.NotAllowed):
        await town.admit(session, посторонний, город, проситель)

    await town.admit(session, президент, город, проситель)
    assert await town.is_citizen(session, проситель.id, город)


async def test_по_приглашению_иначе_не_войти(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """`invite` — власть зовёт, человек принимает. Без зова — отказ."""
    город, ядро = await _столица(session, catalog)
    президент, _ = await _житель(session, ядро, "Президент")
    await town.install_founder(session, город, президент)
    город.charter = {**город.charter, town.ADMISSION: town.INVITE}
    await session.flush()

    гость, тело = await _житель(session, ядро, "Гость")
    with pytest.raises(town.NotAllowed):
        await town.join(session, тело, город)

    await town.invite(session, президент, город, гость)
    await town.join(session, тело, город)
    assert await town.is_citizen(session, гость.id, город)


async def test_выход_свободен_но_с_задержкой(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Иначе из города выходят прямо перед приговором (D-160)."""
    from datetime import UTC, datetime, timedelta

    from src.constants import registry as R

    город, ядро = await _столица(session, catalog)
    identity, тело = await _житель(session, ядро, "Уходящий")
    await town.join(session, тело, город)

    ушёл = datetime.now(UTC)
    запись = await town.leave(session, constants, identity, now=ушёл)
    assert запись.leaving_at == ушёл + timedelta(days=constants[R.CITY_EXIT_DELAY])
    assert await town.is_citizen(session, identity.id, город), (
        "до срока человек ещё гражданин"
    )

    #: Срок вышел — задание журнала закрывает гражданство.
    from sqlalchemy import select as _select

    from src.models.job import Job, JobKind, JobState

    задание = (
        await session.execute(
            _select(Job).where(
                Job.kind == JobKind.CITIZENSHIP_EXIT.value,
                Job.state == JobState.PENDING,
            )
        )
    ).scalars().first()
    assert задание is not None
    await town.exited(session, задание)
    assert await town.citizenship(session, identity.id) is None


async def test_изгнание_идёт_по_праву_суда(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Изгнание — санкция, а не кадровое решение: право `justice`."""
    город, ядро = await _столица(session, catalog)
    президент, _ = await _житель(session, ядро, "Президент")
    await town.install_founder(session, город, президент)
    изгой, тело = await _житель(session, ядро, "Изгой")
    await town.join(session, тело, город)

    посторонний, _ = await _житель(session, ядро, "Посторонний")
    with pytest.raises(town.NotAllowed):
        await town.exile(session, посторонний, город, изгой)

    await town.exile(session, президент, город, изгой)
    assert await town.citizenship(session, изгой.id) is None


async def test_город_печатает_за_свой_счёт_только_своим(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """«гражданам» значит гражданам: до D-160 казна платила за чужих."""
    from src.engine import death

    город, ядро = await _столица(session, catalog)
    город.laws = {**город.laws, "body_print": "гражданам"}
    await session.flush()

    гость, _ = await _житель(session, ядро, "Гость")
    свой, тело_своего = await _житель(session, ядро, "Горожанин")
    await town.join(session, тело_своего, город)

    assert not await death._city_pays(session, constants, ядро, гость.id)
    assert await death._city_pays(session, constants, ядро, свой.id)


async def test_землю_города_берут_по_коду_закону(
    session: AsyncSession, catalog: Catalog
) -> None:
    """`build_permit` по умолчанию отдаёт участки гражданам (D-089, D-160)."""
    город, _ = await _столица(session, catalog)
    assert town.may_take_city_land(catalog, город, True)
    assert not town.may_take_city_land(catalog, город, False)

    город.laws = {**город.laws, "build_permit": "все"}
    assert town.may_take_city_land(catalog, город, False), "город вправе открыться"
