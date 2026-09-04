# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The collapse: how a session ends badly (D-143, D-111, D-294).

Stability at zero costs **everything mined during the session** -- plus tool
wear, and then the body itself: the **first** cave-in a body lives through
spares it, with a roll for a wound; the **second** kills it, and everything it
carried stays lying on the floor of the node (D-294). The environment is the
only source of death in the alpha (D-111), and this module is where it lives.

The stake alone never held the mechanic up. Walking out banks the haul at any
moment (`face.leave`), so a miner who left at the first sign of trouble, walked
straight back in and dropped the roof risked one swing's worth of ore -- and
got a whole roof for it, because a collapsed working starts over (D-188).
Support was optional again, which is the hole D-143 exists to close. What the
count adds is a price that cannot be banked: the body.
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
    rubble_depth,
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
    roof: float,
    noise: random.Random,
    moment: datetime,
) -> Sight:
    """Collapse: everything mined during the session is lost, plus wear, plus the body's turn.

    The body's second cave-in is its last. The count is on the body and is
    read and written here under the row lock `face.swing` already holds.

    `roof` is the one that came down -- the swing's own answer, passed in
    because it no longer survives anywhere to be read: the rubble is cleared
    below, and the vein is left saying the working starts over. The summary
    the player gets must be of the face that buried them and not of the fresh
    one they are not standing in.
    """
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

    #: A cave-in kills -- newcomer and oldtimer alike (08-danger, D-111): the
    #: environment is the only source of death in the alpha, and without this
    #: death in the game never comes. It no longer rolls for it (D-294). **The
    #: first cave-in spares the body, the second takes it**, and the count sits
    #: on the body, so a newly printed one meets the roof with its grace back.
    #:
    #: The coin it replaces -- `mine.collapse_death_chance`, a twentieth --
    #: could be waited out, and that is what players did: the haul is banked by
    #: walking out (`leave`), so the stake of a collapse shrank to the one swing
    #: taken after the last exit, and dropping the roof on purpose was the
    #: cheapest way to get a whole one back. Two cave-ins now cost a body, and
    #: the arithmetic no longer works.
    #:
    #: How many it lives through is the vault's to say (D-065) -- a playtest
    #: that wants a miner to get two warnings changes `mine.collapses_survived`
    #: and the two lines of copy that name that count, never this file (the
    #: constant carries the note). Counted under the body's row lock: `face.swing` takes
    #: the row FOR UPDATE before the swing that arrives here, and a count that
    #: decides a death belongs on the same list as money, remainders and stamina.
    body.cave_ins += 1
    killed = body.cave_ins > constants[R.MINE_COLLAPSES_SURVIVED]
    #: The sparing one still rolls for a wound, and that roll remembers (D-213).
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
    #: The working is buried, not cleared (D-301). It starts over as D-188
    #: always promised -- otherwise a collapsed vein would be locked forever,
    #: and veins are finite already (P2) -- but the first miner to come back
    #: digs the rubble out first, swinging for nothing. Free, the clearing was
    #: a gift to whoever stayed in the face: the roof is shared (D-188,
    #: D-099), so a body dropped on purpose handed the artel a whole working
    #: for the price of half a life (D-294), and the support the mechanic is
    #: built around went back to being optional.
    remember_roof(vein, -rubble_depth(constants, vein))
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
        sight = await _sight(session, constants, mining, body, roof, vein.roof_salt)

        #: Buried, not scattered: the rock comes down on the body and its
        #: pocket in one place, so everything it carried stays lying on the
        #: floor of the node, whole (D-294). Whoever comes to the face next
        #: finds it -- the kit of the second cave-in is the artel's business,
        #: not a sink.
        await death.die(session, constants, body, cause="cave_in", now=moment, buried=True)
        return sight
    return await _sight(session, constants, mining, body, roof, vein.roof_salt)
