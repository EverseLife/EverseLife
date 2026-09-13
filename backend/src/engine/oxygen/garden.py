# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The hydroponics breathes (D-288, D-340): what the sown beds of a sealed
hull give back to its vessels.

A bay is a plot aboard (D-234) with a «Гидропонная установка» standing in its
compartment. While a culture grows on a bed there, the bed breathes out
`oxygen.hydroponics_rate` an hour per square metre into the vessels on the
unit's oxygen line, in line order. No unit in the compartment -- the beds grow
and nothing is collected; no line -- the same. Nothing waits: the beds do not
stand the way a machine stands for a full outlet, and what finds no room stays
in the compartment's air, which is to say it is not kept.

**Counted by compartment, not by unit.** How much area one unit serves is not
decided yet (OQ-171), so a compartment's growing area breathes once however
many units stand in it, into the union of their lines -- the way two engines
share one fuel port.

**Growing is what the bed says.** Sown, not ripe, not dead: the bed's own
scales as the farm's tick last wrote them. A ripening or a death is written
the hour it happens (`farm.tick_plots`), so a stretch reads it at most a tick
late; the life of a bed is not recomputed here, because that is the farm's
arithmetic and a second copy of it would drift.

**Only sealed.** Under a sky that has air the hatch may as well be open, and
the bays breathe into the planet's air like the crew does (`_base.sealed`).
"""

from __future__ import annotations

import uuid
from decimal import ROUND_FLOOR

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import liquid, world
from src.engine.ship import lines
from src.engine.ship._base import AIR, HYDROPONICS
from src.engine.ship.belonging import nodes_of
from src.models.farm import Plot, PlotState
from src.models.inventory import Container, ContainerKind, Item
from src.models.ship import Ship
from src.units import (
    ROUND_AMOUNT,
    ROUND_REMAINDER,
    SCALE_MAX,
    SCALE_MIN,
    amount,
    on_grid,
)


async def has_bays(session: AsyncSession, ship: Ship) -> bool:
    """Whether a hydroponic unit stands anywhere aboard -- one small query."""
    nodes = await nodes_of(session, ship)
    yards = select(Container.id).where(
        Container.kind == ContainerKind.NODE, Container.owner_id.in_([node.id for node in nodes])
    )
    found = await session.scalar(
        select(Item.id)
        .where(
            Item.container_id.in_(yards),
            Item.installed.is_(True),
            Item.type_key.in_(world.station_names(HYDROPONICS)),
        )
        .limit(1)
    )
    return found is not None


async def growing_area(session: AsyncSession, node_ids: list[uuid.UUID]) -> dict[uuid.UUID, float]:
    """Square metres under a growing culture, by compartment. A read."""
    if not node_ids:
        return {}
    beds = (
        (
            await session.execute(
                select(Plot).where(Plot.node_id.in_(node_ids), Plot.state == PlotState.SOWN)
            )
        )
        .scalars()
        .all()
    )
    area: dict[uuid.UUID, float] = {}
    for bed in beds:
        if bed.culture_id is None or float(bed.growth) >= SCALE_MAX:
            continue
        if float(bed.health) <= SCALE_MIN:
            continue
        area[bed.node_id] = area.get(bed.node_id, 0.0) + float(bed.area_m2)
    return area


async def breathe_out(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    ship: Ship,
    hold: list[Item],
    hours: float,
) -> float:
    """Pour what the hull's bays breathed this stretch down their lines.

    The caller holds the hull's row -- `air_grown`, the thousandths carried
    over, is written under it -- and has read the hold. Returns what went
    into vessels.
    """
    rate = constants[R.OXYGEN_HYDROPONICS_RATE]
    names = world.station_names(HYDROPONICS)
    units = sorted(
        (one for one in hold if one.installed and one.type_key in names), key=lambda one: one.id
    )
    if rate <= 0 or hours <= 0 or not units:
        return 0.0
    yards = {
        yard.id: yard.owner_id
        for yard in (
            await session.execute(
                select(Container).where(Container.id.in_({one.container_id for one in units}))
            )
        )
        .scalars()
        .all()
    }
    area = await growing_area(session, list(set(yards.values())))
    if not area:
        return 0.0
    kept = 0.0
    carried = float(ship.air_grown)
    for yard_id, node_id in sorted(yards.items(), key=lambda pair: pair[1]):
        grown = area.get(node_id, 0.0) * rate * hours
        if grown <= 0:
            continue
        #: Down to the thousandth, the rest carried: a bed's minute is less
        #: than one, and rounding each minute would add or drop a fifth.
        owed = grown + carried
        given = float(on_grid(owed, ROUND_AMOUNT, ROUND_FLOOR))
        carried = owed - given
        if given <= 0:
            continue
        bay = [one for one in units if one.container_id == yard_id]
        vessels = await lines.port_vessels(session, catalog, ship, bay, lines.AIR_PORT, things=hold)
        fresh = Item(container_id=yard_id, type_key=AIR, amount=amount(given), quality=None)
        session.add(fresh)
        await session.flush()
        kept += given - await liquid.fill_or_drop(session, catalog, fresh, vessels)
    ship.air_grown = on_grid(max(0.0, carried), ROUND_REMAINDER, ROUND_FLOOR)
    await session.flush()
    return kept
