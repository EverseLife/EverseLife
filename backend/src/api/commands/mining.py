# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The face, the rigs, the mint.

Split out of `api/session.py` (review 2026-08-23, wave 3): the
socket loop stayed there, the commands live by domain.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.commands.common import _alive, _body, _own_item, _stamp
from src.api.commands.views import _optional_uuid, _sight, _tiers
from src.api.registry import Refused, command
from src.constants import current, current_catalog
from src.engine import (
    coin,
    mining,
    rig,
)
from src.engine import pow as device
from src.models.identity import Identity
from src.models.mining import MiningSession, Pace, PowChallenge, SessionState
from src.models.rig import Rig as RigRow
from src.models.world import Vein
from src.units import amount_float


@command("pow.challenge")
async def _challenge(state: dict, db: AsyncSession, message: dict) -> dict:
    """Issue a device-fee challenge. The client computes it in a Web Worker."""
    identity = await db.get(Identity, state["identity_id"])
    if identity is None:  # pragma: no cover
        raise Refused(key="cmd-identity-gone")
    task = await device.issue(db, current(), identity.account_id)
    return {"challenge": str(task.id), "nonce": task.nonce.hex()}


@command("mine.start")
async def _mine_start(state: dict, db: AsyncSession, message: dict) -> dict:
    """Open a face. Without a paid challenge the session does not start."""
    constants = current()
    body = await _body(db, state["identity_id"])
    if body is None:
        raise Refused(key="cmd-no-live-body")

    task = await db.get(PowChallenge, uuid.UUID(message["challenge"]))
    if task is None or task.account_id != (await db.get(Identity, body.identity_id)).account_id:
        raise Refused(key="cmd-not-your-job")
    await device.verify(db, constants, task, bytes.fromhex(message["answer"]))

    vein = await db.get(Vein, uuid.UUID(message["vein"]))
    if vein is None:
        raise Refused(key="cmd-no-such-vein")

    session = await mining.start(
        db,
        constants,
        body,
        vein,
        catalog=current_catalog(),
        tool_item_id=_optional_uuid(message.get("tool")),
        pace=Pace(message.get("pace", Pace.STEADY.value)),
    )
    task.spent_on_session_id = session.id
    state["session_id"] = session.id
    return _sight(session, await mining.sight(db, constants, session))


@command("mine.swing")
async def _mine_swing(state: dict, db: AsyncSession, message: dict) -> dict:
    """One swing of the pickaxe: ore into the hands, the roof a little weaker. The reply is the
    sight, not the hidden number (D-092)."""
    session = await _active(state, db)
    return _sight(session, await mining.swing(db, current(), session))


@command("mine.timber")
async def _mine_timber(state: dict, db: AsyncSession, message: dict) -> dict:
    """Set a support: one timber from the hands props the roof (D-143)."""
    session = await _active(state, db)
    return _sight(session, await mining.timber(db, current(), session))


@command("mine.pace")
async def _mine_pace(state: dict, db: AsyncSession, message: dict) -> dict:
    """Set the pace of mining: `pace` is `careful`, `steady` or `hard` -- output against roof risk
    (D-143)."""
    session = await _active(state, db)
    pace = Pace(message["pace"])
    return _sight(session, await mining.set_pace(db, current(), session, pace))


@command("mine.leave")
async def _mine_leave(state: dict, db: AsyncSession, message: dict) -> dict:
    """Leave the face: the session ends, what was mined is in the hands (D-143)."""
    session = await _active(state, db)
    haul = await mining.leave(db, current(), session)
    state.pop("session_id", None)
    return {"left": True, "haul": haul}


@command("coin.mint")
async def _coin_mint(state: dict, db: AsyncSession, message: dict) -> dict:
    """Mint a coin. One fineness for the whole world -- 900 per mille, no choice (D-016)."""
    body = await _alive(state, db)
    batch = await coin.mint(
        db,
        current(),
        current_catalog(),
        body,
        str(message["coin"]),
        float(message["count"]),
        tiers=_tiers(message),
    )
    return {
        "batch": str(batch.id),
        "coin": batch.output,
        "units": amount_float(batch.units),
        "fineness": float(batch.fineness),
        "spent": batch.spent,
        "ready_at": _stamp(batch.ready_at),
    }


@command("coin.melt")
async def _coin_melt(state: dict, db: AsyncSession, message: dict) -> dict:
    """Melt coins: metal returns by their fineness, minus loss."""
    body = await _alive(state, db)
    item = await _own_item(db, body, message["item"])
    batch = await coin.melt(db, current(), current_catalog(), body, item, float(message["count"]))
    return {
        "batch": str(batch.id),
        "coin": batch.output,
        "units": amount_float(batch.units),
        "fineness": float(batch.fineness),
        "ready_at": _stamp(batch.ready_at),
    }


@command("rig.place")
async def _rig_place(state: dict, db: AsyncSession, message: dict) -> dict:
    """Place a drilling rig on a vein. From then on it works without the player (D-115)."""
    body = await _alive(state, db)
    item = await _own_item(db, body, message["item"])
    vein = await db.get(Vein, uuid.UUID(message["vein"]))
    if vein is None:
        raise Refused(key="cmd-no-such-vein")
    installation = await rig.place(db, body, item, vein)
    return {"rig": str(installation.id), "vein": vein.resource}


@command("rig.status")
async def _rig_status(state: dict, db: AsyncSession, message: dict) -> dict:
    """What stands in the node: hopper, fuel, condition. In-person scene."""
    body = await _body(db, state["identity_id"])
    if body is None:
        raise Refused(key="cmd-no-live-body")
    return {"rigs": await rig.status(db, current(), body.node_id)}


@command("rig.empty")
async def _rig_empty(state: dict, db: AsyncSession, message: dict) -> dict:
    """Empty the hopper. On foot: without a carter the enterprise stands still."""
    body = await _alive(state, db)
    installation = await db.get(RigRow, uuid.UUID(message["rig"]))
    if installation is None:
        raise Refused(key="cmd-no-such-rig")
    taken = await rig.empty_hopper(db, current(), body, installation)
    return {"taken": taken}


async def _active(state: dict, db: AsyncSession) -> MiningSession:
    session_id = state.get("session_id")
    if session_id is None:
        #: The client may have reconnected -- look for the body's open session.
        body = await _body(db, state["identity_id"])
        if body is None:
            raise Refused(key="cmd-no-live-body")
        found = (
            (
                await db.execute(
                    select(MiningSession).where(
                        MiningSession.body_id == body.id,
                        MiningSession.state == SessionState.ACTIVE,
                    )
                )
            )
            .scalars()
            .first()
        )
        if found is None:
            raise Refused(key="cmd-session-not-open")
        state["session_id"] = found.id
        return found

    session = await db.get(MiningSession, session_id)
    if session is None:  # pragma: no cover
        raise Refused(key="cmd-session-gone")
    return session
