# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The device fee (D-110, D-112, D-113).

The tests run Argon2id on **cheap** parameters -- through the very edit
mechanism by which the admin panel changes balance without a release. That
also checks that hot constant override works on live code, not only in the
loader test.

Production `pow.*` is left alone: 64 MB and three passes per call would make
the test suite unfit for running on every commit -- and that is exactly the
price a farm pays, and the whole point of the fee.
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
    """The same challenge, but affordable for the test suite."""
    return constants.with_overrides({"pow.memory_per_session": 8, "pow.argon_iterations": 1})


async def _account_(session: AsyncSession) -> Account:
    identity = await world.create_identity(session, f"Игрок-{uuid.uuid4().hex[:8]}")
    account = await session.get(Account, identity.account_id)
    assert account is not None
    return account


async def test_correct_answer_opens_session(session: AsyncSession, cheap: Constants) -> None:
    account = await _account_(session)
    task = await device.issue(session, cheap, account.id, now=NOW)

    #: Exactly this the client computes in a Web Worker without blocking the interface.
    answer = device.solve(cheap, account.id, task.nonce)
    await device.verify(session, cheap, task, answer, now=NOW)

    assert task.solved_at == NOW


async def test_foreign_answer_does_not_pass(session: AsyncSession, cheap: Constants) -> None:
    account = await _account_(session)
    task = await device.issue(session, cheap, account.id, now=NOW)

    with pytest.raises(device.WrongAnswer):
        await device.verify(session, cheap, task, b"\x00" * 32, now=NOW)


async def test_account_bound_to_login(session: AsyncSession, cheap: Constants) -> None:
    """Otherwise a farm would compute once and present the answer with a thousand characters."""
    first = await _account_(session)
    second = await _account_(session)
    task = await device.issue(session, cheap, first.id, now=NOW)

    foreign = device.solve(cheap, second.id, task.nonce)
    with pytest.raises(device.WrongAnswer):
        await device.verify(session, cheap, task, foreign, now=NOW)


async def test_challenge_is_single_use(session: AsyncSession, cheap: Constants) -> None:
    """Every session must be paid for: the work is fixed, not "as much as you managed"."""
    account = await _account_(session)
    task = await device.issue(session, cheap, account.id, now=NOW)
    answer = device.solve(cheap, account.id, task.nonce)

    await device.verify(session, cheap, task, answer, now=NOW)
    with pytest.raises(device.WrongAnswer, match="уже предъявлена"):
        await device.verify(session, cheap, task, answer, now=NOW)


async def test_challenges_differ_each_time(session: AsyncSession, cheap: Constants) -> None:
    account = await _account_(session)
    nonces = {
        (await device.issue(session, cheap, account.id, now=NOW)).nonce for _ in range(5)
    }
    assert len(nonces) == 5


async def test_start_rate_limited(session: AsyncSession, cheap: Constants) -> None:
    """Verification costs the server as much as the fee costs the client (`pow.verify_cost`)."""
    account = await _account_(session)
    for _ in range(POW_STARTS_PER_WINDOW):
        await device.issue(session, cheap, account.id, now=NOW)

    with pytest.raises(device.TooManyStarts):
        await device.issue(session, cheap, account.id, now=NOW)

    #: The window slides: beyond it the counter no longer interferes.
    later = NOW + POW_WINDOW + timedelta(minutes=1)
    assert await device.issue(session, cheap, account.id, now=later)


async def test_difficulty_does_not_adapt_to_device(constants: Constants) -> None:
    """Otherwise a farm would declare itself weak (01-tech-notes).

    The computation parameters come from constants and are the same for a
    phone and for the server: `solve`'s signature has not a single input about the device.
    """

    import inspect

    params = set(inspect.signature(device.solve).parameters)
    assert params == {"constants", "account_id", "nonce"}
