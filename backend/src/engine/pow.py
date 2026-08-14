"""Плата устройства — фиксированный объём вычислений на сессию (D-110, D-112).

Что эта плата делает и чего не делает, стоит держать в голове целиком, иначе
её легко «улучшить» до бессмыслицы:

* **делает:** тысяча персонажей означает тысячекратную нагрузку на железо при
  том же выходе. Это налог на масштаб;
* **не делает:** не уравнивает игроков (десктоп считает в 5–20 раз быстрее
  телефона) и не мешает одиночному боту — скрипт на одной машине платит
  столько же, сколько человек на той же машине.

Работа **фиксированная**: не «сколько успел», а один пропуск Argon2id в сессию.
Сложность не подстраивается под слабое устройство — иначе ферма объявит себя
слабой.

Проверка стоит серверу ровно столько же (`pow.verify_cost` — та же оценка),
поэтому старты сессий ограничиваются по частоте на аккаунт.
"""

from __future__ import annotations

import asyncio
import hmac
import secrets
import uuid
from datetime import UTC, datetime

from argon2.low_level import Type, hash_secret_raw
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.models.mining import PowChallenge
from src.runtime import (
    POW_HASH_BYTES,
    POW_NONCE_BYTES,
    POW_PARALLELISM,
    POW_STARTS_PER_WINDOW,
    POW_WINDOW,
)
from src.units import KIB_PER_MIB


class PowError(Exception):
    pass


class WrongAnswer(PowError):
    """Ответ не сходится. Сессию не открываем."""


class TooManyStarts(PowError):
    """Частота стартов превышена: проверка стоит серверу ровно как счёт."""


def solve(constants: Constants, account_id: uuid.UUID, nonce: bytes) -> bytes:
    """Посчитать оценку. Ровно это делает клиент в Web Worker.

    Здесь она нужна дважды: сервер той же функцией проверяет ответ, а тесты
    ею же играют роль клиента.
    """
    return hash_secret_raw(
        secret=account_id.bytes,
        salt=nonce,
        time_cost=int(constants[R.POW_ARGON_ITERATIONS]),
        memory_cost=int(constants[R.POW_MEMORY_PER_SESSION] * KIB_PER_MIB),
        parallelism=POW_PARALLELISM,
        hash_len=POW_HASH_BYTES,
        type=Type.ID,
    )


async def issue(
    session: AsyncSession,
    constants: Constants,
    account_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> PowChallenge:
    """Выдать задачу. Здесь же — единственное ограничение частоты."""
    moment = now or datetime.now(UTC)

    recent = await session.scalar(
        select(func.count())
        .select_from(PowChallenge)
        .where(
            PowChallenge.account_id == account_id,
            PowChallenge.issued_at > moment - POW_WINDOW,
        )
    )
    if recent is not None and recent >= POW_STARTS_PER_WINDOW:
        raise TooManyStarts(
            f"аккаунт {account_id}: {recent} стартов за {POW_WINDOW}, это слишком часто"
        )

    #: Время проставляется явно, а не умолчанием базы: окно частоты и журнал
    #: обязаны смотреть на одни и те же часы.
    challenge = PowChallenge(
        account_id=account_id,
        nonce=secrets.token_bytes(POW_NONCE_BYTES),
        issued_at=moment,
    )
    session.add(challenge)
    await session.flush()
    return challenge


async def verify(
    session: AsyncSession,
    constants: Constants,
    challenge: PowChallenge,
    answer: bytes,
    *,
    now: datetime | None = None,
) -> None:
    """Проверить ответ той же оценкой (`pow.verify_cost`).

    Счёт идёт в отдельном потоке: 64 МБ и три прохода блокируют цикл событий
    ощутимо дольше, чем допустимо для сервера с чатом и торгами.
    """
    if challenge.spent_on_session_id is not None or challenge.solved_at is not None:
        raise WrongAnswer("задача уже предъявлена: платить надо за каждую сессию")

    expected = await asyncio.to_thread(solve, constants, challenge.account_id, challenge.nonce)
    #: Сравнение постоянного времени: ответ приходит от недоверенной стороны.
    if not hmac.compare_digest(expected, answer):
        raise WrongAnswer("оценка не сходится")

    challenge.solved_at = now or datetime.now(UTC)
    await session.flush()
