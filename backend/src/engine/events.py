"""Writing to the event journal.

An event is written **in the same transaction** as its consequences.
Otherwise the journal desynchronises from the world exactly when it is needed
most -- when examining a disputed situation.
"""

from __future__ import annotations

import uuid
from typing import Any

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
