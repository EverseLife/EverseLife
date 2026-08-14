"""Скелет мира: узел, личность, тело, имущество, знание.

Проверяется главное различие всей модели: **знание живёт в личности, имущество —
в теле** (D-011, D-012, D-033). Из него следует всё поведение при смерти, и
ошибиться здесь дороже, чем где-либо ещё.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import world
from src.models.event import Event
from src.models.identity import Knowledge
from src.models.inventory import Item
from src.units import amount_float


async def _обжитый_узел(session: AsyncSession):
    node = await world.create_node(session, "terra.capital", "Столица", area_m2=200)
    identity = await world.create_identity(session, "Тэрн")
    body = await world.print_body(session, identity, node)
    return node, identity, body


async def test_тело_печатается_с_полной_выносливостью(
    session: AsyncSession, constants: Constants
) -> None:
    _, _, body = await _обжитый_узел(session)
    await session.commit()
    assert float(body.stamina) == constants[R.BODY_STAMINA_MAX]


async def test_у_тела_есть_инвентарь_с_рождения(session: AsyncSession) -> None:
    _, _, body = await _обжитый_узел(session)
    container = await world.body_container(session, body)
    assert container.owner_id == body.id


async def test_знание_копируется_один_раз(session: AsyncSession) -> None:
    """Библиотека не отказывает, но и второй копии в голове не заводит (D-053)."""
    _, identity, _ = await _обжитый_узел(session)

    assert await world.learn(session, identity, "Гвозди") is not None
    assert await world.learn(session, identity, "Гвозди") is None
    await session.commit()

    total = await session.scalar(
        select(func.count()).select_from(Knowledge).where(Knowledge.identity_id == identity.id)
    )
    assert total == 1


async def test_появление_предмета_обязано_иметь_основание(session: AsyncSession) -> None:
    """Материя не создаётся из ничего: у любого прихода есть названный источник (И1)."""
    node, identity, body = await _обжитый_узел(session)
    container = await world.body_container(session, body)

    await world.grant_item(
        session, container, "Железная руда", amount=12.5, quality=60, origin="сценарий отладки"
    )
    await session.commit()

    item = (await session.execute(select(Item))).scalar_one()
    assert item.type_key == "Железная руда"
    assert amount_float(item.amount) == 12.5
    assert float(item.condition) == float(item.condition_cap)

    появление = (
        await session.execute(select(Event).where(Event.kind == "item.created"))
    ).scalar_one()
    assert появление.payload["origin"] == "сценарий отладки"


async def test_каждое_изменение_мира_попадает_в_журнал(session: AsyncSession) -> None:
    await _обжитый_узел(session)
    await session.commit()

    kinds = set(
        (await session.execute(select(Event.kind))).scalars().all()
    )
    assert {"identity.created", "body.printed"} <= kinds


async def test_событие_помнит_на_каких_числах_произошло(
    session: AsyncSession, constants: Constants
) -> None:
    """Разбор старого эпизода после правки баланса иначе ничего не доказывает (D-065)."""
    await _обжитый_узел(session)
    await session.commit()

    event = (
        await session.execute(select(Event).where(Event.kind == "body.printed"))
    ).scalar_one()
    assert event.constants_digest == constants.digest
