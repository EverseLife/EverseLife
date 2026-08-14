"""Носимое: масса, предел и слоты снаряжения (D-146).

Предел носимого был записан константой с самого начала, но у предметов не было
массы — и игрок носил в кармане тысячу руды. Проверяется то, ради чего масса
вводилась:

* у предмета есть вес, и он приходит из данных вольта, а не из кода;
* в руки не берут больше предела — ни с рынка, ни из бункера;
* рюкзак и экзоскелет предел поднимают, одежда и броня — нет;
* слот один на вещь: три рюкзака не наденешь, иначе предела не существует.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import gear, world

BACKPACK = "Рюкзак"
EXO = "Экзоскелет"
CLOTHES = "Одежда"
ARMOUR = "Броня"


async def _тело(session: AsyncSession):
    метка = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.gear.{метка}", "Узел", area_m2=100)
    identity = await world.create_identity(session, f"Носильщик-{метка}")
    body = await world.print_body(session, identity, node)
    return node, identity, body


async def _дать(session: AsyncSession, body, что: str, сколько: float = 1):
    карман = await world.body_container(session, body)
    return await world.grant_item(
        session, карман, что, amount=сколько, quality=60, origin="тест"
    )


# --- масса ------------------------------------------------------------------


def test_масса_приходит_из_данных(catalog: Catalog) -> None:
    """Вес — содержание вольта, а не число в коде (D-065, D-146)."""
    книга = catalog.recipes
    assert книга.mass_of(BACKPACK) > 0
    assert книга.mass_of(EXO) > книга.mass_of(BACKPACK), "экзоскелет тяжелее рюкзака"
    #: Незнакомое имя массы не имеет: дыра должна быть видна, а не занулена.
    assert книга.mass_of("Философский камень") == 0


async def test_нагрузка_считает_всё_включая_надетое(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Экзоскелет не становится невесомым оттого, что его надели."""
    _, _, body = await _тело(session)
    рюкзак = await _дать(session, body, BACKPACK)
    await gear.equip(session, constants, catalog, body, рюкзак)

    несёт = await gear.load_of(session, catalog, body)
    assert несёт == pytest.approx(catalog.recipes.mass_of(BACKPACK))


# --- предел -----------------------------------------------------------------


async def test_больше_предела_в_руки_не_берут(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Не сообщение об ошибке, а причина существования повозок."""
    _, _, body = await _тело(session)
    предел = constants[R.INVENTORY_CARRY_MASS]
    камень = "Камень"
    сколько = предел / catalog.recipes.mass_of(камень) + 1

    with pytest.raises(gear.Overloaded):
        await gear.check_carry(session, constants, catalog, body, камень, сколько)


async def test_рюкзак_поднимает_предел_а_одежда_нет(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Слот занимают оба, но переносимое добавляет только носильное."""
    _, _, body = await _тело(session)
    базовый = await gear.capacity(session, constants, catalog, body)
    assert базовый == pytest.approx(constants[R.INVENTORY_CARRY_MASS])

    одежда = await _дать(session, body, CLOTHES)
    await gear.equip(session, constants, catalog, body, одежда)
    assert await gear.capacity(session, constants, catalog, body) == pytest.approx(
        базовый
    ), "одежда переносимого не добавляет"

    рюкзак = await _дать(session, body, BACKPACK)
    await gear.equip(session, constants, catalog, body, рюкзак)
    бонусы = constants[R.INVENTORY_CARRY_BONUS]
    assert await gear.capacity(session, constants, catalog, body) == pytest.approx(
        базовый + бонусы[BACKPACK]
    )


async def test_экзоскелет_поднимает_сильнее_рюкзака(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Капитал в снаряжении: экзоскелет — верх ветки носимого."""
    бонусы = constants[R.INVENTORY_CARRY_BONUS]
    assert бонусы[EXO] > бонусы[BACKPACK]

    _, _, body = await _тело(session)
    рюкзак = await _дать(session, body, BACKPACK)
    экзо = await _дать(session, body, EXO)
    await gear.equip(session, constants, catalog, body, рюкзак)
    await gear.equip(session, constants, catalog, body, экзо)

    #: Слоты разные — спина и каркас, — значит работают оба.
    assert await gear.capacity(session, constants, catalog, body) == pytest.approx(
        constants[R.INVENTORY_CARRY_MASS] + бонусы[BACKPACK] + бонусы[EXO]
    )


# --- слоты ------------------------------------------------------------------


async def test_слот_один_на_вещь(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Иначе игрок надевает три рюкзака и предела больше не существует."""
    _, _, body = await _тело(session)
    первый = await _дать(session, body, BACKPACK)
    второй = await _дать(session, body, BACKPACK)

    await gear.equip(session, constants, catalog, body, первый)
    await gear.equip(session, constants, catalog, body, второй)

    надето = await gear.equipped(session, body)
    assert list(надето) == ["спина"], "в слоте одна вещь"
    assert надето["спина"].id == второй.id, "новая вытеснила прежнюю"
    бонусы = constants[R.INVENTORY_CARRY_BONUS]
    assert await gear.capacity(session, constants, catalog, body) == pytest.approx(
        constants[R.INVENTORY_CARRY_MASS] + бонусы[BACKPACK]
    ), "два рюкзака не складываются"


async def test_броня_и_одежда_делят_один_слот(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Тело одно: надел броню — снял одежду."""
    _, _, body = await _тело(session)
    одежда = await _дать(session, body, CLOTHES)
    броня = await _дать(session, body, ARMOUR)

    await gear.equip(session, constants, catalog, body, одежда)
    await gear.equip(session, constants, catalog, body, броня)
    надето = await gear.equipped(session, body)
    assert надето["тело"].type_key == ARMOUR


async def test_не_снаряжение_не_надевается(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Слот берётся из данных: у кирки его нет, и надеть её нельзя."""
    _, _, body = await _тело(session)
    кирка = await _дать(session, body, "Железная кирка")
    with pytest.raises(gear.NotGear):
        await gear.equip(session, constants, catalog, body, кирка)


async def test_снятое_перестаёт_поднимать_предел(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, _, body = await _тело(session)
    рюкзак = await _дать(session, body, BACKPACK)
    await gear.equip(session, constants, catalog, body, рюкзак)
    снято = await gear.unequip(session, body, "спина")

    assert снято is not None and снято.id == рюкзак.id
    assert await gear.capacity(session, constants, catalog, body) == pytest.approx(
        constants[R.INVENTORY_CARRY_MASS]
    )
