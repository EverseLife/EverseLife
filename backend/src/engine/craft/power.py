# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Electricity for a machine that runs on it (D-269).

Energy is a condition of the machine's work, not an input of the recipe: the
vault marks the machine `powered`, and a manual batch at it draws
`craft.powered_energy_per_hour` for its hours, up front like the materials
(D-135). In a city the pool feeds it at the tariff and the master pays the
treasury -- the same bill an automat gets (D-253); outside a city the cells
standing beside the machine feed it and nobody is billed, the energy was
bought when they were charged (D-071). Neither there -- the machine stands
still, and the refusal comes before a material is touched.

The forecast asks the same question without drinking: what the batch will
take and what it will cost (D-092). A machine driven by the hands answers
nothing at all, and the plan carries no key for it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import battery, energy, events
from src.engine.craft._base import CraftError
from src.models.event import EventKind
from src.models.identity import Body
from src.models.world import Node
from src.units import amount_float


class Unpowered(CraftError):
    """The machine runs on electricity and there is none to be had here."""


def need_of(constants: Constants, catalog: Catalog, machine: str | None, hours: float) -> float:
    """What these hours at this machine draw; nought for one driven by the hands."""
    if machine is None or hours <= 0 or not catalog.recipes.powered(machine):
        return 0.0
    return hours * constants[R.CRAFT_POWERED_ENERGY_PER_HOUR]


async def forecast(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    machine: str | None,
    hours: float,
) -> tuple[float, int | None] | None:
    """The energy and its price before the batch -- a read, nothing is drawn.

    Outside a city there is no price at all, not a nought: the cells beside
    the machine were paid for at charging, and the window tells the cells
    apart from a tariff by the price being absent (D-225) -- the same
    question `draw` asks, so the two never disagree about where the walls
    are. Whether the cells hold enough is the start's question, and its
    refusal says so; the forecast tells what will be asked of them.
    """
    need = need_of(constants, catalog, machine, hours)
    if need <= 0:
        return None
    node = await session.get(Node, body.node_id)
    if node is None or await energy.grid_node(session, node) is None:
        return need, None
    return need, await energy.price_of(session, constants, node, need)


async def draw(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    machine: str | None,
    hours: float,
    *,
    now: datetime | None = None,
) -> float:
    """Take the electricity for these hours up front, like the materials (D-135).

    Returns what was drawn. The cells beside the machine are locked in id order
    like every write-off over a node's yard (`battery.batteries_in`), and asked
    whether they hold enough **before** any is taken: a half-fed batch is an
    automat's business (D-253), not a master's -- the master is refused whole.
    """
    need = need_of(constants, catalog, machine, hours)
    if need <= 0 or machine is None:
        return 0.0
    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover -- a body without a node is a bug
        raise CraftError(key="craft-body-off-node")
    if await energy.grid_node(session, node) is not None:
        #: The pool door is the automat's and the printer's too; only the words
        #: are the machine's own -- a batch that "requires energy" is not what
        #: happened, the machine stood still -- and one refusal class whichever
        #: side of the walls it stood on: the caller asks "is it unpowered".
        try:
            await energy.draw_for_work(session, constants, body, need, goods=machine, now=now)
        except energy.NotEnough as short:
            raise Unpowered(
                key="craft-unpowered-short",
                inner=short.inner,
                goods=machine,
                need=short.params["need"],
                have=short.params["have"],
            ) from short
        except energy.NoGrid as nowhere:  # pragma: no cover -- the grid was just seen
            raise Unpowered(key="craft-unpowered-no-grid", goods=machine) from nowhere
        return need

    moment = now or datetime.now(UTC)
    cells = await battery.batteries_in(session, node)
    have = sum(
        battery.charge_of(constants, cell, now=moment) * amount_float(cell.amount) for cell in cells
    )
    if not cells:
        raise Unpowered(key="craft-unpowered-no-grid", goods=machine)
    if have < need:
        raise Unpowered(key="craft-unpowered-cells", goods=machine, need=need, have=have)
    await battery.drain_cells(session, constants, cells, need, now=moment)
    #: The same line in the journal as a draw from the pool: what the machine
    #: drank is one story whichever side of the walls it stood on.
    await events.record(
        session,
        EventKind.ENERGY_DRAWN,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        energy=need,
        paid=0,
        work=machine,
    )
    return need
