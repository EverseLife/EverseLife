# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The first step of registration asks whether the email is free (D-187):
refused where it is typed, in the words `join` would use, not at the door."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.registry import Refused
from src.api.session import _join_check
from src.engine import world
from src.engine.account import AccountError


async def test_a_free_email_passes_and_a_taken_one_is_refused_as_at_the_door(
    session: AsyncSession,
) -> None:
    stamp = uuid.uuid4().hex[:8]
    address = f"newcomer-{stamp}@example.com"
    assert await _join_check(session, {"email": address}) == {"free": True}
    await world.create_identity(session, f"Taken-{stamp}", email=address, password="kirka-i-krep")
    with pytest.raises(Refused) as refused:
        await _join_check(session, {"email": address.upper()})
    assert refused.value.key == "cmd-email-taken"


async def test_a_bad_address_is_refused_before_it_is_looked_up(session: AsyncSession) -> None:
    with pytest.raises(AccountError) as refused:
        await _join_check(session, {"email": "not an address"})
    assert refused.value.key == "account-bad-email"
