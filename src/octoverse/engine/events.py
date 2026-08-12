"""Запись в журнал событий.

Событие пишется **в той же транзакции**, что и его последствия. Иначе журнал
рассинхронизируется с миром ровно в тот момент, когда он нужнее всего —
при разборе спорной ситуации.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from octoverse.constants import HOLDER
from octoverse.models.event import Event, EventKind


async def record(
    session: AsyncSession,
    kind: EventKind | str,
    *,
    actor_identity_id: uuid.UUID | None = None,
    node_id: uuid.UUID | None = None,
    **payload: Any,
) -> Event:
    """Записать событие. Возвращает объект — на него ссылаются проводки."""
    event = Event(
        kind=str(kind),
        actor_identity_id=actor_identity_id,
        node_id=node_id,
        payload=payload,
        constants_digest=HOLDER.current().digest if HOLDER.is_loaded() else None,
    )
    session.add(event)
    #: flush, а не commit: событие обязано получить id внутри общей транзакции,
    #: но фиксируется вместе со своими последствиями.
    await session.flush()
    return event
