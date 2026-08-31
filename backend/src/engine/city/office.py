# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Offices and the powers they carry.

Power in a city is an **office**, not an intention: somebody holds a post, the
post carries named powers (D-134), and every guarded action asks `require`
rather than asking who the player is. That indirection is the point -- it is
what lets a charter move a power from the ruler to the council without
touching the code that spends money or hands out land.

This module does not depend on the `law` module or on `citizen` -- though
`covers` does know how a law-scoped power is *shaped* (`law:<id>`), which is
not the same thing. It is the floor of the city's stack for that reason:
everything else here asks it who may do a thing, and it asks nobody. The
contract in `pyproject.toml` is what keeps that true.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.engine import events
from src.engine.city._base import (
    CityError,
    NotAllowed,
)
from src.engine.city.hall import require_at_hall
from src.models.city import (
    LAW_SCOPE,
    City,
    Office,
    Power,
)
from src.models.event import EventKind
from src.models.identity import Identity


async def _office(
    session: AsyncSession,
    city: City,
    identity_id: uuid.UUID,
    *,
    title: str,
    powers: tuple[str, ...],
    by: uuid.UUID | None,
) -> Office:
    office = Office(
        city_id=city.id,
        identity_id=identity_id,
        title=title,
        powers=list(powers),
        appointed_by_identity_id=by,
    )
    session.add(office)
    await session.flush()
    return office


async def offices(session: AsyncSession, city: City) -> list[Office]:
    """The city's current offices. Vacated ones stay in the journal but not here."""
    rows = (
        (
            await session.execute(
                select(Office).where(Office.city_id == city.id, Office.revoked_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def powers_of(session: AsyncSession, identity_id: uuid.UUID, city: City) -> set[str]:
    """This identity's rights in this city, as strings (D-155).

    A right can be broad (`treasury`) or narrow (`law:import_duty`). The engine
    stores them the same way -- as a string -- because the list of specific laws
    comes from the vault and is not in code.
    """
    found: set[str] = set()
    for office in await offices(session, city):
        if office.identity_id != identity_id:
            continue
        found.update(str(raw) for raw in office.powers or ())
    return found


def covers(held: set[str], needed: str) -> bool:
    """Whether the set of rights covers the required one. `laws` covers any `law:<id>`."""
    if needed in held:
        return True
    return needed.startswith(LAW_SCOPE) and Power.LAWS.value in held


async def may(
    session: AsyncSession, identity_id: uuid.UUID, city: City, power: Power | str
) -> bool:
    needed = power.value if isinstance(power, Power) else str(power)
    return covers(await powers_of(session, identity_id, city), needed)


async def require(
    session: AsyncSession, identity_id: uuid.UUID, city: City, power: Power | str
) -> None:
    needed = power.value if isinstance(power, Power) else str(power)
    if not await may(session, identity_id, city, needed):
        raise NotAllowed(key="city-no-power", power=needed, city=city.name)


async def appoint(
    session: AsyncSession,
    by: Identity,
    city: City,
    whom: Identity,
    *,
    title: str,
    powers: tuple[str, ...],
    body=None,
) -> Office:
    """Appoint to an office. In person: decisions are made in the town hall (D-155).

    Only what you have yourself can be given -- with coverage in mind: a holder
    of `laws` may grant `law:toll`, a holder of `law:toll` may not. Otherwise
    anyone given `offices` would appoint themselves everything else.
    """
    await require_at_hall(session, body, city)
    await require(session, by.id, city, Power.OFFICES)
    own_items = await powers_of(session, by.id, city)
    extra = {right for right in powers if not covers(own_items, right)}
    if extra:
        raise NotAllowed(key="city-powers-not-own", extra=", ".join(sorted(extra)))
    if not powers:
        raise CityError(key="city-office-no-powers")

    #: Re-appointment rewrites the office rather than creating a second one.
    for prior in await offices(session, city):
        if prior.identity_id == whom.id:
            prior.revoked_at = datetime.now(UTC)
    await session.flush()

    office = await _office(session, city, whom.id, title=title, powers=tuple(powers), by=by.id)
    await events.record(
        session,
        EventKind.CITY_OFFICE_APPOINTED,
        actor_identity_id=by.id,
        node_id=city.node_id,
        city_id=str(city.id),
        whom=whom.name,
        whom_identity_id=str(whom.id),
        title=title,
        powers=list(powers),
    )
    return office


async def revoke(
    session: AsyncSession, by: Identity, city: City, office: Office, *, body=None
) -> Office:
    """Vacate an office. The founder cannot be removed: that is the charter's business, not the
    engine's."""
    await require_at_hall(session, body, city)
    await require(session, by.id, city, Power.OFFICES)
    if office.city_id != city.id:
        raise CityError(key="city-office-other-city")
    if office.identity_id == city.founder_identity_id:
        raise NotAllowed(key="city-founder-not-dismissed")
    office.revoked_at = datetime.now(UTC)
    await session.flush()

    await events.record(
        session,
        EventKind.CITY_OFFICE_REVOKED,
        actor_identity_id=by.id,
        node_id=city.node_id,
        city_id=str(city.id),
        office_id=str(office.id),
        whom_identity_id=str(office.identity_id),
    )
    return office
