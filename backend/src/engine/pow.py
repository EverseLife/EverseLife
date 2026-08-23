# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The device fee -- a fixed amount of computation per session (D-110, D-112).

What this fee does and does not do is worth keeping in mind whole, otherwise
it is easy to "improve" it into nonsense:

* **does:** a thousand characters means a thousandfold load on hardware for
  the same output. It is a tax on scale;
* **does not:** it does not equalise players (a desktop computes 5-20 times
  faster than a phone) and does not stop a solitary bot -- a script on one
  machine pays as much as a human on the same machine.

The work is **fixed**: not "as much as you managed" but one Argon2id pass per
session. Difficulty does not adapt to a weak device -- otherwise a farm would
declare itself weak.

Verification costs the server exactly as much (`pow.verify_cost` is the same
estimate), so session starts are rate-limited per account.
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
from src.engine.errors import Refusal
from src.models.mining import PowChallenge
from src.runtime import (
    POW_HASH_BYTES,
    POW_NONCE_BYTES,
    POW_PARALLELISM,
    POW_STARTS_PER_WINDOW,
    POW_WINDOW,
)
from src.units import KIB_PER_MIB


class PowError(Refusal):
    pass


class WrongAnswer(PowError):
    """The answer does not match. The session is not opened."""


class TooManyStarts(PowError):
    """Start rate exceeded: verification costs the server exactly as much as the fee."""


def solve(constants: Constants, account_id: uuid.UUID, nonce: bytes) -> bytes:
    """Compute the estimate. This is exactly what the client does in a Web Worker.

    It is needed here twice: the server checks the answer with the same
    function, and tests play the client's role with it.
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
    """Issue a challenge. The only rate limit is here too."""
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

    #: The time is set explicitly rather than by the database default: the
    #: rate window and the journal must look at the same clock.
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
    """Verify the answer with the same estimate (`pow.verify_cost`).

    The computation runs in a separate thread: 64 MB and three passes block
    the event loop noticeably longer than is acceptable for a server with chat
    and trading.
    """
    if challenge.spent_on_session_id is not None or challenge.solved_at is not None:
        raise WrongAnswer("задача уже предъявлена: платить надо за каждую сессию")

    expected = await asyncio.to_thread(solve, constants, challenge.account_id, challenge.nonce)
    #: Constant-time comparison: the answer comes from an untrusted party.
    if not hmac.compare_digest(expected, answer):
        raise WrongAnswer("оценка не сходится")

    challenge.solved_at = now or datetime.now(UTC)
    await session.flush()
