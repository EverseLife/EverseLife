# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The memory of places: what an identity has been to (D-319, п. 6).

Memory instead of fog. A place one has stood in stays on one's map after one
has left it -- darker than what is in sight, but there, with its ways and its
signs as the world knows them now. It is **knowledge** (`Knowledge`, kind
`place`): bound to the identity, not to the body, and taken away neither by
death, nor by court, nor by city (invariant I8).

Written by the arrival job and by a scout's return, never by a read (`look`
does not write). The record carries the moment of the last visit; a second
visit renews it. Above the ceiling `map.memory_places` the oldest is
forgotten -- the home, the plot and the city alike, one rule without pins
(OQ-142): recency decides and nothing else.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.models.identity import Knowledge, KnowledgeKind


async def remember(
    session: AsyncSession,
    constants: Constants,
    identity_id: uuid.UUID,
    keys: Iterable[str],
    *,
    at: datetime,
    cap: int | None = None,
) -> None:
    """Write the places into the identity's memory, renewing what is already there,
    and forget the oldest beyond the ceiling.

    One statement to write, one to forget. The write is an upsert on the
    identity-kind-key unique, and the **later** visit wins whichever job fires
    first -- a retried job with an old moment must not age a fresh memory into
    the first to be forgotten. The forgetting is a `DELETE` over the rows past
    the ceiling; under two jobs of one identity in the same instant it is
    serialised by the lock every caller already holds on the body
    (`walk.arrive`, `explore.returned` take the row `FOR UPDATE`), not by this
    statement -- and a ceiling one over for a moment is a count of memories,
    not money: the next arrival trims it.
    """
    rows = [
        {
            "id": uuid.uuid4(),
            "identity_id": identity_id,
            "kind": KnowledgeKind.PLACE,
            "key": key,
            "discovered": False,
            "acquired_at": at,
        }
        for key in dict.fromkeys(keys)
    ]
    if not rows:
        return
    stmt = insert(Knowledge).values(rows)
    await session.execute(
        stmt.on_conflict_do_update(
            constraint="uq_knowledge_identity_key",
            set_={"acquired_at": func.greatest(Knowledge.acquired_at, stmt.excluded.acquired_at)},
        )
    )
    ceiling = int(constants[R.MAP_MEMORY_PLACES]) if cap is None else cap
    #: The rows past the ceiling, oldest first: the newest `ceiling` stay.
    beyond = (
        select(Knowledge.id)
        .where(Knowledge.identity_id == identity_id, Knowledge.kind == KnowledgeKind.PLACE)
        .order_by(Knowledge.acquired_at.desc(), Knowledge.id)
        .offset(ceiling)
    )
    await session.execute(delete(Knowledge).where(Knowledge.id.in_(beyond)))


async def known(session: AsyncSession, identity_id: uuid.UUID) -> set[str]:
    """The keys of every place the identity remembers."""
    rows = await session.execute(
        select(Knowledge.key).where(
            Knowledge.identity_id == identity_id, Knowledge.kind == KnowledgeKind.PLACE
        )
    )
    return set(rows.scalars())
