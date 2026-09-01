# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The collapse: how a session ends badly (D-143, D-111).

Stability at zero costs **everything mined during the session** -- that is
the stake, growing as things go -- plus tool wear and, by the vault's two
rolls, a wound or a death. The environment is the only source of death in
the alpha (D-111), and this module is where it lives.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import death, events, luck
from src.engine.mining._base import (
    Sight,
    _sight,
    _tool,
    _wear_tool_for_session,
    remember_roof,
    session_container,
)
from src.models.event import EventKind
from src.models.identity import Body, Wound
from src.models.inventory import Item
from src.models.mining import MiningSession, SessionState
from src.models.world import Vein
from src.units import amount_float


async def collapse(
    session: AsyncSession,
    constants: Constants,
    mining: MiningSession,
    body: Body,
    vein: Vein,
    noise: random.Random,
    moment: datetime,
) -> Sight:
    """Collapse: everything mined during the session is lost, plus wear and maybe a wound."""
    container = await session_container(session, mining)
    lost_items = (
        (await session.execute(select(Item).where(Item.container_id == container.id)))
        .scalars()
        .all()
    )
    lost = sum(amount_float(item.amount) for item in lost_items)
    for item in lost_items:
        await session.delete(item)

    await _wear_tool_for_session(
        session, constants, await _tool(session, mining), extra=constants[R.MINE_COLLAPSE_WEAR]
    )

    #: A cave-in kills -- newcomer and oldtimer alike (08-danger, D-111). The
    #: environment is the only source of death in the alpha, and without this
    #: roll death in the game never comes. Rarer than it wounds: death is not an
    #: ordinary end of the day.
    #: Both rolls remember (D-213). Death by a fair coin came twice in a row
    #: often enough, and the second one reads as the world having it in for
    #: you -- while the mean, which is what the vault states, is untouched.

    killed = await luck.hit(
        session,
        body.identity_id,
        luck.MINE_DEATH,
        constants[R.MINE_COLLAPSE_DEATH_CHANCE],
        dice=noise,
    )
    wounded = not killed and await luck.hit(
        session,
        body.identity_id,
        luck.MINE_WOUND,
        constants[R.MINE_COLLAPSE_WOUND_CHANCE],
        dice=noise,
    )
    if wounded:
        recovery = constants[R.WOUND_RECOVERY_HOURS]
        session.add(
            Wound(
                body_id=body.id,
                #: The same key the death of this collapse writes (D-251):
                #: stored for the journal and the court, read by neither yet.
                cause="cave_in",
                heals_at=moment + timedelta(hours=noise.uniform(recovery.min, recovery.max)),
            )
        )

    mining.state = SessionState.COLLAPSED
    mining.ended_at = moment
    #: The rubble is cleared and the working starts over (D-188): otherwise a
    #: collapsed vein would be locked forever, and veins are finite already (P2).
    await remember_roof(session, mining, roof=None)
    await session.flush()

    await events.record(
        session,
        EventKind.MINING_COLLAPSED,
        actor_identity_id=body.identity_id,
        node_id=vein.node_id,
        session_id=str(mining.id),
        lost=lost,
        swings=mining.swings,
        wounded=wounded,
        killed=killed,
    )
    if killed:
        #: The summary is assembled **before** death: the player must see how
        #: the session ended, not an empty screen. The body is dead after that.
        sight = await _sight(session, constants, mining, body)

        await death.die(session, constants, body, cause="cave_in", now=moment)
        return sight
    return await _sight(session, constants, mining, body)
