# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""craft: a batch aboard that works through the hull's lines (D-288, D-340).

The electrolyser making the air aboard is plumbed: its water comes from the
vessels on its water line and nowhere else -- not the master's canister, not
the tank beside it in the room -- and its oxygen and hydrogen go into the
vessels on their lines. The hull is one building, and a machine in the engine
room fills a cylinder in the hold.

Three rules follow, and all three are said before the work, not after it:

* **a port with no line is refused by name.** An inlet with no line reaches
  nothing (D-288 as amended 2026-09-04), and "not enough water" beside a full
  tank would send the master looking in the wrong place;
* **the oxygen must have somewhere to go.** A master is not a machine and has
  no backlog: a batch whose outlet cannot take the whole of it is refused at
  the start, while nothing is spent. Space poured away by somebody else during
  the hours is the finish's business, and it spills with a word, as a batch
  on the ground does;
* **the hydrogen never stands in the way.** A vent pours what fits and lets
  the rest go overboard; no line at all is not a refusal (owner, 2026-09-13);
* **the tanks are the owner's.** A batch on the lines is work that reaches
  the place, and only whoever may dispose of it runs one (D-315).

Only the air is plumbed: the same electrolyser making oxidiser works room by
room, as every other batch does (`ship.lines.plumbed_for`).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog
from src.engine import liquid
from src.engine.craft._base import CraftError
from src.engine.ship import lines
from src.models.identity import Body
from src.models.world import Node
from src.units import AMOUNT_SCALE

#: Amounts split into thousandths: room for exactly the batch must not read short.
_EPS = 1 / AMOUNT_SCALE


class PortDry(CraftError):
    """A port of the plumbed machine has no line to anything aboard."""


class OutletFull(CraftError):
    """The vessels on the machine's outlet cannot take the whole batch."""


class LinesNotYours(CraftError):
    """The lines reach the owner's tanks, and this body may not dispose of the place."""


async def require_plumbing(
    session: AsyncSession, body: Body, plumbed: lines.Plumbing | None, *, lock: bool
) -> None:
    """The doors a batch on the lines passes before anything is read off them.

    **Whose tanks.** The lines reach every vessel of the hull, so a batch on
    them is the work that reaches the place, and the place is the owner's to
    dispose of (D-315): a guest at the owner's electrolyser works from its own
    hands in any other batch, and must not drain the owner's tanks and fill
    the owner's cylinders through this one (review 2026-09-13).

    **A port with no line** is refused by name.

    **The vessels are taken first** when the batch is started: the vessel
    rows before the stacks inside them, in id order -- the order a hand's pour
    and the automat on the same lines take them in, so the three queue rather
    than deadlock.
    """
    if plumbed is None:
        return
    from src.engine import station  # noqa: PLC0415 -- lazy: station imports craft

    node = await session.get(Node, body.node_id)
    if node is None or not await station.may_build(session, body, node):
        raise LinesNotYours(key="craft-lines-not-yours", station=plumbed.machine.type_key)
    if plumbed.dry:
        port = plumbed.dry[0]
        raise PortDry(
            key="craft-port-no-line",
            station=plumbed.machine.type_key,
            goods=port.liquids[0],
            way=port.way,
        )
    if lock:
        await liquid.lock_vessels(session, plumbed.vessels)


async def require_room(
    session: AsyncSession,
    catalog: Catalog,
    plumbed: lines.Plumbing | None,
    output: str,
    units: float,
    *,
    lock: bool,
) -> None:
    """Refuse a batch its outlet cannot take whole -- before anything is spent.

    Locked at the start, read in the forecast: the start's answer is acted on
    in the same transaction, the forecast's only shown.
    """
    if plumbed is None or output not in plumbed.outlets:
        return
    room = await liquid.room_in(session, catalog, plumbed.outlets[output], output, lock=lock)
    if room + _EPS < units:
        raise OutletFull(
            key="craft-outlet-full",
            station=plumbed.machine.type_key,
            goods=output,
            room=room,
            units=units,
        )
