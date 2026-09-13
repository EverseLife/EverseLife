# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The automat aboard, on the hull's lines (D-288, D-340).

On the ground an automat works off its node: lubricant and liquid inputs from
the vessels standing in the yard, a liquid output into them (D-253). Aboard
the hull is one building, and the air machine -- the reactor programmed with
the air -- works through its ports instead: water and lubricant from the
vessels on their lines, oxygen into the vessels on its outlet in line order,
hydrogen into the vessels on its vent and overboard when they are full or
there are none. The four limiters are the same four: lubricant, inputs, room
and electricity (the hull's cells, the bus of D-288).

**Stalls are told once.** Standing still is not an error -- it is the
enterprise's obligation -- but a crew that is not told the air machine stopped
learns it when the cylinders run dry. So the reason is kept on the row
(`Automat.stall`) and the journal is written when it appears or changes, to
everybody aboard: the line of a port that ran dry, the outlet full, the cells
flat. Working again clears it without a word.

**Locks in the hand's order.** The vessel rows first, in id order, then the
stacks inside them in one query: a hand pouring into the same tank takes the
vessels first as well (`liquid.pour`), and two orders over the same rows would
be a deadlock waiting for its minute.
"""

from __future__ import annotations

import math
from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import events, liquid, stock, wear, world
from src.engine import ship as vessels
from src.engine.automat._base import LUBE
from src.engine.automat.bill import draw_energy
from src.engine.craft import Procedure
from src.engine.ship import lines
from src.models.automat import Automat as AutomatRow
from src.models.event import EventKind
from src.models.inventory import Container, Item
from src.models.world import Node
from src.units import AMOUNT_SCALE, amount, amount_float

#: The reason a machine stands when it is not a port's: the cells are flat.
#: Every other reason is the name of the port that stopped it (D-340).
POWER = "power"

#: How much shorter than the stretch the work may come out before the
#: machine is said to have stood: the last digit of an amount is rounding.
_EPS = 1 / AMOUNT_SCALE


async def advance_on_lines(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    row: AutomatRow,
    machine: Item,
    node: Node,
    yard: Container,
    proc: Procedure,
    plumbed: lines.Plumbing,
    *,
    hours: float,
    unit_hours: float,
    now: datetime,
) -> float:
    """Advance a plumbed automat by `hours`. Returns the units paid out.

    The caller holds the row, has charged the wear and knows the programme;
    this is the stretch itself, as `run.advance` does it on the ground.
    """
    book = catalog.recipes
    ports = {
        port.name: port
        for port in lines.plumbed_for(constants, catalog, machine.type_key, proc.output)
    }
    await liquid.lock_vessels(session, plumbed.vessels)

    #: Every stack the stretch touches in ONE query and one lock order: the
    #: plumbed liquids off their lines, anything else off the yard (the air
    #: has nothing else, and a second recipe on lines would).
    lube_names = tuple(sorted(world.station_names(LUBE)))
    loose = [name for name in proc.per_unit if name not in plumbed.inlets]
    yard_reach = await liquid.reach(session, catalog, yard) if loose else []
    within: dict[str, tuple] = {
        name: plumbed.inlets.get(name, ()) for name in (*proc.per_unit, *lube_names)
    }
    for name in loose:
        within[name] = tuple(yard_reach)
    everywhere = sorted({box for boxes in within.values() for box in boxes})
    found = await stock.locked_stacks(session, everywhere, tuple(within)) if everywhere else []
    by_name: dict[str, list[Item]] = {}
    for name, boxes in within.items():
        rank = {box: place for place, box in enumerate(boxes)}
        by_name[name] = sorted(
            (one for one in found if one.type_key == name and one.container_id in rank),
            key=lambda one: (rank[one.container_id], one.id),
        )

    lube_rate = constants[R.AUTO_LUBE_PER_HOUR]
    lube_stacks = [stack for name in lube_names for stack in by_name.get(name, [])]
    lube_have = sum(amount_float(stack.amount) for stack in lube_stacks)
    lube_hours = (lube_have / lube_rate) if lube_rate > 0 else hours

    backlog = float(row.backlog)
    units_by_inputs = math.inf
    short_of: str | None = None
    for name, per in proc.per_unit.items():
        if per <= 0:
            continue
        have = sum(amount_float(stack.amount) for stack in by_name.get(name, [])) / per
        if have < units_by_inputs:
            units_by_inputs, short_of = have, name
    input_hours = max(0.0, (units_by_inputs - backlog) * unit_hours)

    outlet = plumbed.outlets.get(proc.output, [])
    room_units = (
        await liquid.room_in(session, catalog, outlet, proc.output, lock=False)
        if book.is_liquid(proc.output)
        else math.inf
    )
    room_hours = max(0.0, (room_units - backlog) * unit_hours)

    #: Which limiter binds, by the name the crew is told: the lubricant's
    #: port, the input that runs out first, the outlet.
    limits = [
        (name, value)
        for name, value in (
            (lines.LUBE_PORT, lube_hours),
            (short_of, input_hours),
            (proc.output, room_hours),
        )
        if name is not None
    ]
    worked = max(0.0, min(hours, *(value for _, value in limits)))
    stall = min(limits, key=lambda one: one[1])[0] if worked + _EPS < hours else None

    energy_rate = constants[R.AUTO_ENERGY_PER_HOUR]
    if worked > 0 and energy_rate > 0:
        powered = await draw_energy(session, constants, row, node, worked, energy_rate, now=now)
        if powered + _EPS < worked:
            stall = POWER
        worked = powered

    produced = 0.0
    if worked > 0:
        progress = min(backlog + worked / unit_hours, units_by_inputs, room_units)
        paid = progress
        if paid > 0:
            await _pay_out(
                session, constants, catalog, row, machine, yard, proc, plumbed, paid, by_name
            )
            produced = paid
            if row.owner_identity_id is not None:
                await events.announce(
                    session,
                    touches=("node",),
                    identity_id=row.owner_identity_id,
                    event="automat.paid",
                    goods=proc.output,
                    made=amount_float(amount(paid)),
                )
        row.backlog = Decimal(str(max(0.0, progress - paid)))
        if lube_rate > 0:
            await stock.consume(session, lube_stacks, amount(lube_rate * worked))

    await _tell(session, row, machine, plumbed, ports, stall)
    row.counted_at = now
    await session.flush()
    return produced


async def _pay_out(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    row: AutomatRow,
    machine: Item,
    yard: Container,
    proc: Procedure,
    plumbed: lines.Plumbing,
    paid: float,
    by_name: dict[str, list[Item]],
) -> None:
    """Consume the inputs for `paid` units and pour the output down the lines.

    The oxygen into its outlet in line order: the room was counted under the
    vessels' locks, so nothing spills but what a race the locks forbid would
    spill. The hydrogen into its vent, and the rest overboard without a word.
    """
    for name, per in proc.per_unit.items():
        if per > 0:
            await stock.consume(session, by_name.get(name, []), amount(per * paid))
    quality = Decimal(str(min(constants[R.AUTO_QUALITY_CAP], wear.effective(constants, machine))))
    fresh = Item(container_id=yard.id, type_key=proc.output, amount=amount(paid), quality=quality)
    session.add(fresh)
    await session.flush()
    spilled = await liquid.fill_or_drop(
        session, catalog, fresh, plumbed.outlets.get(proc.output, [])
    )
    if spilled > 0:  # pragma: no cover -- the vessels are locked for the stretch
        await events.record(
            session,
            EventKind.STORAGE_SPILLED,
            node_id=row.node_id,
            automat=str(row.id),
            spilled=spilled,
            goods=proc.output,
        )
    for name, per in catalog.recipes.byproduct_of(proc.output).items():
        extra = Item(
            container_id=yard.id, type_key=name, amount=amount(per * paid), quality=quality
        )
        session.add(extra)
        await session.flush()
        await liquid.fill_or_drop(session, catalog, extra, plumbed.vents.get(name, []))


async def _tell(
    session: AsyncSession,
    row: AutomatRow,
    machine: Item,
    plumbed: lines.Plumbing,
    ports: dict[str, lines.Port],
    stall: str | None,
) -> None:
    """Keep the reason on the row, and tell the crew when it appears or changes."""
    if stall == row.stall:
        return
    row.stall = stall
    if stall is None:
        return
    port = ports.get(stall)
    if stall == POWER:
        kind, goods = EventKind.SHIP_MACHINE_UNPOWERED, machine.type_key
    elif port is not None and port.pours:
        kind, goods = EventKind.SHIP_MACHINE_FULL, port.liquids[0]
    else:
        #: A dry line, or -- for an input that is no port -- the yard ran out.
        kind, goods = EventKind.SHIP_MACHINE_DRY, stall if port is None else port.liquids[0]
    hull = plumbed.ship
    crew = await vessels.crew_of(session, hull)
    aboard = {
        f"crew{seat}_identity_id": str(member.identity_id) for seat, member in enumerate(crew)
    }
    await events.record(
        session,
        kind,
        actor_identity_id=hull.owner_identity_id,
        node_id=hull.connector_node_id,
        ship_id=str(hull.id),
        name=hull.name,
        item_id=str(machine.id),
        #: What stopped it, by goods key, for the line in the journal: the
        #: liquid of the dry or full port, or the machine itself for the cells.
        goods=goods,
        machine=machine.type_key,
        why=stall,
        **({"programmer_identity_id": str(row.owner_identity_id)} if row.owner_identity_id else {}),
        **aboard,
    )
