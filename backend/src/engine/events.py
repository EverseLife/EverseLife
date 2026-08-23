# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Writing to the event journal.

An event is written **in the same transaction** as its consequences.
Otherwise the journal desynchronises from the world exactly when it is needed
most -- when examining a disputed situation.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import HOLDER
from src.models.event import Event, EventKind


async def record(
    session: AsyncSession,
    kind: EventKind | str,
    *,
    actor_identity_id: uuid.UUID | None = None,
    node_id: uuid.UUID | None = None,
    **payload: Any,
) -> Event:
    """Record an event. Returns the object -- postings reference it."""
    event = Event(
        kind=str(kind),
        actor_identity_id=actor_identity_id,
        node_id=node_id,
        payload=payload,
        constants_digest=HOLDER.current().digest if HOLDER.is_loaded() else None,
    )
    session.add(event)
    #: flush, not commit: the event must get an id inside the shared
    #: transaction but is committed together with its consequences.

    await session.flush()
    return event


async def announce(
    session: AsyncSession,
    *,
    touches: tuple[str, ...] | list[str],
    identity_id: uuid.UUID | None = None,
    node_id: uuid.UUID | None = None,
    event: str | None = None,
    who: str | None = None,
    **extra: Any,
) -> None:
    """Tell a player, or everyone in a node, that something changed -- for
    what the journal does not record (D-226): room talk has no history (D-070)
    but the room must still hear that a line was said.

    `extra` rides along to the client as is -- the line itself for room
    talk -- so the room can show it without asking. Keep it to what the
    recipient could have seen by asking (8-session-protocol).

    Goes out with the transaction's commit, like an event; nothing if it rolls
    back. The API process listens on the `touch` channel (`api/push.py`).
    NOTIFY payloads are capped at 8000 bytes; a line is far below that.
    """
    note = {
        "touches": list(touches),
        "identity_id": None if identity_id is None else str(identity_id),
        "node_id": None if node_id is None else str(node_id),
        "event": event,
        "who": who,
        **extra,
    }
    await session.execute(
        text("SELECT pg_notify('touch', :note)"), {"note": json.dumps(note, ensure_ascii=False)}
    )
