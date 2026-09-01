# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Veins on the move: the measure against a staked claim (D-197).

A share of the shaken veins goes out and lights up next door, and whoever was
working one stops working it -- through `mining.leave`, so the ore is carried
out rather than entombed.
"""

from __future__ import annotations

import random
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine.plates._base import _adjacency, _exempt, _surface
from src.models.identity import Body, BodyState
from src.models.mining import MiningSession, SessionState
from src.models.world import Node, Vein


async def _move_veins(
    session: AsyncSession,
    constants: Constants,
    dice: random.Random,
    shaken: list[Node],
    *,
    now: datetime,
) -> int:
    """A share of the shaken veins goes out, and as many light up next door.

    The measure against a staked claim (D-197): the vein leaves by itself. It
    lights up in a **neighbour** rather than anywhere, so the map keeps meaning
    something -- what moves is the claim, not the geography.

    Whoever was working it stops working it: matter is worked in person (D-044),
    and a face two passes away is not the face under this pick.
    """
    ids = [node.id for node in shaken]
    veins = (
        (
            await session.execute(
                select(Vein).where(Vein.node_id.in_(ids)).order_by(Vein.id).with_for_update()
            )
        )
        .scalars()
        .all()
    )
    share = constants[R.PYROXIS_VEIN_RELOCATE_SHARE]
    ways = await _adjacency(session)
    #: The exempt ground is not a destination either. The plateau is never
    #: shaken (`clock._choose`), so a vein that moved onto it would stay there
    #: for ever -- the one claim on the planet nothing can ever take away,
    #: which is the whole thing this machinery exists against (D-197). The
    #: ground under a docked ship is out for the same reason it is out of the
    #: draw: what stands there is not touched by the event.
    spared = await _exempt(session)
    places = {node.id: node for node in await _surface(session) if node.id not in spared}
    moved = 0
    for vein in veins:
        if dice.random() > share:
            continue
        neighbours = [places[one] for one in ways.get(vein.node_id, set()) if one in places]
        if not neighbours:
            continue
        await _close_faces(session, constants, vein, now=now)
        vein.node_id = dice.choice(neighbours).id
        moved += 1
    await session.flush()
    return moved


async def _close_faces(
    session: AsyncSession, constants: Constants, vein: Vein, *, now: datetime
) -> None:
    """End the sessions at a vein about to move out from under them.

    Through `mining.leave`, not by writing the state by hand: leaving a face is
    what carries the ore out of it into the pocket, wears the tool for the
    session and tells the journal. Set by hand, the session would close with the
    haul still lying in a container nobody will ever open again -- the ground
    moved, and that is not the miner's mistake (D-143).

    Called **before** the vein moves, so the ore is carried out of the face
    where it was actually mined.

    The session rows go first and nothing shared is held before them (the
    caller holds only the veins): they are the gate this path shares with
    `death.die`, whose first lock is the same row through `mining.abandon`.
    Held-then-wanted the other way -- the death holding the pocket these
    faces' hauls land in, this path holding the session -- the two deadlocked
    (ABBA); at the gate the loser waits holding nothing the winner could want.
    """
    from src.engine import mining  # noqa: PLC0415 -- lazy: breaks the cycle with mining

    working = (
        (
            await session.execute(
                select(MiningSession)
                .where(
                    MiningSession.vein_id == vein.id,
                    MiningSession.state == SessionState.ACTIVE,
                )
                .order_by(MiningSession.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    for face in working:
        #: Reread after the lock, like everything else taken under one. The
        #: other end of this race is `mining.abandon`, which closes the same
        #: session when the miner dies; whichever gets the row first finishes,
        #: and the second must see a closed session rather than a stale copy.
        if face.state is not SessionState.ACTIVE:  # pragma: no cover -- closed under the lock
            continue
        body = await session.get(Body, face.body_id)
        if body is None or body.state is not BodyState.ALIVE:
            #: A session open at a dead body: worlds that ran before
            #: `mining.abandon` existed have them, and `leave` would carry the
            #: ore into a pocket nobody will ever open. Closed the way a death
            #: closes it -- the haul stays lying in the node.
            if body is not None:
                await mining.abandon(session, body, now=now)
            continue
        await mining.leave(session, constants, face, now=now)
