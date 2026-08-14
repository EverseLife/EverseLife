"""Плата устройства (D-110, D-112, D-113).

Тесты гоняют Argon2id на **дешёвых** параметрах — через тот самый механизм
правок, которым админ-панель меняет баланс без выката версии. Заодно это
проверка, что горячая подмена констант работает на живом коде, а не только
в тесте загрузчика.

Боевые `pow.*` не трогаются: 64 МБ и три прохода на каждый вызов сделали бы
набор тестов непригодным для запуска на каждом коммите — а это ровно та цена,
которую платит ферма, и в этом весь смысл платы.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.engine import pow as device
from src.engine import world
from src.models.identity import Account
from src.runtime import POW_STARTS_PER_WINDOW, POW_WINDOW

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def cheap(constants: Constants) -> Constants:
    """Та же задача, но по карману набору тестов."""
    return constants.with_overrides({"pow.memory_per_session": 8, "pow.argon_iterations": 1})


async def _аккаунт(session: AsyncSession) -> Account:
    identity = await world.create_identity(session, f"Игрок-{uuid.uuid4().hex[:8]}")
    account = await session.get(Account, identity.account_id)
    assert account is not None
    return account


async def test_верный_ответ_открывает_сессию(session: AsyncSession, cheap: Constants) -> None:
    account = await _аккаунт(session)
    задача = await device.issue(session, cheap, account.id, now=NOW)

    #: Ровно это клиент считает в Web Worker, не блокируя интерфейс.
    ответ = device.solve(cheap, account.id, задача.nonce)
    await device.verify(session, cheap, задача, ответ, now=NOW)

    assert задача.solved_at == NOW


async def test_чужой_ответ_не_проходит(session: AsyncSession, cheap: Constants) -> None:
    account = await _аккаунт(session)
    задача = await device.issue(session, cheap, account.id, now=NOW)

    with pytest.raises(device.WrongAnswer):
        await device.verify(session, cheap, задача, b"\x00" * 32, now=NOW)


async def test_счёт_привязан_к_аккаунту(session: AsyncSession, cheap: Constants) -> None:
    """Иначе ферма считала бы один раз и предъявляла ответ тысячей персонажей."""
    первый = await _аккаунт(session)
    второй = await _аккаунт(session)
    задача = await device.issue(session, cheap, первый.id, now=NOW)

    чужой = device.solve(cheap, второй.id, задача.nonce)
    with pytest.raises(device.WrongAnswer):
        await device.verify(session, cheap, задача, чужой, now=NOW)


async def test_задача_одноразовая(session: AsyncSession, cheap: Constants) -> None:
    """Платить надо за каждую сессию: работа фиксированная, а не «сколько успел»."""
    account = await _аккаунт(session)
    задача = await device.issue(session, cheap, account.id, now=NOW)
    ответ = device.solve(cheap, account.id, задача.nonce)

    await device.verify(session, cheap, задача, ответ, now=NOW)
    with pytest.raises(device.WrongAnswer, match="уже предъявлена"):
        await device.verify(session, cheap, задача, ответ, now=NOW)


async def test_задачи_разные_каждый_раз(session: AsyncSession, cheap: Constants) -> None:
    account = await _аккаунт(session)
    nonces = {
        (await device.issue(session, cheap, account.id, now=NOW)).nonce for _ in range(5)
    }
    assert len(nonces) == 5


async def test_частота_стартов_ограничена(session: AsyncSession, cheap: Constants) -> None:
    """Проверка стоит серверу столько же, сколько счёт клиенту (`pow.verify_cost`)."""
    account = await _аккаунт(session)
    for _ in range(POW_STARTS_PER_WINDOW):
        await device.issue(session, cheap, account.id, now=NOW)

    with pytest.raises(device.TooManyStarts):
        await device.issue(session, cheap, account.id, now=NOW)

    #: Окно скользит: за его пределами счётчик уже не мешает.
    позже = NOW + POW_WINDOW + timedelta(minutes=1)
    assert await device.issue(session, cheap, account.id, now=позже)


async def test_сложность_не_подстраивается_под_устройство(constants: Constants) -> None:
    """Иначе ферма объявит себя слабой (01-tech-notes).

    Параметры счёта берутся из констант и одинаковы для телефона и для сервера:
    в подписи `solve` нет ни одного входа про устройство.
    """
    import inspect

    параметры = set(inspect.signature(device.solve).parameters)
    assert параметры == {"constants", "account_id", "nonce"}
